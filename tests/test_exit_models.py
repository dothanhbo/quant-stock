from types import SimpleNamespace

import pandas as pd
import pytest

from backtesting.engine import _simulate_exit
from backtesting.exit_models import (
    ATRExitModel,
    BaseExitModel,
    BreakEvenExitModel,
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

class UpdatingExitModel(BaseExitModel):

    def __init__(self):
        self.update_calls = 0

    def calculate_levels(
        self,
        entry_price,
        entry_row,
        config,
    ):
        return (
            entry_price * 0.95,
            entry_price * 1.10,
        )

    def update_levels(
        self,
        *,
        entry_price,
        current_row,
        current_stop,
        current_target,
        highest_price,
        config,
    ):
        self.update_calls += 1

        return (
            current_stop,
            current_target,
        )

def test_simulate_exit_calls_update_levels():
    model = UpdatingExitModel()

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
                "open": 101.0,
                "high": 102.0,
                "low": 100.0,
                "close": 101.0,
                "ATR14": 2.0,
            },
            {
                "time": "2026-01-06",
                "open": 102.0,
                "high": 103.0,
                "low": 101.0,
                "close": 102.0,
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
        exit_model=model,
    )

    assert model.update_calls >= 1
    assert result is not None

def test_break_even_exit_keeps_original_stop_before_trigger():
    model = BreakEvenExitModel(
        trigger_pct=5.0,
    )

    stop, target = model.update_levels(
        entry_price=100.0,
        current_row={
            "high": 104.0,
        },
        current_stop=95.0,
        current_target=110.0,
        highest_price=104.0,
        config=None,
    )

    assert stop == pytest.approx(95.0)
    assert target == pytest.approx(110.0)


def test_break_even_exit_moves_stop_to_entry_after_trigger():
    model = BreakEvenExitModel(
        trigger_pct=5.0,
    )

    stop, target = model.update_levels(
        entry_price=100.0,
        current_row={
            "high": 106.0,
        },
        current_stop=95.0,
        current_target=110.0,
        highest_price=106.0,
        config=None,
    )

    assert stop == pytest.approx(100.0)
    assert target == pytest.approx(110.0)


def test_break_even_exit_never_lowers_stop():
    model = BreakEvenExitModel(
        trigger_pct=5.0,
    )

    stop, target = model.update_levels(
        entry_price=100.0,
        current_row={
            "high": 110.0,
        },
        current_stop=102.0,
        current_target=115.0,
        highest_price=110.0,
        config=None,
    )

    assert stop == pytest.approx(102.0)
    assert target == pytest.approx(115.0)


def test_break_even_exit_rejects_invalid_trigger():
    with pytest.raises(ValueError):
        BreakEvenExitModel(
            trigger_pct=0,
        )

def test_simulate_exit_with_break_even_model():
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
                "open": 102.0,
                "high": 106.0,
                "low": 101.0,
                "close": 105.0,
                "ATR14": 2.0,
            },
            {
                "time": "2026-01-06",
                "open": 99.0,
                "high": 101.0,
                "low": 98.0,
                "close": 99.0,
                "ATR14": 2.0,
            },
        ]
    )

    config = SimpleNamespace(
        max_holding_days=3,
        stop_loss_pct=5.0,
        take_profit_pct=15.0,
    )

    result = _simulate_exit(
        price_df=price_df,
        entry_index=0,
        config=config,
        exit_model=BreakEvenExitModel(
            trigger_pct=5.0,
        ),
    )

    assert result.stop_price == pytest.approx(
        100.0
    )

    assert result.exit_price == pytest.approx(
        99.0
    )

    assert result.exit_reason == (
        ExitReason.STOP_LOSS
    )