from types import SimpleNamespace

import pandas as pd

from backtesting.engine import _simulate_exit
from backtesting.exit_models import ATRExitModel


def test_max_holding_days_counts_sessions_after_entry():
    dates = pd.date_range("2026-01-01", periods=5, freq="D")

    df = pd.DataFrame(
        {
            "time": dates,
            "open": [100.0] * 5,
            "high": [101.0] * 5,
            "low": [99.0] * 5,
            "close": [100.0] * 5,
            "ATR14": [2.0] * 5,
        }
    )

    config = SimpleNamespace(max_holding_days=2)

    result = _simulate_exit(
        df,
        entry_index=0,
        config=config,
        exit_model=ATRExitModel(
            stop_atr_multiplier=2.0,
            target_atr_multiplier=4.0,
        ),
    )

    assert result.exit_index == 2