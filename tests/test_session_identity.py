from __future__ import annotations

import unittest

from p2p.session import is_own_message


class TestSessionIdentity(unittest.TestCase):
    def test_instance_guard(self):
        # Same visible name (both sides use the mailbox address) but different
        # process instances: this is the peer, not our own message.
        self.assertFalse(
            is_own_message({"name": "a@example.com", "instance": "peer1"}, "a@example.com", "self1")
        )
        # Same process instance: our own message.
        self.assertTrue(
            is_own_message({"name": "a@example.com", "instance": "self1"}, "a@example.com", "self1")
        )
        # Legacy peer without an instance id: fall back to the name.
        self.assertTrue(is_own_message({"name": "a@example.com"}, "a@example.com", "self1"))
        self.assertFalse(is_own_message({"name": "b@example.com"}, "a@example.com", "self1"))


if __name__ == "__main__":
    unittest.main()
