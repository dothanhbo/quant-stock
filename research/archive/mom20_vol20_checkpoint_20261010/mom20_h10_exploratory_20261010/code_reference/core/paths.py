from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MARKET_DATABASE_PATH = PROJECT_ROOT / "data" / "market.db"


def resolve_market_database_path(
    database_path: str | Path | None = None,
    *,
    root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Return the canonical absolute path for the market database.

    Explicit paths win. Otherwise, a non-empty MARKET_DATABASE_PATH is used;
    absent or blank configuration falls back to the project market database.
    Relative paths are always interpreted from the project root. Diagnostic
    callers may supply an inspection root/environment; runtime defaults remain
    the production project root and process environment.
    """
    if database_path is not None:
        candidate = Path(database_path).expanduser()
    else:
        values = os.environ if environ is None else environ
        configured_path = str(values.get("MARKET_DATABASE_PATH", "")).strip()
        candidate = (
            Path(configured_path).expanduser()
            if configured_path
            else root / "data" / "market.db"
        )

    if not candidate.is_absolute():
        candidate = root / candidate

    return candidate.resolve()
