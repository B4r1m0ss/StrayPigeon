"""UDP hole punching and QUIC on the *same* UDP socket.

QUIC is carried over UDP. A punched NAT mapping is tied to the local UDP
port, so punching on one socket and then starting QUIC on another socket
would waste the punch. This module wraps one asyncio datagram endpoint:

- P2P1 punch probes are answered in-place;
- all other datagrams are handed to aioquic's QuicServer / QuicConnection.

The client starts the QUIC handshake only after it has seen a valid P2P1
probe/response; the server's QuicServer is already listening on the shared
socket.
"""

from __future__ import annotations

import asyncio
import secrets
import socket
from pathlib import Path
from typing import Callable

from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.asyncio.server import QuicServer
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.connection import QuicConnection

from .punch import make_probe, parse_probe
from .quic_transport import QuicChatProtocol

Address = tuple[str, int]


class _PunchBase:
    def __init__(
        self,
        configuration: QuicConfiguration,
        sid: str,
        lease_id: str,
        psk: bytes,
        on_message: Callable[[str, bytes], None] | None = None,
    ) -> None:
        self.configuration = configuration
        self.sid = sid
        self.lease_id = lease_id
        self.psk = psk
        self.on_message = on_message
        self.transport: asyncio.DatagramTransport | None = None
        self.observed: Address | None = None
        self._punch_event = asyncio.Event()
        self._punch_tasks: list[asyncio.Task] = []

    def connection_made(self, transport) -> None:
        self.transport = transport

    def connection_lost(self, exc) -> None:
        self.transport = None
        self._cancel_punch()

    def error_received(self, exc) -> None:
        pass

    def _handle_punch(self, data: bytes, addr: Address) -> bool:
        parsed = parse_probe(data, self.psk)
        if not parsed or parsed[0] != self.sid:
            return False
        assert self.transport is not None
        self.transport.sendto(data, addr)
        if self.observed is None:
            self.observed = addr
            self._punch_event.set()
        return True

    async def _punch(
        self, candidates: list[Address], interval: float = 0.2, timeout: float = 15.0
    ) -> Address | None:
        probe = make_probe(self.sid, self.lease_id, self.psk)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self.observed is not None:
                return self.observed
            assert self.transport is not None
            for addr in candidates:
                try:
                    self.transport.sendto(probe, addr)
                except OSError:
                    pass
            try:
                await asyncio.wait_for(self._punch_event.wait(), interval)
            except asyncio.TimeoutError:
                pass
        return self.observed

    def _cancel_punch(self) -> None:
        for task in self._punch_tasks:
            task.cancel()
        self._punch_tasks.clear()


class PunchQuicClient(_PunchBase):
    def __init__(
        self,
        configuration: QuicConfiguration,
        sid: str,
        lease_id: str,
        psk: bytes,
        on_message: Callable[[str, bytes], None] | None = None,
    ) -> None:
        super().__init__(configuration, sid, lease_id, psk, on_message)
        self.quic = QuicConnection(configuration=configuration)
        self.protocol = QuicChatProtocol(
            self.quic,
            on_message=lambda _protocol, data: (
                self.on_message("client", data) if self.on_message else None
            ),
        )

    async def start(
        self,
        local_port: int,
        candidates: list[Address],
        timeout: float = 15.0,
        sock: socket.socket | None = None,
    ) -> Address:
        loop = asyncio.get_running_loop()
        if sock is not None:
            await loop.create_datagram_endpoint(lambda: self, sock=sock)
        else:
            await loop.create_datagram_endpoint(
                lambda: self, local_addr=("0.0.0.0", local_port)
            )
        observed = await self._punch(candidates, timeout=timeout)
        if observed is None:
            raise TimeoutError("UDP punch timed out")
        assert self.transport is not None
        self.protocol.connection_made(self.transport)
        self.protocol.connect(observed)
        await asyncio.wait_for(self.protocol.wait_connected(), timeout)
        return observed

    def datagram_received(self, data: bytes, addr: Address) -> None:
        if self._handle_punch(data, addr):
            return
        self.protocol.datagram_received(data, addr)

    async def close(self) -> None:
        self._cancel_punch()
        try:
            self.protocol.close()
            await self.protocol.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        if self.transport is not None:
            self.transport.close()
            self.transport = None


class PunchQuicServer(_PunchBase):
    def __init__(
        self,
        configuration: QuicConfiguration,
        sid: str,
        lease_id: str,
        psk: bytes,
        on_message: Callable[[str, bytes], None] | None = None,
    ) -> None:
        super().__init__(configuration, sid, lease_id, psk, on_message)
        self.quic_server = QuicServer(
            configuration=configuration,
            create_protocol=lambda *args, **kwargs: QuicChatProtocol(
                *args,
                on_message=lambda _protocol, data: (
                    self.on_message("server", data) if self.on_message else None
                ),
                **kwargs,
            ),
        )

    async def start(
        self,
        host: str,
        port: int,
        candidates: list[Address] | None = None,
        timeout: float = 15.0,
        sock: socket.socket | None = None,
    ) -> None:
        loop = asyncio.get_running_loop()
        if sock is not None:
            await loop.create_datagram_endpoint(lambda: self, sock=sock)
        else:
            await loop.create_datagram_endpoint(
                lambda: self, local_addr=(host, port)
            )
        assert self.transport is not None
        self.quic_server.connection_made(self.transport)
        if candidates:
            self._punch_tasks.append(asyncio.create_task(self._punch(candidates, timeout=timeout)))

    def connection_made(self, transport) -> None:
        super().connection_made(transport)
        if self.quic_server is not None:
            self.quic_server.connection_made(transport)

    def datagram_received(self, data: bytes, addr: Address) -> None:
        if self._handle_punch(data, addr):
            return
        self.quic_server.datagram_received(data, addr)

    async def close(self) -> None:
        self._cancel_punch()
        self.quic_server.close()
        self.transport = None
