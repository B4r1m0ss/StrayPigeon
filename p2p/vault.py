"""Encrypted local vault for mailbox credentials and the PSK.

At rest:
- scrypt (n=2^15, r=8, p=1) derives a 256-bit key from the user passphrase;
- ChaCha20-Poly1305 encrypts the JSON payload;
- the file is written with mode 0600; the passphrase is never stored.

Why not hash the authorization code: the IMAP/SMTP login needs the original
secret, so it must be recoverable. Hashing only applies to verifiers, not to
credentials that must be replayed to a server.

In transit the authorization code is protected by TLS (IMAP 993 / SMTP 465 or
STARTTLS). The vault protects it at rest.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

AAD = b"p2p-vault-v1"
KDF_N = 2**15
KDF_R = 8
KDF_P = 1
MIN_PASSPHRASE = 8


def default_config_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "email-p2p"


def default_vault_path() -> Path:
    return default_config_dir() / "vault.json"


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text, validate=True)


def _derive(passphrase: str, salt: bytes) -> bytes:
    if len(passphrase) < MIN_PASSPHRASE:
        raise ValueError(f"vault passphrase must be at least {MIN_PASSPHRASE} characters")
    return hashlib.scrypt(
        passphrase.encode("utf-8"),
        salt=salt,
        n=KDF_N,
        r=KDF_R,
        p=KDF_P,
        dklen=32,
        maxmem=64 * 1024 * 1024,
    )


def save_vault(path: str | Path, passphrase: str, data: dict[str, Any]) -> Path:
    path = Path(path).expanduser()
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    key = _derive(passphrase, salt)
    ct = ChaCha20Poly1305(key).encrypt(
        nonce, json.dumps(data, sort_keys=True).encode("utf-8"), AAD
    )
    doc = {
        "v": 1,
        "kdf": "scrypt",
        "n": KDF_N,
        "r": KDF_R,
        "p": KDF_P,
        "salt": _b64e(salt),
        "nonce": _b64e(nonce),
        "ct": _b64e(ct),
    }
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    os.replace(tmp, path)
    if os.name == "posix":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return path


def load_vault(path: str | Path, passphrase: str) -> dict[str, Any]:
    path = Path(path).expanduser()
    doc = json.loads(path.read_text(encoding="utf-8"))
    if int(doc.get("v", 0)) != 1 or doc.get("kdf") != "scrypt":
        raise ValueError("unsupported vault format")
    if int(doc.get("n", 0)) != KDF_N or int(doc.get("r", 0)) != KDF_R or int(doc.get("p", 0)) != KDF_P:
        raise ValueError("unsupported KDF parameters")
    try:
        salt = _b64d(doc["salt"])
        nonce = _b64d(doc["nonce"])
        ct = _b64d(doc["ct"])
    except (KeyError, ValueError) as exc:
        raise ValueError("corrupted vault") from exc
    key = _derive(passphrase, salt)
    try:
        plain = ChaCha20Poly1305(key).decrypt(nonce, ct, AAD)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("wrong passphrase or corrupted vault") from exc
    return json.loads(plain)


def change_passphrase(
    path: str | Path, old_passphrase: str, new_passphrase: str
) -> Path:
    """Re-encrypt the vault under a new passphrase, keeping its payload.

    The old file is left untouched until a new temp vault has been written and
    successfully re-read with the new passphrase. No backup is kept.
    """
    path = Path(path).expanduser()
    if len(new_passphrase) < MIN_PASSPHRASE:
        raise ValueError(f"passphrase must be at least {MIN_PASSPHRASE} characters")
    data = load_vault(path, old_passphrase)
    tmp = path.with_name(path.name + ".new")
    try:
        save_vault(tmp, new_passphrase, data)
        if load_vault(tmp, new_passphrase) != data:
            raise ValueError("vault verification failed after re-encryption")
        os.replace(tmp, path)
        if os.name == "posix":
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return path


def clear_vault(path: str | Path) -> Path:
    """Delete the vault, overwriting its bytes first where possible.

    No backup is kept. On SSDs/journaling filesystems this is best-effort, not
    a guaranteed physical erase.
    """
    path = Path(path).expanduser()
    if not path.exists():
        return path
    try:
        size = path.stat().st_size
        with path.open("r+b") as fh:
            fh.write(secrets.token_bytes(size))
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        pass
    path.unlink()
    return path
