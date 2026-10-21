"""Identity onboarding helpers shared by the CLI and the future setup wizard.

An identity is *only* the local mailbox account. It never contains a peer;
conversations are contacts in the vault (see :mod:`p2p.contacts`).
"""

from __future__ import annotations

from pathlib import Path

from .mailcheck import check_imap, check_smtp
from .vault import MIN_PASSPHRASE, clear_vault, save_vault

IMAP_HOSTS = {
    "126.com": "imap.126.com",
    "163.com": "imap.163.com",
    "yeah.net": "imap.yeah.net",
    "qq.com": "imap.qq.com",
    "gmail.com": "imap.gmail.com",
    "outlook.com": "outlook.office365.com",
    "hotmail.com": "outlook.office365.com",
    "yahoo.com": "imap.mail.yahoo.com",
}
SMTP_HOSTS = {
    "126.com": "smtp.126.com",
    "163.com": "smtp.163.com",
    "yeah.net": "smtp.yeah.net",
    "qq.com": "smtp.qq.com",
    "gmail.com": "smtp.gmail.com",
    "outlook.com": "smtp.office365.com",
    "hotmail.com": "smtp.office365.com",
    "yahoo.com": "smtp.mail.yahoo.com",
}


def _domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower()


def default_imap_host(email: str) -> str:
    return IMAP_HOSTS.get(_domain(email), f"imap.{_domain(email)}")


def default_smtp_host(email: str) -> str:
    return SMTP_HOSTS.get(_domain(email), f"smtp.{_domain(email)}")


def validate_email(email: str, what: str = "email") -> str:
    value = str(email).strip()
    if "@" not in value or value.startswith("@") or value.endswith("@"):
        raise ValueError(f"invalid {what}: {value!r}")
    return value


def build_identity(
    mode: str,
    my_email: str,
    auth_code: str,
    imap_host: str,
    smtp_host: str = "",
    smtp_port: int = 465,
    smtp_starttls: bool = False,
    signal_timeout: float | None = None,
    replay_window: float | None = None,
) -> dict:
    """Validate and build a v2 identity dict. No peer is stored."""
    if mode not in ("smtp", "imap"):
        raise ValueError(f"unsupported mode: {mode!r}")
    my_email = validate_email(my_email)
    auth_code = str(auth_code)
    if not auth_code:
        raise ValueError("authorization code is required")
    imap_host = str(imap_host).strip()
    if not imap_host:
        raise ValueError("IMAP host is required")
    if mode == "smtp":
        smtp_host = str(smtp_host).strip()
        if not smtp_host:
            raise ValueError("SMTP host is required")
        try:
            smtp_port = int(smtp_port)
        except (TypeError, ValueError) as exc:
            raise ValueError("SMTP port must be an integer") from exc
        if not 1 <= smtp_port <= 65535:
            raise ValueError("SMTP port must be between 1 and 65535")

    timeout_default = 600.0 if mode == "smtp" else 300.0
    signal_timeout = float(signal_timeout or timeout_default)
    replay_window = float(replay_window or timeout_default)
    if signal_timeout <= 0 or replay_window <= 0:
        raise ValueError("signal_timeout and replay_window must be positive")

    identity = {
        "my_email": my_email,
        "mode": mode,
        "imap_host": imap_host,
        "imap_password": auth_code,
        "signal_timeout": signal_timeout,
        "replay_window": replay_window,
    }
    if mode == "smtp":
        identity.update(
            {
                "smtp_host": smtp_host,
                "smtp_port": smtp_port,
                "smtp_starttls": bool(smtp_starttls),
            }
        )
    return identity


def new_vault_payload(identity: dict) -> dict:
    return {"version": 2, "identity": identity, "contacts": {}}


def check_identity(identity: dict, folder_base: str = "p2p-setup-check") -> None:
    """Verify IMAP (and SMTP in smtp mode); raises on failure."""
    mode = str(identity.get("mode") or "")
    my_email = str(identity.get("my_email") or "")
    auth_code = str(identity.get("imap_password") or "")
    imap_host = str(identity.get("imap_host") or "")
    check_imap(imap_host, my_email, auth_code, folder_base=folder_base)
    if mode == "smtp":
        check_smtp(
            str(identity.get("smtp_host") or ""),
            int(identity.get("smtp_port") or 465),
            my_email,
            auth_code,
            bool(identity.get("smtp_starttls")),
        )


def create_identity_vault(
    path: str | Path,
    passphrase: str,
    identity: dict,
    force: bool = False,
) -> Path:
    """Write a v2 identity vault; refuses to overwrite unless forced."""
    path = Path(path).expanduser()
    if path.exists() and not force:
        raise ValueError(f"vault already exists: {path} (clear it or use force)")
    if len(passphrase) < MIN_PASSPHRASE:
        raise ValueError(f"vault passphrase must be at least {MIN_PASSPHRASE} characters")
    save_vault(path, passphrase, new_vault_payload(identity))
    return path


def reset_identity_vault(
    path: str | Path, passphrase: str, identity: dict
) -> Path:
    """Clear the old vault (no backup), then create a fresh identity vault."""
    path = Path(path).expanduser()
    clear_vault(path)
    return create_identity_vault(path, passphrase, identity, force=True)
