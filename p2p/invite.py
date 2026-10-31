"""Invite-code encoding/decoding for a single conversation.

The invite is per contact, not per identity: it contains the two mailbox
addresses (SMTP) or the shared mailbox/folder (IMAP) plus this conversation's
PSK. It is normally created by ``scripts/contact.py new`` and joined by
``scripts/contact.py join``.
"""

from __future__ import annotations

import base64
import hashlib
import json


def parse_invite(text: str) -> dict:
    raw = ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("p2p1:"):
            raw = line[5:].strip()
            break
    if not raw:
        raw = "".join(text.split())
    if not raw:
        raise ValueError("invalid invite: empty code")
    pad = "=" * ((8 - len(raw) % 8) % 8)
    try:
        data = json.loads(base64.b32decode(raw + pad, casefold=True))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"invalid invite: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("invalid invite: not an object")
    declared = data.pop("sum", None)
    if declared:
        expected = hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:12]
        if str(declared) != expected:
            raise ValueError("invite checksum mismatch (code copied wrong)")
    if int(data.get("v", 0)) != 1:
        raise ValueError("unsupported invite version")
    mode = data.get("mode")
    if mode == "smtp":
        for key in ("my_email", "peer_email", "psk_b64"):
            if not data.get(key):
                raise ValueError(f"invite missing {key}")
    elif mode == "imap":
        for key in ("imap_host", "mailbox", "folder", "psk_b64"):
            if not data.get(key):
                raise ValueError(f"invite missing {key}")
    else:
        raise ValueError(f"unsupported invite mode: {mode!r}")
    return data


def encode_invite(payload: dict) -> str:
    body = {k: v for k, v in payload.items() if k != "sum"}
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    doc = dict(body)
    doc["sum"] = digest
    code = (
        base64.b32encode(
            json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    return "p2p1:" + code
