#!/usr/bin/env python3
"""Run email-p2p using an encrypted vault and a selected contact.

The identity is your mailbox account; the contact supplies the peer mailbox
and that conversation's PSK. Legacy flat vaults are migrated automatically.

Usage:
  python3 scripts/run_vault.py --list-contacts
  python3 scripts/run_vault.py --contact bob@example.com --role initiator --port 40000
  python3 scripts/run_vault.py --contact bob@example.com --role responder \
      --port 40000 --advertise-host 192.168.1.23
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

from p2p.__main__ import main as app_main  # noqa: E402
from p2p.contacts import list_contacts, migrate_vault, select_contact  # noqa: E402
from p2p.runtime_env import apply_vault_env  # noqa: E402
from p2p.vault import default_vault_path, load_vault  # noqa: E402


def usage() -> None:
    print(
        "usage: run_vault.py [--vault PATH] [--contact EMAIL] --role initiator|responder "
        "[--port 40000] [--advertise-host IP] [--message TEXT] "
        "[--punch-window 10] [--keepalive 15] [app options]"
    )
    print("       run_vault.py [--vault PATH] --list-contacts")


def _has(rest: list[str], flag: str) -> bool:
    return any(a == flag or a.startswith(flag + "=") for a in rest)


def _pop_value(rest: list[str], flag: str) -> tuple[list[str], str | None]:
    """Remove --flag VALUE / --flag=VALUE from rest; return value if present."""
    out: list[str] = []
    value: str | None = None
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == flag:
            if i + 1 >= len(rest):
                raise SystemExit(f"{flag} needs a value")
            value = rest[i + 1]
            i += 2
            continue
        if arg.startswith(flag + "="):
            value = arg.split("=", 1)[1]
            i += 1
            continue
        out.append(arg)
        i += 1
    return out, value


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--vault", default=str(default_vault_path()))
    parser.add_argument("--contact", default=None)
    parser.add_argument("--list-contacts", action="store_true")
    known, rest = parser.parse_known_args()

    if "-h" in rest or "--help" in rest:
        usage()
        return 0
    if _has(rest, "--signal"):
        print("[FAIL] --signal is fixed by the vault identity")
        return 2
    if _has(rest, "--name"):
        print("[FAIL] --name is fixed by the vault identity")
        return 2

    # Legacy alias: --peer selects a contact. The peer name itself is always
    # supplied by the selected contact.
    rest, legacy_peer = _pop_value(rest, "--peer")
    if known.contact and legacy_peer:
        print("[FAIL] use either --contact or --peer, not both")
        return 2
    contact_id = known.contact or legacy_peer

    vault_path = Path(known.vault).expanduser()
    passphrase = os.environ.get("P2P_VAULT_PASSPHRASE", "")
    if not passphrase:
        passphrase = getpass.getpass("Vault passphrase: ")
    try:
        data = migrate_vault(load_vault(vault_path, passphrase))
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {exc}")
        return 1

    if known.list_contacts:
        items = list_contacts(data)
        if not items:
            print("[info] no contacts; use scripts/contact.py new --peer ...")
            return 0
        print(f"{'CONTACT':32} {'MODE':6} {'FOLDER':24} PEER")
        for key, contact in items:
            print(
                f"{key:32} {str(contact.get('mode', '')):6} "
                f"{str(contact.get('folder', '')):24} {contact.get('peer_email', '')}"
            )
        return 0

    if not _has(rest, "--role"):
        usage()
        print("[FAIL] --role is required")
        return 2

    identity = data["identity"]
    try:
        key, contact = select_contact(data, contact_id)
        signal = apply_vault_env(data, key)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {exc}")
        return 1

    mode = str(identity.get("mode") or "")
    name = str(identity["my_email"])
    peer = str(contact.get("peer_email") or ("peer" if mode == "imap" else ""))
    default_signal = float(identity.get("signal_timeout") or (600.0 if mode == "smtp" else 300.0))
    default_replay = float(identity.get("replay_window") or (600.0 if mode == "smtp" else 300.0))

    argv = list(rest)
    if not _has(argv, "--port"):
        argv += ["--port", "40000"]
    if not _has(argv, "--name"):
        argv += ["--name", name]
    if peer and not _has(argv, "--peer"):
        argv += ["--peer", peer]
    if not _has(argv, "--signal-timeout"):
        argv += ["--signal-timeout", str(default_signal)]
    if not _has(argv, "--replay-window"):
        argv += ["--replay-window", str(default_replay)]
    argv = ["--signal", signal] + argv
    return app_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
