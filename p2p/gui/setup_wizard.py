from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from p2p.onboarding import (
    build_identity,
    check_identity,
    create_identity_vault,
    default_imap_host,
    default_smtp_host,
)
from p2p.vault import MIN_PASSPHRASE, default_vault_path, load_vault


class _CheckWorker(QThread):
    done = pyqtSignal(bool, str)

    def __init__(self, identity: dict):
        super().__init__()
        self.identity = identity

    def run(self) -> None:
        try:
            check_identity(self.identity)
            self.done.emit(True, "IMAP/SMTP OK")
        except Exception as exc:  # noqa: BLE001
            self.done.emit(False, f"{type(exc).__name__}: {exc}")


class _IdentityPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Your identity")
        self.setSubTitle("Only your own mailbox. The peer belongs to a contact, not setup.")
        self.mode = QComboBox()
        self.mode.addItems(["smtp", "imap"])
        self.email = QLineEdit()
        self.email.setPlaceholderText("you@example.com")
        note = QLabel(
            "SMTP: each side uses its own mailbox (recommended).\n"
            "IMAP: one shared mailbox and folder for a local test."
        )
        note.setWordWrap(True)
        form = QFormLayout(self)
        form.addRow("Mode", self.mode)
        form.addRow("Email", self.email)
        form.addRow(note)
        self.registerField("mode", self.mode, "currentText")
        self.registerField("email*", self.email)

    def isComplete(self) -> bool:
        return bool(self.email.text().strip())


class _MailPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Mailbox credentials")
        self.setSubTitle("The authorization code stays in the encrypted vault.")
        self.auth = QLineEdit()
        self.auth.setEchoMode(QLineEdit.EchoMode.Password)
        self.imap = QLineEdit()
        self.smtp = QLineEdit()
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(465)
        self.starttls = QCheckBox("SMTP STARTTLS")
        self.test_btn = QPushButton("Test connection")
        self.test_btn.clicked.connect(self._test)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.identity: dict | None = None
        self._worker: _CheckWorker | None = None

        form = QFormLayout(self)
        form.addRow("Auth code / app password", self.auth)
        form.addRow("IMAP host", self.imap)
        form.addRow("SMTP host", self.smtp)
        form.addRow("SMTP port", self.port)
        form.addRow(self.starttls)
        test_row = QHBoxLayout()
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.status, 1)
        form.addRow(test_row)
        self.registerField("auth*", self.auth)
        self.registerField("imap_host*", self.imap)
        self.registerField("smtp_host", self.smtp)
        self.registerField("smtp_port", self.port)
        self.registerField("smtp_starttls", self.starttls)
        self.smtp.textChanged.connect(self.completeChanged.emit)
        self.starttls.toggled.connect(self._on_starttls)

    def _on_starttls(self, checked: bool) -> None:
        # 465 = implicit TLS; 587 = submission with STARTTLS. Only auto-adjust
        # the common defaults; the user can still type another port.
        if checked and self.port.value() == 465:
            self.port.setValue(587)
        elif not checked and self.port.value() == 587:
            self.port.setValue(465)

    def initializePage(self) -> None:
        email = str(self.field("email") or "")
        mode = str(self.field("mode") or "smtp")
        if not self.imap.text():
            self.imap.setText(default_imap_host(email))
        if not self.smtp.text():
            self.smtp.setText(default_smtp_host(email))
        is_smtp = mode == "smtp"
        self.smtp.setEnabled(is_smtp)
        self.port.setEnabled(is_smtp)
        self.starttls.setEnabled(is_smtp)

    def isComplete(self) -> bool:
        if not self.auth.text().strip() or not self.imap.text().strip():
            return False
        if str(self.field("mode") or "smtp") == "smtp" and not self.smtp.text().strip():
            return False
        return True

    def _build(self) -> dict:
        return build_identity(
            mode=str(self.field("mode") or "smtp"),
            my_email=str(self.field("email") or ""),
            auth_code=self.auth.text(),
            imap_host=self.imap.text(),
            smtp_host=self.smtp.text(),
            smtp_port=self.port.value(),
            smtp_starttls=self.starttls.isChecked(),
        )

    def validatePage(self) -> bool:
        try:
            self.identity = self._build()
        except ValueError as exc:
            QMessageBox.warning(self, "StrayPigeon", str(exc))
            return False
        return True

    def _test(self) -> None:
        if self._worker is not None:
            return
        try:
            identity = self._build()
        except ValueError as exc:
            QMessageBox.warning(self, "StrayPigeon", str(exc))
            return
        self.test_btn.setEnabled(False)
        self.status.setText("testing…")
        self._worker = _CheckWorker(identity)
        self._worker.done.connect(self._test_done)
        self._worker.finished.connect(self._test_finished)
        self._worker.start()

    def _test_done(self, ok: bool, message: str) -> None:
        self.status.setText(("OK: " if ok else "FAIL: ") + message)
        self.test_btn.setEnabled(True)
        # Keep self._worker alive until QThread.finished fires; dropping the
        # last reference here can abort with "QThread destroyed while running".

    def _test_finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()


