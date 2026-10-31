from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import tempfile
import unittest
from pathlib import Path

try:  # optional QUIC extra
    from p2p.quic_file import FileReceiver, FileSender
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
class QuicStressTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        cert, key = make_self_signed_cert(self.root / "cert")
        self._server_config = server_configuration(cert, key)
        self._client_config = client_configuration()
        self._port = _free_udp_port()
        self.receiver = FileReceiver(self.root / "received")
        self.server_manager = QuicSessionManager(
            max_sessions=16, on_message=self._server_message
        )
        await self.server_manager.start_server(
            "127.0.0.1", self._port, self._server_config
        )
        self.senders: dict[str, FileSender] = {}
        self.client_manager = QuicSessionManager(
            max_sessions=16, on_message=self._client_message
        )

    async def asyncTearDown(self) -> None:
        await self.client_manager.close()
        await self.server_manager.close()
        self._tmp.cleanup()

    def _client_message(self, session_id: str, data: bytes) -> None:
        sender = self.senders.get(session_id)
        session = self.client_manager.get_session(session_id)
        if sender is not None and session is not None:
            sender.on_frame(session, data)

    def _server_message(self, session_id: str, data: bytes) -> None:
        session = self.server_manager.get_session(session_id)
        if session is not None:
            self.receiver.on_frame(session, data)

    async def test_16_concurrent_1mb_files_on_one_udp_port(self) -> None:
        tasks = []
        for i in range(16):
            source = self.root / f"source-{i:02d}.bin"
            payload = os.urandom(1024 * 1024)
            source.write_bytes(payload)
            session = await self.client_manager.connect(
                f"c{i:02d}", "127.0.0.1", self._port, self._client_config
            )
            sender = FileSender(source, chunk_size=256 * 1024)
            self.senders[f"c{i:02d}"] = sender
            tasks.append(sender.start(session, timeout=60.0))
        await asyncio.gather(*tasks)
        self.assertEqual(self.server_manager.active_sessions, 16)
        self.assertEqual(self.client_manager.active_sessions, 16)
        for i in range(16):
            source = self.root / f"source-{i:02d}.bin"
            target = self.root / "received" / source.name
            self.assertTrue(target.exists(), f"missing {target.name}")
            self.assertEqual(
                hashlib.sha256(target.read_bytes()).hexdigest(),
                hashlib.sha256(source.read_bytes()).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
