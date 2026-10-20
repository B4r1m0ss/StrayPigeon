"""Transport layer.

v0.1 (this version): raw UDP CHAT frames over the punched socket.
v0.2: AEAD frames `seq(8) || nonce(12) || ciphertext || tag`.
v0.3: QUIC (aioquic) over the *same* punched UDP socket.

Do not create a new socket for the transport: the NAT mapping is tied to the
local port that did the punching.
"""

from __future__ import annotations


class QuicTransport:
    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError(
            "QUIC transport is planned for v0.3; see docs/PROTOCOL.md"
        )
