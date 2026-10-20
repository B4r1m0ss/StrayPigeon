from __future__ import annotations

import socket


def bind_udp(host: str, port: int) -> socket.socket:
    """Bind one UDP socket and keep it for the whole session.

    Do NOT close and rebind: the NAT mapping is tied to this local port.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((host, port))
    s.setblocking(False)
    return s


def parse_addr(text: str) -> tuple[str, int]:
    host, port = text.rsplit(":", 1)
    return host, int(port)


def fmt_addr(addr: tuple[str, int]) -> str:
    return f"{addr[0]}:{addr[1]}"


def local_ipv4() -> str:
    """Best-effort local LAN address (no packet is sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 192.0.2.1 is TEST-NET-1; connect() only selects a route.
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
