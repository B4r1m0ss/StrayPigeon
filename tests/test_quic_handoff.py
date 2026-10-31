from __future__ import annotations

import asyncio
import secrets
import socket
import tempfile
import threading
import unittest
from pathlib import Path

try:  # optional QUIC extra
    from p2p.config import Config
    from p2p.quic_punch import PunchQuicClient, PunchQuicServer
    from p2p.quic_transport import (
        client_configuration,
        make_self_signed_cert,
        server_configuration,
    )
    from p2p.session import Session

    HAVE_QUIC = True
except Exception:  # noqa: BLE001
    HAVE_QUIC = False


def _free_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


@unittest.skipUnless(HAVE_QUIC, "aioquic not installed (pip install aioquic)")
class QuicHandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_signal_punch_then_quic_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            shared = root / "shared"
            logs = root / "logs"
            psk = secrets.token_bytes(32)
            cfg_a = Config(
                name="alice",
                peer="bob",
                port=_free_udp_port(),
                role="initiator",
                signal_mode="file",
                shared_dir=str(shared),
                log_dir=str(logs),
                quic_handoff=True,
                punch_window=8.0,
                signal_timeout=10.0,
                _psk=psk,
            )
            cfg_b = Config(
                name="bob",
                peer="alice",
                port=_free_udp_port(),
                role="responder",
                signal_mode="file",
                shared_dir=str(shared),
                log_dir=str(logs),
                quic_handoff=True,
                punch_window=8.0,
                signal_timeout=10.0,
                _psk=psk,
            )
            alice = Session(cfg_a)
            bob = Session(cfg_b)
            errors: list[BaseException] = []

            def run(session: Session) -> None:
                try:
                    session.run()
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            ta = threading.Thread(target=run, args=(alice,), daemon=True)
            tb = threading.Thread(target=run, args=(bob,), daemon=True)
            ta.start()
            tb.start()
            ta.join(30.0)
            tb.join(30.0)
            self.assertFalse(errors, errors)
            self.assertIsNotNone(alice.peer_addr)
            self.assertIsNotNone(bob.peer_addr)
            self.assertIsNotNone(alice.sock)
            self.assertIsNotNone(bob.sock)
            self.assertEqual(alice.sid, bob.sid)

            cert, key = make_self_signed_cert(root / "cert")
            received: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()
            server = PunchQuicServer(
                server_configuration(cert, key),
                alice.sid,
                "0011223344556677",
                psk,
                on_message=lambda sid, data: received.put_nowait((sid, data)),
            )
            client = PunchQuicClient(
                client_configuration(),
                alice.sid,
                "8899aabbccddeeff",
                psk,
            )
            await server.start(
                "127.0.0.1",
                0,
                candidates=[alice.peer_addr],
                timeout=10.0,
                sock=bob.sock,
            )
            observed = await client.start(
                0,
                [bob.peer_addr],
                timeout=10.0,
                sock=alice.sock,
            )
            self.assertIsNotNone(observed)
            client.protocol.send_message("quic over punched udp")
            sid, payload = await asyncio.wait_for(received.get(), 5.0)
            self.assertEqual(sid, "server")
            self.assertEqual(payload, b"quic over punched udp")
            await client.close()
            await server.close()


if __name__ == "__main__":
    unittest.main()
