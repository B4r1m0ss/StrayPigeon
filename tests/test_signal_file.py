from __future__ import annotations

import tempfile
import unittest

from p2p.signal.file import FileChannel


class TestFileChannel(unittest.TestCase):
    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            alice = FileChannel(d, "alice", "bob")
            bob = FileChannel(d, "bob", "alice")
            alice.send("s1", 1, b"hello")
            self.assertEqual(bob.recv("s1", 0), [(1, b"hello")])
            self.assertEqual(bob.recv("s1", 1), [])

    def test_sid_filter(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            alice = FileChannel(d, "alice", "bob")
            bob = FileChannel(d, "bob", "alice")
            alice.send("s1", 1, b"one")
            alice.send("s2", 2, b"two")
            self.assertEqual(bob.recv("s1", 0), [(1, b"one")])
            self.assertEqual(len(bob.recv("", 0)), 2)


if __name__ == "__main__":
    unittest.main()
