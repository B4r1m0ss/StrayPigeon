from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
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
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from p2p.config import DEFAULT_STUN, Config
from p2p.contacts import create_contact, join_contact, list_contacts, migrate_vault
from p2p.vault import (
    change_passphrase,
    clear_vault,
    default_vault_path,
    load_vault,
    save_vault,
)

from .setup_wizard import IdentityWizard
from .vault_manager import ChangePassphraseDialog, VaultManagerDialog
from .worker import SessionWorker


class FirewallDialog(QDialog):
    """Confirm opening one UDP port in the host firewall."""

    def __init__(self, port: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("StrayPigeon — allow UDP port")
        self.resize(500, 240)
        self.allow_public = QCheckBox("Also allow on public networks (less safe)")
        if os.name != "nt":
            self.allow_public.setVisible(False)
        if os.name == "nt":
            detail = (
                "Windows will show a UAC prompt. Only this UDP port is opened; "
                "the firewall is not disabled."
            )
        else:
            detail = (
                "StrayPigeon cannot change the system firewall without root. "
                "The next dialog shows the command to run yourself."
            )
        text = QLabel(f"Open inbound UDP {port} in the local firewall?\n\n{detail}")
        text.setWordWrap(True)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        allow = QPushButton("Continue")
        allow.setDefault(True)
        allow.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(allow)
        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addWidget(self.allow_public)
        layout.addLayout(buttons)


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
        create_btn = QPushButton("Create identity…")
        create_btn.clicked.connect(self._create_identity)
        quit_btn = QPushButton("Quit")
        quit_btn.clicked.connect(self.reject)

        form = QFormLayout()
        form.addRow("Vault", path_row)
        form.addRow("Passphrase", self.pass_edit)
        buttons = QHBoxLayout()
        buttons.addWidget(create_btn)
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
            self.status.setText(f"No vault at {path}. Create a new identity or run setup.py.")
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

    def _create_identity(self) -> None:
        path = Path(self.path_edit.text()).expanduser()
        wizard = IdentityWizard(self, path=path)
        if wizard.exec() != QDialog.DialogCode.Accepted:
            return
        if wizard.vault_path is None or wizard.data is None:
            return
        self.vault_path = wizard.vault_path
        self.passphrase = wizard.passphrase
        self.data = wizard.data
        self.accept()


class MainWindow(QWidget):
    def __init__(self, data: dict, vault_path: Path, passphrase: str) -> None:
        super().__init__()
        self.data = data
        self.vault_path = vault_path
        self.passphrase = passphrase
        self.worker: SessionWorker | None = None
        self.connected = False
        self.reopen_vault = False
        self.contact_key: str | None = None
        self.current_peer = ""
        self.setWindowTitle("StrayPigeon")
        self.resize(1050, 720)

        identity = data["identity"]
        self.identity_label = QLabel(
            f"<b>{identity.get('my_email', '')}</b><br>{identity.get('mode', '')}"
        )
        self.identity_label.setWordWrap(True)
        self.vault_btn = QPushButton("Vault…")
        self.vault_btn.setToolTip("Change passphrase, clear the vault, or lock it")
        self.vault_btn.clicked.connect(self._manage_vault)
        identity_row = QHBoxLayout()
        identity_row.addWidget(self.identity_label, 1)
        identity_row.addWidget(self.vault_btn)

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
        self.firewall_btn = QPushButton("Allow UDP")
        self.firewall_btn.setToolTip("Open the local firewall for the UDP port above")
        self.firewall_btn.clicked.connect(self._allow_firewall)
        port_row = QHBoxLayout()
        port_row.addWidget(self.port, 1)
        port_row.addWidget(self.firewall_btn)
        settings = QFormLayout()
        settings.addRow("Role", self.role)
        settings.addRow("Port", port_row)
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
        left_layout.addLayout(identity_row)
        left_layout.addWidget(QLabel("Contacts"))
        left_layout.addWidget(self.contact_list, 1)
        left_layout.addLayout(contact_buttons)
        left_layout.addLayout(settings)
        left_layout.addLayout(connection_buttons)
        left.setMaximumWidth(360)

        self.state_label = QLabel("idle")
        self.state_label.setWordWrap(True)
        self.punch_bar = QProgressBar()
        self.punch_bar.setRange(0, 300)
        self.punch_bar.setValue(0)
        self.punch_bar.setTextVisible(True)
        self.punch_bar.setVisible(False)
        self.punch_timer = QTimer(self)
        self.punch_timer.setInterval(1000)
        self.punch_timer.timeout.connect(self._tick_punch)
        self._punch_start = 0.0
        self._punch_window = 300.0
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
        right_layout.addWidget(self.punch_bar)
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
        self.role.setCurrentText(str(contact.get("role") or "initiator"))
        self.port.setValue(int(contact.get("local_port") or 40000))
        self.chat.clear()
        self.state_label.setText(f"idle — {self.current_peer}")
        self.connect_btn.setEnabled(True)

    # ---- connection ----------------------------------------------------
    def _build_config(self, key: str) -> Config:
        contact = (self.data.get("contacts") or {}).get(key)
        if not isinstance(contact, dict):
            raise ValueError(f"contact not found: {key}")
        stun = tuple(s.strip() for s in self.stun.text().split(",") if s.strip())
        if not stun:
            stun = DEFAULT_STUN
        role = self.role.currentText() or str(contact.get("role") or "initiator")
        port = int(self.port.value())
        # Persist the per-contact choices so the next Connect is one click.
        contact["role"] = role
        contact["local_port"] = port
        return Config.from_vault(
            self.data,
            key,
            port=port,
            role=role,
            advertise_host=self.advertise.text().strip() or "127.0.0.1",
            stun_servers=stun,
            log_dir=str(Path.cwd() / "state" / "logs"),
            punch_window=300.0,
            keepalive=15.0,
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
            if not self._save():
                return
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
            self.firewall_btn,
            self.vault_btn,
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
        if state == "punching":
            window = float(payload.get("window") or 300.0)
            self.state_label.setText(f"punching — {peer}（最多 {window:.0f}s）")
            self._append(f"--- punching UDP to {peer} ... ---")
            self._start_punch_progress(window)
        elif state == "connected":
            self._stop_punch_progress()
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
            self._stop_punch_progress()
            self.state_label.setText("stopped")
        else:
            self.state_label.setText(state or "…")

    def _start_punch_progress(self, window: float) -> None:
        self._punch_start = time.monotonic()
        self._punch_window = max(1.0, float(window))
        self.punch_bar.setRange(0, int(self._punch_window))
        self.punch_bar.setValue(0)
        self.punch_bar.setFormat("punching… %p%")
        self.punch_bar.setVisible(True)
        self.punch_timer.start()

    def _tick_punch(self) -> None:
        elapsed = time.monotonic() - self._punch_start
        self.punch_bar.setValue(min(int(elapsed), int(self._punch_window)))
        if elapsed >= self._punch_window:
            self.punch_timer.stop()

    def _stop_punch_progress(self) -> None:
        self.punch_timer.stop()
        self.punch_bar.setVisible(False)
        self.punch_bar.setValue(0)

    def _on_message(self, text: str, peer: str) -> None:
        name = self.current_peer or peer
        self._append(f"[{name}] {text}")

    def _on_error(self, code: str, detail: str) -> None:
        self._stop_punch_progress()
        self._append(f"[error] {code} {detail}".rstrip())
        self.state_label.setText(f"error — {code}")

    def _on_finished(self) -> None:
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.deleteLater()
        self.connected = False
        self._stop_punch_progress()
        self.input.setEnabled(False)
        self.send_btn.setEnabled(False)
        self._lock_controls(False)
        self.disconnect_btn.setEnabled(False)
        self.state_label.setText("stopped")
        self._append("--- disconnected ---")

    def _disconnect(self) -> None:
        if self.worker is None:
            return
        self._stop_punch_progress()
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

    # ---- vault management ---------------------------------------------
    def _manage_vault(self) -> None:
        if self.worker is not None:
            QMessageBox.information(
                self, "StrayPigeon", "Disconnect before managing the vault."
            )
            return
        dialog = VaultManagerDialog(self.vault_path, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.action == "change":
            self._change_passphrase()
        elif dialog.action == "clear":
            self._clear_vault()
        elif dialog.action == "lock":
            self.reopen_vault = True
            self.close()

    def _change_passphrase(self) -> None:
        dialog = ChangePassphraseDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        old, new = dialog.values()
        try:
            change_passphrase(self.vault_path, old, new)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "StrayPigeon", f"Cannot change passphrase: {exc}")
            return
        self.passphrase = new
        QMessageBox.information(
            self,
            "StrayPigeon",
            "Vault passphrase changed. The old passphrase no longer works.",
        )

    def _clear_vault(self) -> None:
        answer = QMessageBox.warning(
            self,
            "StrayPigeon",
            "Delete the local vault?\n\n"
            "This removes your identity and ALL contacts/PSKs. "
            "The peer side is not notified, and no backup is made.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            clear_vault(self.vault_path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "StrayPigeon", f"Cannot clear vault: {exc}")
            return
        self.reopen_vault = True
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.worker is not None:
            self.worker.stop()
            self.worker.wait(2000)
        event.accept()

    # ---- firewall helper ----------------------------------------------
    def _allow_firewall(self) -> None:
        port = int(self.port.value())
        dialog = FirewallDialog(port, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if os.name == "nt":
            self._run_windows_firewall(port, dialog.allow_public.isChecked())
        else:
            self._show_linux_firewall(port)

    def _firewall_script(self) -> Path:
        if getattr(sys, "frozen", False):
            base = Path(getattr(sys, "_MEIPASS", Path.cwd()))
        else:
            base = Path(__file__).resolve().parents[2]
        return base / "scripts" / "allow_firewall.ps1"

    def _run_windows_firewall(self, port: int, allow_public: bool) -> None:
        script = self._firewall_script()
        if not script.exists():
            QMessageBox.critical(
                self, "StrayPigeon", f"Firewall helper not found:\n{script}"
            )
            return
        profile = "Any" if allow_public else "Private,Domain"
        args = (
            f'-NoProfile -ExecutionPolicy Bypass -File "{script}" '
            f'-Port {port} -Profile "{profile}"'
        )
        try:
            import ctypes

            rc = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", "powershell.exe", args, None, 1
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "StrayPigeon", f"Cannot start UAC helper: {exc}")
            return
        if rc <= 32:
            QMessageBox.critical(self, "StrayPigeon", "Could not start the elevated helper.")
            return
        QMessageBox.information(
            self,
            "StrayPigeon",
            "UAC prompt opened. Approve it to allow UDP port "
            f"{port} ({profile}).",
        )

    def _show_linux_firewall(self, port: int) -> None:
        if shutil.which("ufw"):
            command = f"sudo ufw allow {port}/udp"
        elif shutil.which("firewall-cmd"):
            command = (
                f"sudo firewall-cmd --add-port={port}/udp --permanent && "
                "sudo firewall-cmd --reload"
            )
        elif shutil.which("nft"):
            command = (
                f"sudo nft add rule inet filter input udp dport {port} accept  "
                "# adjust table/chain if needed"
            )
        else:
            command = (
                f"# No ufw/firewall-cmd/nft found.\n"
                f"# Allow inbound UDP {port} in your firewall."
            )
        dialog = QDialog(self)
        dialog.setWindowTitle("StrayPigeon — firewall command")
        dialog.resize(620, 180)
        edit = QPlainTextEdit(command)
        edit.setReadOnly(True)
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(command))
        close = QPushButton("Close")
        close.clicked.connect(dialog.accept)
        buttons = QHBoxLayout()
        buttons.addWidget(copy)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Run this in a terminal (root required):"))
        layout.addWidget(edit, 1)
        layout.addLayout(buttons)
        dialog.exec()

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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    app = QApplication([sys.argv[0], *args])
    app.setApplicationName("StrayPigeon")
    while True:
        vault = VaultDialog()
        if vault.exec() != QDialog.DialogCode.Accepted:
            return 0
        window = MainWindow(vault.data, vault.vault_path, vault.passphrase)
        window.show()
        app.exec()
        if not window.reopen_vault:
            return 0
