"""Qt bridge for the QUIC chat/file layer.

One asyncio loop lives in a QThread. Qt signals are emitted from that thread;
Qt queues them to the GUI thread automatically. The controller never touches
widgets and never persists messages.
"""

from __future__ import annotations

import asyncio
import secrets
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, QThread, pyqtSignal

try:  # optional QUIC extra
    from p2p.quic_file import FileReceiver, FileSender, decode_frame
    from p2p.quic_punch import PunchQuicClient, PunchQuicServer
    from p2p.quic_transport import (
        QuicChatSession,
        QuicSessionManager,
        client_configuration,
        make_self_signed_cert,
        server_configuration,
    )

    HAVE_QUIC = True
except Exception:  # noqa: BLE001
    HAVE_QUIC = False


class _LoopThread(QThread):
    def __init__(self, controller: "QuicController") -> None:
        super().__init__()
        self.controller = controller
        self.loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()

    def run(self) -> None:
        loop = asyncio.new_event_loop()
        self.loop = loop
        asyncio.set_event_loop(loop)
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    def wait_ready(self) -> None:
        self._ready.wait(5.0)

    def submit(self, coro):
        if self.loop is None:
            raise RuntimeError("asyncio loop is not running")
        return asyncio.run_coroutine_threadsafe(coro, self.loop)


