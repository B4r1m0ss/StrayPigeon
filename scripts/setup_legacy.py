#!/usr/bin/env python3
"""Interactive OR config-file setup.

Modes:
  smtp (default, product): each side has its own mailbox; auth codes are local.
  imap (test harness): one shared mailbox; both sides need its auth code.

Non-interactive (recommended for repeated tests):
  python3 scripts/setup.py --config setup.local.json

A config JSON can contain:
  mode, name, peer,
  my_email, peer_email, imap_host, smtp_host, smtp_port, smtp_starttls,
  auth_code, vault, vault_passphrase, invite, folder, psk_b64, test,
  join / join_file (for the joiner)

Secrets in the config are plaintext; chmod 600 and delete the file after setup.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import secrets
import stat
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from p2p.__main__ import main as app_main  # noqa: E402
from p2p.mailcheck import check_imap, check_smtp  # noqa: E402
from p2p.runtime_env import apply_vault_env  # noqa: E402
from p2p.vault import MIN_PASSPHRASE, default_vault_path, save_vault  # noqa: E402

IMAP_HOSTS = {
    "126.com": "imap.126.com",
    "163.com": "imap.163.com",
    "yeah.net": "imap.yeah.net",
    "qq.com": "imap.qq.com",
    "gmail.com": "imap.gmail.com",
    "outlook.com": "outlook.office365.com",
    "hotmail.com": "outlook.office365.com",
    "yahoo.com": "imap.mail.yahoo.com",
}
SMTP_HOSTS = {
    "126.com": "smtp.126.com",
    "163.com": "smtp.163.com",
    "yeah.net": "smtp.yeah.net",
    "qq.com": "smtp.qq.com",
    "gmail.com": "smtp.gmail.com",
    "outlook.com": "smtp.office365.com",
    "hotmail.com": "smtp.office365.com",
    "yahoo.com": "smtp.mail.yahoo.com",
}


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt) as exc:  # noqa: BLE001
        raise SystemExit("\nsetup cancelled") from exc
    return value or (default or "")


def ask_secret(prompt: str) -> str:
    try:
        return getpass.getpass(f"{prompt}: ")
    except (EOFError, KeyboardInterrupt) as exc:  # noqa: BLE001
        raise SystemExit("\nsetup cancelled") from exc


def ask_bool(prompt: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    try:
        value = input(f"{prompt} [{hint}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt) as exc:  # noqa: BLE001
        raise SystemExit("\nsetup cancelled") from exc
    if not value:
        return default
    return value.startswith("y")


def _domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


def default_imap_host(email: str) -> str:
    return IMAP_HOSTS.get(_domain(email), f"imap.{_domain(email)}")


def default_smtp_host(email: str) -> str:
    return SMTP_HOSTS.get(_domain(email), f"smtp.{_domain(email)}")


def require_distinct_emails(my_email: str, peer_email: str) -> None:
    if peer_email.strip().lower() == my_email.strip().lower():
        raise SystemExit(
            "per-user mode needs a DIFFERENT peer mailbox; "
            "for a single shared mailbox use --mode imap (test mode)"
        )


def load_config(path: Path) -> dict:
    path = path.expanduser()
    if not path.exists():
        raise SystemExit(f"config not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"invalid config JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("config must be a JSON object")
    return data


def warn_secret_file(path: Path) -> None:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        if os.name == "posix" and mode & 0o077:
            print(f"[warn] {path} is mode {oct(mode)}; run: chmod 600 {path}", file=sys.stderr)
    except OSError:
        pass
    print("[warn] config contains secrets (auth_code/vault_passphrase); delete it after setup.", file=sys.stderr)


def resolve(base: Path, value: str) -> Path:
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = base / p
    return p.resolve()


def parse_invite(text: str) -> dict:
    raw = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("p2p1:"):
            raw = line[5:].strip()
            break
    if not raw:
        # Bare base32 body (prefix optional), whitespace/newlines removed.
        raw = "".join(text.split())
    if not raw:
        raise SystemExit("invalid invite: empty code")
    pad = "=" * ((8 - len(raw) % 8) % 8)
    try:
        data = json.loads(base64.b32decode(raw + pad, casefold=True))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"invalid invite: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("invalid invite: not an object")
    declared = data.pop("sum", None)
    if declared:
        expected = hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:12]
        if str(declared) != expected:
            raise SystemExit("invite checksum mismatch (code copied wrong)")
    if int(data.get("v", 0)) != 1:
        raise SystemExit("unsupported invite version")
    mode = data.get("mode")
    if mode == "smtp":
        for key in ("my_email", "peer_email", "psk_b64"):
            if not data.get(key):
                raise SystemExit(f"invite missing {key}")
    elif mode == "imap":
        for key in ("imap_host", "mailbox", "folder", "psk_b64"):
            if not data.get(key):
                raise SystemExit(f"invite missing {key}")
    else:
        raise SystemExit(f"unsupported invite mode: {mode!r}")
    return data


def encode_invite(payload: dict) -> str:
    body = {k: v for k, v in payload.items() if k != "sum"}
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    doc = dict(body)
    doc["sum"] = digest
    code = base64.b32encode(
        json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return "p2p1:" + code


def write_invite(path: Path, payload: dict) -> tuple[Path, str]:
    """Write an optional backup file and return (path, copyable code)."""
    code = encode_invite(payload)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        path = path.parent / f"invite-{int(time.time())}.txt"
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("# Share via Signal/offline, NOT email.\n")
        fh.write("# Contains the email addresses + PSK, NOT the authorization code.\n")
        fh.write(code + "\n")
    return path, code


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactive or config-file setup")
    parser.add_argument("--config", default=None, help="JSON file with all setup fields")
    parser.add_argument("--mode", choices=("smtp", "imap"), default="smtp")
    parser.add_argument("--join", default=None, help="invite code (p2p1:...)")
    parser.add_argument("--join-file", default=None, help="legacy: file containing a p2p1: invite")
    parser.add_argument("--vault", default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-test", action="store_true")
    parser.add_argument("--imap-host", default=None)
    parser.add_argument("--smtp-host", default=None)
    parser.add_argument("--smtp-port", type=int, default=None)
    parser.add_argument("--smtp-starttls", action="store_true")
    parser.add_argument("--folder", default=None)
    parser.add_argument("--start", action="store_true", help="start the chat after setup")
    parser.add_argument("--no-start", action="store_true", help="setup only, do not start")
    parser.add_argument("--role", choices=("initiator", "responder"), default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--advertise-host", default=None)
    parser.add_argument("--message", default=None, help="one-shot message after connecting")
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

    join_code = args.join or cfg.get("join")
    join_file = args.join_file or cfg.get("join_file")
    if not join_code and not join_file and cfg_path is None:
        action = ask("Create a new invite (c) or join an existing one (j)?", "c").strip().lower()
        if action.startswith("j"):
            raw = input("Paste invite code (p2p1: optional): ").strip()
            if not raw:
                raise SystemExit("invite code is required")
            join_code = raw
    joining = bool(join_code or join_file)
    mode = cfg.get("mode") or args.mode
    peer_email = ""
    smtp_host = ""
    smtp_port = 465
    starttls = False
    folder = "INBOX"

    if joining:
        if join_code:
            text = str(join_code)
        else:
            jf = Path(str(join_file)).expanduser()
            if not jf.is_absolute():
                jf = base / jf
            text = jf.read_text(encoding="utf-8")
        invite = parse_invite(text)
        mode = invite["mode"]
        psk_b64 = invite["psk_b64"]
        if mode == "smtp":
            my_email = invite["peer_email"]
            peer_email = invite["my_email"]
            require_distinct_emails(my_email, peer_email)
            print(f"[*] joining per-user mode: {peer_email} <-> {my_email}")
            imap_host = cfg.get("imap_host") or args.imap_host or ask("Your IMAP host", default_imap_host(my_email))
            smtp_host = cfg.get("smtp_host") or args.smtp_host or ask("Your SMTP host", default_smtp_host(my_email))
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
            folder = "INBOX"
        else:
            my_email = invite["mailbox"]
            imap_host = invite["imap_host"]
            folder = invite["folder"]
            print(f"[*] joining shared-mailbox TEST mode: {my_email} (folder {folder})")
    else:
        if mode == "smtp":
            my_email = cfg.get("my_email") or ask("Your email address")
            if "@" not in my_email:
                raise SystemExit("invalid email address")
            peer_email = cfg.get("peer_email") or ask("Peer email address")
            if "@" not in peer_email:
                raise SystemExit("invalid peer email address")
            require_distinct_emails(my_email, peer_email)
            imap_host = cfg.get("imap_host") or args.imap_host or ask("Your IMAP host", default_imap_host(my_email))
            smtp_host = cfg.get("smtp_host") or args.smtp_host or ask("Your SMTP host", default_smtp_host(my_email))
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
            folder = "INBOX"
        else:
            my_email = cfg.get("my_email") or ask("Shared mailbox email")
            if "@" not in my_email:
                raise SystemExit("invalid email address")
            imap_host = cfg.get("imap_host") or args.imap_host or ask("IMAP host", default_imap_host(my_email))
            folder = cfg.get("folder") or args.folder or ask("IMAP folder", f"p2p-{secrets.token_hex(4)}")
        psk_b64 = cfg.get("psk_b64") or base64.b64encode(secrets.token_bytes(32)).decode("ascii")

    # Identity = mailbox address; no separate short-name prompt.
    name = my_email
    peer = peer_email if mode == "smtp" else "peer"
    account = cfg.get("auth_code") or ask_secret("Authorization code / app password (hidden)")
    if not account:
        raise SystemExit("authorization code is required")

    do_test = (not args.no_test) and bool(cfg.get("test", True))
    if not do_test:
        print("[warn] IMAP/SMTP check skipped")
    else:
        print("[*] testing IMAP login / ID / append / search / fetch ...")
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

    data = {
        "v": 1,
        "mode": mode,
        "my_email": my_email,
        "peer_email": peer_email,
        "imap_host": imap_host,
        "imap_password": account,
        "psk_b64": psk_b64,
        "name": name,
        "peer": peer,
        "created_at": int(time.time()),
    }
    if mode == "smtp":
        data.update({"smtp_host": smtp_host, "smtp_port": smtp_port, "smtp_starttls": starttls})
    else:
        data["folder"] = folder
    # Long defaults for delayed signaling; override with flags/config.
    data["signal_timeout"] = float(
        args.signal_timeout or cfg.get("signal_timeout") or (600.0 if mode == "smtp" else 300.0)
    )
    data["replay_window"] = float(
        args.replay_window or cfg.get("replay_window") or (600.0 if mode == "smtp" else 300.0)
    )

    save_vault(vault_path, passphrase, data)
    print(f"[ok] encrypted vault: {vault_path}")

    if not joining:
        if mode == "smtp":
            invite_payload = {
                "v": 1,
                "mode": "smtp",
                "my_email": my_email,
                "peer_email": peer_email,
                "psk_b64": psk_b64,
            }
        else:
            invite_payload = {
                "v": 1,
                "mode": "imap",
                "imap_host": imap_host,
                "mailbox": my_email,
                "folder": folder,
                "psk_b64": psk_b64,
            }
        invite_value = cfg.get("invite") or (vault_path.parent / "invite.txt")
        invite_path = resolve(base, str(invite_value)) if not isinstance(invite_value, Path) else invite_value
        invite_path, invite_code = write_invite(invite_path, invite_payload)
        print("[ok] invite code (copy this to the peer; no file path needed):")
        print(invite_code)
        print(f"[ok] optional backup file: {invite_path}")
        print("[info] the invite contains the PSK; the peer pastes it at the prompt.")
        if mode == "smtp":
            print("[info] per-user mode: each side uses its OWN mailbox; auth codes are never shared.")
        else:
            print("[warn] shared-mailbox TEST mode: both sides need the SAME mailbox's auth code.")
        print("[next] peer: python3 scripts/setup.py --join \"<paste the p2p1: code>\" --start")
        print(f"[next] you:  python3 scripts/run_vault.py --vault {vault_path} --role initiator --port 40000")
    else:
        print(f"[next] you: python3 scripts/run_vault.py --vault {vault_path} --role responder --port 40000")
    print("[note] for two machines add: --advertise-host <your LAN IP>")

    if args.no_start:
        return 0
    if cfg_path is not None:
        start_now = bool(args.start or cfg.get("start"))
    else:
        start_now = bool(args.start or ask_bool("Start communication now?", True))
    if not start_now:
        return 0

    role = args.role or cfg.get("role") or ("responder" if joining else "initiator")
    if role not in ("initiator", "responder"):
        raise SystemExit(f"invalid role: {role!r}")
    if args.port is not None:
        port = args.port
    elif cfg.get("port"):
        port = int(cfg["port"])
    elif cfg_path is None:
        port = int(ask("Port", "40000"))
    else:
        port = 40000
    advertise_host = args.advertise_host or cfg.get("advertise_host")
    if not advertise_host:
        if cfg_path is None:
            advertise_host = ask("Advertise host (LAN IP for two machines, Enter for 127.0.0.1)", "127.0.0.1")
        else:
            advertise_host = "127.0.0.1"
    signal = apply_vault_env(data)
    timeout = float(data.get("signal_timeout") or (600.0 if mode == "smtp" else 300.0))
    replay_window = float(data.get("replay_window") or (600.0 if mode == "smtp" else 300.0))
    argv = [
        "--signal", signal,
        "--name", str(data["name"]),
        "--peer", str(data["peer"]),
        "--port", str(port),
        "--role", role,
        "--advertise-host", advertise_host,
        "--signal-timeout", str(timeout),
        "--replay-window", str(replay_window),
    ]
    message = args.message or cfg.get("message")
    if message:
        argv += ["--message", str(message)]
    print(f"[*] starting {role} on port {port} (advertise {advertise_host}) ...")
    return app_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
