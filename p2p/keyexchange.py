"""Authenticated ephemeral key exchange.

Design:
- The 32-byte PSK (shared out-of-band) authenticates the *signaling* messages.
- Each side generates an ephemeral X25519 key pair per session.
- The public keys travel inside the PSK-encrypted OFFER/ANSWER, so a malicious
  mail server cannot substitute them.
- Both sides compute the same DH secret and derive a per-session data key with
  HKDF over (DH, sid, initiator_pub, responder_pub).
- Data frames are ChaCha20-Poly1305 with a monotonically increasing sequence.

Why this shape:
- No PKI, no online server.
- Forward secrecy for data: an ephemeral DH secret is not derivable from the
  PSK, so a later PSK leak does not decrypt past data sessions.
- The PSK is never sent over the mail channel.

This is not a full Noise handshake; it is the smallest thing that has the
properties we need and is easy to test.
"""

from __future__ import annotations

import base64
import os
import struct

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SIG_INFO = b"p2p1-signal"
DATA_INFO = b"p2p1-data"
NONCE_LEN = 12
TAG_LEN = 16
FRAME_HDR = 8


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text, validate=True)


def _hkdf(ikm: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=length, salt=salt, info=info
    ).derive(ikm)


class KeyPair:
    def __init__(self, private_key: X25519PrivateKey):
        self._private = private_key
        self.public_bytes = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )

    @classmethod
    def generate(cls) -> "KeyPair":
        return cls(X25519PrivateKey.generate())

    def public_b64(self) -> str:
        return _b64e(self.public_bytes)

    def dh(self, peer_public_b64: str) -> bytes:
        peer = X25519PublicKey.from_public_bytes(_b64d(peer_public_b64))
        return self._private.exchange(peer)

    def close(self) -> None:
        # Best effort; Python cannot guarantee zeroization of immutable ints.
        self._private = None  # type: ignore[assignment]


def signal_key(psk: bytes, sid: str) -> bytes:
    if len(psk) != 32:
        raise ValueError("PSK must be 32 bytes")
    return _hkdf(psk, sid.encode("utf-8"), SIG_INFO)


def derive_session_key(
    dh: bytes, sid: str, initiator_pub: bytes, responder_pub: bytes
) -> bytes:
    """Bind the DH secret to the session and to both public keys."""
    transcript = sid.encode("utf-8") + b"|" + initiator_pub + b"|" + responder_pub
    return _hkdf(dh, sid.encode("utf-8"), DATA_INFO + b"|" + transcript)


def seal_signal(
    psk: bytes, sid: str, seq: int, msg_type: str, plaintext: bytes
) -> dict:
    key = signal_key(psk, sid)
    nonce = os.urandom(NONCE_LEN)
    aad = f"p2p1|{sid}|{seq}|{msg_type}".encode("utf-8")
    ct = ChaCha20Poly1305(key).encrypt(nonce, plaintext, aad)
    return {
        "v": 1,
        "sid": sid,
        "seq": seq,
        "type": msg_type,
        "nonce": _b64e(nonce),
        "ct": _b64e(ct),
    }


def open_signal(psk: bytes, envelope: dict) -> bytes:
    if int(envelope.get("v", 0)) != 1:
        raise ValueError("unsupported signal envelope version")
    sid = str(envelope["sid"])
    seq = int(envelope["seq"])
    msg_type = str(envelope["type"])
    key = signal_key(psk, sid)
    nonce = _b64d(str(envelope["nonce"]))
    ct = _b64d(str(envelope["ct"]))
    aad = f"p2p1|{sid}|{seq}|{msg_type}".encode("utf-8")
    return ChaCha20Poly1305(key).decrypt(nonce, ct, aad)


def seal_frame(key: bytes, seq: int, plaintext: bytes) -> bytes:
    if len(key) != 32:
        raise ValueError("session key must be 32 bytes")
    seq_b = struct.pack("!Q", seq)
    nonce = os.urandom(NONCE_LEN)
    aad = seq_b + b"|" + DATA_INFO
    ct = ChaCha20Poly1305(key).encrypt(nonce, plaintext, aad)
    return seq_b + nonce + ct


def open_frame(key: bytes, blob: bytes, last_seq: int = 0) -> tuple[int, bytes]:
    if len(blob) < FRAME_HDR + NONCE_LEN + TAG_LEN:
        raise ValueError("frame too short")
    seq = struct.unpack("!Q", blob[:FRAME_HDR])[0]
    if seq <= last_seq:
        raise ValueError("replayed or out-of-order frame")
    nonce = blob[FRAME_HDR : FRAME_HDR + NONCE_LEN]
    ct = blob[FRAME_HDR + NONCE_LEN :]
    aad = blob[:FRAME_HDR] + b"|" + DATA_INFO
    return seq, ChaCha20Poly1305(key).decrypt(nonce, ct, aad)
