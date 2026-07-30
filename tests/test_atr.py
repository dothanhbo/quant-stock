import pandas as pd

from strategy.indicators import calculate_atr


def test_atr_is_positive_after_warmup():
    rows = 40
    close = pd.Series([100 + i * 0.5 for i in range(rows)])
    df = pd.DataFrame({
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
    })
    atr = calculate_atr(df, period=14)
    assert atr.iloc[-1] > 0
    assert not pd.isna(atr.iloc[-1])