class QuicController(QObject):
    state_changed = pyqtSignal(str, str)
    message_received = pyqtSignal(str, str)
    file_progress = pyqtSignal(str, str, int, int)
    file_finished = pyqtSignal(str, str)
    error = pyqtSignal(str, str)

    def __init__(
        self,
        cert_dir: str | Path,
        target_dir: str | Path,
        max_sessions: int = 16,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if not HAVE_QUIC:
            raise RuntimeError("aioquic is not installed")
        self.cert_dir = Path(cert_dir)
        self.target_dir = Path(target_dir)
        self.max_sessions = max(1, min(16, int(max_sessions)))
        self._thread = _LoopThread(self)
        self._thread.start()
        self._thread.wait_ready()
        self._client_manager: QuicSessionManager | None = None
        self._server_manager: QuicSessionManager | None = None
        self._receiver: FileReceiver | None = FileReceiver(
            self.target_dir, on_progress=self._on_file_progress
        )
        self._sessions: dict[str, QuicChatSession] = {}
        self._senders: dict[str, FileSender] = {}
        self._client_config = None
        self._server_config = None
        self._punch_sessions: dict[str, object] = {}

    # ---- setup / server ------------------------------------------------
    def start_server(self, host: str, port: int) -> None:
        self._thread.submit(self._async_start_server(host, port))

    async def _async_start_server(self, host: str, port: int) -> None:
        cert, key = make_self_signed_cert(self.cert_dir)
        self._server_config = server_configuration(cert, key)
        self._receiver = FileReceiver(self.target_dir, on_progress=self._on_file_progress)
        self._server_manager = QuicSessionManager(
            max_sessions=self.max_sessions,
            on_message=self._on_message,
            on_state=self._on_state,
        )
        await self._server_manager.start_server(host, port, self._server_config)
        self.state_changed.emit("", "server")

    # ---- handed-off punched socket ------------------------------------
    def start_handoff(self, session_id: str, handoff: dict) -> None:
        self._thread.submit(self._async_handoff(session_id, dict(handoff)))

    async def _async_handoff(self, session_id: str, handoff: dict) -> None:
        sock = handoff.get("socket")
        sid = str(handoff.get("sid") or "")
        psk = handoff.get("psk")
        role = str(handoff.get("role") or "initiator")
        candidates = list(handoff.get("peer_candidates") or [])
        if sock is None or not sid or not isinstance(psk, (bytes, bytearray)):
            self.error.emit(session_id, "invalid handoff")
            return
        try:
            if role == "responder":
                if self._server_config is None:
                    cert, key = make_self_signed_cert(self.cert_dir)
                    self._server_config = server_configuration(cert, key)
                punch: object = PunchQuicServer(
                    self._server_config,
                    sid,
                    secrets.token_hex(8),
                    bytes(psk),
                    on_message=lambda _sid, data: self._on_message(session_id, data),
                )
                await punch.start(  # type: ignore[attr-defined]
                    "0.0.0.0", 0, candidates=candidates, timeout=20.0, sock=sock
                )
                # The server is listening immediately, but QUIC is not
                # connected until the initiator's handshake arrives.
                await punch.wait_connected(timeout=20.0)  # type: ignore[attr-defined]
            else:
                if self._client_config is None:
                    self._client_config = client_configuration()
                punch = PunchQuicClient(
                    self._client_config,
                    sid,
                    secrets.token_hex(8),
                    bytes(psk),
                    on_message=lambda _sid, data: self._on_message(session_id, data),
                )
                await punch.start(  # type: ignore[attr-defined]
                    0, candidates, timeout=20.0, sock=sock
                )
        except Exception as exc:  # noqa: BLE001
            self.error.emit(session_id, f"handoff: {type(exc).__name__}: {exc}")
            self.state_changed.emit(session_id, "error")
            return
        self._punch_sessions[session_id] = punch
        if isinstance(punch, PunchQuicClient):
            self._sessions[session_id] = QuicChatSession(
                session_id, punch.protocol, None, self
            )
        self.state_changed.emit(session_id, "connected")

    # ---- client --------------------------------------------------------
    def connect(self, session_id: str, host: str, port: int) -> None:
        self._thread.submit(self._async_connect(session_id, host, port))

    async def _async_connect(self, session_id: str, host: str, port: int) -> None:
        if self._client_manager is None:
            self._client_manager = QuicSessionManager(
                max_sessions=self.max_sessions,
                on_message=self._on_message,
                on_state=self._on_state,
            )
        if self._client_config is None:
            self._client_config = client_configuration()
        try:
            session = await self._client_manager.connect(
                session_id, host, port, self._client_config
            )
        except Exception as exc:  # noqa: BLE001
            self.error.emit(session_id, f"{type(exc).__name__}: {exc}")
            self.state_changed.emit(session_id, "error")
            return
        self._sessions[session_id] = session
        self.state_changed.emit(session_id, "connected")

    def send(self, session_id: str, text: str) -> None:
        punch = self._punch_sessions.get(session_id)
        if punch is not None:
            if isinstance(punch, PunchQuicClient):
                self._thread.loop.call_soon_threadsafe(
                    punch.protocol.send_message, text  # type: ignore[attr-defined]
                )
            else:
                self._thread.loop.call_soon_threadsafe(
                    punch.send_message, text  # type: ignore[attr-defined]
                )
            return
        session = self._sessions.get(session_id)
        if session is None:
            self.error.emit(session_id, "not connected")
            return
        self._thread.submit(session.send(text))

    def _handoff_session(self, session_id: str) -> QuicChatSession | None:
        session = self._sessions.get(session_id)
        if session is not None:
            return session
        punch = self._punch_sessions.get(session_id)
        protocol = None
        if isinstance(punch, PunchQuicClient):
            protocol = punch.protocol
        elif isinstance(punch, PunchQuicServer):
            protocols = punch.protocols()
            if protocols:
                protocol = protocols[0]
        if protocol is None:
            return None
        session = QuicChatSession(session_id, protocol, None, self)
        self._sessions[session_id] = session
        return session

    def send_file(self, session_id: str, path: str) -> None:
        punch = self._punch_sessions.get(session_id)
        if isinstance(punch, (PunchQuicClient, PunchQuicServer)):
            session = self._handoff_session(session_id)
            if session is None:
                self.error.emit(session_id, "not connected")
                return
            self._thread.submit(self._async_send_file(session_id, session, path))
            return
        session = self._sessions.get(session_id)
        if session is None and self._server_manager is not None:
            session = self._server_manager.get_session(session_id)
        if session is None:
            self.error.emit(session_id, "not connected")
            return
        self._thread.submit(self._async_send_file(session_id, session, path))

    async def _async_send_file(
        self, session_id: str, session: QuicChatSession, path: str
    ) -> None:
        sender = FileSender(
            path,
            on_progress=lambda fid, done, total: self.file_progress.emit(
                session_id, fid, done, total
            ),
        )
        self._senders[session_id] = sender
        try:
            await sender.start(session, timeout=600.0)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(session_id, f"file: {type(exc).__name__}: {exc}")
        finally:
            self._senders.pop(session_id, None)
            self.file_finished.emit(session_id, sender.file_id)

    def disconnect(self, session_id: str) -> None:
        punch = self._punch_sessions.pop(session_id, None)
        if punch is not None:
            self._thread.submit(punch.close())  # type: ignore[attr-defined]
            self.state_changed.emit(session_id, "closed")
            return
        session = self._sessions.pop(session_id, None)
        if session is not None:
            self._thread.submit(session.close())
        self.state_changed.emit(session_id, "closed")

    # ---- events from asyncio ------------------------------------------
    def _on_message(self, session_id: str, data: bytes) -> None:
        receiver = self._receiver
        sender = self._senders.get(session_id)
        if receiver is not None or sender is not None:
            try:
                header, _ = decode_frame(data)
                kind = str(header.get("kind") or "")
            except Exception:  # noqa: BLE001
                kind = ""
            if kind.startswith("file_"):
                session = self._handoff_session(session_id)
                if session is None and self._server_manager is not None:
                    session = self._server_manager.get_session(session_id)
                if session is None:
                    self.error.emit(session_id, "file frame for unknown session")
                    return
                if kind in ("file_accept", "file_done", "file_error") and sender is not None:
                    sender.on_frame(session, data)
                elif receiver is not None:
                    receiver.on_frame(session, data)
                return
        try:
            self.message_received.emit(session_id, data.decode("utf-8"))
        except UnicodeDecodeError:
            self.error.emit(session_id, "non-UTF-8 chat payload")

    def _on_state(self, session_id: str, state: str) -> None:
        self.state_changed.emit(session_id, state)

    def _on_file_progress(self, file_id: str, done: int, total: int) -> None:
        self.file_progress.emit("", file_id, done, total)

    def stop(self) -> None:
        futures = []
        try:
            for punch in list(self._punch_sessions.values()):
                futures.append(self._thread.submit(punch.close()))  # type: ignore[attr-defined]
            self._punch_sessions.clear()
            if self._client_manager is not None:
                futures.append(self._thread.submit(self._client_manager.close()))
            if self._server_manager is not None:
                futures.append(self._thread.submit(self._server_manager.close()))
            for future in futures:
                try:
                    future.result(3.0)
                except Exception:  # noqa: BLE001
                    pass
        finally:
            loop = self._thread.loop
            if loop is not None:
                loop.call_soon_threadsafe(loop.stop)
            self._thread.wait(3000)
