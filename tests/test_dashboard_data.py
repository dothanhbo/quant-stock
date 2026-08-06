from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.data import DashboardPaths, compute_overview, load_market_health, load_positions


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
