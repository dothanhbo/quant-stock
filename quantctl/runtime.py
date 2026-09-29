from __future__ import annotations

import sys
from typing import TextIO


def _configure_stream(stream: TextIO | None) -> None:
    if stream is None:
        return
    reconfigure = getattr(stream, "reconfigure", None)
    if not callable(reconfigure):
        return
    try:
        reconfigure(encoding="utf-8", errors="backslashreplace")
    except (OSError, ValueError):
        # Test captures, closed streams, and embedded hosts may not permit it.
        return


def configure_utf8_stdio() -> None:
    """Make current-process terminal output Unicode-safe when supported."""
    _configure_stream(sys.stdout)
    _configure_stream(sys.stderr)
