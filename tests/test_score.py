import pandas as pd

from strategy.scoring import calculate_score


def _latest(**overrides):
    values = {
        "close": 120.0,
        "EMA10": 115.0,
        "EMA20": 110.0,
        "EMA50": 100.0,
        "EMA20_Rising": True,
        "Vol_Ratio": 2.1,
        "RSI": 58.0,
        "ADX14": 31.0,
        "Breakout_20D": True,
        "Volume_Breakout_5D": True,
        "Green_Candle": True,
        "Close_Upper_Half": True,
        "Body_Ratio": 0.5,
    }
    values.update(overrides)
    return pd.Series(values)


def test_score_is_capped_at_100():
    score, reasons = calculate_score(_latest(), relative_strength=12.0)
    assert score == 100
    assert reasons


def test_stronger_relative_strength_scores_higher():
    weak, _ = calculate_score(_latest(), relative_strength=-5.0)
    strong, _ = calculate_score(_latest(), relative_strength=8.0)
    assert strong > weak
