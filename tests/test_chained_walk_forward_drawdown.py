import pandas as pd
import pytest

from backtesting.walk_forward import calculate_chained_drawdown_pct


def test_chained_drawdown_preserves_peak_across_folds() -> None:
    first = pd.DataFrame({"equity": [100.0, 150.0, 140.0]})
    second = pd.DataFrame({"equity": [140.0, 120.0, 130.0]})

    result = calculate_chained_drawdown_pct([first, second])

    assert result == pytest.approx(-20.0)


def test_chained_drawdown_handles_empty_curves() -> None:
    assert calculate_chained_drawdown_pct([]) == 0.0
    assert calculate_chained_drawdown_pct([pd.DataFrame()]) == 0.0
