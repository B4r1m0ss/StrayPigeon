"""NetEase (126/163/yeah.net) IMAP ID command.

Some providers reject clients that do not identify themselves with the IMAP
ID extension, returning "Unsafe Login". `imaplib` does not ship an ID helper,
so we register the command and send it once after LOGIN.

Ref: RFC 2971 (IMAP4 ID extension).
"""

from __future__ import annotations

import imaplib
import os


def needs_id(host: str) -> bool:
    override = os.environ.get("P2P_IMAP_ID", "").strip().lower()
    if override in ("1", "true", "yes"):
        return True
    if override in ("0", "false", "no"):
        return False
    h = host.lower().strip()
    return h.endswith("126.com") or h.endswith("163.com") or h.endswith("yeah.net")


def send_id(client: imaplib.IMAP4, name: str = "email-p2p", version: str = "0.3") -> None:
    if "ID" not in imaplib.Commands:
        # ID is valid in the authenticated state (after LOGIN).
        imaplib.Commands["ID"] = ("AUTH",)
    args = f'("name" "{name}" "version" "{version}" "vendor" "email-p2p")'
    typ, _data = client._simple_command("ID", args)  # type: ignore[attr-defined]
    if typ != "OK":
        raise RuntimeError(f"IMAP ID rejected: {typ}")
