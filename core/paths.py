from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MARKET_DATABASE_PATH = PROJECT_ROOT / "data" / "market.db"


def resolve_market_database_path(
    database_path: str | Path | None = None,
) -> Path:
    """Return the canonical absolute path for the market database.

    Explicit paths win. Otherwise, a non-empty MARKET_DATABASE_PATH is used;
    absent or blank configuration falls back to the project market database.
    Relative paths are always interpreted from the project root.
    """
    if database_path is not None:
        candidate = Path(database_path).expanduser()
    else:
        configured_path = os.getenv("MARKET_DATABASE_PATH", "").strip()
        candidate = (
            Path(configured_path).expanduser()
            if configured_path
            else DEFAULT_MARKET_DATABASE_PATH
        )

    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate

    return candidate.resolve()
