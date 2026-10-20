#!/usr/bin/env python3
"""Same-machine integration demo.

Alice and Bob are two separate processes on 127.0.0.1. They exchange their
candidate through a shared directory (the FileChannel), punch UDP, then send
one message each. No external server, no hardcoded secrets.
"""
from __future__ import annotations

import base64
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
STATE.mkdir(parents=True, exist_ok=True)
run_dir = Path(tempfile.mkdtemp(prefix="demo-", dir=STATE))
shared = run_dir / "shared"
logs = run_dir / "logs"
shared.mkdir(parents=True, exist_ok=True)
logs.mkdir(parents=True, exist_ok=True)


def cmd(name: str, peer: str, port: int, role: str, message: str) -> list[str]:
    return [
        sys.executable, "-m", "p2p",
        "--name", name,
        "--peer", peer,
        "--port", str(port),
        "--role", role,
        "--signal", "file",
        "--shared-dir", str(shared),
        "--log-dir", str(logs),
        "--message", message,
        "--chat-wait", "2",
        "--chat-delay", "0.5",
        "--punch-window", "5",
        "--signal-timeout", "10",
    ]


env = os.environ.copy()
env["PYTHONPATH"] = str(ROOT)
# Random PSK per run, shared by both children via the environment.
# It is never written to disk and never printed.
env["P2P_PSK_B64"] = base64.b64encode(secrets.token_bytes(32)).decode("ascii")

alice = subprocess.Popen(
    cmd("alice", "bob", 40101, "initiator", "hello from alice"),
    cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)
bob = subprocess.Popen(
    cmd("bob", "alice", 40102, "responder", "hello from bob"),
    cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)

try:
    out_alice, _ = alice.communicate(timeout=20)
    out_bob, _ = bob.communicate(timeout=20)
except subprocess.TimeoutExpired:
    alice.kill()
    bob.kill()
    out_alice, _ = alice.communicate()
    out_bob, _ = bob.communicate()

print("--- alice ---")
print(out_alice)
print("--- bob ---")
print(out_bob)

ok = (
    "CONNECTED" in out_alice
    and "CONNECTED" in out_bob
    and "hello from bob" in out_alice
    and "hello from alice" in out_bob
)
print(f"run dir: {run_dir}")
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
