from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

_SECRET_KEYS = ("psk", "password", "passwd", "secret", "token", "credential", "private_key")


def redact(value: Any, key: str = "") -> Any:
    """Best-effort redaction for logs. Never log raw secrets."""
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, key) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if key and any(s in key.lower() for s in _SECRET_KEYS):
        return "***"
    if isinstance(value, str) and len(value) > 200:
        return value[:200] + "..."
    return value


class JsonlLogger:
    """Append-only JSONL log with 0600 permissions.

    Chat text is NOT logged; only its length. This keeps the log useful for
    debugging without turning it into a copy of the conversation.
    """

    def __init__(self, log_dir: str | Path, name: str):
        self.path = Path(log_dir) / f"{name}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        self.fh = os.fdopen(fd, "a", encoding="utf-8")
        self.name = name

    def event(self, kind: str, **fields: Any) -> None:
        rec: dict[str, Any] = {"ts": round(time.time(), 3), "name": self.name, "event": kind}
        rec.update(redact(fields))
        self.fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        self.fh.flush()
        if kind in ("CONNECTED", "ERROR", "INFO"):
            print(f"[{self.name}] {kind} {redact(fields)}", flush=True)

    def close(self) -> None:
        try:
            self.fh.close()
        except OSError:
            pass
