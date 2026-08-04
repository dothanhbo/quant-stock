import inspect

from backtesting.engine import (
    run_backtest,
)


def test_run_backtest_accepts_heat_limit():
    signature = inspect.signature(
        run_backtest
    )

    assert (
        "max_portfolio_heat_pct"
        in signature.parameters
    )

    parameter = signature.parameters[
        "max_portfolio_heat_pct"
    ]

    assert parameter.default is None
