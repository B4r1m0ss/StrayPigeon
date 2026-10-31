from __future__ import annotations

import base64
import email
import imaplib
import time

from ..imap_id import needs_id, send_id


class ImapStoreChannel:
    """Shared-mailbox store. EXPERIMENTAL: not used by the v0.1 demo.

    Both peers use the same mailbox and folder. Messages are appended without
    SMTP delivery. The body is base64; v0.2 wraps it in the AEAD envelope.

    SECURITY:
    - the password is passed in by the caller from an environment variable;
    - it is never logged and never written to disk;
    - use a dedicated mailbox + app password, never the main password.
    """

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        folder: str = "p2p-demo",
        search: str = "ALL",
    ):
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
        # Bound blocking IMAP calls so a GUI Disconnect cannot hang forever.
        try:
            self.conn.sock.settimeout(10.0)
        except Exception:  # noqa: BLE001
            pass
        if needs_id(self.host):
            try:
                send_id(self.conn)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"IMAP ID failed for {self.host}") from exc
        try:
            self.conn.create(self.folder)
        except imaplib.IMAP4.error:
            pass
        self.conn.select(self.folder)

    def send(self, sid: str, seq: int, blob: bytes) -> None:
        self._connect()
        body = base64.b64encode(blob).decode("ascii")
        msg = (
            f"Subject: p2p1/{sid}/{seq}\r\n"
            "Content-Type: text/plain; charset=us-ascii\r\n"
            "Content-Transfer-Encoding: 7bit\r\n"
            f"\r\n{body}\r\n"
        )
        typ, _ = self.conn.append(
            self.folder, "", imaplib.Time2Internaldate(time.time()), msg.encode("ascii")
        )
        if typ != "OK":
            raise RuntimeError("IMAP APPEND failed")

    def recv(self, sid: str, after: int) -> list[tuple[int, bytes]]:
        self._connect()
        # TODO(v0.2): cache UIDs and only fetch UID > last_uid. For the first
        # working version, fetch all and filter by the seq embedded in Subject.
        typ, data = self.conn.uid("SEARCH", None, self.search)
        if typ != "OK" or not data:
            return []
        out: list[tuple[int, int, bytes]] = []
        for uid in data[0].split():
            typ, msg_data = self.conn.uid("FETCH", uid, "(RFC822)")
            if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                continue
            raw = msg_data[0][1]
            try:
                parsed = email.message_from_bytes(raw)
                subject = str(parsed.get("Subject", ""))
                parts = subject.split("/")
                if len(parts) != 3 or parts[0] != "p2p1":
                    continue
                msg_sid, msg_seq = parts[1], int(parts[2])
                if sid and msg_sid != sid:
                    continue
                if msg_seq <= after:
                    continue
                payload = parsed.get_payload(decode=True) or b""
                blob = base64.b64decode(payload.strip())
                out.append((int(uid), msg_seq, blob))
            except Exception:  # noqa: BLE001
                continue
        # Highest IMAP UID == newest email. Return newest first so a responder
        # does not pick an older p2p1/... candidate from a previous attempt.
        out.sort(key=lambda item: item[0], reverse=True)
        return [(msg_seq, blob) for _, msg_seq, blob in out]

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
