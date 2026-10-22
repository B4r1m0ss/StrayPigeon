from __future__ import annotations

import base64
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from p2p.gui.multi_session import MultiSessionWindow
from p2p.signal.mail_router import RoutedSignal


class FakeController(QObject):
    state_changed = pyqtSignal(str, str)
    message_received = pyqtSignal(str, str)
    file_progress = pyqtSignal(str, str, int, int)
    error = pyqtSignal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []
        self.max_sessions = 16

    def start_server(self, host: str, port: int) -> None:
        self.calls.append(("server", host, port))

    def connect(self, session_id: str, host: str, port: int) -> None:
        self.calls.append(("connect", session_id, host, port))
        self.state_changed.emit(session_id, "connecting")

    def send(self, session_id: str, text: str) -> None:
        self.calls.append(("send", session_id, text))

    def send_file(self, session_id: str, path: str) -> None:
        self.calls.append(("file", session_id, path))

    def disconnect(self, session_id: str) -> None:
        self.calls.append(("disconnect", session_id))

    def start_handoff(self, session_id: str, handoff: dict) -> None:
        self.calls.append(("handoff", session_id, handoff.get("sid")))

    def stop(self) -> None:
        self.calls.append(("stop",))


class FakeWorker(QObject):
    state = pyqtSignal(dict)
    error = pyqtSignal(str, str)
    handoff_ready = pyqtSignal(dict)
    finished = pyqtSignal()

    def __init__(self, cfg=None) -> None:
        super().__init__()
        self.cfg = cfg
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


class FakeRouter:
    def __init__(self) -> None:
        self.on_signal = None
        self.contacts = {}

    def bind_contact(self, contact_id, psk):
        self.contacts[contact_id] = psk

    def emit_candidate(self, contact_id: str, ip: str, port: int) -> None:
        assert self.on_signal is not None
        self.on_signal(
            RoutedSignal(
                uid=1,
                sid="sid-" + contact_id,
                seq=1,
                msg_type="CANDIDATE",
                contact_id=contact_id,
                message={
                    "type": "CANDIDATE",
                    "candidates": [{"ip": ip, "port": port, "kind": "srflx"}],
                },
            )
        )


class MultiSessionWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        self._message_patch = patch("p2p.gui.multi_session.QMessageBox.critical")
        self._message_patch.start()
        self.addCleanup(self._message_patch.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.data = {
            "version": 2,
            "identity": {"my_email": "a@example.com", "mode": "smtp", "imap_host": "imap.example.com", "imap_password": "secret", "smtp_host": "smtp.example.com", "smtp_port": 465, "smtp_starttls": False},
            "contacts": {
                "b@example.com": {
                    "peer_email": "b@example.com",
                    "psk_b64": base64.b64encode(b"b" * 32).decode("ascii"),
                    "mode": "smtp",
                    "role": "initiator",
                    "local_port": 40000,
                    "peer_host": "127.0.0.1",
                    "peer_port": 40001,
                },
                "c@example.com": {
                    "peer_email": "c@example.com",
                    "psk_b64": base64.b64encode(b"c" * 32).decode("ascii"),
                    "mode": "smtp",
                    "role": "responder",
                    "local_port": 40002,
                    "peer_host": "127.0.0.1",
                    "peer_port": 40003,
                },
            },
        }
        self.controller = FakeController()
        self.router = FakeRouter()
        self.worker = FakeWorker()
        self.window = MultiSessionWindow(
            self.data,
            Path(self._tmp.name) / "vault.json",
            "pass",
            controller=self.controller,
            mail_router=self.router,
            worker_factory=lambda cfg: self.worker,
        )
        self.window.show()

    def tearDown(self) -> None:
        self.window.close()
        self._tmp.cleanup()

    def test_tabs_and_one_click_connect(self) -> None:
        self.assertEqual(self.window.tab_widget.count(), 2)
        self.assertEqual(self.window.contact_list.count(), 2)
        self.assertEqual(self.window.max_sessions.value(), 16)
        self.controller.calls.clear()
        self.window.contact_list.setCurrentRow(0)
        self.window._connect()
        self.assertTrue(self.worker.started)
        self.window._on_handoff(
            "b@example.com",
            {
                "socket": None,
                "peer_addr": ("127.0.0.1", 40001),
                "peer_candidates": [("127.0.0.1", 40001)],
                "sid": "sid-test",
                "psk": b"x" * 32,
                "role": "initiator",
            },
        )
        self.assertIn(("handoff", "b@example.com", "sid-test"), self.controller.calls)

    def test_send_chat_and_file_progress(self) -> None:
        self.window.contact_list.setCurrentRow(0)
        tab = self.window._tabs["b@example.com"]
        tab.input.setText("hello")
        self.window._send("b@example.com")
        self.assertIn(("send", "b@example.com", "hello"), self.controller.calls)
        self.assertIn("[me] hello", tab.chat.toPlainText())

        self.controller.send_file("b@example.com", "/tmp/x.bin")
        self.assertIn(("file", "b@example.com", "/tmp/x.bin"), self.controller.calls)
        self.controller.file_progress.emit("b@example.com", "x.bin", 2, 4)
        self.assertTrue(tab.progress.isVisible())
        self.assertEqual(tab.progress.value(), 2)

    def test_disconnect_stops_worker_and_releases_vault_guard(self) -> None:
        self.window.contact_list.setCurrentRow(0)
        self.window._connect()
        self.assertTrue(self.worker.started)
        self.assertIn("b@example.com", self.window._workers)
        self.window._disconnect()
        self.assertTrue(self.worker.stopped)
        self.assertNotIn("b@example.com", self.window._workers)
        self.assertIn(("disconnect", "b@example.com"), self.controller.calls)
        self.assertTrue(self.window.connect_btn.isEnabled())
        self.assertFalse(self.window.disconnect_btn.isEnabled())

    def test_incoming_message_and_state_routing(self) -> None:
        self.controller.state_changed.emit("b@example.com", "connected")
        self.controller.message_received.emit("b@example.com", "hi there")
        tab = self.window._tabs["b@example.com"]
        self.assertIn("connected", tab.state.text())
        self.assertIn("[peer] hi there", tab.chat.toPlainText())

    def test_mail_candidate_auto_connects(self) -> None:
        self.router.emit_candidate("b@example.com", "192.0.2.10", 40123)
        self.assertTrue(self.worker.started)
        self.window._on_handoff(
            "b@example.com",
            {
                "socket": None,
                "peer_addr": ("127.0.0.1", 40001),
                "peer_candidates": [("127.0.0.1", 40001)],
                "sid": "sid-test",
                "psk": b"x" * 32,
                "role": "initiator",
            },
        )
        self.assertIn(("handoff", "b@example.com", "sid-test"), self.controller.calls)
        self.assertEqual(
            self.data["contacts"]["b@example.com"]["peer_host"], "192.0.2.10"
        )
        self.assertEqual(
            self.data["contacts"]["b@example.com"]["peer_port"], 40123
        )

    def test_close_stops_controller(self) -> None:
        self.window.close()
        self.assertIn(("stop",), self.controller.calls)


if __name__ == "__main__":
    unittest.main()
