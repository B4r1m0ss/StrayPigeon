from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from p2p.config import Config
from p2p.session import Session


class SessionWorker(QThread):
    """Run the blocking Session in a Qt thread and expose signals."""

    state = pyqtSignal(dict)
    message = pyqtSignal(str, str)  # text, peer address
    error = pyqtSignal(str, str)  # code, detail

    def __init__(self, cfg: Config):
        super().__init__()
        self.session = Session(cfg)
        self.session.gui = True
        self.session.event_sink = self._on_event

    def _on_event(self, kind: str, payload: dict) -> None:
        if kind == "state":
            self.state.emit(dict(payload))
        elif kind == "chat":
            self.message.emit(str(payload.get("text", "")), str(payload.get("peer", "")))
        elif kind == "error":
            self.error.emit(str(payload.get("code", "E")), str(payload.get("detail", "")))

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
            self.state.emit({"state": "stopped", "code": code})
