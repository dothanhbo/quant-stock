from __future__ import annotations

import pandas as pd

from strategy.indicators import add_indicators


def prices(*, future_close: float) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=40, freq="D")
    close = [100.0] * 25 + [110.0] + [106.0] * 13 + [future_close]
    return pd.DataFrame({
        "time": dates,
        "open": close,
        "high": [value + 1.0 for value in close],
        "low": [value - 1.0 for value in close],
        "close": close,
        "volume": [1_000_000.0] * 40,
    })


def test_recent_breakout_excludes_current_session() -> None:
    result = add_indicators(prices(future_close=130.0))

    assert bool(result.iloc[25]["Breakout_20D"]) is True
    assert bool(result.iloc[25]["Recent_Breakout_10D"]) is False
    assert bool(result.iloc[26]["Recent_Breakout_10D"]) is True


def test_entry_v2_context_has_no_future_lookahead() -> None:
    normal = add_indicators(prices(future_close=106.0))
    shocked = add_indicators(prices(future_close=500.0))

    columns = [
        "Recent_Breakout_10D",
        "Touched_EMA10",
        "Reclaimed_EMA10",
    ]
    assert normal.loc[38, columns].tolist() == shocked.loc[38, columns].tolist()
