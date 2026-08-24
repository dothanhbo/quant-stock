from __future__ import annotations

import pandas as pd

from strategy.trend_pullback_retest_entry import (
    TrendPullbackRetestEntryModel,
)


def latest(**overrides) -> pd.Series:
    values = {
        "close": 103.0,
        "EMA10": 102.0,
        "EMA20": 100.0,
        "EMA50": 95.0,
        "EMA20_Rising": True,
        "RSI": 58.0,
        "ADX14": 24.0,
        "ATR14": 3.0,
        "Distance_EMA20_Pct": 3.0,
        "Return_3D_Pct": 1.5,
        "Recent_Breakout_10D": True,
        "Touched_EMA10": True,
        "Reclaimed_EMA10": True,
        "Green_Candle": True,
        "Close_Upper_Half": True,
    }
    values.update(overrides)
    return pd.Series(values)


def market_config() -> dict:
    return {
        "regime": "BULL",
        "atr_stop_multiplier": 2.0,
        "rr_ratio": 2.0,
    }


def test_pullback_retest_v2_passes_confirmed_reclaim() -> None:
    decision = TrendPullbackRetestEntryModel().evaluate(
        latest=latest(),
        relative_strength=5.0,
        market_config=market_config(),
    )

    assert decision["status"] == "PASSED"
    assert decision["score"] == 100
    assert decision["entry_model"] == "trend_pullback_retest_v2"


def test_pullback_retest_v2_requires_prior_breakout() -> None:
    decision = TrendPullbackRetestEntryModel().evaluate(
        latest=latest(Recent_Breakout_10D=False),
        relative_strength=5.0,
        market_config=market_config(),
    )

    assert decision["status"] == "REJECTED"
    assert "recent_breakout" in decision["failed_conditions"]


def test_pullback_retest_v2_rejects_close_without_reclaim() -> None:
    decision = TrendPullbackRetestEntryModel().evaluate(
        latest=latest(Reclaimed_EMA10=False),
        relative_strength=5.0,
        market_config=market_config(),
    )

    assert "pullback_reclaim" in decision["failed_conditions"]
