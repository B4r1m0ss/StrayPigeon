from __future__ import annotations

import threading
import time
import unittest

from p2p.net import bind_udp
from p2p.punch import punch


class TestPunch(unittest.TestCase):
    def test_two_local_sockets(self) -> None:
        a = bind_udp("127.0.0.1", 0)
        b = bind_udp("127.0.0.1", 0)
        try:
            a_addr = a.getsockname()
            b_addr = b.getsockname()
            sid = "00" * 16
            lease = "11" * 8
            results: dict[str, tuple[str, int] | None] = {}

            def run(sock, peer, key: str) -> None:
                results[key] = punch(sock, peer, sid, lease, psk=None, interval=0.05, window=3.0)

            t1 = threading.Thread(target=run, args=(a, b_addr, "a"))
            t2 = threading.Thread(target=run, args=(b, a_addr, "b"))
            t1.start()
            t2.start()
            t1.join()
            t2.join()
            self.assertIsNotNone(results["a"])
            self.assertIsNotNone(results["b"])
        finally:
            a.close()
            b.close()

    def test_stop_event_aborts_punch(self) -> None:
        sock = bind_udp("127.0.0.1", 0)
        try:
            stop = threading.Event()
            stop.set()
            start = time.monotonic()
            result = punch(
                sock,
                [("127.0.0.1", 1)],
                "00" * 16,
                "11" * 8,
                psk=None,
                window=30.0,
                stop_event=stop,
            )
            self.assertIsNone(result)
            self.assertLess(time.monotonic() - start, 1.0)
        finally:
            sock.close()


if __name__ == "__main__":
    unittest.main()
