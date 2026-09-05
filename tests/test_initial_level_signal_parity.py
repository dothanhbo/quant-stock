import pandas as pd

from backtesting.engine import BacktestConfig, _simulate_exit
from backtesting.exit_models import ATRExitModel


def test_atr_initial_levels_use_signal_time_atr_with_next_open_entry():
    prices = pd.DataFrame([
        {
            "time": "2026-01-02", "open": 100.0, "high": 101.0,
            "low": 99.0, "close": 100.0, "ATR14": 5.0,
        },
        {
            "time": "2026-01-05", "open": 110.0, "high": 111.0,
            "low": 109.0, "close": 110.0, "ATR14": 9.0,
        },
    ])

    result = _simulate_exit(
        price_df=prices,
        entry_index=1,
        config=BacktestConfig(max_holding_days=1),
        exit_model=ATRExitModel(
            stop_atr_multiplier=2.0,
            target_atr_multiplier=4.0,
        ),
        initial_level_row=prices.iloc[0],
    )

    # Signal T ATR=5 is frozen; entry is T+1 open=110.
    assert result.entry_price == 110.0
    assert result.initial_stop_price == 100.0
    assert result.initial_target_price == 130.0
