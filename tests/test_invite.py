from __future__ import annotations

import unittest

from p2p.invite import encode_invite, parse_invite


class InviteTests(unittest.TestCase):
    def test_smtp_roundtrip(self) -> None:
        payload = {
            "v": 1,
            "mode": "smtp",
            "my_email": "alice@example.com",
            "peer_email": "bob@example.com",
            "psk_b64": "cHNr",
        }
        code = encode_invite(payload)
        parsed = parse_invite(code)
        self.assertEqual(parsed["mode"], "smtp")
        self.assertEqual(parsed["my_email"], "alice@example.com")
        self.assertEqual(parsed["peer_email"], "bob@example.com")
        self.assertEqual(parsed["psk_b64"], "cHNr")

    def test_bare_code_without_prefix(self) -> None:
        payload = {
            "v": 1,
            "mode": "smtp",
            "my_email": "a@example.com",
            "peer_email": "b@example.com",
            "psk_b64": "cHNr",
        }
        code = encode_invite(payload)
        self.assertEqual(parse_invite(code[5:])["peer_email"], "b@example.com")

    def test_checksum_detects_typo(self) -> None:
        payload = {
            "v": 1,
            "mode": "smtp",
            "my_email": "a@example.com",
            "peer_email": "b@example.com",
            "psk_b64": "cHNr",
        }
        code = encode_invite(payload)
        with self.assertRaises(ValueError):
            parse_invite(code[:-1] + ("A" if code[-1] != "A" else "B"))


if __name__ == "__main__":
    unittest.main()
