from __future__ import annotations

import secrets
import time
from dataclasses import asdict, dataclass


@dataclass
class Candidate:
    """One reachable address, with a lease.

    A candidate is a *lease*, not a fact: the sender keeps the NAT mapping
    alive and re-advertises when it changes.
    """

    family: str
    ip: str
    port: int
    kind: str
    lease_id: str
    obtained_at: float
    expires_at: float
    mapping: str = "unknown"

    @classmethod
    def host(cls, ip: str, port: int, ttl: float = 45.0) -> "Candidate":
        now = time.time()
        return cls(
            family="ipv4",
            ip=ip,
            port=port,
            kind="host",
            lease_id=secrets.token_hex(8),
            obtained_at=now,
            expires_at=now + ttl,
        )

    def alive(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) < self.expires_at

    @classmethod
    def from_dict(cls, raw: dict) -> "Candidate":
        return cls(
            family=str(raw.get("family", "ipv4")),
            ip=str(raw["ip"]),
            port=int(raw["port"]),
            kind=str(raw.get("kind", "host")),
            lease_id=str(raw.get("lease_id", "")),
            obtained_at=float(raw.get("obtained_at", 0.0)),
            expires_at=float(raw.get("expires_at", 0.0)),
            mapping=str(raw.get("mapping", "unknown")),
        )

    def to_dict(self) -> dict:
        return asdict(self)
