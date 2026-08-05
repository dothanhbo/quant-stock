from datetime import date

import pytest

from execution.exit_engine import (
    ExitEngine,
    ExitEngineConfig,
)
from execution.exit_models import (
    ExitBar,
    ExitReason,
    PositionExitState,
    SameBarExitPolicy,
)


def make_state(
    **changes,
) -> PositionExitState:
    values = {
        "symbol": "VNM",
        "entry_date": date(
            2026,
            8,
            1,
        ),
        "entry_price": 59_500.0,
        "quantity": 300,
        "stop_price": 58_000.0,
        "take_profit_price": 62_750.0,
        "highest_price": 60_000.0,
        "trailing_stop_price": None,
        "trailing_atr_multiplier": None,
        "maximum_holding_days": None,
    }

    values.update(
        changes
    )

    return PositionExitState(
        **values
    )


def make_bar(
    **changes,
) -> ExitBar:
    values = {
        "symbol": "VNM",
        "valuation_date": date(
            2026,
            8,
            5,
        ),
        "open_price": 59_000.0,
        "high_price": 60_000.0,
        "low_price": 58_500.0,
        "close_price": 59_300.0,
        "atr": 800.0,
    }

    values.update(
        changes
    )

    return ExitBar(
        **values
    )


def test_hold_when_no_exit_condition() -> None:
    decision = ExitEngine().evaluate(
        state=make_state(),
        bar=make_bar(),
    )

    assert not decision.should_exit
    assert decision.reason is None
    assert (
        decision.highest_price
        == 60_000.0
    )


def test_stop_loss_intraday() -> None:
    decision = ExitEngine().evaluate(
        state=make_state(),
        bar=make_bar(
            low_price=57_900.0,
        ),
    )

    assert decision.should_exit
    assert (
        decision.reason
        == ExitReason.STOP_LOSS
    )
    assert (
        decision.execution_price
        == 58_000.0
    )


def test_gap_down_executes_at_open() -> None:
    decision = ExitEngine().evaluate(
        state=make_state(),
        bar=make_bar(
            open_price=57_500.0,
            high_price=58_200.0,
            low_price=57_000.0,
            close_price=57_800.0,
        ),
    )

    assert decision.should_exit
    assert (
        decision.reason
        == ExitReason.STOP_LOSS
    )
    assert (
        decision.execution_price
        == 57_500.0
    )


def test_take_profit_intraday() -> None:
    decision = ExitEngine().evaluate(
        state=make_state(),
        bar=make_bar(
            high_price=63_000.0,
        ),
    )

    assert decision.should_exit
    assert (
        decision.reason
        == ExitReason.TAKE_PROFIT
    )
    assert (
        decision.execution_price
        == 62_750.0
    )


def test_same_bar_uses_conservative_stop() -> None:
    decision = ExitEngine().evaluate(
        state=make_state(),
        bar=make_bar(
            high_price=63_000.0,
            low_price=57_500.0,
        ),
    )

    assert decision.should_exit
    assert (
        decision.reason
        == ExitReason.STOP_LOSS
    )


def test_same_bar_can_use_target_policy() -> None:
    engine = ExitEngine(
        ExitEngineConfig(
            same_bar_policy=(
                SameBarExitPolicy
                .OPTIMISTIC_TARGET
            )
        )
    )

    decision = engine.evaluate(
        state=make_state(),
        bar=make_bar(
            high_price=63_000.0,
            low_price=57_500.0,
        ),
    )

    assert decision.should_exit
    assert (
        decision.reason
        == ExitReason.TAKE_PROFIT
    )


def test_trailing_stop_only_moves_up() -> None:
    decision = ExitEngine().evaluate(
        state=make_state(
            take_profit_price=None,
            trailing_stop_price=58_500.0,
            trailing_atr_multiplier=2.0,
        ),
        bar=make_bar(
            open_price=60_500.0,
            high_price=63_000.0,
            low_price=60_000.0,
            close_price=62_500.0,
            atr=1_000.0,
        ),
    )

    assert not decision.should_exit
    assert (
        decision.trailing_stop_price
        == 61_000.0
    )
    assert (
        decision.effective_stop_price
        == 61_000.0
    )


def test_trailing_stop_exit() -> None:
    decision = ExitEngine().evaluate(
        state=make_state(
            highest_price=63_000.0,
            trailing_stop_price=61_000.0,
            trailing_atr_multiplier=2.0,
        ),
        bar=make_bar(
            open_price=61_500.0,
            high_price=62_000.0,
            low_price=60_500.0,
            close_price=60_800.0,
            atr=1_000.0,
        ),
    )

    assert decision.should_exit
    assert (
        decision.reason
        == ExitReason.TRAILING_STOP
    )
    assert (
        decision.execution_price
        == 61_000.0
    )


def test_time_exit() -> None:
    decision = ExitEngine().evaluate(
        state=make_state(
            maximum_holding_days=4,
        ),
        bar=make_bar(),
    )

    assert decision.should_exit
    assert (
        decision.reason
        == ExitReason.TIME_EXIT
    )
    assert (
        decision.execution_price
        == 59_300.0
    )


def test_exit_signal() -> None:
    decision = ExitEngine().evaluate(
        state=make_state(),
        bar=make_bar(),
        exit_signal=True,
    )

    assert decision.should_exit
    assert (
        decision.reason
        == ExitReason.EXIT_SIGNAL
    )


def test_rejects_symbol_mismatch() -> None:
    with pytest.raises(
        ValueError,
        match="không khớp",
    ):
        ExitEngine().evaluate(
            state=make_state(),
            bar=make_bar(
                symbol="HPG",
            ),
        )
