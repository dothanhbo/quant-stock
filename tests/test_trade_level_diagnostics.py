import pandas as pd
import pytest

from research.run_trade_level_diagnostics import (
    aggregate_dimension,
    calculate_excursions,
    classify_trade_path,
    score_bucket,
)


def test_calculate_excursions_uses_lifecycle_high_and_low() -> None:
    bars = pd.DataFrame({
        "high": [101.0, 108.0, 104.0],
        "low": [98.0, 96.0, 99.0],
    })

    mfe, mae = calculate_excursions(entry_price=100.0, bars=bars)

    assert mfe == pytest.approx(8.0)
    assert mae == pytest.approx(-4.0)


def test_trade_path_separates_entry_failure_from_giveback() -> None:
    assert classify_trade_path(return_pct=-3.0, mfe_pct=1.0) == (
        "LOSS_NEVER_WORKED"
    )
    assert classify_trade_path(return_pct=-1.0, mfe_pct=4.0) == (
        "LOSS_GAVE_BACK_PROFIT"
    )
    assert classify_trade_path(return_pct=2.0, mfe_pct=6.0) == (
        "WIN_GAVE_BACK_3PCT_PLUS"
    )
    assert classify_trade_path(return_pct=4.0, mfe_pct=5.0) == (
        "WIN_CAPTURED"
    )


def test_score_bucket_boundaries() -> None:
    assert score_bucket(None) == "UNKNOWN"
    assert score_bucket(64.9) == "<65"
    assert score_bucket(65.0) == "65-69"
    assert score_bucket(85.0) == "85+"


def test_aggregate_dimension_reports_win_rate_and_excursions() -> None:
    trades = pd.DataFrame({
        "case_id": ["case", "case"],
        "market_regime": ["BULL", "BULL"],
        "net_return_pct": [5.0, -2.0],
        "net_pnl": [500.0, -200.0],
        "mfe_pct": [7.0, 1.0],
        "mae_pct": [-1.0, -4.0],
        "giveback_from_mfe_pct": [2.0, 3.0],
        "position_value": [10_000.0, 20_000.0],
        "risk_pct": [1.0, 2.0],
    })

    result = aggregate_dimension(trades, dimension="market_regime")

    assert result.iloc[0]["trades"] == 2
    assert result.iloc[0]["win_rate_pct"] == 50.0
    assert result.iloc[0]["average_mfe_pct"] == 4.0
