from __future__ import annotations

from .cli import main
from .runtime import configure_utf8_stdio


if __name__ == "__main__":
    configure_utf8_stdio()
    raise SystemExit(main())
