#!/usr/bin/env python3
"""Probe the local NAT: learn the public mapping and classify it.

Usage:
  python3 scripts/nat_probe.py
  python3 scripts/nat_probe.py --stun stun.l.google.com:19302 --stun stun.cloudflare.com:3478
  python3 scripts/nat_probe.py --json

No secrets, UDP only, no traffic to the peer.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from p2p import stun  # noqa: E402

DEFAULT_SERVERS = ("stun.l.google.com:19302", "stun.cloudflare.com:3478")


def main() -> int:
    p = argparse.ArgumentParser(description="Probe NAT mapping behaviour with STUN")
    p.add_argument("--stun", action="append", default=[], help="host:port; repeat for two servers")
    p.add_argument("--timeout", type=float, default=3.0)
    p.add_argument("--json", action="store_true")
    a = p.parse_args()

    servers = a.stun or list(DEFAULT_SERVERS)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))
    sock.setblocking(False)
    rows: list[dict] = []
    try:
        for server in servers[:2]:
            try:
                ip, port = stun.query(sock, server, timeout=a.timeout)
                print(f"[ok]   {server} -> {ip}:{port}")
                rows.append({"server": server, "mapped": f"{ip}:{port}"})
            except Exception as exc:  # noqa: BLE001
                print(f"[fail] {server}: {exc}")
                rows.append({"server": server, "error": str(exc)})

        mapped = [r["mapped"] for r in rows if "mapped" in r]
        if len(mapped) >= 2:
            ports = {int(m.rsplit(":", 1)[1]) for m in mapped}
            kind = "eim" if len(ports) == 1 else "apdm"
            if kind == "eim":
                print("mapping = EIM (endpoint-independent): UDP hole punching can work")
            else:
                print("mapping = APDM (symmetric): without a relay, punching will fail")
        else:
            kind = "unknown"
            print("mapping = unknown: need two successful STUN probes")
            print("hint: UDP may be blocked; try another network, or pass --stun")

        if a.json:
            print(json.dumps({"mapping": kind, "servers": rows}, indent=2, ensure_ascii=False))
        return 0 if kind != "unknown" else 1
    finally:
        sock.close()


if __name__ == "__main__":
    raise SystemExit(main())
