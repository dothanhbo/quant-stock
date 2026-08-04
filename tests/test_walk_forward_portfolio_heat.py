import pandas as pd

from research.walk_forward_portfolio_heat import (
    heat_label,
    parse_heat_levels,
    summarize_by_heat,
)


def test_parse_heat_levels():
    result = parse_heat_levels(
        "unlimited,3,4,5"
    )

    assert result == [
        None,
        3.0,
        4.0,
        5.0,
    ]


def test_heat_label():
    assert heat_label(None) == "unlimited"
    assert heat_label(3.0) == "heat_3_0"


def test_summarize_by_heat():
    windows = pd.DataFrame(
        [
            {
                "heat_label": "heat_3_0",
                "max_portfolio_heat_pct": 3.0,
                "enough_trades": True,
                "total_trades": 20,
                "rejected_by_heat": 5,
                "total_return_pct": 10.0,
                "sharpe_ratio": 1.2,
                "max_drawdown_pct": -6.0,
                "profit_factor": 1.4,
                "peak_portfolio_heat_pct": 2.9,
            },
            {
                "heat_label": "heat_3_0",
                "max_portfolio_heat_pct": 3.0,
                "enough_trades": True,
                "total_trades": 18,
                "rejected_by_heat": 4,
                "total_return_pct": 3.0,
                "sharpe_ratio": 0.7,
                "max_drawdown_pct": -8.0,
                "profit_factor": 1.2,
                "peak_portfolio_heat_pct": 3.0,
            },
            {
                "heat_label": "heat_3_0",
                "max_portfolio_heat_pct": 3.0,
                "enough_trades": True,
                "total_trades": 15,
                "rejected_by_heat": 3,
                "total_return_pct": -1.0,
                "sharpe_ratio": -0.1,
                "max_drawdown_pct": -9.0,
                "profit_factor": 0.9,
                "peak_portfolio_heat_pct": 2.8,
            },
        ]
    )

    result = summarize_by_heat(
        windows
    )

    row = result.iloc[0]

    assert row["windows_total"] == 3
    assert row["positive_windows"] == 2
    assert (
        row[
            "positive_window_rate_pct"
        ]
        == 66.66666666666666
    )
    assert row["total_oos_trades"] == 53
    assert row["total_heat_rejections"] == 12
    assert bool(row["robust"]) is True
