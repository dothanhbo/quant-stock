from research.walk_forward import (
    WalkForwardConfig,
    build_walk_forward_windows,
    summarize_walk_forward,
)

import pandas as pd


def test_build_rolling_windows():
    config = WalkForwardConfig(
        start_date="2020-01-01",
        end_date="2024-07-31",
        train_years=2,
        test_months=12,
        step_months=12,
    )

    windows = build_walk_forward_windows(config)

    assert len(windows) == 3
    assert windows[0].train_start == "2020-01-01"
    assert windows[0].train_end == "2021-12-31"
    assert windows[0].test_start == "2022-01-01"
    assert windows[0].test_end == "2022-12-31"
    assert windows[-1].test_end == "2024-07-31"


def test_build_anchored_windows():
    config = WalkForwardConfig(
        start_date="2020-01-01",
        end_date="2024-07-31",
        train_years=2,
        test_months=12,
        step_months=12,
        anchored=True,
    )

    windows = build_walk_forward_windows(config)

    assert windows[0].train_start == "2020-01-01"
    assert windows[1].train_start == "2020-01-01"
    assert windows[1].train_end == "2022-12-31"


def test_summarize_walk_forward():
    summary = pd.DataFrame(
        [
            {
                "enough_trades": True,
                "total_trades": 10,
                "total_return_pct": 8.0,
                "sharpe_ratio": 1.1,
                "max_drawdown_pct": -6.0,
                "profit_factor": 1.5,
                "win_rate_pct": 55.0,
            },
            {
                "enough_trades": True,
                "total_trades": 12,
                "total_return_pct": 3.0,
                "sharpe_ratio": 0.7,
                "max_drawdown_pct": -8.0,
                "profit_factor": 1.2,
                "win_rate_pct": 51.0,
            },
            {
                "enough_trades": True,
                "total_trades": 8,
                "total_return_pct": -2.0,
                "sharpe_ratio": -0.2,
                "max_drawdown_pct": -10.0,
                "profit_factor": 0.9,
                "win_rate_pct": 43.0,
            },
        ]
    )

    result = summarize_walk_forward(summary)

    assert result["windows_total"] == 3
    assert result["positive_windows"] == 2
    assert result["total_oos_trades"] == 30
    assert result["robust"] is True
