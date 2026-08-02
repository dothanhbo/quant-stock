from itertools import product

from research.parameter_space import (
    ParameterSpace,
)


def generate_parameter_sets(
    parameter_space: ParameterSpace,
):
    for values in product(
        parameter_space.stop_loss_values,
        parameter_space.take_profit_values,
        parameter_space.holding_days_values,
        parameter_space.adx_values,
    ):
        yield {
            "stop_loss_pct": values[0],
            "take_profit_pct": values[1],
            "max_holding_days": values[2],
            "min_adx": values[3],
        }