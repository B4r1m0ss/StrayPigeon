from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path

try:  # optional QUIC extra
    from p2p.quic_file import FileReceiver, FileSender, sha256_file
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
class QuicFileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        cert, key = make_self_signed_cert(self.root / "cert")
        self._server_config = server_configuration(cert, key)
        self._client_config = client_configuration()
        self._port = _free_udp_port()
        self.target_dir = self.root / "received"
        self.receiver = FileReceiver(self.target_dir)
        self.server_manager = QuicSessionManager(
            max_sessions=16, on_message=self._server_message
        )
        await self.server_manager.start_server(
            "127.0.0.1", self._port, self._server_config
        )
        self.client_manager = QuicSessionManager(
            max_sessions=16, on_message=self._client_message
        )
        self.sender: FileSender | None = None
        self.progress: list[tuple[str, int, int]] = []

    async def asyncTearDown(self) -> None:
        await self.client_manager.close()
        await self.server_manager.close()
        self._tmp.cleanup()

    def _server_message(self, session_id: str, data: bytes) -> None:
        session = self.server_manager.get_session(session_id)
        if session is not None:
            self.receiver.on_frame(session, data)

    def _client_message(self, session_id: str, data: bytes) -> None:
        session = self.client_manager.get_session(session_id)
        if session is not None and self.sender is not None:
            self.sender.on_frame(session, data)

    async def _connect(self):
        return await self.client_manager.connect(
            "file", "127.0.0.1", self._port, self._client_config
        )

    async def test_send_4mb_file(self) -> None:
        source = self.root / "source.bin"
        payload = os.urandom(4 * 1024 * 1024)
        source.write_bytes(payload)
        self.sender = FileSender(
            source,
            chunk_size=256 * 1024,
            on_progress=lambda fid, done, total: self.progress.append(
                (fid, done, total)
            ),
        )
        session = await self._connect()
        await self.sender.start(session, timeout=30.0)
        target = self.target_dir / "source.bin"
        self.assertTrue(target.exists())
        self.assertEqual(sha256_file(target, 256 * 1024), sha256_file(source, 256 * 1024))
        self.assertEqual(target.read_bytes(), payload)
        self.assertTrue(self.progress)
        self.assertEqual(self.progress[-1][1], self.progress[-1][2])

    async def test_resume_partial_file(self) -> None:
        source = self.root / "resume.bin"
        payload = os.urandom(4 * 1024 * 1024)
        source.write_bytes(payload)
        chunk_size = 256 * 1024
        file_id = "resume-test"
        # Simulate a previous partial receive: first two chunks + state.
        self.target_dir.mkdir(parents=True, exist_ok=True)
        part = self.target_dir / "resume.bin.part"
        state = self.target_dir / "resume.bin.part.json"
        part.write_bytes(payload[: 2 * chunk_size])
        state.write_text(
            json.dumps(
                {
                    "file_id": file_id,
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "chunk_size": chunk_size,
                    "have": [0, 1],
                }
            ),
            encoding="utf-8",
        )
        self.sender = FileSender(source, chunk_size=chunk_size, file_id=file_id)
        session = await self._connect()
        await self.sender.start(session, timeout=30.0)
        target = self.target_dir / "resume.bin"
        self.assertTrue(target.exists())
        self.assertEqual(target.read_bytes(), payload)
        self.assertFalse(part.exists())
        self.assertFalse(state.exists())


if __name__ == "__main__":
    unittest.main()
