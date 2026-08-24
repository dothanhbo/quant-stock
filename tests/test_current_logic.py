import math
from types import SimpleNamespace

from backtesting.current_logic import CurrentScannerExitModel


def test_current_logic_exit_uses_regime_config() -> None:
    row = {
        "Market_Regime": "BULL",
        "ATR14": 2.0,
        "EMA20": 98.0,
    }

    stop, target = CurrentScannerExitModel().calculate_levels(
        entry_price=100.0,
        entry_row=row,
        config=SimpleNamespace(),
    )

    assert math.isclose(stop, 97.02)
    assert math.isclose(target, 107.45)
