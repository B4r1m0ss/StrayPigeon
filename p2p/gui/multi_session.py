from __future__ import annotations

import base64
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from p2p.config import Config
from p2p.signal.mail_router import ImapMailbox, MailRouter, RoutedSignal

from .quic_controller import QuicController
from .worker import SessionWorker


class ChatTab(QWidget):
    def __init__(self, contact_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.contact_id = contact_id
        self.state = QLabel("idle")
        self.state.setStyleSheet("font-weight: 600; padding: 2px 0;")
        self.conn_progress = QProgressBar()
        self.conn_progress.setRange(0, 0)
        self.conn_progress.setTextVisible(False)
        self.conn_progress.setVisible(False)
        self.chat = QPlainTextEdit()
        self.chat.setReadOnly(True)
        self.chat.setPlaceholderText("No messages yet.")
        self.input = QLineEdit()
        self.input.setPlaceholderText("Type a message")
        self.send_btn = QPushButton("Send")
        self.file_btn = QPushButton("Send file…")
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        row.addWidget(self.send_btn)
        row.addWidget(self.file_btn)
        layout = QVBoxLayout(self)
        layout.addWidget(self.state)
        layout.addWidget(self.conn_progress)
        layout.addWidget(self.chat, 1)
        layout.addWidget(self.progress)
        layout.addLayout(row)

    def append(self, text: str) -> None:
        self.chat.appendPlainText(text)
        bar = self.chat.verticalScrollBar()
        bar.setValue(bar.maximum())

    def set_state(self, text: str) -> None:
        self.state.setText(text)

    def set_connecting(self, active: bool) -> None:
        self.conn_progress.setVisible(active)

    def set_progress(self, file_id: str, done: int, total: int) -> None:
        self.progress.setVisible(True)
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self.progress.setFormat(f"{Path(file_id).name if file_id else 'file'} %p%")


class _RouterThread(QThread):
    def __init__(self, router: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.router = router
        self._stop = False

    def run(self) -> None:
        while not self._stop:
            try:
                self.router.poll_once()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
            for _ in range(20):
                if self._stop:
                    return
                self.msleep(100)

    def stop(self) -> None:
        self._stop = True
        self.wait(3000)


class MultiSessionWindow(QWidget):
    """Contact list + per-contact chat tabs on top of :class:`QuicController`."""

    routed_signal = pyqtSignal(object)

    def __init__(
        self,
        data: dict,
        vault_path: str | Path,
        passphrase: str,
        controller: object | None = None,
        mail_router: object | None = None,
        auto_connect: bool = True,
        worker_factory: object | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.data = data
        self.vault_path = Path(vault_path)
        self.passphrase = passphrase
        self.auto_connect = auto_connect
        self.mail_router = mail_router
        self.worker_factory = worker_factory
        self._workers: dict[str, object] = {}
        self._handoff_keys: set[str] = set()
        self._router_thread: _RouterThread | None = None
        self._connected: set[str] = set()
        self.setWindowTitle("StrayPigeon — multi-session")
        self.resize(1100, 760)
        self._tabs: dict[str, ChatTab] = {}

        self.contact_list = QListWidget()
        self.contact_list.currentItemChanged.connect(self._on_contact_changed)

        self.tab_widget = QTabWidget()

        self.max_sessions = QSpinBox()
        self.max_sessions.setRange(1, 16)
        self.max_sessions.setValue(16)
        self.max_sessions.setToolTip("Maximum concurrent sessions (1–16)")
        self.local_port = QSpinBox()
        self.local_port.setRange(1, 65535)
        self.local_port.setValue(40000)
        self.peer_host = QLineEdit("127.0.0.1")
        self.peer_port = QSpinBox()
        self.peer_port.setRange(1, 65535)
        self.peer_port.setValue(40000)
        self.connect_btn = QPushButton("Connect")
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setEnabled(False)

        settings = QFormLayout()
        settings.addRow("Max sessions", self.max_sessions)
        settings.addRow("Local UDP port", self.local_port)
        settings.addRow("Peer host", self.peer_host)
        settings.addRow("Peer port", self.peer_port)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Contacts"))
        left_layout.addWidget(self.contact_list, 1)
        left_layout.addLayout(settings)
        left_layout.addWidget(self.connect_btn)
        left_layout.addWidget(self.disconnect_btn)
        self.status_label = QLabel("Ready")
        self.status_label.setWordWrap(True)
        left_layout.addWidget(self.status_label)
        left.setMaximumWidth(330)
        self.left_layout = left_layout

        layout = QHBoxLayout(self)
        layout.addWidget(left)
        layout.addWidget(self.tab_widget, 1)

        self.controller = controller
        if self.controller is None:
            self.controller = QuicController(
                cert_dir=self.vault_path.parent / "quic",
                target_dir=Path.cwd() / "state" / "received",
                max_sessions=int(self.max_sessions.value()),
            )
        self.controller.state_changed.connect(self._on_state)
        self.controller.message_received.connect(self._on_message)
        self.controller.file_progress.connect(self._on_file_progress)
        self.controller.error.connect(self._on_error)
        self.connect_btn.clicked.connect(self._connect)
        self.disconnect_btn.clicked.connect(self._disconnect)
        self.max_sessions.valueChanged.connect(self._on_max_changed)
        self.routed_signal.connect(self._on_routed_signal)
        self._refresh_contacts()
        self._bind_router()

    # ---- UI ------------------------------------------------------------
    def _refresh_contacts(self) -> None:
        self.contact_list.clear()
        self.tab_widget.clear()
        self._tabs.clear()
        contacts = self.data.get("contacts") or {}
        for key in sorted(contacts):
            contact = contacts[key]
            item = QListWidgetItem(f"{key} [{contact.get('mode', '')}]")
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.contact_list.addItem(item)
            tab = ChatTab(key)
            self._tabs[key] = tab
            self.tab_widget.addTab(tab, key)
            tab.send_btn.clicked.connect(lambda _=False, k=key: self._send(k))
            tab.input.returnPressed.connect(lambda k=key: self._send(k))
            tab.file_btn.clicked.connect(lambda _=False, k=key: self._pick_file(k))
        if self.contact_list.count():
            self.contact_list.setCurrentRow(0)
        else:
            self.connect_btn.setEnabled(False)

    def _current_key(self) -> str | None:
        item = self.contact_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _on_contact_changed(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            self.connect_btn.setEnabled(False)
            return
        key = str(current.data(Qt.ItemDataRole.UserRole) or "")
        self.tab_widget.setCurrentWidget(self._tabs[key])
        contact = (self.data.get("contacts") or {}).get(key, {})
        self.peer_host.setText(str(contact.get("peer_host") or "127.0.0.1"))
        self.peer_port.setValue(int(contact.get("peer_port") or 40000))
        self.connect_btn.setEnabled(True)

    def _on_max_changed(self, value: int) -> None:
        if hasattr(self.controller, "max_sessions"):
            self.controller.max_sessions = int(value)

    # ---- mail signal routing ------------------------------------------
    def _bind_router(self) -> None:
        if self.mail_router is None:
            identity = self.data.get("identity") or {}
            host = str(identity.get("imap_host") or "")
            user = str(identity.get("my_email") or "")
            password = str(identity.get("imap_password") or "")
            if host and user and password:
                try:
                    self.mail_router = MailRouter(
                        ImapMailbox(host, user, password, "INBOX")
                    )
                except Exception:  # noqa: BLE001
                    self.mail_router = None
        router = self.mail_router
        if router is None:
            return
        for key, contact in (self.data.get("contacts") or {}).items():
            try:
                psk = base64.b64decode(str(contact.get("psk_b64") or ""), validate=True)
                router.bind_contact(key, psk)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                continue
        if hasattr(router, "on_signal"):
            router.on_signal = self.routed_signal.emit  # type: ignore[attr-defined]
        if hasattr(router, "poll_once"):
            self._router_thread = _RouterThread(router, self)
            self._router_thread.start()

    def _on_routed_signal(self, routed: object) -> None:
        if not isinstance(routed, RoutedSignal):
            return
        contact_id = routed.contact_id
        contact = (self.data.get("contacts") or {}).get(contact_id)
        if not isinstance(contact, dict):
            return
        for candidate in (routed.message or {}).get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            ip = str(candidate.get("ip") or "")
            port = candidate.get("port")
            if ip and port:
                contact["peer_host"] = ip
                contact["peer_port"] = int(port)
                break
        tab = self._tabs.get(contact_id)
        if tab is not None:
            tab.set_state("peer ready")
        if self.auto_connect and contact_id not in self._connected:
            self._connect_contact(contact_id)

    # ---- actions -------------------------------------------------------
    def _connect_contact(self, key: str) -> None:
        tab = self._tabs.get(key)
        contact = (self.data.get("contacts") or {}).get(key)
        if tab is None or not isinstance(contact, dict):
            QMessageBox.information(self, "StrayPigeon", "Select a contact first.")
            return
        if key in self._workers:
            QMessageBox.information(
                self, "StrayPigeon", "This contact is already connecting. Use Disconnect first."
            )
            return
        if hasattr(self.controller, "max_sessions") and self.controller.max_sessions <= 0:
            QMessageBox.warning(self, "StrayPigeon", "Maximum sessions must be at least 1.")
            return
        role = str(contact.get("role") or "initiator")
        port = int(contact.get("local_port") or self.local_port.value())
        try:
            cfg = Config.from_vault(
                self.data,
                key,
                port=port,
                role=role,
                advertise_host=self.advertise_host_text(),
                log_dir=str(Path.cwd() / "state" / "logs"),
                quic_handoff=True,
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "StrayPigeon", f"Cannot start signaling: {exc}")
            return
        worker = self.worker_factory(cfg) if self.worker_factory is not None else SessionWorker(cfg)
        self._workers[key] = worker
        worker.state.connect(lambda payload, k=key: self._on_worker_state(k, payload))
        worker.error.connect(lambda code, detail, k=key: self._on_error(k, f"{code} {detail}"))
        worker.handoff_ready.connect(lambda handoff, k=key: self._on_handoff(k, handoff))
        worker.finished.connect(lambda k=key: self._on_worker_finished(k))
        tab.set_state("starting signaling…")
        tab.set_connecting(True)
        tab.append("[conn] starting signaling / STUN / UDP punch…")
        self.status_label.setText(f"{key}: starting signaling…")
        self.connect_btn.setText("Connecting…")
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        worker.start()

    def advertise_host_text(self) -> str:
        # The old per-contact advertise host field is not in this window yet;
        # 127.0.0.1 is the safe default for loopback/LAN tests.
        return "127.0.0.1"

    def _on_worker_state(self, key: str, payload: dict) -> None:
        state = str(payload.get("state") or "…")
        labels = {
            "bound": "local UDP socket bound",
            "peer_ready": "peer candidates received",
            "punching": "UDP punching…",
            "quic_ready": "punch done; handing off to QUIC",
        }
        label = labels.get(state, state)
        tab = self._tabs.get(key)
        if tab is not None:
            tab.set_state(label)
            if state != "stopped":
                tab.append(f"[conn] {label}")
            if state in ("bound", "peer_ready", "punching", "quic_ready"):
                tab.set_connecting(True)
        if state != "stopped":
            self.status_label.setText(f"{key}: {label}")
        if state in ("bound", "peer_ready", "punching"):
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)

    def _on_worker_finished(self, key: str) -> None:
        self._workers.pop(key, None)
        if key in self._handoff_keys:
            return
        tab = self._tabs.get(key)
        if tab is not None:
            tab.set_connecting(False)
            tab.append("[conn] signaling/punch session ended")
        self.connect_btn.setText("Connect")
        if not self._workers:
            self.connect_btn.setEnabled(self.contact_list.count() > 0)
            self.status_label.setText("Ready")
            if key not in self._connected:
                self.disconnect_btn.setEnabled(False)

    def _on_handoff(self, key: str, handoff: dict) -> None:
        self._handoff_keys.add(key)
        tab = self._tabs.get(key)
        if tab is not None:
            tab.set_state("QUIC handshake…")
            tab.set_connecting(True)
            tab.append("[conn] starting QUIC handshake on the punched socket…")
        self.status_label.setText(f"{key}: QUIC handshake…")
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self.controller.start_handoff(key, handoff)

    def _connect(self) -> None:
        key = self._current_key()
        if not key:
            QMessageBox.information(self, "StrayPigeon", "Select a contact first.")
            return
        self._connect_contact(key)

    def _send(self, key: str) -> None:
        tab = self._tabs.get(key)
        if tab is None:
            return
        text = tab.input.text().strip()
        if not text:
            return
        self.controller.send(key, text)
        tab.append(f"[me] {text}")
        tab.input.clear()

    def _pick_file(self, key: str) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Send file")
        if not path:
            return
        tab = self._tabs[key]
        tab.append(f"[file] sending {Path(path).name}")
        tab.set_progress(Path(path).name, 0, 1)
        self.controller.send_file(key, path)

    def _disconnect(self) -> None:
        key = self._current_key()
        if not key:
            return
        worker = self._workers.pop(key, None)
        if worker is not None:
            try:
                worker.stop()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
            # Give the blocking Session a moment to leave punch/signaling;
            # the tab is released regardless, so Vault is reachable again.
            if hasattr(worker, "wait"):
                try:
                    worker.wait(3000)  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass
        self.controller.disconnect(key)
        self._connected.discard(key)
        self._handoff_keys.discard(key)
        tab = self._tabs.get(key)
        if tab is not None:
            tab.set_state("disconnected")
            tab.set_connecting(False)
        self.disconnect_btn.setEnabled(False)
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("Connect")
        self.status_label.setText(f"{key}: disconnected")

    # ---- controller signals -------------------------------------------
    def _tab_for(self, session_id: str) -> ChatTab | None:
        return self._tabs.get(session_id) or self.tab_widget.currentWidget()

    def _on_state(self, session_id: str, state: str) -> None:
        if state == "connected":
            self._connected.add(session_id)
            self._handoff_keys.discard(session_id)
            self.connect_btn.setEnabled(False)
            self.disconnect_btn.setEnabled(True)
            self.connect_btn.setText("Connect")
            self.status_label.setText(f"{session_id}: connected")
        elif state in ("closed", "error"):
            self._connected.discard(session_id)
            self._handoff_keys.discard(session_id)
            self.connect_btn.setEnabled(self.contact_list.count() > 0)
            self.disconnect_btn.setEnabled(False)
            self.connect_btn.setText("Connect")
            self.status_label.setText(f"{session_id}: {state}")
        tab = self._tab_for(session_id)
        if tab is not None:
            tab.set_state(state)
            tab.set_connecting(False)
            if state in ("connected", "closed", "error"):
                tab.append(f"[conn] {state}")

    def _on_message(self, session_id: str, text: str) -> None:
        tab = self._tab_for(session_id)
        if tab is not None:
            tab.append(f"[peer] {text}")

    def _on_file_progress(
        self, session_id: str, file_id: str, done: int, total: int
    ) -> None:
        tab = self._tab_for(session_id)
        if tab is not None:
            tab.set_progress(file_id, done, total)

    def _on_error(self, session_id: str, text: str) -> None:
        tab = self._tab_for(session_id)
        if tab is not None:
            tab.append(f"[error] {text}")
            tab.set_state("error")
            tab.set_connecting(False)
        self.connect_btn.setText("Connect")
        self.status_label.setText(f"{session_id}: error")
        QMessageBox.warning(self, "StrayPigeon", text)

    def closeEvent(self, event) -> None:  # noqa: N802
        for worker in list(self._workers.values()):
            try:
                worker.stop()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
        self._workers.clear()
        if self._router_thread is not None:
            self._router_thread.stop()
            self._router_thread = None
        try:
            self.controller.stop()
        except Exception:  # noqa: BLE001
            pass
        event.accept()
