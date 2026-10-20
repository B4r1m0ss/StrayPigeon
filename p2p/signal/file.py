from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path


class FileChannel:
    """Shared-directory signaling, for local tests and the first demo.

    Layout:
        <root>/<self_name>/00000000000000000001.json
        <root>/<peer_name>/00000000000000000001.json

    Each file is a JSON envelope. In v0.1 the blob is plaintext JSON; in v0.2
    the same blob will carry an AEAD envelope. No secrets are written here.
    """

    def __init__(self, shared_dir: str | Path, self_name: str, peer_name: str):
        self.root = Path(shared_dir)
        self.self_name = self_name
        self.peer_name = peer_name
        self.out = self.root / self_name
        self.inp = self.root / peer_name
        self.out.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.inp.mkdir(parents=True, exist_ok=True, mode=0o700)

    def send(self, sid: str, seq: int, blob: bytes) -> None:
        rec = {
            "sid": sid,
            "seq": seq,
            "ts": time.time(),
            "blob": base64.b64encode(blob).decode("ascii"),
        }
        dst = self.out / f"{seq:020d}.json"
        tmp = self.out / f".{seq:020d}.tmp"
        tmp.write_text(json.dumps(rec), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, dst)

    def recv(self, sid: str, after: int) -> list[tuple[int, bytes]]:
        out: list[tuple[int, bytes]] = []
        for path in sorted(self.inp.glob("*.json")):
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
                seq = int(rec["seq"])
                if seq <= after:
                    continue
                if sid and rec.get("sid") not in ("", sid):
                    continue
                out.append((seq, base64.b64decode(rec["blob"])))
            except (OSError, ValueError, KeyError):
                continue
        out.sort(key=lambda item: item[0])
        return out

    def close(self) -> None:
        return None
