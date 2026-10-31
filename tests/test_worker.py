from __future__ import annotations

import unittest

from p2p.config import Config
from p2p.gui.worker import SessionWorker


class SessionWorkerTests(unittest.TestCase):
    def test_worker_keeps_config_for_handoff_payload(self) -> None:
        cfg = Config(
            name="alice@example.com",
            peer="bob@example.com",
            port=40000,
            role="initiator",
            log_dir="/tmp/straypigeon-worker-test",
            _psk=b"x" * 32,
        )
        worker = SessionWorker(cfg)
        self.assertIs(worker.cfg, cfg)
        self.assertEqual(worker.cfg.role, "initiator")


if __name__ == "__main__":
    unittest.main()
