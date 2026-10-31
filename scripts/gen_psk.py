#!/usr/bin/env python3
"""Generate a 32-byte PSK and write it to a 0600 .env-style file.

The key is generated with `secrets.token_bytes(32)` (OS CSPRNG, already
hardware-seeded). It is never printed, never committed, and never derived
from hardware IDs (MAC/CPU serial/hostname are low-entropy and public).

Usage:
  python3 scripts/gen_psk.py --out .env

Both peers must use the SAME value. Share it out-of-band (QR/Signal/offline),
never by email. Do not commit .env.
"""
from __future__ import annotations

import argparse
import base64
import os
import secrets
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=".env")
    a = p.parse_args()

    out = Path(a.out)
    if out.exists():
        raise SystemExit(f"{out} already exists; refusing to overwrite")

    psk = secrets.token_bytes(32)
    line = "P2P_PSK_B64=" + base64.b64encode(psk).decode("ascii") + "\n"
    fd = os.open(out, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(line)

    print(f"wrote {out} (mode 0600)")
    print("Do not commit it. Share it out-of-band (QR / Signal / offline).")
    print("Both peers must use the SAME value.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
