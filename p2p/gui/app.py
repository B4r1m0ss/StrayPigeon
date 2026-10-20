from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from p2p.config import DEFAULT_STUN, Config
from p2p.contacts import create_contact, join_contact, list_contacts, migrate_vault
from p2p.runtime_env import apply_vault_env
from p2p.vault import default_vault_path, load_vault, save_vault

from .worker import SessionWorker


class VaultDialog(QDialog):
    """Unlock an existing identity vault. New identities use scripts/setup.py."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("StrayPigeon — unlock vault")
        self.resize(560, 200)
        self.vault_path: Path | None = None
        self.passphrase = ""
        self.data: dict | None = None

        self.path_edit = QLineEdit(str(default_vault_path()))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_row = QHBoxLayout()
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse)

        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_edit.returnPressed.connect(self._unlock)

        self.status = QLabel("")
        self.status.setWordWrap(True)

        unlock = QPushButton("Unlock")
        unlock.setDefault(True)
        unlock.clicked.connect(self._unlock)
        quit_btn = QPushButton("Quit")
        quit_btn.clicked.connect(self.reject)

        form = QFormLayout()
        form.addRow("Vault", path_row)
        form.addRow("Passphrase", self.pass_edit)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(quit_btn)
        buttons.addWidget(unlock)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.status)
        layout.addLayout(buttons)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Vault", str(Path.home()))
        if path:
            self.path_edit.setText(path)

    def _unlock(self) -> None:
        path = Path(self.path_edit.text()).expanduser()
        passphrase = self.pass_edit.text()
        if not path.exists():
            self.status.setText(f"No vault at {path}. Run scripts/setup.py first.")
            return
        try:
            raw = load_vault(path, passphrase)
            data = migrate_vault(raw)
            if not isinstance(data.get("identity"), dict):
                raise ValueError("vault has no identity section")
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"Unlock failed: {exc}")
            return
        self.vault_path = path
        self.passphrase = passphrase
        self.data = data
        self.accept()


class MainWindow(QWidget):
    def __init__(self, data: dict, vault_path: Path, passphrase: str) -> None:
        super().__init__()
        self.data = data
        self.vault_path = vault_path
        self.passphrase = passphrase
        self.worker: SessionWorker | None = None
        self.connected = False
        self.contact_key: str | None = None
        self.current_peer = ""
        self.setWindowTitle("StrayPigeon")
        self.resize(1050, 720)

        identity = data["identity"]
        self.identity_label = QLabel(
            f"<b>{identity.get('my_email', '')}</b><br>{identity.get('mode', '')}"
        )
        self.identity_label.setWordWrap(True)

        self.contact_list = QListWidget()
        self.contact_list.currentItemChanged.connect(self._on_contact_changed)

        self.new_btn = QPushButton("New contact")
        self.new_btn.clicked.connect(self._new_contact)
        self.join_btn = QPushButton("Join invite")
        self.join_btn.clicked.connect(self._join_contact)
        contact_buttons = QHBoxLayout()
        contact_buttons.addWidget(self.new_btn)
        contact_buttons.addWidget(self.join_btn)

        self.role = QComboBox()
        self.role.addItems(["initiator", "responder"])
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(40000)
        self.advertise = QLineEdit("127.0.0.1")
        self.stun = QLineEdit("stun.miwifi.com:3478, stun.cloudflare.com:3478")
        settings = QFormLayout()
        settings.addRow("Role", self.role)
        settings.addRow("Port", self.port)
        settings.addRow("Advertise", self.advertise)
        settings.addRow("STUN", self.stun)

        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self._connect)
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setEnabled(False)
        self.disconnect_btn.clicked.connect(self._disconnect)
        connection_buttons = QHBoxLayout()
        connection_buttons.addWidget(self.connect_btn)
        connection_buttons.addWidget(self.disconnect_btn)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.identity_label)
        left_layout.addWidget(QLabel("Contacts"))
        left_layout.addWidget(self.contact_list, 1)
        left_layout.addLayout(contact_buttons)
        left_layout.addLayout(settings)
        left_layout.addLayout(connection_buttons)
        left.setMaximumWidth(360)

        self.state_label = QLabel("idle")
        self.state_label.setWordWrap(True)
        self.chat = QPlainTextEdit()
        self.chat.setReadOnly(True)
        self.chat.setPlaceholderText("Connect to a contact to start chatting.")
        self.input = QLineEdit()
        self.input.setPlaceholderText("Type a message")
        self.input.returnPressed.connect(self._send)
        self.input.setEnabled(False)
        self.send_btn = QPushButton("Send")
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(self._send)
        message_row = QHBoxLayout()
        message_row.addWidget(self.input, 1)
        message_row.addWidget(self.send_btn)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(self.state_label)
        right_layout.addWidget(self.chat, 1)
        right_layout.addLayout(message_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([330, 720])
        layout = QHBoxLayout(self)
        layout.addWidget(splitter)

        self._refresh_contacts()

    # ---- contacts ------------------------------------------------------
    def _selected_key(self) -> str | None:
        item = self.contact_list.currentItem()
        if item is None:
            return None
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def _refresh_contacts(self, select: str | None = None) -> None:
        wanted = select or self._selected_key()
        self.contact_list.blockSignals(True)
        self.contact_list.clear()
        for key, contact in list_contacts(self.data):
            item = QListWidgetItem(
                f"{contact.get('peer_email', key)}  [{contact.get('mode', '')}]"
            )
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.contact_list.addItem(item)
        self.contact_list.blockSignals(False)
        if self.contact_list.count():
            row = 0
            if wanted:
                for i in range(self.contact_list.count()):
                    if self.contact_list.item(i).data(Qt.ItemDataRole.UserRole) == wanted:
                        row = i
                        break
            self.contact_list.setCurrentRow(row)
            self._on_contact_changed(self.contact_list.currentItem(), None)
        else:
            self.contact_key = None
            self.current_peer = ""
            self.connect_btn.setEnabled(False)
            self.state_label.setText("no contacts — create or join one")

    def _select_contact(self, key: str) -> None:
        for i in range(self.contact_list.count()):
            if self.contact_list.item(i).data(Qt.ItemDataRole.UserRole) == key:
                self.contact_list.setCurrentRow(i)
                return

    def _on_contact_changed(self, current: QListWidgetItem | None, _previous) -> None:
        if self.worker is not None:
            return
        if current is None:
            self.contact_key = None
            self.current_peer = ""
            self.connect_btn.setEnabled(False)
            return
        self.contact_key = str(current.data(Qt.ItemDataRole.UserRole) or "")
        contact = (self.data.get("contacts") or {}).get(self.contact_key, {})
        self.current_peer = str(contact.get("peer_email") or "")
        self.chat.clear()
        self.state_label.setText(f"idle — {self.current_peer}")
        self.connect_btn.setEnabled(True)

    # ---- connection ----------------------------------------------------
    def _build_config(self, key: str) -> Config:
        identity = self.data["identity"]
        contact = (self.data.get("contacts") or {}).get(key)
        if not isinstance(contact, dict):
            raise ValueError(f"contact not found: {key}")
        signal = apply_vault_env(self.data, key)
        stun = tuple(s.strip() for s in self.stun.text().split(",") if s.strip())
        if not stun:
            stun = DEFAULT_STUN
        return Config(
            name=str(identity.get("my_email") or ""),
            peer=str(contact.get("peer_email") or ("peer" if signal == "imap" else "")),
            port=int(self.port.value()),
            role=self.role.currentText(),
            signal_mode=signal,
            advertise_host=self.advertise.text().strip() or "127.0.0.1",
            stun_servers=stun,
            punch_window=300.0,
            keepalive=15.0,
            signal_timeout=float(identity.get("signal_timeout") or 600.0),
            replay_window=float(identity.get("replay_window") or 600.0),
            log_dir=str(Path.cwd() / "state" / "logs"),
        )

    def _connect(self) -> None:
        if self.worker is not None:
            return
        key = self.contact_key
        if not key:
            QMessageBox.information(self, "StrayPigeon", "Select or create a contact first.")
            return
        try:
            cfg = self._build_config(key)
            self.worker = SessionWorker(cfg)
        except BaseException as exc:  # noqa: BLE001
            self.worker = None
            QMessageBox.critical(self, "StrayPigeon", f"Cannot start: {exc}")
            return
        self.worker.state.connect(self._on_state)
        self.worker.message.connect(self._on_message)
        self.worker.error.connect(self._on_error)
        self.worker.finished.connect(self._on_finished)
        self.connected = False
        self.chat.clear()
        self._append(f"--- connecting as {self.role.currentText()} to {self.current_peer} ---")
        self.state_label.setText("connecting…")
        self._lock_controls(True)
        self.worker.start()

    def _lock_controls(self, locked: bool) -> None:
        for widget in (
            self.contact_list,
            self.new_btn,
            self.join_btn,
            self.role,
            self.port,
            self.advertise,
            self.stun,
        ):
            widget.setEnabled(not locked)
        self.connect_btn.setEnabled(not locked and bool(self.contact_key))
        self.disconnect_btn.setEnabled(locked)
        if locked:
            self.input.setEnabled(False)
            self.send_btn.setEnabled(False)

    def _on_state(self, payload: dict) -> None:
        state = str(payload.get("state") or "")
        peer = str(payload.get("peer") or "")
        if state == "connected":
            self.connected = True
            self.state_label.setText(f"connected — {peer}")
            self.input.setEnabled(True)
            self.send_btn.setEnabled(True)
            self.input.setFocus()
            self._append(f"--- connected: {peer} ---")
        elif state == "peer_ready":
            nat = payload.get("nat", "unknown")
            count = payload.get("candidates", 0)
            self.state_label.setText(f"peer ready — {peer} ({nat}, {count} candidates)")
            self._append(f"--- peer ready: {peer} ({nat}, {count} candidates) ---")
        elif state == "bound":
            self.state_label.setText("bound — collecting candidates…")
        elif state == "stopped":
            self.state_label.setText("stopped")
        else:
            self.state_label.setText(state or "…")

    def _on_message(self, text: str, peer: str) -> None:
        name = self.current_peer or peer
        self._append(f"[{name}] {text}")

    def _on_error(self, code: str, detail: str) -> None:
        self._append(f"[error] {code} {detail}".rstrip())
        self.state_label.setText(f"error — {code}")

    def _on_finished(self) -> None:
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.deleteLater()
        self.connected = False
        self.input.setEnabled(False)
        self.send_btn.setEnabled(False)
        self._lock_controls(False)
        self.disconnect_btn.setEnabled(False)
        self.state_label.setText("stopped")
        self._append("--- disconnected ---")

    def _disconnect(self) -> None:
        if self.worker is None:
            return
        self.state_label.setText("disconnecting…")
        self.disconnect_btn.setEnabled(False)
        # Do not block the GUI thread. Session.stop() interrupts the
        # candidate wait and the punch loop; finished then resets the UI.
        self.worker.stop()

    def _send(self) -> None:
        if self.worker is None or not self.connected:
            return
        text = self.input.text().strip()
        if not text:
            return
        self.worker.send(text)
        self._append(f"[me] {text}")
        self.input.clear()
        self.input.setFocus()

    def _append(self, text: str) -> None:
        self.chat.appendPlainText(text)
        bar = self.chat.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ---- contact management -------------------------------------------
    def _save(self) -> bool:
        try:
            save_vault(self.vault_path, self.passphrase, self.data)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "StrayPigeon", f"Cannot save vault: {exc}")
            return False
        return True

    def _new_contact(self) -> None:
        if self.worker is not None:
            return
        peer, ok = QInputDialog.getText(self, "New contact", "Peer email:")
        if not ok or not peer.strip():
            return
        try:
            key, _contact, code = create_contact(self.data, peer.strip())
        except ValueError as exc:
            if "already exists" in str(exc):
                answer = QMessageBox.question(
                    self, "StrayPigeon", f"{exc}\nReplace and generate a new invite?"
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                try:
                    key, _contact, code = create_contact(
                        self.data, peer.strip(), force=True
                    )
                except Exception as exc2:  # noqa: BLE001
                    QMessageBox.critical(self, "StrayPigeon", str(exc2))
                    return
            else:
                QMessageBox.critical(self, "StrayPigeon", str(exc))
                return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "StrayPigeon", str(exc))
            return
        if not self._save():
            return
        self._refresh_contacts(select=key)
        self._show_invite(code)

    def _join_contact(self) -> None:
        if self.worker is not None:
            return
        text, ok = QInputDialog.getMultiLineText(self, "Join invite", "Paste the p2p1: code:")
        if not ok or not text.strip():
            return
        try:
            key, _contact = join_contact(self.data, text.strip())
        except ValueError as exc:
            if "already exists" in str(exc):
                answer = QMessageBox.question(
                    self, "StrayPigeon", f"{exc}\nReplace the existing contact?"
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                try:
                    key, _contact = join_contact(self.data, text.strip(), force=True)
                except Exception as exc2:  # noqa: BLE001
                    QMessageBox.critical(self, "StrayPigeon", str(exc2))
                    return
            else:
                QMessageBox.critical(self, "StrayPigeon", str(exc))
                return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "StrayPigeon", str(exc))
            return
        if not self._save():
            return
        self._refresh_contacts(select=key)
        QMessageBox.information(self, "StrayPigeon", f"Contact saved: {key}")

    def _show_invite(self, code: str) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Invite code")
        dialog.resize(560, 260)
        edit = QPlainTextEdit(code)
        edit.setReadOnly(True)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(code))
        close = QPushButton("Close")
        close.clicked.connect(dialog.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(copy)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Send this out-of-band; it contains this conversation's PSK."))
        layout.addWidget(edit, 1)
        layout.addLayout(buttons)
        dialog.exec()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(2000)
        event.accept()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    app = QApplication([sys.argv[0], *args])
    app.setApplicationName("StrayPigeon")
    vault = VaultDialog()
    if vault.exec() != QDialog.DialogCode.Accepted:
        return 0
    window = MainWindow(vault.data, vault.vault_path, vault.passphrase)
    window.show()
    return app.exec()
