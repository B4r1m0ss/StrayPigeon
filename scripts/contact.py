#!/usr/bin/env python3
"""Create, join, or list per-conversation contacts.

The identity (your mailbox) lives in the vault. Each contact carries its own
PSK, role, and (for shared-IMAP test mode) folder. Peer information lives
here, never in identity setup.

Examples:
  python3 scripts/contact.py new --peer bob@example.com
  python3 scripts/contact.py new --peer bob@example.com --role responder
  python3 scripts/contact.py join "p2p1:..."
  python3 scripts/contact.py list
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from p2p.contacts import (  # noqa: E402
    create_contact,
    join_contact,
    list_contacts,
    migrate_vault,
)
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


def _new(args, path: Path) -> int:
    data, passphrase = _load(path)
    key, contact, invite_code = create_contact(
        data,
        args.peer,
        folder=args.folder,
        force=args.force,
        role=args.role,
        local_port=args.port,
    )
    save_vault(path, passphrase, data)
    print(f"[ok] contact saved: {key}")
    print(f"[role] this side: {contact['role']} (peer gets the opposite role)")
    print("[ok] invite code (send out-of-band; it contains this conversation's PSK):")
    print(invite_code)
    print('[next] peer: python3 scripts/contact.py join "<paste p2p1: code>"')
    print(
        f"[next] you:  python3 scripts/run_vault.py --contact {key} "
        f"--role {contact['role']} --port 40000"
    )
    return 0


def _join(args, path: Path) -> int:
    text = args.invite
    if not text:
        text = input("Paste invite code (p2p1: optional): ").strip()
    if not text:
        raise ValueError("invite code is required")
    data, passphrase = _load(path)
    key, contact = join_contact(
        data, text, peer_label=args.peer, force=args.force, local_port=args.port
    )
    save_vault(path, passphrase, data)
    print(f"[ok] contact saved: {key}")
    print(f"[role] this side: {contact['role']} (taken from the invite)")
    print(
        f"[next] you: python3 scripts/run_vault.py --contact {key} "
        f"--role {contact['role']} --port 40000"
    )
    return 0


def _list(args, path: Path) -> int:
    data, _passphrase = _load(path)
    items = list_contacts(data)
    if not items:
        print("[info] no contacts; use: contact.py new --peer ...")
        return 0
    print(f"{'CONTACT':32} {'MODE':6} {'ROLE':10} {'PORT':6} {'FOLDER':24} PEER")
    for key, contact in items:
        print(
            f"{key:32} {str(contact.get('mode', '')):6} "
            f"{str(contact.get('role', 'initiator')):10} "
            f"{int(contact.get('local_port') or 40000):6} "
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
    p_new.add_argument(
        "--role",
        choices=("initiator", "responder"),
        default="initiator",
        help="this side's role; the joiner gets the opposite role",
    )
    p_new.add_argument(
        "--port", type=int, default=40000, help="local UDP port for this contact"
    )
    p_new.add_argument("--force", action="store_true", help="replace an existing contact")
    p_new.set_defaults(func=_new)

    p_join = sub.add_parser("join", help="join an invite and save the contact")
    p_join.add_argument("invite", nargs="?", default=None)
    p_join.add_argument("--peer", default=None, help="contact label in IMAP mode")
    p_join.add_argument(
        "--port", type=int, default=40000, help="local UDP port for this contact"
    )
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
