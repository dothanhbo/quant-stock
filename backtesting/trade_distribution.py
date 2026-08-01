from __future__ import annotations

from collections import Counter
from typing import Any

from backtesting.trade import Trade


def _profit_bucket(return_pct: float) -> str:
    if return_pct <= -10:
        return "<=-10%"
    if return_pct < -5:
        return "-10% to -5%"
    if return_pct < 0:
        return "-5% to 0%"
    if return_pct < 5:
        return "0% to 5%"
    if return_pct < 10:
        return "5% to 10%"
    if return_pct <= 20:
        return "10% to 20%"
    return ">20%"


def _holding_bucket(holding_days: int) -> str:
    if holding_days == 0:
        return "0 days"
    if holding_days <= 5:
        return "1-5 days"
    if holding_days <= 10:
        return "6-10 days"
    if holding_days <= 20:
        return "11-20 days"
    return "21+ days"


def calculate_trade_distribution(
    trades: list[Trade],
) -> dict[str, Any]:
    profit_labels = [
        "<=-10%",
        "-10% to -5%",
        "-5% to 0%",
        "0% to 5%",
        "5% to 10%",
        "10% to 20%",
        ">20%",
    ]

    holding_labels = [
        "0 days",
        "1-5 days",
        "6-10 days",
        "11-20 days",
        "21+ days",
    ]

    profit_counter: Counter[str] = Counter()
    holding_counter: Counter[str] = Counter()
    exit_reason_counter: Counter[str] = Counter()

    for trade in trades:
        profit_counter[
            _profit_bucket(float(trade.net_return_pct))
        ] += 1

        holding_counter[
            _holding_bucket(int(trade.holding_days))
        ] += 1

        reason = (
            trade.exit_reason.value
            if trade.exit_reason is not None
            else "Unknown"
        )

        exit_reason_counter[reason] += 1

    return {
        "profit_distribution": {
            label: int(profit_counter[label])
            for label in profit_labels
        },
        "holding_distribution": {
            label: int(holding_counter[label])
            for label in holding_labels
        },
        "exit_reason_distribution": dict(
            sorted(exit_reason_counter.items())
        ),
    }