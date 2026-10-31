"""One mailbox poller that routes p2p1/<sid>/<seq> signals to contacts.

The old design had one IMAP connection per Session. That does not scale to 16
concurrent chats and makes multiple sessions race for the same newest email.
This router:
- fetches only UIDs newer than last_uid;
- parses p2p1/<sid>/<seq>;
- routes a bound sid to its contact;
- for an unbound sid, tries each contact PSK and binds on success;
- de-duplicates by (sid, seq);
- optionally falls back to SEARCH ALL for servers like 163.

The router is transport-agnostic; tests use a fake source, production uses
:class:`ImapMailbox`.
"""

from __future__ import annotations

import base64
import email
import imaplib
import json
from dataclasses import dataclass
from typing import Callable, Protocol

from ..imap_id import needs_id, send_id
from ..keyexchange import open_signal


@dataclass(frozen=True)
class RawMail:
    uid: int
    subject: str
    body: str  # base64-encoded envelope, as stored in the mail body


@dataclass(frozen=True)
class RoutedSignal:
    uid: int
    sid: str
    seq: int
    msg_type: str
    contact_id: str
    message: dict


class MailboxSource(Protocol):
    def fetch_after(self, last_uid: int) -> list[RawMail]: ...


class MailRouter:
    def __init__(
        self,
        source: MailboxSource,
        contacts: dict[str, bytes] | None = None,
        on_signal: Callable[[RoutedSignal], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.source = source
        self.contacts = dict(contacts or {})
        self.on_signal = on_signal
        self.on_error = on_error
        self.last_uid = 0
        self._sid_to_contact: dict[str, str] = {}
        self._last_seq: dict[str, int] = {}

    def bind_contact(self, contact_id: str, psk: bytes) -> None:
        if len(psk) != 32:
            raise ValueError("PSK must be 32 bytes")
        self.contacts[contact_id] = psk

    def unbind_contact(self, contact_id: str) -> None:
        """Forget a contact PSK and all sids bound to it (vault deletion)."""
        self.contacts.pop(contact_id, None)
        for sid, bound in list(self._sid_to_contact.items()):
            if bound == contact_id:
                del self._sid_to_contact[sid]

    def bind_sid(self, sid: str, contact_id: str) -> None:
        if contact_id not in self.contacts:
            raise ValueError(f"unknown contact: {contact_id}")
        self._sid_to_contact[sid] = contact_id

    def contact_for_sid(self, sid: str) -> str | None:
        return self._sid_to_contact.get(sid)

    def poll_once(self) -> list[RoutedSignal]:
        routed: list[RoutedSignal] = []
        for raw in self.source.fetch_after(self.last_uid):
            self.last_uid = max(self.last_uid, raw.uid)
            try:
                sid, seq, envelope = self._parse(raw)
            except Exception as exc:  # noqa: BLE001
                self._error(f"bad mail uid={raw.uid}: {type(exc).__name__}: {exc}")
                continue
            contact_id = self._sid_to_contact.get(sid)
            plain: bytes | None = None
            if contact_id is not None:
                psk = self.contacts.get(contact_id)
                if psk is None:
                    self._error(f"sid {sid} bound to missing contact {contact_id}")
                    continue
                try:
                    plain = open_signal(psk, envelope)
                except Exception as exc:  # noqa: BLE001
                    self._error(f"AUTH failed for {contact_id}/{sid}: {type(exc).__name__}")
                    continue
            else:
                for candidate, psk in self.contacts.items():
                    try:
                        plain = open_signal(psk, envelope)
                    except Exception:  # noqa: BLE001
                        continue
                    contact_id = candidate
                    self._sid_to_contact[sid] = candidate
                    break
                if plain is None or contact_id is None:
                    self._error(f"no contact PSK matches sid {sid}")
                    continue
            if seq <= self._last_seq.get(sid, 0):
                continue
            self._last_seq[sid] = seq
            try:
                message = json.loads(plain)
            except Exception as exc:  # noqa: BLE001
                self._error(f"bad signal JSON for {contact_id}/{sid}: {exc}")
                continue
            if not isinstance(message, dict):
                self._error(f"bad signal payload for {contact_id}/{sid}")
                continue
            msg_type = str(envelope.get("type") or message.get("type") or "")
            result = RoutedSignal(
                uid=raw.uid,
                sid=sid,
                seq=seq,
                msg_type=msg_type,
                contact_id=contact_id,
                message=message,
            )
            routed.append(result)
            if self.on_signal is not None:
                self.on_signal(result)
        return routed

    def _parse(self, raw: RawMail) -> tuple[str, int, dict]:
        parts = raw.subject.strip().split("/")
        if len(parts) != 3 or parts[0] != "p2p1":
            raise ValueError("not a p2p1 subject")
        sid = parts[1]
        seq = int(parts[2])
        body = base64.b64decode(raw.body.strip(), validate=False)
        envelope = json.loads(body.decode("utf-8"))
        if not isinstance(envelope, dict):
            raise ValueError("envelope is not an object")
        if int(envelope.get("v", 0)) != 1:
            raise ValueError("unsupported envelope version")
        if str(envelope.get("sid")) != sid:
            raise ValueError("subject sid != envelope sid")
        if int(envelope.get("seq", -1)) != seq:
            raise ValueError("subject seq != envelope seq")
        return sid, seq, envelope

    def _error(self, message: str) -> None:
        if self.on_error is not None:
            self.on_error(message)


class ImapMailbox:
    """Production single-IMAP source with 163-style SEARCH ALL fallback."""

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        folder: str = "INBOX",
        search: str = 'SUBJECT "p2p1/"',
    ) -> None:
        self.host = host
        self.user = user
        self._password = password
        self.folder = folder
        self.search = search
        self.conn: imaplib.IMAP4_SSL | None = None

    def _connect(self) -> None:
        if self.conn is not None:
            return
        self.conn = imaplib.IMAP4_SSL(self.host, 993)
        self.conn.login(self.user, self._password)
        try:
            self.conn.sock.settimeout(10.0)
        except Exception:  # noqa: BLE001
            pass
        if needs_id(self.host):
            try:
                send_id(self.conn)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"IMAP ID failed for {self.host}") from exc
        self.conn.select(self.folder)

    def _search(self, query: str) -> list[int]:
        typ, data = self.conn.uid("SEARCH", None, query)  # type: ignore[union-attr]
        if typ != "OK" or not data or not data[0]:
            return []
        return [int(part) for part in data[0].split()]

    def fetch_after(self, last_uid: int) -> list[RawMail]:
        self._connect()
        uids = self._search(self.search)
        if not uids and self.search != "ALL":
            # 163 does not always match SUBJECT "p2p1/"; scan ALL but still
            # filter by Subject in Python (and by last_uid here).
            uids = self._search("ALL")
        out: list[RawMail] = []
        for uid in sorted(uids):
            if uid <= last_uid:
                continue
            typ, msg_data = self.conn.uid("FETCH", str(uid), "(RFC822)")  # type: ignore[union-attr]
            if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                continue
            raw = msg_data[0][1]
            try:
                parsed = email.message_from_bytes(raw)
                subject = str(parsed.get("Subject", ""))
                parts = subject.split("/")
                if len(parts) != 3 or parts[0] != "p2p1":
                    continue
                body = (parsed.get_payload(decode=True) or b"").decode("ascii").strip()
                if not body:
                    continue
                out.append(RawMail(uid=uid, subject=subject, body=body))
            except Exception:  # noqa: BLE001
                continue
        return out

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.sock.settimeout(2.0)
            except Exception:  # noqa: BLE001
                pass
            try:
                self.conn.logout()
            except Exception:  # noqa: BLE001
                pass
            self.conn = None
