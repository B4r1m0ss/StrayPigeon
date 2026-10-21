"""Reliable large-file transfer over an established QUIC chat session.

Frames are one JSON header + optional binary payload per QUIC stream. A
receiver writes chunks directly to a .part file, keeps a small JSON state for
resume, and verifies SHA-256 before atomically renaming the final file.

No whole-file buffering, no artificial rate limit, and no message persistence.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .quic_transport import QuicChatSession

DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 MiB
_MAX_CHUNK_SIZE = 4 * 1024 * 1024


def encode_frame(header: dict[str, Any], payload: bytes = b"") -> bytes:
    return json.dumps(header, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    ) + b"\n" + payload


def decode_frame(data: bytes) -> tuple[dict[str, Any], bytes]:
    header_raw, sep, payload = data.partition(b"\n")
    if not sep:
        raise ValueError("file frame has no header separator")
    header = json.loads(header_raw.decode("ascii"))
    if not isinstance(header, dict):
        raise ValueError("file frame header is not an object")
    return header, payload


def sha256_file(path: str | Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while True:
            block = fh.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def safe_name(name: str) -> str:
    candidate = Path(str(name or "file.bin")).name
    if candidate in ("", ".", ".."):
        return "file.bin"
    return candidate


@dataclass
class _Incoming:
    file_id: str
    name: str
    size: int
    sha256: str
    chunk_size: int
    chunk_count: int
    target: Path
    part: Path
    state: Path
    fh: Any
    have: set[int] = field(default_factory=set)
    received: int = 0
    end_received: bool = False


class FileReceiver:
    """Receives file frames from the remote peer and writes to target_dir."""

    def __init__(
        self,
        target_dir: str | Path,
        on_progress: Callable[[str, int, int], None] | None = None,
    ) -> None:
        self.target_dir = Path(target_dir).expanduser()
        self.target_dir.mkdir(parents=True, exist_ok=True)
        self.on_progress = on_progress
        self._incoming: dict[str, _Incoming] = {}
        self.completed: dict[str, Path] = {}
        self.errors: dict[str, str] = {}

    def on_frame(self, session: QuicChatSession, data: bytes) -> None:
        try:
            header, payload = decode_frame(data)
            kind = str(header.get("kind") or "")
            file_id = str(header.get("file_id") or "")
            if not file_id:
                raise ValueError("file frame missing file_id")
            if kind == "file_offer":
                self._offer(session, header, file_id)
            elif kind == "file_chunk":
                self._chunk(session, header, file_id, payload)
            elif kind == "file_end":
                self._finish(session, file_id)
            elif kind == "file_abort":
                self._abort(file_id, str(header.get("message") or "peer aborted"))
            else:
                raise ValueError(f"unknown file frame: {kind!r}")
        except Exception as exc:  # noqa: BLE001
            file_id = ""
            try:
                header, _ = decode_frame(data)
                file_id = str(header.get("file_id") or "")
            except Exception:  # noqa: BLE001
                pass
            self.errors[file_id] = f"{type(exc).__name__}: {exc}"
            try:
                session.protocol.send_bytes(
                    encode_frame(
                        {
                            "kind": "file_error",
                            "file_id": file_id,
                            "message": self.errors[file_id],
                        }
                    )
                )
            except Exception:  # noqa: BLE001
                pass

    def _offer(
        self, session: QuicChatSession, header: dict[str, Any], file_id: str
    ) -> None:
        name = safe_name(str(header.get("name") or "file.bin"))
        size = int(header["size"])
        sha256 = str(header["sha256"])
        chunk_size = int(header.get("chunk_size") or DEFAULT_CHUNK_SIZE)
        if size < 0 or chunk_size < 1 or chunk_size > _MAX_CHUNK_SIZE:
            raise ValueError("invalid file size or chunk size")
        chunk_count = int(header.get("chunk_count") or 0)
        expected_count = (size + chunk_size - 1) // chunk_size
        if chunk_count != expected_count:
            raise ValueError("chunk_count does not match size/chunk_size")
        target = self.target_dir / name
        part = target.with_name(target.name + ".part")
        state = target.with_name(target.name + ".part.json")
        have: set[int] = set()
        if part.exists() and state.exists():
            try:
                previous = json.loads(state.read_text(encoding="utf-8"))
                if (
                    previous.get("file_id") == file_id
                    and int(previous.get("size", -1)) == size
                    and str(previous.get("sha256")) == sha256
                    and int(previous.get("chunk_size", -1)) == chunk_size
                ):
                    have = {int(i) for i in previous.get("have", []) if 0 <= int(i) < chunk_count}
            except Exception:  # noqa: BLE001
                have = set()
        if not have:
            part.unlink(missing_ok=True)
            state.unlink(missing_ok=True)
        fh = part.open("r+b" if part.exists() else "w+b")
        incoming = _Incoming(
            file_id=file_id,
            name=name,
            size=size,
            sha256=sha256,
            chunk_size=chunk_size,
            chunk_count=chunk_count,
            target=target,
            part=part,
            state=state,
            fh=fh,
            have=have,
            received=len(have),
        )
        self._incoming[file_id] = incoming
        self._write_state(incoming)
        session.protocol.send_bytes(
            encode_frame(
                {
                    "kind": "file_accept",
                    "file_id": file_id,
                    "have": sorted(have),
                }
            )
        )
        if self.on_progress is not None:
            self.on_progress(file_id, incoming.received, incoming.chunk_count)

    def _chunk(
        self,
        session: QuicChatSession,
        header: dict[str, Any],
        file_id: str,
        payload: bytes,
    ) -> None:
        incoming = self._incoming.get(file_id)
        if incoming is None:
            raise ValueError("file_chunk for unknown file_id")
        index = int(header["index"])
        if index < 0 or index >= incoming.chunk_count:
            raise ValueError("chunk index out of range")
        expected = incoming.chunk_size
        if index == incoming.chunk_count - 1:
            expected = incoming.size - index * incoming.chunk_size
        if len(payload) != expected:
            raise ValueError(f"chunk {index} has {len(payload)} bytes, expected {expected}")
        if index in incoming.have:
            return
        incoming.fh.seek(index * incoming.chunk_size)
        incoming.fh.write(payload)
        incoming.fh.flush()
        incoming.have.add(index)
        incoming.received += 1
        self._write_state(incoming)
        if self.on_progress is not None:
            self.on_progress(file_id, incoming.received, incoming.chunk_count)
        if incoming.end_received and len(incoming.have) == incoming.chunk_count:
            self._finalize(session, incoming)

    def _finish(self, session: QuicChatSession, file_id: str) -> None:
        incoming = self._incoming.get(file_id)
        if incoming is None:
            raise ValueError("file_end for unknown file_id")
        incoming.end_received = True
        # QUIC does not order independent streams, so file_end may arrive
        # before the last chunk. Wait for the missing chunks instead of
        # failing spuriously.
        if len(incoming.have) != incoming.chunk_count:
            return
        self._finalize(session, incoming)

    def _finalize(self, session: QuicChatSession, incoming: _Incoming) -> None:
        file_id = incoming.file_id
        incoming.fh.close()
        if len(incoming.have) != incoming.chunk_count:
            raise ValueError("file_end before all chunks arrived")
        actual = sha256_file(incoming.part, incoming.chunk_size)
        if actual != incoming.sha256:
            incoming.part.unlink(missing_ok=True)
            incoming.state.unlink(missing_ok=True)
            self._incoming.pop(file_id, None)
            raise ValueError("SHA-256 mismatch")
        incoming.part.replace(incoming.target)
        incoming.state.unlink(missing_ok=True)
        self.completed[file_id] = incoming.target
        self._incoming.pop(file_id, None)
        session.protocol.send_bytes(
            encode_frame({"kind": "file_done", "file_id": file_id, "ok": True})
        )

    def _abort(self, file_id: str, message: str) -> None:
        incoming = self._incoming.pop(file_id, None)
        if incoming is not None:
            try:
                incoming.fh.close()
            except Exception:  # noqa: BLE001
                pass
        self.errors[file_id] = message

    def _write_state(self, incoming: _Incoming) -> None:
        incoming.state.write_text(
            json.dumps(
                {
                    "file_id": incoming.file_id,
                    "size": incoming.size,
                    "sha256": incoming.sha256,
                    "chunk_size": incoming.chunk_size,
                    "have": sorted(incoming.have),
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )


class FileSender:
    """Sends one file and waits for accept/done, resuming from receiver state."""

    def __init__(
        self,
        path: str | Path,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        file_id: str | None = None,
        on_progress: Callable[[str, int, int], None] | None = None,
    ) -> None:
        self.path = Path(path).expanduser()
        if not self.path.is_file():
            raise ValueError(f"not a file: {self.path}")
        if not 1 <= chunk_size <= _MAX_CHUNK_SIZE:
            raise ValueError("chunk_size must be 1..4 MiB")
        self.chunk_size = chunk_size
        self.file_id = file_id or secrets.token_hex(16)
        self.on_progress = on_progress
        self.size = self.path.stat().st_size
        self.chunk_count = (self.size + chunk_size - 1) // chunk_size
        self._accepted = asyncio.Event()
        self._done = asyncio.Event()
        self._have: set[int] = set()
        self._error: str | None = None

    async def start(self, session: QuicChatSession, timeout: float = 120.0) -> None:
        digest = sha256_file(self.path, self.chunk_size)
        await session.send_bytes(
            encode_frame(
                {
                    "kind": "file_offer",
                    "file_id": self.file_id,
                    "name": self.path.name,
                    "size": self.size,
                    "sha256": digest,
                    "chunk_size": self.chunk_size,
                    "chunk_count": self.chunk_count,
                }
            )
        )
        await asyncio.wait_for(self._accepted.wait(), timeout)
        if self._error:
            raise RuntimeError(self._error)
        missing = [i for i in range(self.chunk_count) if i not in self._have]
        with self.path.open("rb") as fh:
            for sent, index in enumerate(missing, 1):
                fh.seek(index * self.chunk_size)
                payload = fh.read(self.chunk_size)
                await session.send_bytes(
                    encode_frame(
                        {"kind": "file_chunk", "file_id": self.file_id, "index": index},
                        payload,
                    )
                )
                if self.on_progress is not None:
                    self.on_progress(
                        self.file_id, len(self._have) + sent, self.chunk_count
                    )
                await asyncio.sleep(0)
        await session.send_bytes(
            encode_frame({"kind": "file_end", "file_id": self.file_id})
        )
        await asyncio.wait_for(self._done.wait(), timeout)
        if self._error:
            raise RuntimeError(self._error)

    def on_frame(self, session: QuicChatSession, data: bytes) -> None:
        header, _payload = decode_frame(data)
        file_id = str(header.get("file_id") or "")
        if file_id != self.file_id:
            return
        kind = str(header.get("kind") or "")
        if kind == "file_accept":
            self._have = {int(i) for i in header.get("have", [])}
            self._accepted.set()
        elif kind == "file_done":
            self._done.set()
        elif kind == "file_error":
            self._error = str(header.get("message") or "peer file error")
            self._accepted.set()
            self._done.set()
