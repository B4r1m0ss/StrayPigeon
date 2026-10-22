"""Single GUI for StrayPigeon.

The multi-session QUIC window is the product UI; this subclass adds the
identity/vault/contact/firewall shell which used to live in the old
single-session MainWindow.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from p2p.contacts import create_contact, join_contact
from p2p.vault import change_passphrase, clear_vault, save_vault

from .multi_session import MultiSessionWindow
from .vault_manager import ChangePassphraseDialog, VaultManagerDialog


class FirewallDialog(QDialog):
    def __init__(self, port: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("StrayPigeon — allow UDP port")
        self.resize(500, 240)
        self.allow_public = QCheckBox("Also allow on public networks (less safe)")
        if os.name != "nt":
            self.allow_public.setVisible(False)
        detail = (
            "Windows will show a UAC prompt. Only this UDP port is opened; "
            "the firewall is not disabled."
            if os.name == "nt"
            else "StrayPigeon cannot change the system firewall without root; "
            "the next dialog shows the command to run yourself."
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


class SingleWindow(MultiSessionWindow):
    def __init__(self, data, vault_path, passphrase, **kwargs) -> None:
        super().__init__(data, vault_path, passphrase, **kwargs)
        self.reopen_vault = False
        identity = data.get("identity") or {}
        self.identity_label = QLabel(
            f"<b>{identity.get('my_email', '')}</b><br>{identity.get('mode', '')}"
        )
        self.identity_label.setWordWrap(True)
        self.vault_btn = QPushButton("Vault…")
        self.vault_btn.clicked.connect(self._manage_vault)
        identity_row = QHBoxLayout()
        identity_row.addWidget(self.identity_label, 1)
        identity_row.addWidget(self.vault_btn)
        self.left_layout.insertLayout(0, identity_row)

        self.new_btn = QPushButton("New contact")
        self.new_btn.clicked.connect(self._new_contact)
        self.join_btn = QPushButton("Join invite")
        self.join_btn.clicked.connect(self._join_contact)
        contact_buttons = QHBoxLayout()
        contact_buttons.addWidget(self.new_btn)
        contact_buttons.addWidget(self.join_btn)
        self.left_layout.insertLayout(3, contact_buttons)

        self.firewall_btn = QPushButton("Allow UDP")
        self.firewall_btn.clicked.connect(self._allow_firewall)
        self.left_layout.insertWidget(4, self.firewall_btn)

    # ---- contact management -------------------------------------------
    def _save(self) -> bool:
        try:
            save_vault(self.vault_path, self.passphrase, self.data)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "StrayPigeon", f"Cannot save vault: {exc}")
            return False
        return True

    def _new_contact(self) -> None:
        if self._workers:
            return
        peer, ok = QInputDialog.getText(self, "New contact", "Peer email:")
        if not ok or not peer.strip():
            return
        try:
            _key, _contact, code = create_contact(self.data, peer.strip())
        except ValueError as exc:
            if "already exists" in str(exc):
                answer = QMessageBox.question(
                    self, "StrayPigeon", f"{exc}\nReplace and generate a new invite?"
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                try:
                    _key, _contact, code = create_contact(
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
        self._refresh_contacts()
        self._show_invite(code)

    def _join_contact(self) -> None:
        if self._workers:
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "Join invite", "Paste the p2p1: code:"
        )
        if not ok or not text.strip():
            return
        try:
            _key, _contact = join_contact(self.data, text.strip())
        except ValueError as exc:
            if "already exists" in str(exc):
                answer = QMessageBox.question(
                    self, "StrayPigeon", f"{exc}\nReplace the existing contact?"
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                try:
                    _key, _contact = join_contact(self.data, text.strip(), force=True)
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
        self._refresh_contacts()
        QMessageBox.information(self, "StrayPigeon", "Contact saved.")

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
        layout.addWidget(
            QLabel("Send this out-of-band; it contains this conversation's PSK.")
        )
        layout.addWidget(edit, 1)
        layout.addLayout(buttons)
        dialog.exec()

    # ---- vault ---------------------------------------------------------
    def _manage_vault(self) -> None:
        if self._workers or self._connected:
            QMessageBox.information(self, "StrayPigeon", "Disconnect first.")
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
        QMessageBox.information(self, "StrayPigeon", "Vault passphrase changed.")

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

    # ---- firewall ------------------------------------------------------
    def _allow_firewall(self) -> None:
        port = int(self.local_port.value())
        dialog = FirewallDialog(port, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if os.name == "nt":
            self._run_windows_firewall(port, dialog.allow_public.isChecked())
        else:
            self._show_linux_firewall(port)

    def _run_windows_firewall(self, port: int, allow_public: bool) -> None:
        script = Path(__file__).resolve().parents[2] / "scripts" / "allow_firewall.ps1"
        if not script.exists():
            QMessageBox.critical(self, "StrayPigeon", f"Firewall helper not found:\n{script}")
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
            self, "StrayPigeon", f"UAC prompt opened for UDP {port} ({profile})."
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
            command = f"# No ufw/firewall-cmd/nft found.\n# Allow inbound UDP {port}."
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
