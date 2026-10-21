"""QUIC chat transport and a small per-process session manager.

This module is intentionally independent of the legacy UDP Session and of
PyQt. It gives us a tested chat/concurrency layer first; the GUI wiring and
mail signaling can then move over without another protocol change.

Design:
- one listening UDP port; outbound clients use ephemeral ports;
- each chat message is one bidirectional QUIC stream;
- the manager enforces a hard 1..16 active-session limit;
- no message persistence, no offline queue, no artificial rate limit.
"""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import ssl
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Callable

from aioquic.asyncio import connect, serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import ConnectionTerminated, StreamDataReceived

ALPN = "stray-pigeon/1"


class QuicChatProtocol(QuicConnectionProtocol):
    """One QUIC connection; one stream per chat message."""

    def __init__(
        self,
        *args: Any,
        on_message: Callable[["QuicChatProtocol", bytes], None] | None = None,
        on_connected: Callable[["QuicChatProtocol"], None] | None = None,
        on_closed: Callable[["QuicChatProtocol"], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._buffers: dict[int, bytearray] = {}
        self._on_message = on_message
        self._on_connected = on_connected
        self._on_closed = on_closed
        self._closed_notified = False

    def connection_made(self, transport) -> None:
        super().connection_made(transport)
        if self._on_connected is not None:
            self._on_connected(self)

    def connection_lost(self, exc) -> None:
        super().connection_lost(exc)
        self._notify_closed()

    def _notify_closed(self) -> None:
        if self._closed_notified:
            return
        self._closed_notified = True
        if self._on_closed is not None:
            self._on_closed(self)

    def quic_event_received(self, event) -> None:
        if isinstance(event, StreamDataReceived):
            buf = self._buffers.setdefault(event.stream_id, bytearray())
            buf.extend(event.data)
            if event.end_stream:
                payload = bytes(buf)
                self._buffers.pop(event.stream_id, None)
                if self._on_message is not None:
                    self._on_message(self, payload)
        elif isinstance(event, ConnectionTerminated):
            self._notify_closed()

    def send_message(self, text: str) -> None:
        stream_id = self._quic.get_next_available_stream_id()
        self._quic.send_stream_data(stream_id, text.encode("utf-8"), end_stream=True)
        self.transmit()


class QuicChatSession:
    """A connected chat session; closes its QUIC context on close()."""

    def __init__(
        self,
        session_id: str,
        protocol: QuicChatProtocol,
        stack: AsyncExitStack | None,
        manager: "QuicSessionManager",
    ) -> None:
        self.session_id = session_id
        self.protocol = protocol
        self._stack = stack
        self._manager = manager

    async def send(self, text: str) -> None:
        self.protocol.send_message(text)

    async def close(self) -> None:
        self._manager._sessions.pop(self.session_id, None)
        if self._stack is not None:
            await self._stack.aclose()
        else:
            self.protocol.close()


class QuicSessionManager:
    """One UDP listener plus up to max_sessions active chat sessions."""

    def __init__(
        self,
        max_sessions: int = 16,
        on_message: Callable[[str, bytes], None] | None = None,
        on_state: Callable[[str, str], None] | None = None,
    ) -> None:
        self.max_sessions = max(1, min(16, int(max_sessions)))
        self.on_message = on_message
        self.on_state = on_state
        self._sessions: dict[str, QuicChatSession] = {}
        self._server = None
        self._server_config: QuicConfiguration | None = None
        self._protocol_ids: dict[int, str] = {}

    @property
    def active_sessions(self) -> int:
        return len(self._sessions)

    async def start_server(
        self, host: str, port: int, configuration: QuicConfiguration
    ) -> Any:
        """Start the single listening UDP port; returns aioquic's server."""
        self._server_config = configuration
        self._server = await serve(
            host,
            port,
            configuration=configuration,
            create_protocol=self._make_server_protocol,
        )
        return self._server

    def _make_server_protocol(self, *args: Any, **kwargs: Any) -> QuicChatProtocol:
        if len(self._sessions) >= self.max_sessions:
            raise RuntimeError("max sessions reached")
        protocol = QuicChatProtocol(
            *args,
            on_message=self._dispatch_message,
            on_closed=self._protocol_closed,
            **kwargs,
        )
        session_id = f"in:{id(protocol)}"
        self._protocol_ids[id(protocol)] = session_id
        self._sessions[session_id] = QuicChatSession(
            session_id, protocol, None, self
        )
        if self.on_state is not None:
            self.on_state(session_id, "connected")
        return protocol

    async def connect(
        self,
        session_id: str,
        host: str,
        port: int,
        configuration: QuicConfiguration,
    ) -> QuicChatSession:
        """Open an outbound QUIC chat session, enforcing max_sessions."""
        if len(self._sessions) >= self.max_sessions:
            raise RuntimeError(f"max sessions reached ({self.max_sessions})")
        stack = AsyncExitStack()
        protocol: QuicChatProtocol
        try:
            protocol = await stack.enter_async_context(
                connect(
                    host,
                    port,
                    configuration=configuration,
                    create_protocol=lambda *args, **kwargs: QuicChatProtocol(
                        *args,
                        on_message=self._dispatch_message,
                        on_closed=self._protocol_closed,
                        **kwargs,
                    ),
                )
            )
        except BaseException:
            await stack.aclose()
            raise
        self._protocol_ids[id(protocol)] = session_id
        self._sessions[session_id] = QuicChatSession(
            session_id, protocol, stack, self
        )
        if self.on_state is not None:
            self.on_state(session_id, "connected")
        return self._sessions[session_id]

    def _dispatch_message(self, protocol: QuicChatProtocol, payload: bytes) -> None:
        if self.on_message is None:
            return
        session_id = self._protocol_ids.get(id(protocol), "")
        self.on_message(session_id, payload)

    def _protocol_closed(self, protocol: QuicChatProtocol) -> None:
        session_id = self._protocol_ids.pop(id(protocol), "")
        self._sessions.pop(session_id, None)
        if self.on_state is not None and session_id:
            self.on_state(session_id, "closed")

    async def close(self) -> None:
        for session in list(self._sessions.values()):
            await session.close()
        self._sessions.clear()
        if self._server is not None:
            self._server.close()
            self._server = None
            await asyncio.sleep(0)


def make_self_signed_cert(
    directory: str | Path, common_name: str = "localhost"
) -> tuple[Path, Path]:
    """Create a local self-signed cert/key pair for QUIC.

    Peers will pin the certificate fingerprint in the invite; this helper just
    produces the files.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, common_name)]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName(common_name),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = directory / "cert.pem"
    key_path = directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def client_configuration() -> QuicConfiguration:
    """Local spike/client config; real peers pin the server cert fingerprint."""
    return QuicConfiguration(
        is_client=True,
        alpn_protocols=[ALPN],
        server_name="localhost",
        verify_mode=ssl.CERT_NONE,
    )


def server_configuration(cert_path: str | Path, key_path: str | Path) -> QuicConfiguration:
    cfg = QuicConfiguration(is_client=False, alpn_protocols=[ALPN])
    cfg.load_cert_chain(str(cert_path), str(key_path))
    return cfg
