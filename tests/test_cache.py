import pandas as pd

from strategy.cache import cache_info, clear_indicator_cache, get_indicators_cached


def _data():
    dates = pd.date_range("2025-01-01", periods=80, freq="D")
    close = pd.Series(range(100, 180), dtype=float)
    return pd.DataFrame({
        "time": dates,
        "open": close - 0.5,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": 1_000_000,
    })


def test_indicator_cache_reuses_entry():
    clear_indicator_cache()
    df = _data()
    first = get_indicators_cached("TEST", df)
    second = get_indicators_cached("TEST", df)
    assert len(first) == len(second)
    assert cache_info()["size"] == 1
