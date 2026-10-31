#!/usr/bin/env python3
"""Minimal AF_PACKET UDP capture for the netns testbed.

No tcpdump/libpcap dependency. Captures on one interface, decodes IPv4/UDP,
counts packets and checks whether a plaintext marker appears in any payload.
"""

from __future__ import annotations

import argparse
import json
import socket
import struct
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--iface", required=True)
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--marker", default="STRAYPIGEON_FILE_MARKER")
    args = ap.parse_args()

    marker = args.marker.encode()
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))
    sock.bind((args.iface, 0))
    sock.settimeout(0.5)
    deadline = time.time() + args.seconds
    frames = 0
    udp_packets = 0
    udp_bytes = 0
    ports: dict[int, int] = {}
    marker_hits = 0
    p2p1_hits = 0
    first_src = ""
    first_dst = ""

    while time.time() < deadline:
        try:
            frame, _addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            break
        frames += 1
        if len(frame) < 14:
            continue
        eth_type = struct.unpack_from("!H", frame, 12)[0]
        off = 14
        if eth_type == 0x8100 and len(frame) >= 18:
            eth_type = struct.unpack_from("!H", frame, 16)[0]
            off = 18
        if eth_type != 0x0800 or len(frame) < off + 20:
            continue
        ihl = (frame[off] & 0x0F) * 4
        if len(frame) < off + ihl + 8:
            continue
        proto = frame[off + 9]
        if proto != 17:
            continue
        src = socket.inet_ntoa(frame[off + 12 : off + 16])
        dst = socket.inet_ntoa(frame[off + 16 : off + 20])
        udp_off = off + ihl
        sport, dport, ulen = struct.unpack_from("!HHH", frame, udp_off)
        payload = frame[udp_off + 8 : udp_off + max(8, ulen)]
        udp_packets += 1
        udp_bytes += len(payload)
        ports[sport] = ports.get(sport, 0) + 1
        ports[dport] = ports.get(dport, 0) + 1
        if not first_src:
            first_src, first_dst = f"{src}:{sport}", f"{dst}:{dport}"
        if marker in payload:
            marker_hits += 1
        if payload.startswith(b"P2P1"):
            p2p1_hits += 1

    sock.close()
    out = {
        "iface": args.iface,
        "seconds": args.seconds,
        "frames": frames,
        "udp_packets": udp_packets,
        "udp_payload_bytes": udp_bytes,
        "ports": {str(k): v for k, v in sorted(ports.items())},
        "p2p1_hits": p2p1_hits,
        "marker": args.marker,
        "marker_hits": marker_hits,
        "first_src": first_src,
        "first_dst": first_dst,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
