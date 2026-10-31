from __future__ import annotations

import base64
import os
import unittest
from unittest.mock import patch

from p2p.config import Config
from p2p.session import Session


def _psk(byte: bytes = b"k") -> str:
    return base64.b64encode(byte * 32).decode("ascii")


def _vault() -> dict:
    return {
        "version": 2,
        "identity": {
            "my_email": "alice@126.com",
            "mode": "smtp",
            "imap_host": "imap.126.com",
            "imap_password": "secret",
            "smtp_host": "smtp.126.com",
            "smtp_port": 465,
            "smtp_starttls": False,
            "signal_timeout": 600.0,
            "replay_window": 600.0,
        },
        "contacts": {
            "bob@163.com": {
                "peer_email": "bob@163.com",
                "psk_b64": _psk(b"b"),
                "mode": "smtp",
                "folder": "INBOX",
                "role": "initiator",
                "local_port": 40101,
            },
            "carol@example.com": {
                "peer_email": "carol@example.com",
                "psk_b64": _psk(b"c"),
                "mode": "smtp",
                "folder": "INBOX",
                "role": "responder",
                "local_port": 40102,
            },
        },
    }


class SessionConfigTests(unittest.TestCase):
    def _session(self, cfg: Config) -> Session:
        session = Session(cfg)
        self.addCleanup(session.log.close)
        return session

    def test_from_vault_per_contact(self) -> None:
        data = _vault()
        cfg = Config.from_vault(data, "bob@163.com")
        self.assertEqual(cfg.contact_id, "bob@163.com")
        self.assertEqual(cfg.name, "alice@126.com")
        self.assertEqual(cfg.peer, "bob@163.com")
        self.assertEqual(cfg.peer_email, "bob@163.com")
        self.assertEqual(cfg.role, "initiator")
        self.assertEqual(cfg.port, 40101)
        self.assertEqual(cfg.signal_mode, "smtp")
        self.assertEqual(cfg.imap_host, "imap.126.com")
        self.assertEqual(cfg.imap_user, "alice@126.com")
        self.assertEqual(cfg.imap_password, "secret")
        self.assertEqual(cfg.smtp_host, "smtp.126.com")
        self.assertEqual(cfg.smtp_port, 465)
        self.assertFalse(cfg.smtp_starttls)
        self.assertEqual(len(cfg.psk_bytes() or b""), 32)

    def test_overrides_role_and_port(self) -> None:
        cfg = Config.from_vault(_vault(), "bob@163.com", role="responder", port=40200)
        self.assertEqual(cfg.role, "responder")
        self.assertEqual(cfg.port, 40200)

    def test_two_configs_do_not_use_process_env(self) -> None:
        data = _vault()
        polluted = {
            "P2P_IMAP_HOST": "wrong.example.com",
            "P2P_IMAP_USER": "wrong@example.com",
            "P2P_IMAP_PASSWORD": "wrong-password",
            "P2P_SMTP_HOST": "wrong-smtp.example.com",
            "P2P_MY_EMAIL": "wrong@example.com",
            "P2P_PEER_EMAIL": "wrong-peer@example.com",
            "P2P_PSK_B64": _psk(b"x"),
        }
        with patch.dict(os.environ, polluted, clear=False):
            bob = Config.from_vault(data, "bob@163.com")
            carol = Config.from_vault(data, "carol@example.com")
            bob_chan = self._session(bob)._channel()
            carol_chan = self._session(carol)._channel()
            self.assertEqual(bob_chan.smtp_host, "smtp.126.com")
            self.assertEqual(bob_chan.my_email, "alice@126.com")
            self.assertEqual(bob_chan.peer_email, "bob@163.com")
            self.assertEqual(bob_chan._password, "secret")
            self.assertEqual(carol_chan.peer_email, "carol@example.com")
            self.assertNotEqual(bob.psk_bytes(), carol.psk_bytes())
            self.assertNotEqual(bob_chan.peer_email, carol_chan.peer_email)

    def test_imap_channel_uses_config(self) -> None:
        data = _vault()
        data["identity"] = {
            "my_email": "shared@example.com",
            "mode": "imap",
            "imap_host": "imap.example.com",
            "imap_password": "shared-secret",
            "signal_timeout": 300.0,
            "replay_window": 300.0,
        }
        data["contacts"] = {
            "peer": {
                "peer_email": "peer",
                "psk_b64": _psk(b"i"),
                "mode": "imap",
                "folder": "p2p-test",
                "role": "initiator",
                "local_port": 40103,
            }
        }
        cfg = Config.from_vault(data, "peer")
        chan = self._session(cfg)._channel()
        self.assertEqual(chan.host, "imap.example.com")
        self.assertEqual(chan.user, "shared@example.com")
        self.assertEqual(chan.folder, "p2p-test")
        self.assertEqual(chan._password, "shared-secret")

    def test_short_psk_rejected(self) -> None:
        data = _vault()
        data["contacts"]["bob@163.com"]["psk_b64"] = "cHNr"
        with self.assertRaises(ValueError):
            Config.from_vault(data, "bob@163.com")


if __name__ == "__main__":
    unittest.main()
