"""Apply a decrypted vault payload to the process environment.

Supports both the v2 identity/contacts payload and legacy flat payloads. The
selected contact supplies the peer mailbox and PSK; the identity supplies the
local mailbox credentials.
"""

from __future__ import annotations

import os

from .contacts import migrate_vault, select_contact


def apply_vault_env(data: dict, contact_id: str | None = None) -> str:
    data = migrate_vault(data)
    identity = data["identity"]
    _key, contact = select_contact(data, contact_id)
    mode = str(identity.get("mode") or contact.get("mode") or "")
    if contact.get("mode") and contact["mode"] != mode:
        raise ValueError(f"contact mode {contact['mode']!r} != identity mode {mode!r}")

    if mode == "smtp":
        os.environ["P2P_SMTP_HOST"] = str(identity["smtp_host"])
        os.environ["P2P_SMTP_PORT"] = str(identity.get("smtp_port", 465))
        os.environ["P2P_SMTP_STARTTLS"] = "1" if identity.get("smtp_starttls") else "0"
        os.environ["P2P_MY_EMAIL"] = str(identity["my_email"])
        os.environ["P2P_PEER_EMAIL"] = str(contact["peer_email"])
        os.environ["P2P_IMAP_HOST"] = str(identity["imap_host"])
        os.environ["P2P_IMAP_USER"] = str(identity["my_email"])
        os.environ["P2P_IMAP_PASSWORD"] = str(identity["imap_password"])
        os.environ["P2P_SMTP_FOLDER"] = "INBOX"
        os.environ["P2P_PSK_B64"] = str(contact["psk_b64"])
        return "smtp"
    if mode == "imap":
        os.environ["P2P_IMAP_HOST"] = str(identity["imap_host"])
        os.environ["P2P_IMAP_USER"] = str(identity["my_email"])
        os.environ["P2P_IMAP_PASSWORD"] = str(identity["imap_password"])
        os.environ["P2P_IMAP_FOLDER"] = str(contact["folder"])
        os.environ["P2P_MY_EMAIL"] = str(identity["my_email"])
        os.environ["P2P_PEER_EMAIL"] = str(contact.get("peer_email") or "peer")
        os.environ["P2P_PSK_B64"] = str(contact["psk_b64"])
        return "imap"
    raise ValueError(f"unsupported vault mode: {mode!r}")
