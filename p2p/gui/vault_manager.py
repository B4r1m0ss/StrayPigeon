from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from p2p.vault import MIN_PASSPHRASE


class VaultManagerDialog(QDialog):
    """Choose a vault operation; the caller performs it."""

    def __init__(self, vault_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("StrayPigeon — vault")
        self.resize(520, 240)
        self.action = ""
        self.path = vault_path
        label = QLabel(f"Vault: {vault_path}")
        label.setWordWrap(True)
        change = QPushButton("Change passphrase…")
        change.clicked.connect(lambda: self._choose("change"))
        clear = QPushButton("Clear vault / set up again…")
        clear.clicked.connect(lambda: self._choose("clear"))
        lock = QPushButton("Lock vault")
        lock.clicked.connect(lambda: self._choose("lock"))
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(change)
        buttons.addWidget(clear)
        buttons.addWidget(lock)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        note = QLabel(
            "Changing the passphrase keeps the identity and contacts. "
            "Clearing deletes them; no backup is made."
        )
        note.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(label)
        layout.addWidget(note)
        layout.addStretch(1)
        layout.addLayout(buttons)

    def _choose(self, action: str) -> None:
        self.action = action
        self.accept()


class ChangePassphraseDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("StrayPigeon — change vault passphrase")
        self.resize(480, 220)
        self.old = QLineEdit()
        self.old.setEchoMode(QLineEdit.EchoMode.Password)
        self.new = QLineEdit()
        self.new.setEchoMode(QLineEdit.EchoMode.Password)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.EchoMode.Password)
        form = QFormLayout()
        form.addRow("Current passphrase", self.old)
        form.addRow("New passphrase", self.new)
        form.addRow("Repeat new", self.confirm)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Change")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)

    def accept(self) -> None:
        if not self.old.text():
            QMessageBox.warning(self, "StrayPigeon", "Current passphrase is required.")
            return
        if len(self.new.text()) < MIN_PASSPHRASE:
            QMessageBox.warning(
                self, "StrayPigeon", f"New passphrase must be at least {MIN_PASSPHRASE} characters."
            )
            return
        if self.new.text() != self.confirm.text():
            QMessageBox.warning(self, "StrayPigeon", "New passphrases do not match.")
            return
        super().accept()

    def values(self) -> tuple[str, str]:
        return self.old.text(), self.new.text()
