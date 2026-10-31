#!/usr/bin/env python3
"""Minimal UDP STUN Binding server for the netns testbed."""

from __future__ import annotations

import argparse
import socket
import struct

MAGIC = 0x2112A442


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=3478)
    args = ap.parse_args()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    while True:
        try:
            data, addr = sock.recvfrom(2048)
        except OSError:
            continue
        if len(data) < 20:
            continue
        mtype, _mlen, cookie, txid = struct.unpack_from("!HHI12s", data, 0)
        if mtype != 0x0001 or cookie != MAGIC:
            continue
        xport = addr[1] ^ 0x2112
        xip = struct.unpack("!I", socket.inet_aton(addr[0]))[0] ^ MAGIC
        attr = struct.pack("!HHBBHI", 0x0020, 8, 0, 1, xport, xip)
        resp = struct.pack("!HHI12s", 0x0101, len(attr), MAGIC, txid) + attr
        try:
            sock.sendto(resp, addr)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
