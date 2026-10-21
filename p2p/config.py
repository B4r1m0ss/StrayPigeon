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
    hardcoded. For multi-session GUI use, build a per-contact config with
    :meth:`from_vault`; CLI compatibility still allows environment fallback.
    This object never prints secrets.
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
    quic_handoff: bool = False
    contact_id: str = ""
    my_email: str = ""
    peer_email: str = ""
    imap_host: str = ""
    imap_user: str = ""
    imap_password: str = field(default="", repr=False)
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_starttls: bool = False
    folder: str = "INBOX"
    _psk: bytes | None = field(default=None, repr=False)

    @classmethod
    def from_vault(
        cls,
        data: dict,
        contact_id: str | None = None,
        *,
        port: int | None = None,
        role: str | None = None,
        host: str = "0.0.0.0",
        advertise_host: str = "127.0.0.1",
        log_dir: str = "./state/logs",
        stun_servers: tuple[str, ...] = DEFAULT_STUN,
        keepalive: float = 15.0,
        punch_window: float = 300.0,
        signal_timeout: float | None = None,
        replay_window: float | None = None,
    ) -> "Config":
        """Build per-contact config with no process-wide environment reads."""
        from .contacts import normalize_role, select_contact

        key, contact = select_contact(data, contact_id)
        identity = data.get("identity")
        if not isinstance(identity, dict):
            raise ValueError("vault has no identity")
        mode = str(identity.get("mode") or "")
        if contact.get("mode") and str(contact["mode"]) != mode:
            raise ValueError(f"contact mode {contact['mode']!r} != identity mode {mode!r}")
        psk_raw = base64.b64decode(str(contact.get("psk_b64") or ""), validate=True)
        if len(psk_raw) != 32:
            raise ValueError("contact PSK must decode to exactly 32 bytes")
        selected_port = int(port or contact.get("local_port") or 40000)
        if not 1 <= selected_port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        selected_role = normalize_role(role or contact.get("role"))
        default_timeout = 600.0 if mode == "smtp" else 300.0
        cfg = cls(
            name=str(identity.get("my_email") or ""),
            peer=str(contact.get("peer_email") or ""),
            port=selected_port,
            role=selected_role,
            signal_mode=mode,
            log_dir=log_dir,
            host=host,
            advertise_host=advertise_host,
            keepalive=keepalive,
            punch_window=punch_window,
            signal_timeout=float(signal_timeout or identity.get("signal_timeout") or default_timeout),
            stun_servers=stun_servers,
            replay_window=float(replay_window or identity.get("replay_window") or default_timeout),
            contact_id=key,
            my_email=str(identity.get("my_email") or ""),
            peer_email=str(contact.get("peer_email") or ""),
            imap_host=str(identity.get("imap_host") or ""),
            imap_user=str(identity.get("my_email") or ""),
            imap_password=str(identity.get("imap_password") or ""),
            folder=str(contact.get("folder") or "INBOX"),
        )
        if mode == "smtp":
            cfg.smtp_host = str(identity.get("smtp_host") or "")
            cfg.smtp_port = int(identity.get("smtp_port") or 465)
            cfg.smtp_starttls = bool(identity.get("smtp_starttls"))
        cfg._psk = psk_raw
        return cfg

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
        """Return the explicit PSK, or fall back to P2P_PSK_B64 for CLI use."""
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
