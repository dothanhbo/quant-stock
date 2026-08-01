from datetime import datetime

from backtesting.trade import ExitReason, Trade
from backtesting.trade_distribution import (
    calculate_trade_distribution,
)


def create_trade(
    *,
    entry_price: float,
    exit_price: float,
    entry_date: datetime,
    exit_date: datetime,
    reason: ExitReason,
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
        reason=reason,
    )

    return trade


def test_trade_distribution():
    trades = [
        create_trade(
            entry_price=100,
            exit_price=85,
            entry_date=datetime(2026, 1, 1),
            exit_date=datetime(2026, 1, 1),
            reason=ExitReason.STOP_LOSS,
        ),
        create_trade(
            entry_price=100,
            exit_price=93,
            entry_date=datetime(2026, 2, 1),
            exit_date=datetime(2026, 2, 4),
            reason=ExitReason.STOP_LOSS,
        ),
        create_trade(
            entry_price=100,
            exit_price=98,
            entry_date=datetime(2026, 3, 1),
            exit_date=datetime(2026, 3, 9),
            reason=ExitReason.TIME_EXIT,
        ),
        create_trade(
            entry_price=100,
            exit_price=103,
            entry_date=datetime(2026, 4, 1),
            exit_date=datetime(2026, 4, 13),
            reason=ExitReason.TIME_EXIT,
        ),
        create_trade(
            entry_price=100,
            exit_price=108,
            entry_date=datetime(2026, 5, 1),
            exit_date=datetime(2026, 5, 26),
            reason=ExitReason.TAKE_PROFIT,
        ),
        create_trade(
            entry_price=100,
            exit_price=125,
            entry_date=datetime(2026, 6, 1),
            exit_date=datetime(2026, 7, 10),
            reason=ExitReason.TAKE_PROFIT,
        ),
    ]

    result = calculate_trade_distribution(trades)

    assert result["profit_distribution"] == {
        "<=-10%": 1,
        "-10% to -5%": 1,
        "-5% to 0%": 1,
        "0% to 5%": 1,
        "5% to 10%": 1,
        "10% to 20%": 0,
        ">20%": 1,
    }

    assert result["holding_distribution"] == {
        "0 days": 1,
        "1-5 days": 1,
        "6-10 days": 1,
        "11-20 days": 1,
        "21+ days": 2,
    }

    assert result["exit_reason_distribution"] == {
        "Stop Loss": 2,
        "Take Profit": 2,
        "Time Exit": 2,
    }


def test_trade_distribution_empty():
    result = calculate_trade_distribution([])

    assert result["profit_distribution"] == {
        "<=-10%": 0,
        "-10% to -5%": 0,
        "-5% to 0%": 0,
        "0% to 5%": 0,
        "5% to 10%": 0,
        "10% to 20%": 0,
        ">20%": 0,
    }

    assert result["holding_distribution"] == {
        "0 days": 0,
        "1-5 days": 0,
        "6-10 days": 0,
        "11-20 days": 0,
        "21+ days": 0,
    }

    assert result["exit_reason_distribution"] == {}