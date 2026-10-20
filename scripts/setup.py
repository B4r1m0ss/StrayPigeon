#!/usr/bin/env python3
"""Identity-only setup entry point.

`setup.py` now asks only for *your* mailbox identity (IMAP/SMTP credentials +
vault passphrase). Peer information belongs to a per-conversation contact:

  python3 scripts/contact.py new --peer peer@example.com
  python3 scripts/contact.py join "p2p1:..."

The old flat create/join flow is kept for reference in `setup_legacy.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from setup_identity import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
