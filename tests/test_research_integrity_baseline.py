import pandas as pd
import pytest

from research.run_research_integrity_baseline import (
    build_attribution,
    calculate_cagr_pct,
    calculate_price_drawdown_pct,
    research_gates,
)


def test_integrity_math_uses_full_period_path() -> None:
    assert calculate_cagr_pct(100.0, 121.0, "2020-01-01", "2022-01-01") == (
        pytest.approx(10.0, abs=0.02)
    )
    assert calculate_price_drawdown_pct(pd.Series([100, 150, 120, 130])) == (
        pytest.approx(-20.0)
    )


def test_integrity_attribution_reconciles_trade_pnl() -> None:
    trades = pd.DataFrame([
        {
            "symbol": "AAA", "entry_year": 2025, "market_regime": "BULL",
            "exit_reason": "Take Profit", "net_pnl": 100.0,
            "net_return_pct": 5.0, "transaction_cost": 2.0,
        },
        {
            "symbol": "AAA", "entry_year": 2025, "market_regime": "BULL",
            "exit_reason": "Stop Loss", "net_pnl": -40.0,
            "net_return_pct": -2.0, "transaction_cost": 2.0,
        },
    ])

    result = build_attribution(trades)
    symbol = result[
        (result["dimension"] == "symbol") & (result["value"] == "AAA")
    ].iloc[0]
    assert symbol["trades"] == 2
    assert symbol["total_net_pnl"] == 60.0
    assert symbol["profit_factor"] == 2.5


def test_integrity_gate_cannot_pass_static_current_universe() -> None:
    summary = {
        "profitable_folds": 6,
        "median_test_return_pct": 0.1,
        "return_excluding_first_two_folds_pct": 0.1,
        "recent_3_folds_return_pct": 0.1,
        "strategy_chained_drawdown_pct": -14.0,
        "excess_return_vs_vnindex_pct": 1.0,
        "point_in_time_universe_verified": False,
    }

    gates = research_gates(summary)

    assert gates["gate_point_in_time_universe_verified"] is False
    assert gates["research_gate_passed"] is False
