from __future__ import annotations

import base64
import unittest

from p2p.keyexchange import (
    KeyPair,
    derive_session_key,
    open_frame,
    open_signal,
    seal_frame,
    seal_signal,
)


class TestKeyExchange(unittest.TestCase):
    def test_ecdh_matches(self) -> None:
        a = KeyPair.generate()
        b = KeyPair.generate()
        self.assertEqual(a.dh(b.public_b64()), b.dh(a.public_b64()))

    def test_session_key_matches(self) -> None:
        a = KeyPair.generate()
        b = KeyPair.generate()
        sid = "ab" * 16
        dh_a = a.dh(b.public_b64())
        dh_b = b.dh(a.public_b64())
        ka = derive_session_key(dh_a, sid, a.public_bytes, b.public_bytes)
        kb = derive_session_key(dh_b, sid, a.public_bytes, b.public_bytes)
        self.assertEqual(ka, kb)
        self.assertEqual(len(ka), 32)

    def test_signal_roundtrip_and_tamper(self) -> None:
        psk = b"p" * 32
        sid = "cd" * 16
        env = seal_signal(psk, sid, 1, "CANDIDATE", b"hello")
        self.assertEqual(open_signal(psk, env), b"hello")

        bad = dict(env)
        raw = base64.b64decode(env["ct"])
        bad["ct"] = base64.b64encode(b"\x00" * len(raw)).decode()
        with self.assertRaises(Exception):
            open_signal(psk, bad)

    def test_wrong_psk_fails(self) -> None:
        sid = "ef" * 16
        env = seal_signal(b"a" * 32, sid, 1, "CANDIDATE", b"hello")
        with self.assertRaises(Exception):
            open_signal(b"b" * 32, env)

    def test_frame_roundtrip_replay_tamper(self) -> None:
        key = b"k" * 32
        blob = seal_frame(key, 1, b"hi")
        seq, plain = open_frame(key, blob, 0)
        self.assertEqual((seq, plain), (1, b"hi"))
        with self.assertRaises(Exception):
            open_frame(key, blob, 1)  # replay / old seq

        bad = bytearray(blob)
        bad[-1] ^= 0x01
        with self.assertRaises(Exception):
            open_frame(key, bytes(bad), 0)


if __name__ == "__main__":
    unittest.main()
