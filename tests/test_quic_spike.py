from __future__ import annotations

import asyncio
import datetime
import ipaddress
import os
import socket
import ssl
import tempfile
import unittest
from pathlib import Path

try:  # optional dependency: QUIC is an opt-in extra
    from aioquic.asyncio import connect, serve
    from aioquic.asyncio.protocol import QuicConnectionProtocol
    from aioquic.quic.configuration import QuicConfiguration
    from aioquic.quic.events import StreamDataReceived

    HAVE_QUIC = True
except Exception:  # noqa: BLE001
    HAVE_QUIC = False

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    HAVE_CRYPTO = True
except Exception:  # noqa: BLE001
    HAVE_CRYPTO = False

ALPN = "stray-pigeon/1"


def _free_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _make_cert(directory: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
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


if HAVE_QUIC:

    class EchoProtocol(QuicConnectionProtocol):
        def quic_event_received(self, event) -> None:
            if isinstance(event, StreamDataReceived):
                self._quic.send_stream_data(
                    event.stream_id, event.data, end_stream=event.end_stream
                )
                self.transmit()

    class ClientEchoProtocol(QuicConnectionProtocol):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._buffer = bytearray()
            self._done: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

        def quic_event_received(self, event) -> None:
            if isinstance(event, StreamDataReceived):
                self._buffer.extend(event.data)
                if event.end_stream and not self._done.done():
                    self._done.set_result(bytes(self._buffer))

        async def echo(self, payload: bytes) -> bytes:
            stream_id = self._quic.get_next_available_stream_id()
            self._quic.send_stream_data(stream_id, payload, end_stream=True)
            self.transmit()
            return await asyncio.wait_for(self._done, 15.0)


@unittest.skipUnless(
    HAVE_QUIC and HAVE_CRYPTO, "aioquic/cryptography not installed (pip install aioquic)"
)
class QuicSpikeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        cert_path, key_path = _make_cert(tmp)
        self._server_config = QuicConfiguration(
            is_client=False, alpn_protocols=[ALPN]
        )
        self._server_config.load_cert_chain(str(cert_path), str(key_path))
        self._client_config = QuicConfiguration(
            is_client=True,
            alpn_protocols=[ALPN],
            server_name="localhost",
            verify_mode=ssl.CERT_NONE,
        )
        self._port = _free_udp_port()
        self._server = await serve(
            "127.0.0.1",
            self._port,
            configuration=self._server_config,
            create_protocol=EchoProtocol,
        )

    async def asyncTearDown(self) -> None:
        self._server.close()
        await asyncio.sleep(0.05)
        self._tmp.cleanup()

    async def _echo(self, payload: bytes) -> bytes:
        async with connect(
            "127.0.0.1",
            self._port,
            configuration=self._client_config,
            create_protocol=ClientEchoProtocol,
        ) as client:
            return await client.echo(payload)

    async def test_16_connections_on_one_udp_port(self) -> None:
        payloads = [f"hello-{i:02d}".encode("ascii") for i in range(16)]
        results = await asyncio.gather(*(self._echo(payload) for payload in payloads))
        self.assertEqual(results, payloads)

    async def test_file_sized_stream(self) -> None:
        payload = os.urandom(256 * 1024)
        result = await self._echo(payload)
        self.assertEqual(result, payload)


if __name__ == "__main__":
    unittest.main()
