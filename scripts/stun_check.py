#!/usr/bin/env python3
"""Verbose STUN reachability check.

Prints each step so you can tell whether the problem is DNS, outbound UDP,
a missing response, or a malformed response.

Usage:
  python3 scripts/stun_check.py
  python3 scripts/stun_check.py --stun stun.miwifi.com:3478
  python3 scripts/stun_check.py --stun 1.2.3.4:3478 --timeout 5
"""

from __future__ import annotations

import argparse
import os
import select
import socket
import struct
import sys
import time

MAGIC_COOKIE = 0x2112A442
BINDING_REQUEST = 0x0001
BINDING_RESPONSE = 0x0101
ATTR_XOR_MAPPED_ADDRESS = 0x0020
DEFAULT_SERVERS = ("stun.l.google.com:19302", "stun.cloudflare.com:3478")


def parse_mapped(data: bytes) -> tuple[str, int] | None:
    if len(data) < 20:
        return None
    mtype, mlen, cookie, _ = struct.unpack_from("!HHI12s", data, 0)
    if mtype != BINDING_RESPONSE or cookie != MAGIC_COOKIE:
        return None
    off = 20
    end = 20 + mlen
    while off + 4 <= end:
        atype, alen = struct.unpack_from("!HH", data, off)
        value = data[off + 4: off + 4 + alen]
        if atype == ATTR_XOR_MAPPED_ADDRESS and len(value) >= 8:
            port = struct.unpack_from("!H", value, 2)[0] ^ 0x2112
            addr = struct.unpack_from("!I", value, 4)[0] ^ MAGIC_COOKIE
            return socket.inet_ntoa(struct.pack("!I", addr)), port
        off += 4 + alen + ((4 - alen % 4) % 4)
    return None


def check(server: str, timeout: float) -> bool:
    print(f"== {server}")
    host, port_s = server.rsplit(":", 1)
    port = int(port_s)

    print("   resolving ...", flush=True)
    started = time.time()
    try:
        infos = socket.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_DGRAM)
    except Exception as exc:  # noqa: BLE001
        print(f"   DNS FAIL: {type(exc).__name__}: {exc}")
        return False
    ip = infos[0][4][0]
    print(f"   resolved {ip}:{port} in {time.time() - started:.2f}s", flush=True)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))
    sock.setblocking(False)
    txid = os.urandom(12)
    req = struct.pack("!HHI12s", BINDING_REQUEST, 0, MAGIC_COOKIE, txid)
    try:
        print(f"   sending Binding Request from {sock.getsockname()[0]}:{sock.getsockname()[1]}", flush=True)
        sock.sendto(req, (ip, port))
        deadline = time.time() + timeout
        while time.time() < deadline:
            r, _, _ = select.select([sock], [], [], 0.2)
            if not r:
                continue
            data, addr = sock.recvfrom(2048)
            print(f"   recv {len(data)} bytes from {addr[0]}:{addr[1]}", flush=True)
            if len(data) < 20:
                print("   malformed: too short")
                continue
            mtype, mlen, cookie, rtxid = struct.unpack_from("!HHI12s", data, 0)
            print(
                f"   type=0x{mtype:04x} cookie={'ok' if cookie == MAGIC_COOKIE else 'bad'} "
                f"txid={'match' if rtxid == txid else 'mismatch'}",
                flush=True,
            )
            mapped = parse_mapped(data)
            if mapped:
                print(f"   mapped={mapped[0]}:{mapped[1]}")
                print("   PASS")
                return True
            print("   no XOR-MAPPED-ADDRESS")
        print(f"   NO RESPONSE after {timeout:.1f}s")
        return False
    finally:
        sock.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Verbose STUN check (DNS/send/recv)")
    p.add_argument("--stun", action="append", default=[], help="host:port; repeat for more")
    p.add_argument("--timeout", type=float, default=3.0, help="seconds per server")
    a = p.parse_args()
    servers = a.stun or list(DEFAULT_SERVERS)
    results = [check(s, a.timeout) for s in servers]
    return 0 if any(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
