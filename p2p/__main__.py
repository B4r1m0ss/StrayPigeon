from __future__ import annotations

from .config import Config
from .session import Session


def main(argv: list[str] | None = None) -> int:
    cfg = Config.from_args(argv)
    return Session(cfg).run()


if __name__ == "__main__":
    raise SystemExit(main())
