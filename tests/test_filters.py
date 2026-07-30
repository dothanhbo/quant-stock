import pandas as pd

from strategy.filters import evaluate_conditions, trend_passes


def latest():
    return pd.Series({
        "close": 110,
        "EMA10": 105,
        "EMA20": 100,
        "EMA50": 95,
        "EMA20_Rising": True,
        "Vol_Ratio": 1.3,
        "ADX14": 24,
        "RSI": 58,
        "Distance_EMA20_Pct": 10,
        "Return_3D_Pct": 5,
    })


def test_trend_rules():
    row = latest()
    assert trend_passes(row, "BULL")
    assert trend_passes(row, "SIDEWAY")


def test_conditions_are_independent():
    cfg = {
        "regime": "SIDEWAY",
        "min_volume_ratio": 1.2,
        "min_adx": 20,
        "max_distance_ema20": 12,
        "max_return_3d": 8,
        "min_relative_strength": 0,
    }
    assert all(evaluate_conditions(latest(), 3.0, cfg).values())
