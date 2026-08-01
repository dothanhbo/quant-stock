from datetime import datetime

import pytest

from backtesting.trade import ExitReason, Trade
from backtesting.trade_analytics import (
    calculate_trade_analytics,
)


def create_trade(
    *,
    entry_price: float,
    exit_price: float,
    entry_date: datetime,
    exit_date: datetime,
) -> Trade:
    trade = Trade(
        symbol="TEST",
        entry_date=entry_date,
        entry_price=entry_price,
        quantity=100,
    )

    trade.close(
        exit_date=exit_date,
        exit_price=exit_price,
        reason=(
            ExitReason.TAKE_PROFIT
            if exit_price > entry_price
            else ExitReason.STOP_LOSS
        ),
    )

    return trade


def test_trade_analytics_core_metrics():
    trades = [
        create_trade(
            entry_price=100,
            exit_price=110,
            entry_date=datetime(2026, 1, 1),
            exit_date=datetime(2026, 1, 6),
        ),
        create_trade(
            entry_price=100,
            exit_price=105,
            entry_date=datetime(2026, 2, 1),
            exit_date=datetime(2026, 2, 11),
        ),
        create_trade(
            entry_price=100,
            exit_price=95,
            entry_date=datetime(2026, 3, 1),
            exit_date=datetime(2026, 3, 21),
        ),
    ]

    result = calculate_trade_analytics(trades)

    assert result["expectancy_pct"] == pytest.approx(
        (10 + 5 - 5) / 3
    )

    assert result["expectancy_amount"] == pytest.approx(
        (1000 + 500 - 500) / 3
    )

    assert result["average_win_pct"] == pytest.approx(
        7.5
    )

    assert result["average_loss_pct"] == pytest.approx(
        -5.0
    )

    assert result["average_win_amount"] == pytest.approx(
        750.0
    )

    assert result["average_loss_amount"] == pytest.approx(
        -500.0
    )

    assert result["average_holding_days"] == pytest.approx(
        (5 + 10 + 20) / 3
    )

    assert result["median_holding_days"] == 10
    assert result["max_holding_days"] == 20
    assert result["min_holding_days"] == 5


def test_trade_analytics_empty_trades():
    result = calculate_trade_analytics([])

    assert result["expectancy_pct"] == 0.0
    assert result["expectancy_amount"] == 0.0
    assert result["average_holding_days"] == 0.0