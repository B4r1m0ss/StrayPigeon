from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox

from p2p.gui.single_window import SingleWindow
from p2p.vault import load_vault


class FakeController(QObject):
    state_changed = pyqtSignal(str, str)
    message_received = pyqtSignal(str, str)
    file_progress = pyqtSignal(str, str, int, int)
    error = pyqtSignal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.max_sessions = 16

    def start_handoff(self, *args) -> None:
        pass

    def send(self, *args) -> None:
        pass

    def send_file(self, *args) -> None:
        pass

    def disconnect(self, *args) -> None:
        pass

    def stop(self) -> None:
        pass


class SingleWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        self._tmp = tempfile.TemporaryDirectory()
        psk = base64.b64encode(b"k" * 32).decode("ascii")
        self.data = {
            "version": 2,
            "identity": {
                "my_email": "a@example.com",
                "mode": "smtp",
                "imap_host": "imap.example.com",
                "imap_password": "secret",
                "smtp_host": "smtp.example.com",
                "smtp_port": 465,
                "smtp_starttls": False,
            },
            "contacts": {
                "b@example.com": {
                    "peer_email": "b@example.com",
                    "psk_b64": psk,
                    "mode": "smtp",
                    "role": "initiator",
                    "local_port": 40000,
                }
            },
        }
        self.window = SingleWindow(
            self.data,
            Path(self._tmp.name) / "vault.json",
            "correct-horse",
            controller=FakeController(),
            auto_connect=False,
        )
        self.window.show()

    def tearDown(self) -> None:
        self.window.close()
        self._tmp.cleanup()

    def test_identity_contact_vault_firewall_shell(self) -> None:
        self.assertIn("a@example.com", self.window.identity_label.text())
        self.assertEqual(self.window.vault_btn.text(), "Vault…")
        self.assertEqual(self.window.new_btn.text(), "New contact")
        self.assertEqual(self.window.join_btn.text(), "Join invite")
        self.assertEqual(self.window.delete_btn.text(), "Delete contact")
        self.assertEqual(self.window.firewall_btn.text(), "Allow UDP")
        self.assertEqual(self.window.contact_list.count(), 1)
        self.assertEqual(self.window.tab_widget.count(), 1)


    def test_delete_contact_removes_vault_entry(self) -> None:
        with (
            patch(
                "p2p.gui.single_window.QMessageBox.warning",
                return_value=QMessageBox.StandardButton.Yes,
            ),
            patch("p2p.gui.single_window.QMessageBox.information"),
        ):
            self.window._delete_contact()
        self.assertNotIn("b@example.com", self.window.data["contacts"])
        self.assertEqual(self.window.contact_list.count(), 0)
        self.assertEqual(self.window.tab_widget.count(), 0)
        loaded = load_vault(self.window.vault_path, "correct-horse")
        self.assertNotIn("b@example.com", loaded["contacts"])


if __name__ == "__main__":
    unittest.main()
