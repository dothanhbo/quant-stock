import pandas as pd
import pytest

from backtesting.benchmark import (
    calculate_buy_and_hold_benchmark,
)


def test_buy_and_hold_benchmark():
    prices = pd.DataFrame(
        {
            "time": [
                "2020-01-01",
                "2021-01-01",
                "2022-01-01",
            ],
            "close": [
                100,
                120,
                150,
            ],
        }
    )

    result = calculate_buy_and_hold_benchmark(
        prices,
        initial_capital=1_000_000,
    )

    assert result["benchmark_return_pct"] == pytest.approx(
        50.0
    )

    assert result["benchmark_final_equity"] == pytest.approx(
        1_500_000
    )

    assert result["benchmark_start_price"] == 100
    assert result["benchmark_end_price"] == 150


def test_buy_and_hold_benchmark_empty():
    result = calculate_buy_and_hold_benchmark(
        pd.DataFrame(),
        initial_capital=1_000_000,
    )

    assert result["benchmark_return_pct"] == 0.0
    assert result["benchmark_final_equity"] == 1_000_000