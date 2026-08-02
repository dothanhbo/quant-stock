from __future__ import annotations

from itertools import product

from research.parameter_space import ParameterSpace


def generate_parameter_sets(
    parameter_space: ParameterSpace,
):
    for (
        stop_loss_pct,
        take_profit_pct,
        max_holding_days,
        min_adx,
        break_even_trigger,
    ) in product(
        parameter_space.stop_loss_values,
        parameter_space.take_profit_values,
        parameter_space.max_holding_days_values,
        parameter_space.min_adx_values,
        parameter_space.break_even_trigger_values,
    ):
        yield {
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "max_holding_days": max_holding_days,
            "min_adx": min_adx,
            "break_even_trigger": break_even_trigger,
        }