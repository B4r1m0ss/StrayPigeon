from __future__ import annotations

import hashlib
import os
import socket
import tempfile
import time
import unittest
from pathlib import Path

try:  # optional QUIC extra
    from PyQt6.QtTest import QSignalSpy
    from PyQt6.QtWidgets import QApplication

    from p2p.gui.quic_controller import HAVE_QUIC, QuicController

    HAVE_TEST = HAVE_QUIC
except Exception:  # noqa: BLE001
    HAVE_TEST = False


def _free_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


@unittest.skipUnless(HAVE_TEST, "aioquic or PyQt6 not available")
class QuicControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.server = QuicController(self.root / "server-cert", self.root / "server-files")
        self._port = _free_udp_port()
        self._server_states = QSignalSpy(self.server.state_changed)
        self.server.start_server("127.0.0.1", self._port)
        self.assertTrue(self._wait(lambda: len(self._server_states) > 0))
        self.client = QuicController(self.root / "client-cert", self.root / "client-files")
        self._client_states = QSignalSpy(self.client.state_changed)
        self._server_messages = QSignalSpy(self.server.message_received)
        self._server_files = QSignalSpy(self.server.file_progress)
        self._client_files = QSignalSpy(self.client.file_progress)
        self._client_errors = QSignalSpy(self.client.error)
        self._server_errors = QSignalSpy(self.server.error)

    def tearDown(self) -> None:
        try:
            self.client.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.server.stop()
        except Exception:  # noqa: BLE001
            pass
        self._tmp.cleanup()

    def _wait(self, predicate, timeout: float = 15.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.02)
        self.app.processEvents()
        return bool(predicate())

    def test_one_click_connect_and_chat(self) -> None:
        port = self._port
        self.assertGreater(port, 0)
        self.client.connect("bob", "127.0.0.1", port)
        self.assertTrue(
            self._wait(
                lambda: any(
                    s[1] == "connected" for s in list(self._client_states)
                )
            ),
            "client did not connect",
        )
        self.client.send("bob", "hello over QUIC")
        self.assertTrue(
            self._wait(lambda: len(self._server_messages) > 0),
            "server did not receive chat",
        )
        payload = str(self._server_messages[0][1])
        self.assertEqual(payload, "hello over QUIC")

    def test_file_progress_and_completion(self) -> None:
        port = self._port
        self.client.connect("bob", "127.0.0.1", port)
        self.assertTrue(
            self._wait(
                lambda: any(
                    s[1] == "connected" for s in list(self._client_states)
                )
            )
        )
        source = self.root / "payload.bin"
        source.write_bytes(os.urandom(512 * 1024))
        self.client.send_file("bob", str(source))
        target = self.root / "server-files" / "payload.bin"
        ok = self._wait(lambda: target.exists(), timeout=20.0)
        if not ok:
            print("client errors", list(self._client_errors))
            print("server errors", list(self._server_errors))
            print("server files", list(self._server_files))
            print("client files", list(self._client_files))
        self.assertTrue(ok, "file did not complete")
        self.assertEqual(
            hashlib.sha256(target.read_bytes()).hexdigest(),
            hashlib.sha256(source.read_bytes()).hexdigest(),
        )
        self.assertTrue(len(self._server_files) > 0 or len(self._client_files) > 0)
