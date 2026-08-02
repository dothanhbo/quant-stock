from types import SimpleNamespace

import pandas as pd
import pytest

from backtesting.engine import _simulate_exit
from backtesting.exit_models import (
    ATRExitModel,
    FixedExitModel,
)
from backtesting.exit import ExitReason


def test_fixed_exit_model_calculates_levels():
    model = FixedExitModel()

    config = SimpleNamespace(
        stop_loss_pct=5.0,
        take_profit_pct=10.0,
    )

    entry_row = pd.Series({"ATR14": 2.0})

    stop_price, target_price = model.calculate_levels(
        entry_price=100.0,
        entry_row=entry_row,
        config=config,
    )

    assert stop_price == pytest.approx(95.0)
    assert target_price == pytest.approx(110.0)


def test_atr_exit_model_calculates_levels():
    model = ATRExitModel(
        stop_atr_multiplier=2.0,
        target_atr_multiplier=4.0,
    )

    entry_row = pd.Series({"ATR14": 2.5})

    stop_price, target_price = model.calculate_levels(
        entry_price=100.0,
        entry_row=entry_row,
        config=SimpleNamespace(),
    )

    assert stop_price == pytest.approx(95.0)
    assert target_price == pytest.approx(110.0)


def test_atr_exit_model_accepts_lowercase_atr():
    model = ATRExitModel()

    entry_row = pd.Series({"atr": 3.0})

    stop_price, target_price = model.calculate_levels(
        entry_price=50.0,
        entry_row=entry_row,
        config=SimpleNamespace(),
    )

    assert stop_price == pytest.approx(44.0)
    assert target_price == pytest.approx(62.0)


def test_atr_exit_model_rejects_missing_atr():
    model = ATRExitModel()

    entry_row = pd.Series({"open": 100.0})

    with pytest.raises(ValueError, match="Không tìm thấy ATR"):
        model.calculate_levels(
            entry_price=100.0,
            entry_row=entry_row,
            config=SimpleNamespace(),
        )


def test_atr_exit_model_rejects_invalid_multiplier():
    with pytest.raises(ValueError):
        ATRExitModel(stop_atr_multiplier=0)

    with pytest.raises(ValueError):
        ATRExitModel(target_atr_multiplier=-1)

def test_simulate_exit_with_atr_model():
    price_df = pd.DataFrame(
        [
            {
                "time": "2026-01-02",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "ATR14": 2.0,
            },
            {
                "time": "2026-01-05",
                "open": 100.0,
                "high": 109.0,
                "low": 99.0,
                "close": 108.0,
                "ATR14": 2.0,
            },
        ]
    )

    config = SimpleNamespace(
        max_holding_days=2,
        stop_loss_pct=5.0,
        take_profit_pct=10.0,
    )

    result = _simulate_exit(
        price_df=price_df,
        entry_index=0,
        config=config,
        exit_model=ATRExitModel(
            stop_atr_multiplier=2.0,
            target_atr_multiplier=4.0,
        ),
    )

    assert result.stop_price == pytest.approx(96.0)
    assert result.target_price == pytest.approx(108.0)
    assert result.exit_price == pytest.approx(108.0)
    assert result.exit_reason == ExitReason.TAKE_PROFIT

def test_fixed_exit_model_keeps_levels_unchanged():
    model = FixedExitModel()

    stop, target = model.update_levels(
        entry_price=100.0,
        current_row={
            "high": 108.0,
            "close": 107.0,
        },
        current_stop=95.0,
        current_target=110.0,
        highest_price=108.0,
        config=None,
    )

    assert stop == 95.0
    assert target == 110.0


def test_atr_exit_model_keeps_levels_unchanged():
    model = ATRExitModel(
        stop_atr_multiplier=2.0,
        target_atr_multiplier=4.0,
    )

    stop, target = model.update_levels(
        entry_price=100.0,
        current_row={
            "ATR14": 2.0,
            "high": 108.0,
        },
        current_stop=96.0,
        current_target=108.0,
        highest_price=108.0,
        config=None,
    )

    assert stop == 96.0
    assert target == 108.0