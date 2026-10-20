from __future__ import annotations

import base64
from unittest import TestCase

from p2p.signal.imap_store import ImapStoreChannel
from p2p.signal.smtp_mail import SmtpMailChannel


def _raw(sid: str, seq: int, blob: bytes) -> bytes:
    body = base64.b64encode(blob).decode("ascii")
    return (
        f"Subject: p2p1/{sid}/{seq}\r\n"
        "Content-Type: text/plain; charset=us-ascii\r\n"
        "Content-Transfer-Encoding: 7bit\r\n"
        f"\r\n{body}\r\n"
    ).encode("ascii")


class FakeIMAP:
    def __init__(self, messages: dict[bytes, bytes]):
        self.messages = messages
        self.searches: list[str] = []

    def uid(self, command: str, *args):
        if command == "SEARCH":
            criterion = str(args[-1])
            self.searches.append(criterion)
            if criterion == "ALL":
                return "OK", [b" ".join(self.messages.keys())]
            return "OK", [b""]  # 163-style: SUBJECT "p2p1/" matches nothing
        if command == "FETCH":
            uid = args[0]
            return "OK", [(b"x", self.messages[uid])]
        raise AssertionError(command)


class ImapNewestTests(TestCase):
    def test_channel_returns_newest_uid_first(self) -> None:
        channel = ImapStoreChannel("imap.example", "u", "p")
        channel.conn = FakeIMAP(
            {
                b"101": _raw("a", 1, b"old"),
                b"102": _raw("a", 1, b"new"),
            }
        )
        out = channel.recv("", 0)
        self.assertEqual([blob for _, blob in out], [b"new", b"old"])

    def test_smtp_mail_falls_back_to_all_and_keeps_newest(self) -> None:
        channel = SmtpMailChannel(
            "smtp.example", 465, "me@x", "peer@y", "imap.example", "u", "p"
        )
        channel.conn = FakeIMAP(
            {
                b"101": _raw("s", 1, b"old"),
                b"102": _raw("s", 1, b"new"),
            }
        )
        out = channel.recv("", 0)
        self.assertEqual([blob for _, blob in out], [b"new", b"old"])
        self.assertEqual(channel.conn.searches, ['SUBJECT "p2p1/"', "ALL"])
