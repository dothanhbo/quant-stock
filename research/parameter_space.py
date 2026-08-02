from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParameterSpace:
    stop_loss_values: tuple[float, ...]
    take_profit_values: tuple[float, ...]
    max_holding_days_values: tuple[int, ...]
    min_adx_values: tuple[float, ...]
    break_even_trigger_values: tuple[float, ...]


DEFAULT_PARAMETER_SPACE = ParameterSpace(
    stop_loss_values=(
        3.0,
        5.0,
        7.0,
        10.0,
    ),
    take_profit_values=(
        8.0,
        10.0,
        12.0,
        15.0,
        20.0,
    ),
    max_holding_days_values=(
        10,
        20,
        30,
        40,
    ),
    min_adx_values=(
        20.0,
        25.0,
        30.0,
        35.0,
    ),
    break_even_trigger_values=(
        3.0,
        5.0,
        7.0,
        10.0,
        15.0,
    ),
)

TRAILING_ATR_STOP_VALUES = (
    1.5,
    2.0,
    2.5,
)

TRAILING_ATR_TARGET_VALUES = (
    3.0,
    4.0,
    5.0,
)

TRAILING_ATR_VALUES = (
    1.5,
    2.0,
    2.5,
    3.0,
)