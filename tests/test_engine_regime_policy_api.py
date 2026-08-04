import inspect

from backtesting.engine import (
    run_backtest,
)


def test_run_backtest_accepts_regime_policy():
    signature = inspect.signature(
        run_backtest
    )

    assert (
        "regime_policy"
        in signature.parameters
    )

    parameter = signature.parameters[
        "regime_policy"
    ]

    assert parameter.default is None
