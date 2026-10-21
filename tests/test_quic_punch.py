from __future__ import annotations

import asyncio
import secrets
import socket
import tempfile
import unittest
from pathlib import Path

try:  # optional QUIC extra
    from p2p.quic_punch import PunchQuicClient, PunchQuicServer
    from p2p.quic_transport import (
        client_configuration,
        make_self_signed_cert,
        server_configuration,
    )

    HAVE_QUIC = True
except Exception:  # noqa: BLE001
    HAVE_QUIC = False


def _free_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


@unittest.skipUnless(HAVE_QUIC, "aioquic not installed (pip install aioquic)")
class QuicPunchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        cert, key = make_self_signed_cert(root / "cert")
        self.server_port = _free_udp_port()
        self.client_port = _free_udp_port()
        self.sid = secrets.token_hex(16)
        self.lease = secrets.token_hex(8)
        self.psk = secrets.token_bytes(32)
        self.received: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()
        self.server = PunchQuicServer(
            server_configuration(cert, key),
            self.sid,
            self.lease,
            self.psk,
            on_message=lambda sid, data: self.received.put_nowait((sid, data)),
        )
        self.client = PunchQuicClient(
            client_configuration(),
            self.sid,
            self.lease,
            self.psk,
            on_message=lambda sid, data: self.received.put_nowait((sid, data)),
        )
        await self.server.start(
            "127.0.0.1",
            self.server_port,
            candidates=[("127.0.0.1", self.client_port)],
            timeout=10.0,
        )
        observed = await self.client.start(
            self.client_port,
            [("127.0.0.1", self.server_port)],
            timeout=10.0,
        )
        self.assertEqual(observed, ("127.0.0.1", self.server_port))

    async def asyncTearDown(self) -> None:
        try:
            await self.client.close()
        finally:
            await self.server.close()
            self._tmp.cleanup()

    async def test_punch_then_quic_chat_on_same_socket(self) -> None:
        self.client.protocol.send_message("hello after punch")
        sid, payload = await asyncio.wait_for(self.received.get(), 5.0)
        self.assertEqual(sid, "server")
        self.assertEqual(payload, b"hello after punch")


if __name__ == "__main__":
    unittest.main()
