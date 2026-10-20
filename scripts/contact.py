#!/usr/bin/env python3
"""Create, join, or list per-conversation contacts.

The identity (your mailbox) lives in the vault. Each contact carries its own
PSK, and for the shared-IMAP test mode its own folder. Peer information lives
here, never in identity setup.

Examples:
  python3 scripts/contact.py new --peer bob@example.com
  python3 scripts/contact.py join "p2p1:..."
  python3 scripts/contact.py list
  python3 scripts/run_vault.py --contact bob@example.com --role initiator ...
"""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from p2p.contacts import (  # noqa: E402
    list_contacts,
    migrate_vault,
    normalize_peer,
    upsert_contact,
)
from p2p.invite import encode_invite, parse_invite  # noqa: E402
from p2p.vault import default_vault_path, load_vault, save_vault  # noqa: E402


def _passphrase() -> str:
    value = os.environ.get("P2P_VAULT_PASSPHRASE", "")
    if value:
        return value
    return getpass.getpass("Vault passphrase: ")


def _load(path: Path) -> tuple[dict, str]:
    passphrase = _passphrase()
    data = migrate_vault(load_vault(path, passphrase))
    if not isinstance(data.get("identity"), dict):
        raise ValueError("vault has no identity; run scripts/setup_identity.py first")
    return data, passphrase


def _require_email(value: str, what: str) -> str:
    value = value.strip()
    if "@" not in value:
        raise ValueError(f"invalid {what}: {value!r}")
    return value


def _new(args, path: Path) -> int:
    data, passphrase = _load(path)
    identity = data["identity"]
    mode = str(identity.get("mode") or "")
    my_email = str(identity.get("my_email") or "")
    peer = args.peer.strip()
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
            "psk_b64": base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        }
    elif mode == "imap":
        folder = (args.folder or f"p2p-{secrets.token_hex(4)}").strip()
        if not peer:
            raise ValueError("--peer is required as the contact label in IMAP mode")
        invite_payload = {
            "v": 1,
            "mode": "imap",
            "imap_host": str(identity["imap_host"]),
            "mailbox": my_email,
            "folder": folder,
            "psk_b64": base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        }
    else:
        raise ValueError(f"unsupported identity mode: {mode!r}")

    now = int(time.time())
    contact = {
        "peer_email": peer,
        "psk_b64": invite_payload["psk_b64"],
        "mode": mode,
        "folder": folder,
        "created_at": now,
        "last_used_at": 0,
    }
    key = upsert_contact(data, contact, force=args.force)
    save_vault(path, passphrase, data)
    invite_code = encode_invite(invite_payload)
    print(f"[ok] contact saved: {key}")
    print("[ok] invite code (send out-of-band; it contains this conversation's PSK):")
    print(invite_code)
    print("[next] peer: python3 scripts/contact.py join \"<paste p2p1: code>\"")
    print(f"[next] you:  python3 scripts/run_vault.py --contact {key} --role initiator --port 40000")
    return 0


def _join(args, path: Path) -> int:
    text = args.invite
    if not text:
        text = input("Paste invite code (p2p1: optional): ").strip()
    if not text:
        raise ValueError("invite code is required")
    invite = parse_invite(text)
    data, passphrase = _load(path)
    identity = data["identity"]
    mode = str(identity.get("mode") or "")
    my_email = str(identity.get("my_email") or "")
    if invite["mode"] != mode:
        raise ValueError(f"invite is {invite['mode']}, identity is {mode}")

    if mode == "smtp":
        expected = str(invite["peer_email"])
        if normalize_peer(expected) != normalize_peer(my_email):
            raise ValueError(
                f"invite is for {expected}, but this identity is {my_email}"
            )
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
        peer = (args.peer or "peer").strip() or "peer"
        folder = str(invite["folder"])

    now = int(time.time())
    contact = {
        "peer_email": peer,
        "psk_b64": str(invite["psk_b64"]),
        "mode": mode,
        "folder": folder,
        "created_at": now,
        "last_used_at": 0,
    }
    key = upsert_contact(data, contact, force=args.force)
    save_vault(path, passphrase, data)
    print(f"[ok] contact saved: {key}")
    print(f"[next] you: python3 scripts/run_vault.py --contact {key} --role responder --port 40000")
    return 0


def _list(args, path: Path) -> int:
    data, _passphrase = _load(path)
    items = list_contacts(data)
    if not items:
        print("[info] no contacts; use: contact.py new --peer ...")
        return 0
    print(f"{'CONTACT':32} {'MODE':6} {'FOLDER':24} PEER")
    for key, contact in items:
        print(
            f"{key:32} {str(contact.get('mode', '')):6} "
            f"{str(contact.get('folder', '')):24} {contact.get('peer_email', '')}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage per-conversation contacts")
    parser.add_argument("--vault", default=str(default_vault_path()))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_new = sub.add_parser("new", help="create a contact and an invite code")
    p_new.add_argument("--peer", required=True, help="peer email (or contact label in IMAP mode)")
    p_new.add_argument("--folder", default=None, help="IMAP folder (shared test mode only)")
    p_new.add_argument("--force", action="store_true", help="replace an existing contact")
    p_new.set_defaults(func=_new)

    p_join = sub.add_parser("join", help="join an invite and save the contact")
    p_join.add_argument("invite", nargs="?", default=None)
    p_join.add_argument("--peer", default=None, help="contact label in IMAP mode")
    p_join.add_argument("--force", action="store_true", help="replace an existing contact")
    p_join.set_defaults(func=_join)

    p_list = sub.add_parser("list", help="list saved contacts")
    p_list.set_defaults(func=_list)

    args = parser.parse_args()
    path = Path(args.vault).expanduser()
    try:
        return int(args.func(args, path)) if args.func is not None else 2
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
