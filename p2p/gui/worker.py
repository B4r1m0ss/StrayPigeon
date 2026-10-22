from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from p2p.config import Config
from p2p.session import Session


class SessionWorker(QThread):
    """Run the blocking Session in a Qt thread and expose signals."""

    state = pyqtSignal(dict)
    message = pyqtSignal(str, str)  # text, peer address
    error = pyqtSignal(str, str)  # code, detail
    handoff_ready = pyqtSignal()

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.session = Session(cfg)
        self.session.gui = True
        self.session.event_sink = self._on_event
        self._handoff_payload: dict | None = None

    def _on_event(self, kind: str, payload: dict) -> None:
        if kind == "state":
            self.state.emit(dict(payload))
        elif kind == "chat":
            self.message.emit(str(payload.get("text", "")), str(payload.get("peer", "")))
        elif kind == "error":
            self.error.emit(str(payload.get("code", "E")), str(payload.get("detail", "")))

    def take_handoff(self) -> dict | None:
        payload, self._handoff_payload = self._handoff_payload, None
        return payload

    def send(self, text: str) -> None:
        self.session.outbox.put(text)

    def stop(self) -> None:
        self.session.stop()

    def run(self) -> None:  # noqa: D401 - QThread entry point
        code = 1
        try:
            code = self.session.run()
        except BaseException as exc:  # noqa: BLE001
            self.error.emit("E_GUI", f"{type(exc).__name__}: {exc}")
        finally:
            if (
                self.session.handoff
                and self.session.sock is not None
                and self.session.peer_addr is not None
                and self.session.sid
                and self.session.psk is not None
            ):
                self._handoff_payload = {
                    "socket": self.session.sock,
                    "peer_addr": self.session.peer_addr,
                    "peer_candidates": [
                        (c.ip, c.port) for c in self.session.peer_candidates
                    ],
                    "sid": self.session.sid,
                    "psk": self.session.psk,
                    "role": self.cfg.role,
                }
                self.handoff_ready.emit()
            self.state.emit({"state": "stopped", "code": code})
