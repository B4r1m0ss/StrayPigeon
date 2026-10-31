from __future__ import annotations

import secrets
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

try:  # optional QUIC extra
    from PyQt6.QtCore import QCoreApplication

    from p2p.config import Config
    from p2p.gui import quic_controller
    from p2p.session import Session

    QuicController = quic_controller.QuicController
    HAVE_QUIC = bool(quic_controller.HAVE_QUIC)
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
class ControllerHandoffFileTests(unittest.TestCase):
    def test_file_both_directions_over_punched_socket(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            app = QCoreApplication.instance() or QCoreApplication([])
            shared = root / "shared"
            psk = secrets.token_bytes(32)
            alice = Session(
                Config(
                    name="alice",
                    peer="bob",
                    port=_free_udp_port(),
                    role="initiator",
                    signal_mode="file",
                    shared_dir=str(shared),
                    log_dir=str(root / "logs"),
                    quic_handoff=True,
                    punch_window=8.0,
                    signal_timeout=10.0,
                    _psk=psk,
                )
            )
            bob = Session(
                Config(
                    name="bob",
                    peer="alice",
                    port=_free_udp_port(),
                    role="responder",
                    signal_mode="file",
                    shared_dir=str(shared),
                    log_dir=str(root / "logs"),
                    quic_handoff=True,
                    punch_window=8.0,
                    signal_timeout=10.0,
                    _psk=psk,
                )
            )
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
            self.assertIsNotNone(alice.sock)
            self.assertIsNotNone(bob.sock)
            self.assertIsNotNone(alice.peer_addr)
            self.assertIsNotNone(bob.peer_addr)
            self.assertEqual(alice.sid, bob.sid)

            controller = QuicController(
                cert_dir=root / "quic", target_dir=root / "received"
            )
            states: set[tuple[str, str]] = set()
            ctl_errors: list[tuple[str, str]] = []
            controller.state_changed.connect(lambda sid, state: states.add((sid, state)))
            controller.error.connect(lambda sid, text: ctl_errors.append((sid, text)))
            controller.start_handoff(
                "bob",
                {
                    "socket": bob.sock,
                    "peer_addr": bob.peer_addr,
                    "peer_candidates": [bob.peer_addr],
                    "sid": alice.sid,
                    "psk": psk,
                    "role": "responder",
                },
            )
            controller.start_handoff(
                "alice",
                {
                    "socket": alice.sock,
                    "peer_addr": alice.peer_addr,
                    "peer_candidates": [alice.peer_addr],
                    "sid": alice.sid,
                    "psk": psk,
                    "role": "initiator",
                },
            )

            deadline = time.time() + 20.0
            while time.time() < deadline:
                app.processEvents()
                if ("alice", "connected") in states and ("bob", "connected") in states:
                    break
                time.sleep(0.05)
            self.assertIn(("alice", "connected"), states)
            self.assertIn(("bob", "connected"), states)

            try:
                jobs = (
                    ("alice", "from_alice_1.bin"),
                    ("alice", "from_alice_2.bin"),
                    ("bob", "from_bob_1.bin"),
                    ("bob", "from_bob_2.bin"),
                )
                for sender, name in jobs:
                    source = root / name
                    source.write_bytes(secrets.token_bytes(300_000))
                    controller.send_file(sender, str(source))
                    target = root / "received" / name
                    deadline = time.time() + 20.0
                    while time.time() < deadline:
                        app.processEvents()
                        if target.exists() and not target.with_name(target.name + ".part").exists():
                            break
                        time.sleep(0.05)
                    self.assertTrue(target.exists(), f"{name} was not received")
                    self.assertEqual(target.read_bytes(), source.read_bytes())
                self.assertFalse(ctl_errors, ctl_errors)
            finally:
                controller.stop()


if __name__ == "__main__":
    unittest.main()
