from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from p2p.onboarding import (
    build_identity,
    create_identity_vault,
    default_imap_host,
    default_smtp_host,
    new_vault_payload,
    reset_identity_vault,
)
from p2p.vault import load_vault


def _smtp_identity() -> dict:
    return build_identity(
        mode="smtp",
        my_email="alice@126.com",
        auth_code="secret",
        imap_host="imap.126.com",
        smtp_host="smtp.126.com",
        smtp_port=465,
    )


class OnboardingTests(unittest.TestCase):
    def test_default_hosts(self) -> None:
        self.assertEqual(default_imap_host("a@126.com"), "imap.126.com")
        self.assertEqual(default_smtp_host("a@163.com"), "smtp.163.com")

    def test_build_smtp_identity_has_no_peer(self) -> None:
        identity = _smtp_identity()
        self.assertEqual(identity["mode"], "smtp")
        self.assertEqual(identity["my_email"], "alice@126.com")
        self.assertEqual(identity["imap_password"], "secret")
        self.assertNotIn("peer", identity)
        self.assertEqual(identity["signal_timeout"], 600.0)
        payload = new_vault_payload(identity)
        self.assertEqual(payload["version"], 2)
        self.assertEqual(payload["contacts"], {})

    def test_build_imap_identity(self) -> None:
        identity = build_identity(
            mode="imap",
            my_email="shared@example.com",
            auth_code="secret",
            imap_host="imap.example.com",
        )
        self.assertEqual(identity["mode"], "imap")
        self.assertNotIn("smtp_host", identity)
        self.assertEqual(identity["signal_timeout"], 300.0)

    def test_invalid_identity(self) -> None:
        with self.assertRaises(ValueError):
            build_identity("smtp", "bad-email", "x", "imap.example.com", "smtp.example.com")
        with self.assertRaises(ValueError):
            build_identity("smtp", "a@example.com", "x", "imap.example.com", "")
        with self.assertRaises(ValueError):
            build_identity(
                "smtp", "a@example.com", "x", "imap.example.com", "smtp.example.com", 0
            )

    def test_create_and_reset_identity_vault(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "vault.json"
            identity = _smtp_identity()
            create_identity_vault(path, "correct-horse", identity)
            loaded = load_vault(path, "correct-horse")
            self.assertEqual(loaded["identity"], identity)
            self.assertEqual(loaded["contacts"], {})
            with self.assertRaises(ValueError):
                create_identity_vault(path, "correct-horse", identity)

            new_identity = build_identity(
                mode="smtp",
                my_email="bob@163.com",
                auth_code="new-secret",
                imap_host="imap.163.com",
                smtp_host="smtp.163.com",
            )
            reset_identity_vault(path, "new-correct-horse", new_identity)
            with self.assertRaises(ValueError):
                load_vault(path, "correct-horse")
            self.assertEqual(
                load_vault(path, "new-correct-horse")["identity"], new_identity
            )


if __name__ == "__main__":
    unittest.main()
