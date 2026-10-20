from __future__ import annotations

import hashlib
import hmac
import select
import socket
import time

from .net import fmt_addr

MAGIC = b"P2P1"
SID_LEN = 16
LEASE_LEN = 8
TAG_LEN = 8
PROBE_LEN = len(MAGIC) + SID_LEN + LEASE_LEN + TAG_LEN  # 36


def _sid_bytes(sid: str) -> bytes:
    raw = bytes.fromhex(sid)
    if len(raw) != SID_LEN:
        raise ValueError("sid must be 16 bytes of hex")
    return raw


def _lease_bytes(lease_id: str) -> bytes:
    raw = bytes.fromhex(lease_id)
    if len(raw) != LEASE_LEN:
        raise ValueError("lease_id must be 8 bytes of hex")
    return raw


def make_probe(sid: str, lease_id: str, psk: bytes | None) -> bytes:
    s = _sid_bytes(sid)
    lease = _lease_bytes(lease_id)
    tag = (
        hmac.new(psk, s + lease, hashlib.sha256).digest()[:TAG_LEN]
        if psk
        else b"\x00" * TAG_LEN
    )
    return MAGIC + s + lease + tag


def parse_probe(data: bytes, psk: bytes | None) -> tuple[str, str] | None:
    if len(data) != PROBE_LEN or not data.startswith(MAGIC):
        return None
    s = data[4 : 4 + SID_LEN]
    lease = data[4 + SID_LEN : 4 + SID_LEN + LEASE_LEN]
    tag = data[4 + SID_LEN + LEASE_LEN :]
    if psk is not None:
        expected = hmac.new(psk, s + lease, hashlib.sha256).digest()[:TAG_LEN]
        if not hmac.compare_digest(tag, expected):
            return None
    return s.hex(), lease.hex()


def punch(
    sock: socket.socket,
    peer_addrs: tuple[str, int] | list[tuple[str, int]],
    sid: str,
    lease_id: str,
    psk: bytes | None = None,
    interval: float = 0.2,
    window: float = 30.0,
    logger=None,
    avoid: set[tuple[str, int]] | None = None,
    stop_event=None,
) -> tuple[str, int] | None:
    """Send probes to one or more candidates until one comes back.

    Both sides must run this at the same time. The probe itself is the
    "I am here" signal; there is no separate start message.

    `avoid` is a set of our own advertised addresses. On the same machine (or
    behind a hairpinning NAT), a probe can loop back to us; accepting it would
    self-connect. Ignore those sources.

    `stop_event` (threading.Event-like) lets the GUI abort a long punch.
    """
    if isinstance(peer_addrs, tuple):
        addrs = [peer_addrs]
    else:
        addrs = list(peer_addrs)
    if not addrs:
        return None
    avoid = set(avoid or set())
    probe = make_probe(sid, lease_id, psk)
    start = time.time()
    next_send = start
    sent = 0
    if logger:
        logger.event("PUNCH_TX", peers=[fmt_addr(a) for a in addrs], window=window)

    while time.time() - start < window:
        if stop_event is not None and stop_event.is_set():
            if logger:
                logger.event("PUNCH_STOPPED", peers=[fmt_addr(a) for a in addrs], sent=sent)
            return None
        now = time.time()
        if now >= next_send:
            for addr in addrs:
                try:
                    sock.sendto(probe, addr)
                    sent += 1
                except OSError:
                    pass
            next_send = now + interval

        r, _, _ = select.select([sock], [], [], 0.02)
        if not r:
            continue
        try:
            data, addr = sock.recvfrom(2048)
        except OSError:
            continue
        if (addr[0], addr[1]) in avoid:
            continue
        parsed = parse_probe(data, psk)
        if not parsed or parsed[0] != sid:
            continue
        try:
            sock.sendto(data, addr)  # reply to the *observed* source
        except OSError:
            pass
        if logger:
            logger.event("PUNCH_RX", peer=fmt_addr(addr), sent=sent)
        return addr

    if logger:
        logger.event("PUNCH_TIMEOUT", peers=[fmt_addr(a) for a in addrs], sent=sent)
    return None
