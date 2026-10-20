from __future__ import annotations

import os
import unittest

from p2p.runtime_env import apply_vault_env

KEYS = [
    "P2P_SMTP_HOST", "P2P_SMTP_PORT", "P2P_SMTP_STARTTLS",
    "P2P_MY_EMAIL", "P2P_PEER_EMAIL", "P2P_IMAP_HOST", "P2P_IMAP_USER",
    "P2P_IMAP_PASSWORD", "P2P_SMTP_FOLDER", "P2P_IMAP_FOLDER", "P2P_PSK_B64",
]


class TestRuntimeEnv(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in KEYS}

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_smtp(self):
        data = {
            "mode": "smtp",
            "my_email": "alice@example.com",
            "peer_email": "bob@example.com",
            "imap_host": "imap.example.com",
            "imap_password": "secret",
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "smtp_starttls": False,
            "psk_b64": "cHNr",
        }
        self.assertEqual(apply_vault_env(data), "smtp")
        self.assertEqual(os.environ["P2P_MY_EMAIL"], "alice@example.com")
        self.assertEqual(os.environ["P2P_PEER_EMAIL"], "bob@example.com")
        self.assertEqual(os.environ["P2P_SMTP_STARTTLS"], "0")
        self.assertEqual(os.environ["P2P_SMTP_FOLDER"], "INBOX")
        self.assertEqual(os.environ["P2P_IMAP_USER"], "alice@example.com")

    def test_imap(self):
        data = {
            "mode": "imap",
            "my_email": "test@example.com",
            "imap_host": "imap.example.com",
            "imap_password": "secret",
            "folder": "p2p-test",
            "psk_b64": "cHNr",
        }
        self.assertEqual(apply_vault_env(data), "imap")
        self.assertEqual(os.environ["P2P_IMAP_FOLDER"], "p2p-test")
        self.assertEqual(os.environ["P2P_IMAP_USER"], "test@example.com")

    def test_unsupported(self):
        with self.assertRaises(ValueError):
            apply_vault_env({"mode": "file"})

    def test_v2_smtp_selects_contact(self):
        data = {
            "version": 2,
            "identity": {
                "my_email": "alice@example.com",
                "mode": "smtp",
                "imap_host": "imap.example.com",
                "imap_password": "secret",
                "smtp_host": "smtp.example.com",
                "smtp_port": 465,
                "smtp_starttls": False,
                "signal_timeout": 600.0,
                "replay_window": 600.0,
            },
            "contacts": {
                "bob@example.com": {
                    "peer_email": "bob@example.com",
                    "psk_b64": "cHNr",
                    "mode": "smtp",
                    "folder": "INBOX",
                }
            },
        }
        self.assertEqual(apply_vault_env(data, "bob@example.com"), "smtp")
        self.assertEqual(os.environ["P2P_MY_EMAIL"], "alice@example.com")
        self.assertEqual(os.environ["P2P_PEER_EMAIL"], "bob@example.com")
        self.assertEqual(os.environ["P2P_PSK_B64"], "cHNr")

    def test_v2_multiple_contacts_require_selection(self):
        data = {
            "version": 2,
            "identity": {"my_email": "a@example.com", "mode": "smtp"},
            "contacts": {
                "b@example.com": {"peer_email": "b@example.com", "psk_b64": "x", "mode": "smtp"},
                "c@example.com": {"peer_email": "c@example.com", "psk_b64": "y", "mode": "smtp"},
            },
        }
        with self.assertRaises(ValueError):
            apply_vault_env(data)


if __name__ == "__main__":
    unittest.main()
