"""Reusable IMAP capability check (no secret is printed)."""

from __future__ import annotations

import imaplib
import smtplib
import ssl
import time
from email.message import EmailMessage

from .imap_id import needs_id, send_id


class MailCheckError(RuntimeError):
    pass


def check_imap(
    host: str,
    user: str,
    password: str,
    folder_base: str = "p2p-check",
    timeout: float = 15.0,
) -> str:
    """LOGIN -> ID -> CREATE -> APPEND -> SELECT -> SEARCH -> FETCH -> DELETE.

    Raises MailCheckError with a short stage message on failure. Never prints
    or logs the password.
    """
    folder = f"{folder_base}-{int(time.time())}"
    client = None
    try:
        client = imaplib.IMAP4_SSL(host, 993, timeout=timeout)
        client.login(user, password)
        if needs_id(host):
            send_id(client)
        typ, _ = client.create(folder)
        if typ != "OK":
            raise MailCheckError("CREATE failed")
        msg = EmailMessage()
        msg["Subject"] = "p2p-check"
        msg["From"] = user
        msg["To"] = user
        msg.set_content("p2p-check-body")
        typ, _ = client.append(
            folder, "", imaplib.Time2Internaldate(time.time()), msg.as_bytes()
        )
        if typ != "OK":
            raise MailCheckError("APPEND failed")
        typ, _ = client.select(folder)
        if typ != "OK":
            raise MailCheckError("SELECT failed")
        typ, data = client.uid("SEARCH", None, "ALL")
        if typ != "OK" or not data or not data[0].strip():
            raise MailCheckError("SEARCH returned no message")
        typ, _ = client.uid("FETCH", data[0].split()[0], "(RFC822)")
        if typ != "OK":
            raise MailCheckError("FETCH failed")
        client.close()
        try:
            client.delete(folder)
        except Exception:  # noqa: BLE001
            pass
        return folder
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:  # noqa: BLE001
                pass


def check_smtp(
    host: str,
    port: int,
    user: str,
    password: str,
    starttls: bool = False,
    timeout: float = 15.0,
) -> None:
    """Check SMTP login only; does not send an email.

    Raises the underlying exception on failure. Never prints the password.
    """
    context = ssl.create_default_context()
    smtp = None
    try:
        if starttls:
            smtp = smtplib.SMTP(host, port, timeout=timeout)
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        else:
            smtp = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
        smtp.login(user, password)
        smtp.noop()
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:  # noqa: BLE001
                pass
