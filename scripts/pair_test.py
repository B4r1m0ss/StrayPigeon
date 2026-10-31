#!/usr/bin/env python3
"""Non-interactive two-process test using two encrypted vaults.

Prereqs:
  - two vaults created by setup.py (one per role; same IMAP folder or
    two mailboxes depending on mode);
  - P2P_VAULT_PASSPHRASE set (same passphrase for both test vaults), or
    --env-file with P2P_VAULT_PASSPHRASE=...

Usage:
  P2P_VAULT_PASSPHRASE='...' python3 scripts/pair_test.py \
      --alice-vault state/alice-vault.json \
      --bob-vault state/bob-vault.json

It starts run_vault.py for both roles, sends one message each, and checks
that both saw CONNECTED and the peer's message. Prints PASS/FAIL and keeps
the stdout logs under state/pair-*/.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"env file not found: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> int:
    parser = argparse.ArgumentParser(description="Run two vaults end-to-end")
    parser.add_argument("--alice-vault", required=True)
    parser.add_argument("--bob-vault", required=True)
    parser.add_argument("--alice-contact", default=None)
    parser.add_argument("--bob-contact", default=None)
    parser.add_argument("--passphrase-env", default="P2P_VAULT_PASSPHRASE")
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--port-base", type=int, default=40101)
    parser.add_argument("--advertise-host", default="127.0.0.1")
    parser.add_argument("--message-a", default="hello from alice")
    parser.add_argument("--message-b", default="hello from bob")
    parser.add_argument("--chat-wait", type=float, default=8.0)
    parser.add_argument("--punch-window", type=float, default=15.0)
    parser.add_argument("--signal-timeout", type=float, default=120.0)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--work-dir", default=None)
    args = parser.parse_args()

    if args.env_file:
        load_env(Path(args.env_file).expanduser())
    passphrase = os.environ.get(args.passphrase_env, "")
    if not passphrase:
        print(f"[FAIL] set {args.passphrase_env} (or use --env-file)")
        return 2

    work = Path(args.work_dir or f"state/pair-{int(time.time())}")
    work.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env[args.passphrase_env] = passphrase
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    def start(vault: str, role: str, port: int, message: str, out: Path, contact: str | None = None):
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "run_vault.py"),
            "--vault", str(Path(vault).expanduser()),
            "--role", role,
            "--port", str(port),
            "--advertise-host", args.advertise_host,
            "--message", message,
            "--chat-wait", str(args.chat_wait),
            "--punch-window", str(args.punch_window),
            "--signal-timeout", str(args.signal_timeout),
        ]
        if contact:
            cmd += ["--contact", contact]
        fh = open(out, "w", encoding="utf-8")
        proc = subprocess.Popen(
            cmd, cwd=ROOT, env=env, stdout=fh, stderr=subprocess.STDOUT, text=True
        )
        return proc, fh

    a_out = work / "alice.out"
    b_out = work / "bob.out"
    pa, fa = start(args.alice_vault, "initiator", args.port_base, args.message_a, a_out, args.alice_contact)
    pb, fb = start(args.bob_vault, "responder", args.port_base + 1, args.message_b, b_out, args.bob_contact)

    timed_out = False
    try:
        pa.wait(timeout=args.timeout)
        pb.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        pa.kill()
        pb.kill()
        pa.wait()
        pb.wait()
    finally:
        fa.close()
        fb.close()

    out_a = a_out.read_text(encoding="utf-8", errors="replace")
    out_b = b_out.read_text(encoding="utf-8", errors="replace")
    print(f"== work: {work}")
    print("--- alice ---")
    print(out_a)
    print("--- bob ---")
    print(out_b)

    ok = (
        not timed_out
        and "CONNECTED" in out_a
        and "CONNECTED" in out_b
        and args.message_b in out_a
        and args.message_a in out_b
    )
    if timed_out:
        print("[FAIL] timeout")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
