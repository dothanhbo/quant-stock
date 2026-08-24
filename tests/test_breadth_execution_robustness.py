from __future__ import annotations

import pandas as pd
import pytest

from research.monthly_momentum_baseline import prepare_symbol_features
from research.run_breadth_execution_robustness import (
    attach_order_participation,
    build_execution_stress_configs,
    participation_metrics,
)


def prices(*, periods: int = 320) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=periods)
    close = pd.Series(
        [100_000.0 + index * 100.0 for index in range(periods)],
        dtype=float,
    )
    return pd.DataFrame({
        "time": dates,
        "open": close - 50.0,
        "close": close,
        "volume": [1_000_000.0] * periods,
    })


def shared_config() -> dict:
    return {
        "minimum_history_rows": 252,
        "minimum_adtv20": 1.0,
        "maximum_volatility63_pct": 100.0,
        "lot_size": 100,
        "commission_pct": 0.15,
        "sell_tax_pct": 0.10,
    }


def test_execution_stress_has_fixed_eight_case_matrix() -> None:
    cases = build_execution_stress_configs(shared_config())

    assert list(cases) == [
        "next_open__slip_005",
        "next_open__slip_010",
        "next_open__slip_015",
        "next_open__slip_020",
        "delay_1__slip_005",
        "delay_1__slip_010",
        "delay_1__slip_015",
        "delay_1__slip_020",
    ]
    assert cases["next_open__slip_005"].execution_delay_sessions == 0
    assert cases["delay_1__slip_020"].execution_delay_sessions == 1
    assert cases["delay_1__slip_020"].slippage_pct == pytest.approx(0.20)


def test_order_participation_uses_only_signal_date_information() -> None:
    normal = prepare_symbol_features(prices())
    shocked = normal.copy()
    shocked.loc[319, "adtv20"] = 1.0
    signal_date = normal.loc[318, "time"]
    orders = pd.DataFrame([{
        "signal_date": signal_date,
        "execution_date": normal.loc[319, "time"],
        "symbol": "AAA",
        "side": "BUY",
        "quantity": 100,
        "reference_open": 100_000.0,
        "fill_price": 100_050.0,
        "notional": 10_005_000.0,
        "fees_tax": 15_007.5,
        "reason": "TEST",
    }])

    first = attach_order_participation(
        orders,
        feature_cache={"AAA": normal},
    )
    second = attach_order_participation(
        orders,
        feature_cache={"AAA": shocked},
    )

    assert first.loc[0, "participation_pct"] == pytest.approx(
        second.loc[0, "participation_pct"]
    )
    assert pd.Timestamp(first.loc[0, "adtv20_data_date"]) == signal_date


def test_participation_metrics_reports_capacity_thresholds() -> None:
    orders = pd.DataFrame({
        "participation_pct": [0.5, 1.5, 2.5],
    })

    metrics = participation_metrics(
        orders,
        initial_capital=100_000_000.0,
    )

    assert metrics["orders_over_1pct_adtv20"] == 2
    assert metrics["orders_over_2pct_adtv20"] == 1
    assert metrics["estimated_capital_at_1pct_adtv20"] == pytest.approx(
        40_000_000.0
    )
