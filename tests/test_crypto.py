from __future__ import annotations

import unittest

from p2p import crypto


@unittest.skipUnless(crypto.HAVE_CRYPTO, "cryptography is not installed")
class TestCrypto(unittest.TestCase):
    def test_roundtrip(self) -> None:
        key = b"k" * 32
        blob = crypto.seal(key, b"hello", b"aad")
        self.assertEqual(crypto.open_(key, blob, b"aad"), b"hello")

    def test_tamper(self) -> None:
        key = b"k" * 32
        blob = bytearray(crypto.seal(key, b"hello", b"aad"))
        blob[-1] ^= 0x01
        with self.assertRaises(Exception):
            crypto.open_(key, bytes(blob), b"aad")

    def test_hkdf_is_deterministic(self) -> None:
        a = crypto.hkdf_sha256(b"ikm", b"salt", b"info", 32)
        b = crypto.hkdf_sha256(b"ikm", b"salt", b"info", 32)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 32)


if __name__ == "__main__":
    unittest.main()
