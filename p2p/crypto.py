from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

try:  # optional dependency, required only for v0.2+
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

    HAVE_CRYPTO = True
except Exception:  # noqa: BLE001
    HAVE_CRYPTO = False

NONCE_LEN = 12
TAG_LEN = 16


def hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    """RFC 5869 HKDF-SHA256 (extract + expand), stdlib only."""
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    out = b""
    t = b""
    counter = 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        out += t
        counter += 1
    return out[:length]


def seal(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    """AEAD: returns nonce || ciphertext || tag. Key must be 32 bytes."""
    if not HAVE_CRYPTO:
        raise RuntimeError("AEAD requires the optional dependency: pip install cryptography")
    if len(key) != 32:
        raise ValueError("key must be 32 bytes")
    nonce = secrets.token_bytes(NONCE_LEN)
    ct = ChaCha20Poly1305(key).encrypt(nonce, plaintext, aad)
    return nonce + ct


def open_(key: bytes, blob: bytes, aad: bytes) -> bytes:
    if not HAVE_CRYPTO:
        raise RuntimeError("AEAD requires the optional dependency: pip install cryptography")
    if len(key) != 32:
        raise ValueError("key must be 32 bytes")
    if len(blob) < NONCE_LEN + TAG_LEN:
        raise ValueError("ciphertext too short")
    nonce, ct = blob[:NONCE_LEN], blob[NONCE_LEN:]
    return ChaCha20Poly1305(key).decrypt(nonce, ct, aad)


def fingerprint_sha256(der: bytes) -> str:
    return "sha256:" + base64.urlsafe_b64encode(hashlib.sha256(der).digest()).decode().rstrip("=")


def generate_psk() -> bytes:
    return os.urandom(32)


def psk_to_b64(psk: bytes) -> str:
    return base64.b64encode(psk).decode()


def psk_from_b64(text: str) -> bytes:
    raw = base64.b64decode(text, validate=True)
    if len(raw) != 32:
        raise ValueError("PSK must be 32 bytes")
    return raw
