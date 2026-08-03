from __future__ import annotations

import pandas as pd

from strategy.donchian_breakout_entry import (
    DonchianBreakoutEntryModel,
)


def build_latest(
    **overrides,
) -> pd.Series:
    values = {
        "close": 105.0,
        "EMA10": 102.0,
        "EMA20": 100.0,
        "EMA50": 95.0,
        "EMA20_Rising": True,
        "RSI": 60.0,
        "Vol_Ratio": 1.5,
        "ATR14": 3.0,
        "ATR_Percent": 2.8,
        "ADX14": 28.0,
        "Distance_EMA20_Pct": 5.0,
        "Return_3D_Pct": 6.0,
        "Green_Candle": True,
        "Close_Upper_Half": True,
        "Body_Ratio": 0.5,
        "Breakout_20D": True,
        "Volume_Breakout_5D": True,
    }

    values.update(
        overrides
    )

    return pd.Series(values)


def build_market_config() -> dict:
    return {
        "regime": "BULL",
        "min_score": 60,
        "watchlist_margin": 10,
        "rr_ratio": 2.0,
        "atr_stop_multiplier": 1.5,
    }


def test_donchian_breakout_passes():
    model = (
        DonchianBreakoutEntryModel()
    )

    decision = model.evaluate(
        latest=build_latest(),
        relative_strength=5.0,
        market_config=(
            build_market_config()
        ),
    )

    assert (
        decision["status"]
        == "PASSED"
    )

    assert (
        decision["entry_model"]
        == "donchian_breakout_v1"
    )

    assert (
        decision["failed_conditions"]
        == []
    )


def test_donchian_breakout_rejects_without_breakout():
    model = (
        DonchianBreakoutEntryModel()
    )

    decision = model.evaluate(
        latest=build_latest(
            Breakout_20D=False,
        ),
        relative_strength=5.0,
        market_config=(
            build_market_config()
        ),
    )

    assert (
        decision["status"]
        != "PASSED"
    )

    assert (
        "breakout_20d"
        in decision[
            "failed_conditions"
        ]
    )


def test_donchian_breakout_rejects_weak_volume():
    model = (
        DonchianBreakoutEntryModel()
    )

    decision = model.evaluate(
        latest=build_latest(
            Vol_Ratio=0.8,
        ),
        relative_strength=5.0,
        market_config=(
            build_market_config()
        ),
    )

    assert (
        "volume"
        in decision[
            "failed_conditions"
        ]
    )


def test_donchian_breakout_can_require_volume_breakout():
    model = (
        DonchianBreakoutEntryModel(
            require_volume_breakout=True,
        )
    )

    decision = model.evaluate(
        latest=build_latest(
            Volume_Breakout_5D=False,
        ),
        relative_strength=5.0,
        market_config=(
            build_market_config()
        ),
    )

    assert (
        "volume_breakout_5d"
        in decision[
            "failed_conditions"
        ]
    )


def test_donchian_breakout_rejects_invalid_parameters():
    try:
        DonchianBreakoutEntryModel(
            min_adx=-1,
        )

    except ValueError:
        pass

    else:
        raise AssertionError(
            "Expected ValueError"
        )