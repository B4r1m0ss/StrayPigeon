from __future__ import annotations

import argparse
import base64
import os
from dataclasses import dataclass, field

DEFAULT_STUN = ("stun.l.google.com:19302", "stun.cloudflare.com:3478")


@dataclass
class Config:
    """Runtime configuration.

    SECURITY: no secret is accepted on the command line and no secret is
    hardcoded. The PSK, IMAP password, etc. are read from environment
    variables (see .env.example). This object never prints them.
    """

    name: str
    peer: str
    port: int
    role: str
    signal_mode: str = "file"
    shared_dir: str = "./state/shared"
    log_dir: str = "./state/logs"
    host: str = "0.0.0.0"
    advertise_host: str = "127.0.0.1"
    message: str | None = None
    chat_wait: float = 2.0
    chat_delay: float = 0.5
    keepalive: float = 15.0
    punch_window: float = 10.0
    punch_interval: float = 0.2
    signal_timeout: float = 60.0
    stun_servers: tuple[str, ...] = DEFAULT_STUN
    stun_timeout: float = 2.0
    replay_window: float = 120.0
    _psk: bytes | None = field(default=None, repr=False)

    @classmethod
    def from_args(cls, argv: list[str] | None = None) -> "Config":
        p = argparse.ArgumentParser(prog="p2p")
        p.add_argument("--name", required=True, help="local peer name")
        p.add_argument("--peer", required=True, help="remote peer name")
        p.add_argument("--port", type=int, required=True, help="local UDP port")
        p.add_argument("--role", choices=("initiator", "responder"), required=True)
        p.add_argument("--signal", choices=("file", "imap", "smtp"), default="file")
        p.add_argument("--shared-dir", default="./state/shared")
        p.add_argument("--log-dir", default="./state/logs")
        p.add_argument(
            "--advertise-host",
            default="127.0.0.1",
            help="address to advertise; 127.0.0.1 is correct only for same-machine tests",
        )
        p.add_argument("--message", default=None, help="send one message, wait, then exit")
        p.add_argument("--chat-wait", type=float, default=2.0)
        p.add_argument("--chat-delay", type=float, default=0.5)
        p.add_argument(
            "--keepalive",
            type=float,
            default=15.0,
            help="send a UDP keepalive every N seconds; 0 disables",
        )
        p.add_argument("--punch-window", type=float, default=10.0)
        p.add_argument("--signal-timeout", type=float, default=60.0)
        p.add_argument(
            "--stun",
            action="append",
            default=[],
            help="host:port; repeat twice for NAT mapping classification",
        )
        p.add_argument("--stun-timeout", type=float, default=2.0)
        p.add_argument("--replay-window", type=float, default=120.0)
        a = p.parse_args(argv)
        return cls(
            name=a.name,
            peer=a.peer,
            port=a.port,
            role=a.role,
            signal_mode=a.signal,
            shared_dir=a.shared_dir,
            log_dir=a.log_dir,
            advertise_host=a.advertise_host,
            message=a.message,
            chat_wait=a.chat_wait,
            chat_delay=a.chat_delay,
            keepalive=a.keepalive,
            punch_window=a.punch_window,
            signal_timeout=a.signal_timeout,
            stun_servers=tuple(a.stun) if a.stun else DEFAULT_STUN,
            stun_timeout=a.stun_timeout,
            replay_window=a.replay_window,
        )

    def psk_bytes(self) -> bytes | None:
        """PSK from the environment only. Never from source or argv."""
        if self._psk is not None:
            return self._psk
        raw_b64 = os.environ.get("P2P_PSK_B64", "").strip()
        if not raw_b64:
            return None
        try:
            raw = base64.b64decode(raw_b64, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("P2P_PSK_B64 is not valid base64") from exc
        if len(raw) != 32:
            raise ValueError("P2P_PSK_B64 must decode to exactly 32 bytes")
        self._psk = raw
        return raw

    def redacted(self) -> dict:
        """Safe to log: never includes the PSK or any password."""
        return {
            "name": self.name,
            "peer": self.peer,
            "port": self.port,
            "role": self.role,
            "signal": self.signal_mode,
            "shared_dir": self.shared_dir,
            "advertise_host": self.advertise_host,
            "crypto": self.psk_bytes() is not None,
            "stun": list(self.stun_servers),
            "keepalive": self.keepalive,
            "replay_window": self.replay_window,
        }
