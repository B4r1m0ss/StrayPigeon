from __future__ import annotations

import base64
import os
import unittest

from p2p.config import Config


class TestSecrets(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("P2P_PSK_B64", None)

    def test_psk_only_from_env(self) -> None:
        psk = b"x" * 32
        os.environ["P2P_PSK_B64"] = base64.b64encode(psk).decode()
        cfg = Config(name="a", peer="b", port=1, role="initiator")
        self.assertEqual(cfg.psk_bytes(), psk)
        self.assertNotIn(psk.hex(), repr(cfg.redacted()))
        self.assertNotIn("x" * 32, repr(cfg.redacted()))

    def test_missing_psk_is_none(self) -> None:
        cfg = Config(name="a", peer="b", port=1, role="initiator")
        self.assertIsNone(cfg.psk_bytes())


if __name__ == "__main__":
    unittest.main()
