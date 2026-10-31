#!/usr/bin/env python3
"""Headless one-side runner for the Linux netns NAT testbed.

Runs the exact production Session (FileChannel signaling, no SMTP) +
QuicController handoff. The peer runs the same script with the other role.

This is an experiment harness, not part of the GUI.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QCoreApplication

from p2p.config import Config
from p2p.gui.quic_controller import QuicController
from p2p.session import Session


def _free_port() -> int:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("0.0.0.0", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", choices=("initiator", "responder"), required=True)
    ap.add_argument("--shared", required=True)
    ap.add_argument("--result", required=True)
    ap.add_argument("--port", type=int, default=40000)
    ap.add_argument("--advertise", required=True)
    ap.add_argument("--file-size", type=int, default=4 * 1024 * 1024)
    ap.add_argument("--stun", default="")
    ap.add_argument("--second-punch", action="store_true")
    ap.add_argument("--idle", type=float, default=0.0)
    ap.add_argument("--timeout", type=float, default=40.0)
    args = ap.parse_args()

    shared = Path(args.shared).resolve()
    shared.mkdir(parents=True, exist_ok=True)
    logs = shared / "logs" / args.role
    logs.mkdir(parents=True, exist_ok=True)
    psk_path = shared / "psk.bin"
    if not psk_path.exists():
        tmp = psk_path.with_name(f".psk.{os.getpid()}")
        tmp.write_bytes(secrets.token_bytes(32))
        try:
            os.replace(tmp, psk_path)
        except OSError:
            tmp.unlink(missing_ok=True)
    psk = psk_path.read_bytes()
    if len(psk) != 32:
        raise SystemExit(f"invalid psk length: {len(psk)}")
    name = "alice" if args.role == "initiator" else "bob"
    peer = "bob" if args.role == "initiator" else "alice"

    cfg = Config(
        name=name,
        peer=peer,
        port=args.port,
        role=args.role,
        signal_mode="file",
        shared_dir=str(shared),
        log_dir=str(logs),
        advertise_host=args.advertise,
        stun_servers=tuple(x for x in args.stun.split(",") if x),
        punch_window=8.0,
        signal_timeout=15.0,
        quic_handoff=True,
        _psk=psk,
    )
    started = time.time()
    events: list[tuple[str, dict]] = []
    session = Session(cfg)
    session.event_sink = lambda kind, payload: events.append((kind, dict(payload)))
    session_error: list[BaseException] = []

    def run_session() -> None:
        try:
            session.run()
        except BaseException as exc:  # noqa: BLE001
            session_error.append(exc)

    thread = threading.Thread(target=run_session, daemon=True)
    thread.start()
    thread.join(args.timeout)
    if thread.is_alive():
        session.stop()
        thread.join(3.0)
        raise SystemExit("session did not finish (signaling/punch timeout)")
    if session_error:
        raise SystemExit(f"session failed: {session_error[0]!r}")
    if not session.handoff or session.sock is None or session.sid is None:
        raise SystemExit("session did not hand off a socket")

    handoff_at = time.time()
    app = QCoreApplication.instance() or QCoreApplication([])
    controller = QuicController(
        cert_dir=shared / "quic",
        target_dir=shared / "received",
    )
    states: list[tuple[str, str]] = []
    errors: list[tuple[str, str]] = []
    controller.state_changed.connect(lambda sid, state: states.append((sid, state)))
    controller.error.connect(lambda sid, text: errors.append((sid, text)))
    controller.start_handoff(
        args.role,
        {
            "socket": session.sock,
            "peer_addr": session.peer_addr,
            "peer_candidates": [(c.ip, c.port) for c in session.peer_candidates],
            "sid": session.sid,
            "psk": session.psk,
            "role": cfg.role,
            "second_punch": args.second_punch,
        },
    )

    connected_at = None
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        app.processEvents()
        if any(state == "connected" for _, state in states):
            connected_at = time.time()
            break
        time.sleep(0.02)
    if connected_at is None:
        controller.stop()
        raise SystemExit(f"QUIC handoff timeout; errors={errors!r}; states={states!r}")

    idle_seconds = 0.0
    if args.idle > 0:
        idle_start = time.time()
        while time.time() - idle_start < args.idle:
            app.processEvents()
            time.sleep(0.05)
        idle_seconds = time.time() - idle_start

    file_result = None
    if args.role == "initiator":
        source = shared / "source.bin"
        if not source.exists() or source.stat().st_size != args.file_size:
            marker = b"STRAYPIGEON_FILE_MARKER"
            blob = marker + os.urandom(max(0, args.file_size - len(marker)))
            source.write_bytes(blob[: args.file_size])
        file_started = time.time()
        controller.send_file(args.role, str(source))
        target = shared / "received" / "source.bin"
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            app.processEvents()
            if target.exists() and target.stat().st_size == args.file_size:
                break
            time.sleep(0.02)
        file_result = {
            "seconds": time.time() - file_started,
            "bytes": source.stat().st_size,
            "received": target.exists() and target.stat().st_size == args.file_size,
        }
    else:
        target = shared / "received" / "source.bin"
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            app.processEvents()
            if target.exists() and target.stat().st_size == args.file_size:
                break
            time.sleep(0.02)
        file_result = {
            "seconds": None,
            "bytes": target.stat().st_size if target.exists() else 0,
            "received": target.exists() and target.stat().st_size == args.file_size,
        }

    result = {
        "role": args.role,
        "advertise": args.advertise,
        "port": args.port,
        "session_seconds": handoff_at - started,
        "quic_seconds": connected_at - handoff_at,
        "total_seconds": connected_at - started,
        "states": states,
        "errors": errors,
        "idle_seconds": idle_seconds,
        "file": file_result,
    }
    Path(args.result).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    controller.stop()
    return 0 if file_result.get("received") else 2


if __name__ == "__main__":
    raise SystemExit(main())
