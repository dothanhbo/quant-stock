import pandas as pd

from research.run_bull_filter_wfo import BullFilterCase, allow_entry


def _row(**values):
    return pd.Series(values)


def test_market_overheat_blocks_only_bull_entries():
    case = BullFilterCase(
        case_id="test",
        description="test",
        block_market_return_20d_ge=7.0,
    )
    assert not allow_entry(
        case,
        _row(Market_Regime="BULL", Index_Return_20D=7.0),
        {"regime": "BULL", "volume_ratio": 1.0},
    )
    assert allow_entry(
        case,
        _row(Market_Regime="SIDEWAY", Index_Return_20D=20.0),
        {"regime": "SIDEWAY", "volume_ratio": 1.0},
    )


def test_volume_climax_is_only_applied_when_case_requests_it():
    base = BullFilterCase(case_id="base", description="base")
    guarded = BullFilterCase(
        case_id="guarded",
        description="guarded",
        block_volume_ratio_ge=2.5,
    )
    row = _row(Market_Regime="BULL", Index_Return_20D=1.0)
    evaluation = {"regime": "BULL", "volume_ratio": 2.5}
    assert allow_entry(base, row, evaluation)
    assert not allow_entry(guarded, row, evaluation)
