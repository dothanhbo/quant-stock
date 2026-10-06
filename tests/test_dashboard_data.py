from pathlib import Path
import os
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.data import DashboardPaths, compute_overview, load_market_health, load_positions
from core.paths import DEFAULT_MARKET_DATABASE_PATH, PROJECT_ROOT


def test_default_market_database_is_canonical_and_cwd_independent(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MARKET_DATABASE_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    assert DashboardPaths().market_db == DEFAULT_MARKET_DATABASE_PATH.resolve()


def test_default_market_database_honors_root_relative_environment(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MARKET_DATABASE_PATH", "custom/market.db")
    monkeypatch.chdir(tmp_path)

    assert DashboardPaths().market_db == (PROJECT_ROOT / "custom/market.db").resolve()


@pytest.mark.skipif(
    os.environ.get("QUANT_ALLOW_LIVE_DB_TESTS", "").strip() != "1",
    reason=(
        "Reads the real data/market.db and legacy data/paper_trading.db (opened "
        "read-write by dashboard.data). Opt in with QUANT_ALLOW_LIVE_DB_TESTS=1."
    ),
)
def test_real_project_databases_are_readable() -> None:
    paths = DashboardPaths(
        market_db=Path("data/market.db"),
        paper_db=Path("data/paper_trading.db"),
    )
    overview = compute_overview(paths)
    assert overview.equity > 0
    assert overview.cash >= 0
    assert not load_market_health(paths).empty
    positions = load_positions(paths)
    assert "symbol" in positions.columns
