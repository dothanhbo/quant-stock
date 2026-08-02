from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParameterSpace:
    stop_loss_values: list[float]
    take_profit_values: list[float]
    holding_days_values: list[int]
    adx_values: list[float]


DEFAULT_PARAMETER_SPACE = ParameterSpace(
    stop_loss_values=[
        3,
        5,
        7,
        10,
    ],
    take_profit_values=[
        8,
        10,
        12,
        15,
        20,
    ],
    holding_days_values=[
        10,
        20,
        30,
        40,
    ],
    adx_values=[
        20,
        25,
        30,
        35,
    ],
)