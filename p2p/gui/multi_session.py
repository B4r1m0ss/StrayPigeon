from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
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

from .quic_controller import QuicController


class ChatTab(QWidget):
    def __init__(self, contact_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.contact_id = contact_id
        self.state = QLabel("idle")
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
        layout.addWidget(self.chat, 1)
        layout.addWidget(self.progress)
        layout.addLayout(row)

    def append(self, text: str) -> None:
        self.chat.appendPlainText(text)
        bar = self.chat.verticalScrollBar()
        bar.setValue(bar.maximum())

    def set_state(self, text: str) -> None:
        self.state.setText(text)

    def set_progress(self, file_id: str, done: int, total: int) -> None:
        self.progress.setVisible(True)
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)
        self.progress.setFormat(f"{Path(file_id).name if file_id else 'file'} %p%")


class MultiSessionWindow(QWidget):
    """Contact list + per-contact chat tabs on top of :class:`QuicController`."""

    def __init__(
        self,
        data: dict,
        vault_path: str | Path,
        passphrase: str,
        controller: object | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.data = data
        self.vault_path = Path(vault_path)
        self.passphrase = passphrase
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
        left.setMaximumWidth(330)

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
        self._refresh_contacts()
        self.controller.start_server("0.0.0.0", int(self.local_port.value()))

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

    # ---- actions -------------------------------------------------------
    def _connect(self) -> None:
        key = self._current_key()
        if not key:
            return
        tab = self._tabs[key]
        if hasattr(self.controller, "max_sessions") and self.controller.max_sessions <= 0:
            QMessageBox.warning(self, "StrayPigeon", "Maximum sessions must be at least 1.")
            return
        tab.set_state("connecting…")
        self.controller.connect(key, self.peer_host.text().strip(), self.peer_port.value())
        self.disconnect_btn.setEnabled(True)

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
        self.controller.disconnect(key)
        self._tabs[key].set_state("disconnected")
        self.disconnect_btn.setEnabled(False)

    # ---- controller signals -------------------------------------------
    def _tab_for(self, session_id: str) -> ChatTab | None:
        return self._tabs.get(session_id) or self.tab_widget.currentWidget()

    def _on_state(self, session_id: str, state: str) -> None:
        tab = self._tab_for(session_id)
        if tab is not None:
            tab.set_state(state)

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

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self.controller.stop()
        except Exception:  # noqa: BLE001
            pass
        event.accept()
