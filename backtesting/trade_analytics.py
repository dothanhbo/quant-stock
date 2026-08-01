from __future__ import annotations

from statistics import median
from typing import Any

from backtesting.trade import Trade


def calculate_trade_analytics(
    trades: list[Trade],
) -> dict[str, Any]:
    if not trades:
        return {
            "expectancy_pct": 0.0,
            "expectancy_amount": 0.0,
            "average_win_pct": 0.0,
            "average_loss_pct": 0.0,
            "average_win_amount": 0.0,
            "average_loss_amount": 0.0,
            "average_holding_days": 0.0,
            "median_holding_days": 0.0,
            "max_holding_days": 0,
            "min_holding_days": 0,
        }

    winning_trades = [
        trade
        for trade in trades
        if trade.net_pnl > 0
    ]

    losing_trades = [
        trade
        for trade in trades
        if trade.net_pnl < 0
    ]

    returns = [
        float(trade.net_return_pct)
        for trade in trades
    ]

    pnl_values = [
        float(trade.net_pnl)
        for trade in trades
    ]

    holding_days = [
        int(trade.holding_days)
        for trade in trades
    ]

    average_win_pct = (
        sum(
            trade.net_return_pct
            for trade in winning_trades
        )
        / len(winning_trades)
        if winning_trades
        else 0.0
    )

    average_loss_pct = (
        sum(
            trade.net_return_pct
            for trade in losing_trades
        )
        / len(losing_trades)
        if losing_trades
        else 0.0
    )

    average_win_amount = (
        sum(
            trade.net_pnl
            for trade in winning_trades
        )
        / len(winning_trades)
        if winning_trades
        else 0.0
    )

    average_loss_amount = (
        sum(
            trade.net_pnl
            for trade in losing_trades
        )
        / len(losing_trades)
        if losing_trades
        else 0.0
    )

    return {
        "expectancy_pct": float(
            sum(returns) / len(returns)
        ),
        "expectancy_amount": float(
            sum(pnl_values) / len(pnl_values)
        ),
        "average_win_pct": float(
            average_win_pct
        ),
        "average_loss_pct": float(
            average_loss_pct
        ),
        "average_win_amount": float(
            average_win_amount
        ),
        "average_loss_amount": float(
            average_loss_amount
        ),
        "average_holding_days": float(
            sum(holding_days) / len(holding_days)
        ),
        "median_holding_days": float(
            median(holding_days)
        ),
        "max_holding_days": int(
            max(holding_days)
        ),
        "min_holding_days": int(
            min(holding_days)
        ),
    }