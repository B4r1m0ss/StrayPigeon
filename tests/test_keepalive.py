from __future__ import annotations

import socket
import threading
from types import SimpleNamespace
from unittest import TestCase

from p2p.session import Session


def _udp_pair() -> tuple[socket.socket, socket.socket]:
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    server.setblocking(False)
    peer = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    peer.bind(("127.0.0.1", 0))
    peer.settimeout(2.0)
    return server, peer


class KeepaliveTests(TestCase):
    def test_ping_gets_pong(self) -> None:
        server, peer = _udp_pair()
        try:
            session = object.__new__(Session)
            session.sock = server
            peer.sendto(b"PING", server.getsockname())
            session._pump_chat(0.5)
            data, _ = peer.recvfrom(64)
            self.assertEqual(data, b"PONG")
        finally:
            server.close()
            peer.close()

    def test_pong_is_ignored(self) -> None:
        server, peer = _udp_pair()
        try:
            session = object.__new__(Session)
            session.sock = server
            peer.sendto(b"PONG", server.getsockname())
            session._pump_chat(0.2)
            peer.settimeout(0.2)
            with self.assertRaises(socket.timeout):
                peer.recvfrom(64)
        finally:
            server.close()
            peer.close()

    def test_keepalive_sends_ping(self) -> None:
        server, peer = _udp_pair()
        try:
            session = object.__new__(Session)
            session.sock = server
            session.peer_addr = peer.getsockname()
            session._stop = threading.Event()
            session.cfg = SimpleNamespace(keepalive=0.05)
            thread = threading.Thread(target=session._keepalive_loop, daemon=True)
            thread.start()
            data, _ = peer.recvfrom(64)
            self.assertEqual(data, b"PING")
            session._stop.set()
            thread.join(1.0)
            self.assertFalse(thread.is_alive())
        finally:
            server.close()
            peer.close()
