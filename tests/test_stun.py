from __future__ import annotations

import socket
import struct
import threading
import unittest

from p2p import stun


class TestStun(unittest.TestCase):
    def test_query_against_local_fake_server(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(("127.0.0.1", 0))
        server.settimeout(2.0)
        port = server.getsockname()[1]
        done = threading.Event()

        def serve() -> None:
            try:
                data, addr = server.recvfrom(2048)
                _mtype, _mlen, _cookie, txid = struct.unpack_from("!HHI12s", data, 0)
                xport = addr[1] ^ 0x2112
                xip = struct.unpack("!I", socket.inet_aton(addr[0]))[0] ^ 0x2112A442
                attr = struct.pack("!HHBBHI", 0x0020, 8, 0, 1, xport, xip)
                resp = struct.pack("!HHI12s", 0x0101, len(attr), 0x2112A442, txid) + attr
                server.sendto(resp, addr)
            finally:
                server.close()
                done.set()

        t = threading.Thread(target=serve, daemon=True)
        t.start()

        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.bind(("127.0.0.1", 0))
        client.setblocking(False)
        try:
            ip, p = stun.query(client, f"127.0.0.1:{port}", timeout=1.0)
            self.assertEqual(ip, "127.0.0.1")
            self.assertEqual(p, client.getsockname()[1])
        finally:
            client.close()
            done.wait(timeout=2)
            t.join(timeout=1)


if __name__ == "__main__":
    unittest.main()
