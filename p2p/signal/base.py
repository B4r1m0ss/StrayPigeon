"""Signaling channels.

The rest of the program only knows this interface. v0.1 ships `file`; the
IMAP/SMTP implementations follow the same contract.
"""

from __future__ import annotations

from typing import Protocol


class Channel(Protocol):
    def send(self, sid: str, seq: int, blob: bytes) -> None: ...

    def recv(self, sid: str, after: int) -> list[tuple[int, bytes]]: ...

    def close(self) -> None: ...
