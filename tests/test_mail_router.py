from __future__ import annotations

import base64
import json
import os
import time
import unittest
from email.message import EmailMessage
from unittest.mock import patch

from p2p.keyexchange import seal_signal
from p2p.signal.mail_router import ImapMailbox, MailRouter, RawMail


def _mail(
    uid: int,
    sid: str,
    seq: int,
    psk: bytes,
    msg_type: str = "CANDIDATE",
) -> RawMail:
    message = {"type": msg_type, "sid": sid, "ts": time.time()}
    envelope = seal_signal(psk, sid, seq, msg_type, json.dumps(message).encode())
    body = base64.b64encode(json.dumps(envelope).encode("utf-8")).decode("ascii")
    return RawMail(uid=uid, subject=f"p2p1/{sid}/{seq}", body=body)


class FakeMailbox:
    def __init__(self, mails: list[RawMail]) -> None:
        self.mails = mails
        self.calls: list[int] = []

    def fetch_after(self, last_uid: int) -> list[RawMail]:
        self.calls.append(last_uid)
        return [m for m in self.mails if m.uid > last_uid]


class MailRouterTests(unittest.TestCase):
    def test_routes_16_bound_sids(self) -> None:
        contacts = {f"c{i}": os.urandom(32) for i in range(16)}
        mails = []
        router = MailRouter(FakeMailbox(mails), contacts)
        for i in range(16):
            sid = f"sid-{i:02d}"
            router.bind_sid(sid, f"c{i}")
            mails.append(_mail(i + 1, sid, 1, contacts[f"c{i}"]))
        routed = router.poll_once()
        self.assertEqual(len(routed), 16)
        self.assertEqual({r.contact_id for r in routed}, set(contacts))
        self.assertEqual(len({r.sid for r in routed}), 16)
        self.assertEqual(router.last_uid, 16)

    def test_unbound_sid_tries_psk_and_binds(self) -> None:
        psk = os.urandom(32)
        router = MailRouter(FakeMailbox([_mail(1, "new-sid", 1, psk)]), {"bob": psk})
        routed = router.poll_once()
        self.assertEqual(len(routed), 1)
        self.assertEqual(routed[0].contact_id, "bob")
        self.assertEqual(router.contact_for_sid("new-sid"), "bob")

    def test_unknown_psk_is_reported_not_crashing(self) -> None:
        errors: list[str] = []
        router = MailRouter(
            FakeMailbox([_mail(1, "sid-x", 1, os.urandom(32))]),
            {"bob": os.urandom(32)},
            on_error=errors.append,
        )
        self.assertEqual(router.poll_once(), [])
        self.assertTrue(errors)
        self.assertIn("no contact PSK", errors[0])

    def test_last_uid_and_sequence_dedupe(self) -> None:
        psk = os.urandom(32)
        mailbox = FakeMailbox([_mail(10, "sid-x", 1, psk)])
        router = MailRouter(mailbox, {"bob": psk})
        self.assertEqual(len(router.poll_once()), 1)
        self.assertEqual(router.poll_once(), [])
        self.assertEqual(mailbox.calls, [0, 10])

    def test_163_search_all_fallback(self) -> None:
        sid = "sid-163"
        psk = os.urandom(32)
        raw = _mail(7, sid, 1, psk)
        msg = EmailMessage()
        msg["Subject"] = raw.subject
        msg.set_content(raw.body)
        raw_bytes = msg.as_bytes()
        searches: list[str] = []

        class FakeSock:
            def settimeout(self, _timeout: float) -> None:
                pass

        class FakeConn:
            sock = FakeSock()

            def login(self, _user: str, _password: str) -> None:
                pass

            def select(self, _folder: str) -> None:
                pass

            def uid(self, command: str, *args):
                if command == "SEARCH":
                    searches.append(str(args[1]))
                    if args[1] == 'SUBJECT "p2p1/"':
                        return "OK", [b""]
                    return "OK", [b"7"]
                if command == "FETCH":
                    return "OK", [(b"1 (RFC822 {123}", raw_bytes)]
                raise AssertionError(command)

            def logout(self) -> None:
                pass

        conn = FakeConn()
        mailbox = ImapMailbox("imap.example.com", "a@example.com", "secret")
        with patch("p2p.signal.mail_router.imaplib.IMAP4_SSL", return_value=conn):
            fetched = mailbox.fetch_after(0)
        self.assertEqual(len(fetched), 1)
        self.assertEqual(fetched[0].subject, raw.subject)
        self.assertIn('SUBJECT "p2p1/"', searches)
        self.assertIn("ALL", searches)


if __name__ == "__main__":
    unittest.main()
