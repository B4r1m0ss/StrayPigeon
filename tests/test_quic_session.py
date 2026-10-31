from __future__ import annotations

import asyncio
import socket
import tempfile
import unittest
from pathlib import Path

try:  # optional QUIC extra
    from p2p.quic_transport import (
        QuicSessionManager,
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
class QuicSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        cert, key = make_self_signed_cert(Path(self._tmp.name))
        self._server_config = server_configuration(cert, key)
        self._client_config = client_configuration()
        self._port = _free_udp_port()
        self.received: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()
        self.server_manager = QuicSessionManager(
            max_sessions=16,
            on_message=lambda sid, payload: self.received.put_nowait((sid, payload)),
        )
        await self.server_manager.start_server(
            "127.0.0.1", self._port, self._server_config
        )
        self.client_manager = QuicSessionManager(max_sessions=16)

    async def asyncTearDown(self) -> None:
        await self.client_manager.close()
        await self.server_manager.close()
        self._tmp.cleanup()

    async def test_16_chat_sessions_on_one_server_port(self) -> None:
        async def one(i: int):
            session = await self.client_manager.connect(
                f"c{i}", "127.0.0.1", self._port, self._client_config
            )
            await session.send(f"hello-{i:02d}")
            return session

        await asyncio.gather(*(one(i) for i in range(16)))
        messages = []
        for _ in range(16):
            messages.append(await asyncio.wait_for(self.received.get(), 15.0))
        payloads = sorted(payload.decode("utf-8") for _sid, payload in messages)
        self.assertEqual(payloads, [f"hello-{i:02d}" for i in range(16)])
        self.assertEqual(self.server_manager.active_sessions, 16)
        self.assertEqual(self.client_manager.active_sessions, 16)

    async def test_17th_session_is_rejected_at_the_limit(self) -> None:
        for i in range(16):
            await self.client_manager.connect(
                f"c{i}", "127.0.0.1", self._port, self._client_config
            )
        with self.assertRaises(RuntimeError):
            await self.client_manager.connect(
                "c16", "127.0.0.1", self._port, self._client_config
            )
        self.assertEqual(self.client_manager.active_sessions, 16)


if __name__ == "__main__":
    unittest.main()
