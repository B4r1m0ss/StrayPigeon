"""Peer-to-peer SMTP signaling (experimental).

Differences from `ImapStoreChannel`:
- `send` uses SMTP to the peer's address (not APPEND to a shared folder);
- `recv` reads the local INBOX and filters `Subject: p2p1/...`.

Requirements:
- a real mailbox with SPF/DKIM (otherwise machine-generated mail is often
  classified as spam);
- SMTP over implicit TLS on port 465 (the common `SMTP_SSL` setup).

This channel has NOT been validated against a production mailbox. Use IMAP
first; SMTP is the compatibility mode.
"""

from __future__ import annotations

import base64
import smtplib
import ssl
from email.message import EmailMessage

from .imap_store import ImapStoreChannel


class SmtpMailChannel(ImapStoreChannel):
    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        my_email: str,
        peer_email: str,
        imap_host: str,
        user: str,
        password: str,
        folder: str = "INBOX",
        starttls: bool = False,
    ):
        super().__init__(
            imap_host, user, password, folder, search='SUBJECT "p2p1/"'
        )
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.my_email = my_email
        self.peer_email = peer_email
        self.starttls = starttls

    def send(self, sid: str, seq: int, blob: bytes) -> None:
        body = base64.b64encode(blob).decode("ascii")
        msg = EmailMessage()
        msg["Subject"] = f"p2p1/{sid}/{seq}"
        msg["From"] = self.my_email
        msg["To"] = self.peer_email
        msg.set_content(body)
        context = ssl.create_default_context()
        if self.starttls:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                smtp.ehlo()
                smtp.login(self.user, self._password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP_SSL(
                self.smtp_host, self.smtp_port, timeout=15, context=context
            ) as smtp:
                smtp.login(self.user, self._password)
                smtp.send_message(msg)

    def recv(self, sid: str, after: int) -> list[tuple[int, bytes]]:
        out = super().recv(sid, after)
        if out:
            return out
        # Some IMAP servers (e.g. 163) do not match SUBJECT "p2p1/"
        # even though the message is in INBOX. Fall back to ALL and let
        # ImapStoreChannel filter the Subject in Python.
        old = self.search
        self.search = "ALL"
        try:
            return super().recv(sid, after)
        finally:
            self.search = old
