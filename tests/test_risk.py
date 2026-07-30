import pandas as pd

from risk.levels import calculate_risk_levels


def test_risk_levels_have_positive_risk_and_expected_rr():
    latest = pd.Series({"close": 100.0, "ATR14": 2.0, "EMA20": 96.0})
    cfg = {"atr_stop_multiplier": 1.5, "rr_ratio": 2.0}
    result = calculate_risk_levels(latest, cfg)
    assert result["stop_loss"] < result["entry"] < result["take_profit"]
    assert result["rr_ratio"] == 2.0
