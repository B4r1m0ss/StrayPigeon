from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from p2p.vault import load_vault, save_vault


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


if __name__ == "__main__":
    unittest.main()
