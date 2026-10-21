from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from p2p.vault import change_passphrase, clear_vault, load_vault, save_vault


class TestVault(unittest.TestCase):
    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "vault.json"
            data = {"mailbox": "a@example.com", "imap_password": "secret", "psk_b64": "x"}
            save_vault(path, "correct-horse", data)
            self.assertEqual(load_vault(path, "correct-horse"), data)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_wrong_passphrase(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "vault.json"
            save_vault(path, "correct-horse", {"a": 1})
            with self.assertRaises(ValueError):
                load_vault(path, "wrong-passphrase")

    def test_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "vault.json"
            save_vault(path, "correct-horse", {"a": 1})
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace('"ct": "', '"ct": "A'), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_vault(path, "correct-horse")

    def test_change_passphrase(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "vault.json"
            data = {
                "version": 2,
                "identity": {"my_email": "a@example.com", "mode": "smtp"},
                "contacts": {"b@example.com": {"psk_b64": "x"}},
            }
            save_vault(path, "correct-horse", data)
            change_passphrase(path, "correct-horse", "new-correct-horse")
            with self.assertRaises(ValueError):
                load_vault(path, "correct-horse")
            self.assertEqual(load_vault(path, "new-correct-horse"), data)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_change_passphrase_wrong_old_keeps_vault(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "vault.json"
            save_vault(path, "correct-horse", {"a": 1})
            with self.assertRaises(ValueError):
                change_passphrase(path, "wrong-passphrase", "new-correct-horse")
            self.assertEqual(load_vault(path, "correct-horse"), {"a": 1})

    def test_change_passphrase_rejects_short_new(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "vault.json"
            save_vault(path, "correct-horse", {"a": 1})
            with self.assertRaises(ValueError):
                change_passphrase(path, "correct-horse", "short")
            self.assertEqual(load_vault(path, "correct-horse"), {"a": 1})

    def test_clear_vault_no_backup(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "vault.json"
            save_vault(path, "correct-horse", {"a": 1})
            clear_vault(path)
            self.assertFalse(path.exists())
            self.assertFalse((Path(d) / "vault.json.bak").exists())
            with self.assertRaises(FileNotFoundError):
                load_vault(path, "correct-horse")


if __name__ == "__main__":
    unittest.main()
