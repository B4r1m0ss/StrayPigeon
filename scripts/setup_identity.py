#!/usr/bin/env python3
"""Identity-only setup.

This is the new first-run step: it stores *your* mailbox account (IMAP/SMTP
credentials) in the encrypted vault. It does **not** ask for or store a peer.

A conversation is created separately:

  python3 scripts/contact.py new --peer peer@example.com
  python3 scripts/contact.py join "p2p1:..."
  python3 scripts/run_vault.py --contact peer@example.com --role initiator ...

Non-interactive:
  python3 scripts/setup_identity.py --config identity.local.json
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Reuse the small interactive helpers from the legacy setup script. The
# identity/contact split will move these into a core module in a later pass.
from setup_legacy import (  # noqa: E402
    ask,
    ask_bool,
    ask_secret,
    default_imap_host,
    default_smtp_host,
    load_config,
    resolve,
    warn_secret_file,
)
from p2p.mailcheck import check_imap, check_smtp  # noqa: E402
from p2p.vault import MIN_PASSPHRASE, default_vault_path, save_vault  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Setup your mailbox identity (no peer)")
    parser.add_argument("--config", default=None, help="JSON file with identity fields")
    parser.add_argument("--mode", choices=("smtp", "imap"), default="smtp")
    parser.add_argument("--vault", default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-test", action="store_true")
    parser.add_argument("--imap-host", default=None)
    parser.add_argument("--smtp-host", default=None)
    parser.add_argument("--smtp-port", type=int, default=None)
    parser.add_argument("--smtp-starttls", action="store_true")
    parser.add_argument("--signal-timeout", type=float, default=None)
    parser.add_argument("--replay-window", type=float, default=None)
    args = parser.parse_args()

    cfg_path = Path(args.config).expanduser() if args.config else None
    cfg: dict = {}
    base = Path.cwd()
    if cfg_path is not None:
        cfg = load_config(cfg_path)
        warn_secret_file(cfg_path)
        base = cfg_path.parent

    vault_value = cfg.get("vault") or args.vault or str(default_vault_path())
    vault_path = resolve(base, vault_value)
    if vault_path.exists() and not args.force:
        raise SystemExit(f"{vault_path} exists; use --force to overwrite")
    if vault_path.exists():
        print("[warn] --force replaces the vault and all saved contacts")

    mode = cfg.get("mode") or args.mode
    my_email = str(cfg.get("my_email") or ask("Your email address")).strip()
    if "@" not in my_email:
        raise SystemExit("invalid email address")

    imap_host = str(
        cfg.get("imap_host") or args.imap_host or ask("Your IMAP host", default_imap_host(my_email))
    )
    smtp_host = ""
    smtp_port = 465
    starttls = False
    if mode == "smtp":
        smtp_host = str(
            cfg.get("smtp_host") or args.smtp_host or ask("Your SMTP host", default_smtp_host(my_email))
        )
        if "smtp_port" in cfg:
            smtp_port = int(cfg["smtp_port"])
        elif args.smtp_port:
            smtp_port = args.smtp_port
        else:
            smtp_port = int(ask("SMTP port", "465"))
        if "smtp_starttls" in cfg:
            starttls = bool(cfg["smtp_starttls"])
        elif args.smtp_starttls:
            starttls = True
        else:
            starttls = ask_bool("SMTP STARTTLS?", False)

    account = str(cfg.get("auth_code") or ask_secret("Authorization code / app password (hidden)"))
    if not account:
        raise SystemExit("authorization code is required")

    do_test = (not args.no_test) and bool(cfg.get("test", True))
    if not do_test:
        print("[warn] IMAP/SMTP check skipped")
    else:
        print("[*] testing IMAP login / ID ...")
        try:
            check_imap(imap_host, my_email, account, folder_base="p2p-setup-check")
            print("[ok] IMAP works")
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(f"[FAIL] IMAP check failed: {type(exc).__name__}: {exc}") from exc
        if mode == "smtp":
            print("[*] testing SMTP login ...")
            try:
                check_smtp(smtp_host, smtp_port, my_email, account, starttls)
                print("[ok] SMTP works")
            except Exception as exc:  # noqa: BLE001
                raise SystemExit(f"[FAIL] SMTP check failed: {type(exc).__name__}: {exc}") from exc

    passphrase = cfg.get("vault_passphrase") or os.environ.get("P2P_VAULT_PASSPHRASE")
    if passphrase:
        pass2 = passphrase
    elif cfg_path is not None:
        raise SystemExit("config needs vault_passphrase, or set P2P_VAULT_PASSPHRASE")
    else:
        passphrase = ask_secret(f"Vault passphrase (min {MIN_PASSPHRASE}, hidden)")
        pass2 = ask_secret("Repeat passphrase")
    if len(passphrase) < MIN_PASSPHRASE:
        raise SystemExit(f"passphrase must be at least {MIN_PASSPHRASE} characters")
    if passphrase != pass2:
        raise SystemExit("passphrases do not match")

    signal_timeout = float(
        args.signal_timeout
        or cfg.get("signal_timeout")
        or (600.0 if mode == "smtp" else 300.0)
    )
    replay_window = float(
        args.replay_window
        or cfg.get("replay_window")
        or (600.0 if mode == "smtp" else 300.0)
    )
    identity = {
        "my_email": my_email,
        "mode": mode,
        "imap_host": imap_host,
        "imap_password": account,
        "signal_timeout": signal_timeout,
        "replay_window": replay_window,
    }
    if mode == "smtp":
        identity.update(
            {
                "smtp_host": smtp_host,
                "smtp_port": smtp_port,
                "smtp_starttls": starttls,
            }
        )
    data = {"version": 2, "identity": identity, "contacts": {}}
    save_vault(vault_path, passphrase, data)
    print(f"[ok] encrypted identity vault: {vault_path}")
    print("[ok] no peer is stored at this stage.")
    print("[next] create a conversation:")
    print("       python3 scripts/contact.py new --peer peer@example.com")
    print("[next] or join one:")
    print('       python3 scripts/contact.py join "p2p1:..."')
    print("[next] then run with the contact:")
    print("       python3 scripts/run_vault.py --contact peer@example.com --role initiator --port 40000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
