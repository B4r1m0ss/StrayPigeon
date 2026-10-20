"""Vault v2: one identity + many per-conversation contacts.

The identity is the local mailbox account (IMAP/SMTP credentials). It does not
contain a peer. Each contact owns its own PSK and, for the shared-IMAP test
mode, its own folder.

Legacy flat vaults (``my_email``/``peer_email``/``psk_b64``) are migrated in
memory by :func:`migrate_vault`; callers that need persistence can save the
returned dict.
"""

from __future__ import annotations

import base64
import copy
import secrets
import time
from typing import Any

from .invite import encode_invite, parse_invite


def normalize_peer(value: str) -> str:
    return str(value).strip().lower()


def is_v2(data: dict) -> bool:
    return isinstance(data.get("identity"), dict) and isinstance(data.get("contacts"), dict)


def migrate_vault(data: dict) -> dict:
    """Return a deep-copied v2 payload; never mutates ``data``."""
    if not isinstance(data, dict):
        raise ValueError("vault payload must be an object")
    if is_v2(data):
        out = copy.deepcopy(data)
        out.setdefault("version", 2)
        return out

    mode = str(data.get("mode") or "")
    my_email = str(data.get("my_email") or data.get("name") or "").strip()
    if not my_email:
        raise ValueError("vault has no my_email")
    identity: dict[str, Any] = {
        "my_email": my_email,
        "mode": mode,
        "imap_host": str(data.get("imap_host") or ""),
        "imap_password": str(data.get("imap_password") or ""),
        "signal_timeout": float(
            data.get("signal_timeout") or (600.0 if mode == "smtp" else 300.0)
        ),
        "replay_window": float(
            data.get("replay_window") or (600.0 if mode == "smtp" else 300.0)
        ),
    }
    if mode == "smtp":
        identity.update(
            {
                "smtp_host": str(data.get("smtp_host") or ""),
                "smtp_port": int(data.get("smtp_port") or 465),
                "smtp_starttls": bool(data.get("smtp_starttls")),
            }
        )

    peer = str(data.get("peer_email") or data.get("peer") or "").strip() or "peer"
    contact = {
        "peer_email": peer,
        "psk_b64": str(data.get("psk_b64") or ""),
        "mode": mode,
        "folder": str(data.get("folder") or "INBOX"),
        "created_at": data.get("created_at") or 0,
        "last_used_at": 0,
    }
    return {
        "version": 2,
        "identity": identity,
        "contacts": {normalize_peer(peer): contact},
    }


def select_contact(data: dict, contact_id: str | None = None) -> tuple[str, dict]:
    contacts = data.get("contacts")
    if not isinstance(contacts, dict) or not contacts:
        raise ValueError("vault has no contacts; create or join an invite first")
    if contact_id:
        key = normalize_peer(contact_id)
        if key in contacts:
            return key, contacts[key]
        for k, contact in contacts.items():
            if normalize_peer(str(contact.get("peer_email", ""))) == key:
                return k, contact
        raise ValueError(f"contact not found: {contact_id}")
    if len(contacts) == 1:
        key = next(iter(contacts))
        return key, contacts[key]
    names = ", ".join(sorted(contacts))
    raise ValueError(f"multiple contacts; use --contact with one of: {names}")


def upsert_contact(data: dict, contact: dict, force: bool = False) -> str:
    peer = str(contact.get("peer_email") or "").strip()
    if not peer:
        raise ValueError("contact has no peer_email")
    key = normalize_peer(peer)
    contacts = data.setdefault("contacts", {})
    if key in contacts and not force:
        raise ValueError(f"contact already exists: {key} (use --force to replace)")
    contacts[key] = contact
    data["version"] = 2
    return key


def list_contacts(data: dict) -> list[tuple[str, dict]]:
    return sorted((str(k), v) for k, v in (data.get("contacts") or {}).items())


def _require_email(value: str, what: str) -> str:
    value = value.strip()
    if "@" not in value:
        raise ValueError(f"invalid {what}: {value!r}")
    return value


def create_contact(
    data: dict, peer: str, folder: str | None = None, force: bool = False
) -> tuple[str, dict, str]:
    """Create a contact + invite for the identity in ``data``.

    Returns ``(contact_key, contact, invite_code)``. The caller persists the
    vault. This is the shared implementation for the CLI and the GUI.
    """
    identity = data.get("identity")
    if not isinstance(identity, dict):
        raise ValueError("vault has no identity; run setup first")
    mode = str(identity.get("mode") or "")
    my_email = str(identity.get("my_email") or "")
    peer = str(peer).strip()
    psk_b64 = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    if mode == "smtp":
        peer = _require_email(peer, "peer email")
        if normalize_peer(peer) == normalize_peer(my_email):
            raise ValueError("peer and identity must be different mailboxes")
        folder = "INBOX"
        invite_payload = {
            "v": 1,
            "mode": "smtp",
            "my_email": my_email,
            "peer_email": peer,
            "psk_b64": psk_b64,
        }
    elif mode == "imap":
        if not peer:
            raise ValueError("peer/contact label is required in IMAP mode")
        folder = (folder or f"p2p-{secrets.token_hex(4)}").strip()
        invite_payload = {
            "v": 1,
            "mode": "imap",
            "imap_host": str(identity["imap_host"]),
            "mailbox": my_email,
            "folder": folder,
            "psk_b64": psk_b64,
        }
    else:
        raise ValueError(f"unsupported identity mode: {mode!r}")
    now = int(time.time())
    contact = {
        "peer_email": peer,
        "psk_b64": psk_b64,
        "mode": mode,
        "folder": folder,
        "created_at": now,
        "last_used_at": 0,
    }
    key = upsert_contact(data, contact, force=force)
    return key, contact, encode_invite(invite_payload)


def join_contact(
    data: dict,
    text: str,
    peer_label: str | None = None,
    force: bool = False,
) -> tuple[str, dict]:
    """Join an invite and add its contact to ``data`` (caller persists)."""
    identity = data.get("identity")
    if not isinstance(identity, dict):
        raise ValueError("vault has no identity; run setup first")
    invite = parse_invite(text)
    mode = str(identity.get("mode") or "")
    my_email = str(identity.get("my_email") or "")
    if invite["mode"] != mode:
        raise ValueError(f"invite is {invite['mode']}, identity is {mode}")
    if mode == "smtp":
        expected = str(invite["peer_email"])
        if normalize_peer(expected) != normalize_peer(my_email):
            raise ValueError(f"invite is for {expected}, but this identity is {my_email}")
        peer = _require_email(str(invite["my_email"]), "initiator email")
        if normalize_peer(peer) == normalize_peer(my_email):
            raise ValueError("peer and identity must be different mailboxes")
        folder = "INBOX"
    else:
        expected = str(invite["mailbox"])
        if normalize_peer(expected) != normalize_peer(my_email):
            raise ValueError(
                f"invite is for shared mailbox {expected}, but this identity is {my_email}"
            )
        invite_host = str(invite.get("imap_host") or "")
        if invite_host and invite_host.lower() != str(identity.get("imap_host", "")).lower():
            raise ValueError(
                f"invite IMAP host {invite_host} != identity host {identity.get('imap_host')}"
            )
        peer = (peer_label or "peer").strip() or "peer"
        folder = str(invite["folder"])
    contact = {
        "peer_email": peer,
        "psk_b64": str(invite["psk_b64"]),
        "mode": mode,
        "folder": folder,
        "created_at": int(time.time()),
        "last_used_at": 0,
    }
    key = upsert_contact(data, contact, force=force)
    return key, contact