class _VaultPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Vault")
        self.setSubTitle("Local encrypted storage for your mailbox credentials.")
        self.pass1 = QLineEdit()
        self.pass1.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass2 = QLineEdit()
        self.pass2.setEchoMode(QLineEdit.EchoMode.Password)
        self.path = QLineEdit("")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_row = QHBoxLayout()
        path_row.addWidget(self.path, 1)
        path_row.addWidget(browse)
        self.note = QLabel(
            "If the vault already exists it will be overwritten. "
            "All contacts and PSKs are lost. No backup is made."
        )
        self.note.setWordWrap(True)
        form = QFormLayout(self)
        form.addRow("Passphrase", self.pass1)
        form.addRow("Repeat", self.pass2)
        form.addRow("Vault path", path_row)
        form.addRow(self.note)
        self.registerField("vault_path*", self.path)
        self.pass1.textChanged.connect(self.completeChanged.emit)
        self.pass2.textChanged.connect(self.completeChanged.emit)
        self.path.textChanged.connect(self.completeChanged.emit)

    def initializePage(self) -> None:
        suggested = getattr(self.wizard(), "suggested_path", None)
        if suggested:
            self.path.setText(str(suggested))
        elif not self.path.text().strip():
            self.path.setText(str(default_vault_path()))

    def _browse(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Vault path", str(self.path.text() or default_vault_path())
        )
        if path:
            self.path.setText(path)

    def isComplete(self) -> bool:
        p1 = self.pass1.text()
        return (
            len(p1) >= MIN_PASSPHRASE
            and p1 == self.pass2.text()
            and bool(self.path.text().strip())
        )

    def validatePage(self) -> bool:
        p1 = self.pass1.text()
        if len(p1) < MIN_PASSPHRASE:
            QMessageBox.warning(self, "StrayPigeon", f"Passphrase must be at least {MIN_PASSPHRASE} characters.")
            return False
        if p1 != self.pass2.text():
            QMessageBox.warning(self, "StrayPigeon", "Passphrases do not match.")
            return False
        path = Path(self.path.text()).expanduser()
        if path.exists():
            answer = QMessageBox.question(
                self,
                "Overwrite vault",
                f"{path} already exists.\n\n"
                "Overwriting deletes the identity, all contacts and all PSKs. "
                "No backup is made. Continue?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        return True


class IdentityWizard(QWizard):
    """Create a v2 identity vault. Never asks for a peer."""

    def __init__(self, parent=None, path: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("StrayPigeon — setup")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.suggested_path = path
        self.identity: dict | None = None
        self.vault_path: Path | None = None
        self.passphrase = ""
        self.data: dict | None = None
        self.addPage(_IdentityPage())
        self.addPage(_MailPage())
        self.addPage(_VaultPage())

    def accept(self) -> None:
        if self._test_running():
            QMessageBox.information(
                self, "StrayPigeon", "The connection test is still running; please wait."
            )
            return
        mail = self.page(1)
        vault = self.page(2)
        identity = getattr(mail, "identity", None)
        if not isinstance(identity, dict):
            try:
                identity = mail._build()
            except Exception as exc:  # noqa: BLE001
                QMessageBox.critical(self, "StrayPigeon", str(exc))
                return
        path = Path(vault.path.text()).expanduser()
        passphrase = vault.pass1.text()
        try:
            create_identity_vault(path, passphrase, identity, force=True)
            data = load_vault(path, passphrase)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "StrayPigeon", f"Cannot create vault: {exc}")
            return
        self.identity = identity
        self.vault_path = path
        self.passphrase = passphrase
        self.data = data
        super().accept()

    def _test_running(self) -> bool:
        mail_page = self.page(1)
        worker = getattr(mail_page, "_worker", None)
        return worker is not None and worker.isRunning()

    def reject(self) -> None:
        if self._test_running():
            QMessageBox.information(
                self, "StrayPigeon", "The connection test is still running; please wait."
            )
            return
        super().reject()
