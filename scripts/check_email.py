#!/usr/bin/env python3
"""Check mailbox settings without printing secrets.

Usage:
  python3 scripts/check_email.py --imap --env-file .env.local
  python3 scripts/check_email.py --smtp --env-file .env.local

The env file is a local, git-ignored file (chmod 600). The script never prints
the password. It creates a uniquely named IMAP folder, APPENDs one test
message, SEARCHes and FETCHes it, then deletes the folder.

Exit code 0 = the mode is usable; 1 = failure (read the [FAIL] line).
"""
from __future__ import annotations

import argparse
import imaplib
import os
import smtplib
import ssl
import stat
import sys
import time
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from p2p.imap_id import needs_id, send_id  # noqa: E402


def load_env(path: str) -> None:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"env file not found: {p}")
    try:
        mode = stat.S_IMODE(p.stat().st_mode)
        if os.name == "posix" and mode & 0o077:
            print(f"[warn] {p} is mode {oct(mode)}; run: chmod 600 {p}", file=sys.stderr)
    except OSError:
        pass
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def redact_email(addr: str) -> str:
    if "@" not in addr:
        return "***"
    local, domain = addr.split("@", 1)
    return (local[:1] + "***@" + domain) if local else "***@" + domain


def need(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"missing env: {name}")
    return value


def check_imap() -> int:
    host = need("P2P_IMAP_HOST")
    user = need("P2P_IMAP_USER")
    password = need("P2P_IMAP_PASSWORD")
    base = os.environ.get("P2P_IMAP_FOLDER", "p2p-check").strip() or "p2p-check"
    folder = f"{base}-{int(time.time())}"
    print(f"[*] IMAP {host}:993 user={redact_email(user)} folder={folder}")
    client = None
    try:
        client = imaplib.IMAP4_SSL(host, 993, timeout=15)
        client.login(user, password)
        print("[ok] login")
        if needs_id(host):
            send_id(client)
            print("[ok] id")

        typ, _ = client.create(folder)
        if typ != "OK":
            raise RuntimeError(f"CREATE returned {typ}")
        print(f"[ok] create {folder}")

        msg = EmailMessage()
        msg["Subject"] = "p2p-check"
        msg["From"] = user
        msg["To"] = user
        msg.set_content("p2p-check-body")
        typ, _ = client.append(
            folder, "", imaplib.Time2Internaldate(time.time()), msg.as_bytes()
        )
        if typ != "OK":
            raise RuntimeError(f"APPEND returned {typ}")
        print("[ok] append")

        typ, _ = client.select(folder)
        if typ != "OK":
            raise RuntimeError(f"SELECT returned {typ}")
        typ, data = client.uid("SEARCH", None, "ALL")
        if typ != "OK" or not data or not data[0].strip():
            raise RuntimeError("SEARCH returned no message")
        uids = data[0].split()
        print(f"[ok] search: {len(uids)} message(s)")

        typ, msg_data = client.uid("FETCH", uids[0], "(RFC822)")
        if typ != "OK":
            raise RuntimeError(f"FETCH returned {typ}")
        print("[ok] fetch")

        client.close()
        client.delete(folder)
        print(f"[ok] delete {folder}")
        client.logout()
        client = None
        print("[PASS] IMAP usable for the shared-mailbox mode")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] IMAP: {type(exc).__name__}: {exc}")
        if client is not None:
            try:
                client.logout()
            except Exception:  # noqa: BLE001
                pass
        return 1


def check_smtp() -> int:
    host = need("P2P_SMTP_HOST")
    port = int(os.environ.get("P2P_SMTP_PORT", "465") or "465")
    user = os.environ.get("P2P_SMTP_USER", "").strip() or need("P2P_IMAP_USER")
    password = os.environ.get("P2P_SMTP_PASSWORD", "") or need("P2P_IMAP_PASSWORD")
    starttls = os.environ.get("P2P_SMTP_STARTTLS", "0").lower() in ("1", "true", "yes")
    print(f"[*] SMTP {host}:{port} starttls={starttls} user={redact_email(user)}")
    try:
        context = ssl.create_default_context()
        if starttls:
            smtp = smtplib.SMTP(host, port, timeout=15)
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
        else:
            smtp = smtplib.SMTP_SSL(host, port, timeout=15, context=context)
        smtp.login(user, password)
        smtp.noop()
        smtp.quit()
        print("[PASS] SMTP login usable for the SMTP mode")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] SMTP: {type(exc).__name__}: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Check IMAP/SMTP credentials safely")
    parser.add_argument("--imap", action="store_true", help="test IMAP login/append/search/fetch")
    parser.add_argument("--smtp", action="store_true", help="test SMTP login only")
    parser.add_argument("--env-file", default=None, help="load KEY=VALUE from a local file")
    args = parser.parse_args()
    if args.env_file:
        load_env(args.env_file)
    if not args.imap and not args.smtp:
        args.imap = True
    rc = 0
    if args.imap:
        rc |= check_imap()
    if args.smtp:
        rc |= check_smtp()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
