from __future__ import annotations

import os
import select
import socket
import struct
import time

MAGIC_COOKIE = 0x2112A442
BINDING_REQUEST = 0x0001
BINDING_RESPONSE = 0x0101
ATTR_MAPPED_ADDRESS = 0x0001
ATTR_XOR_MAPPED_ADDRESS = 0x0020


def resolve_server(server: str) -> tuple[str, int]:
    host, port = server.rsplit(":", 1)
    infos = socket.getaddrinfo(host, int(port), family=socket.AF_INET, type=socket.SOCK_DGRAM)
    return infos[0][4][0], int(port)


def _iter_attrs(data: bytes, start: int, end: int):
    off = start
    while off + 4 <= end:
        atype, alen = struct.unpack_from("!HH", data, off)
        vstart = off + 4
        vend = vstart + alen
        if vend > end:
            break
        yield atype, data[vstart:vend]
        off = vend + ((4 - (alen % 4)) % 4)


def _parse_xor_mapped(value: bytes) -> tuple[str, int] | None:
    if len(value) < 8:
        return None
    family = value[1]
    if family != 0x01:  # IPv4 only in this skeleton
        return None
    xport = struct.unpack_from("!H", value, 2)[0] ^ 0x2112
    xaddr = struct.unpack_from("!I", value, 4)[0] ^ MAGIC_COOKIE
    return socket.inet_ntoa(struct.pack("!I", xaddr)), xport


def query(sock: socket.socket, server: str, timeout: float = 2.0) -> tuple[str, int]:
    """Minimal STUN Binding Request. Only needs UDP; no external library."""
    host, port = resolve_server(server)
    txid = os.urandom(12)
    req = struct.pack("!HHI12s", BINDING_REQUEST, 0, MAGIC_COOKIE, txid)
    sock.sendto(req, (host, port))
    deadline = time.time() + timeout
    while time.time() < deadline:
        r, _, _ = select.select([sock], [], [], 0.1)
        if not r:
            continue
        try:
            data, _ = sock.recvfrom(2048)
        except OSError:
            continue
        if len(data) < 20:
            continue
        mtype, mlen, cookie, rtxid = struct.unpack_from("!HHI12s", data, 0)
        if cookie != MAGIC_COOKIE or rtxid != txid or mtype != BINDING_RESPONSE:
            continue
        end = 20 + mlen
        for atype, value in _iter_attrs(data, 20, end):
            if atype == ATTR_XOR_MAPPED_ADDRESS:
                parsed = _parse_xor_mapped(value)
                if parsed:
                    return parsed
            if atype == ATTR_MAPPED_ADDRESS and len(value) >= 8:
                port_ = struct.unpack_from("!H", value, 2)[0]
                ip_ = socket.inet_ntoa(value[4:8])
                return ip_, port_
    raise TimeoutError(f"STUN timeout: {server}")


def classify_nat(sock: socket.socket, servers: list[str]) -> str:
    """Compare mapped ports across two destinations.

    eim  = endpoint-independent mapping (hole punching can work)
    apdm = address/port-dependent mapping (symmetric; no relay => fail)
    unknown = fewer than two successful probes
    """
    mapped: list[tuple[str, int]] = []
    for server in servers[:2]:
        try:
            mapped.append(query(sock, server))
        except Exception:  # noqa: BLE001
            continue
    if len(mapped) < 2:
        return "unknown"
    return "eim" if mapped[0][1] == mapped[1][1] else "apdm"
