from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backtesting.trade import ExitReason, ExitExecution


@dataclass(slots=True)
class ExitResult:
    entry_index: int
    exit_index: int

    entry_date: datetime
    exit_date: datetime

    entry_date: Timestamp
    exit_date: Timestamp

    entry_price: float
    exit_price: float

    stop_price: float
    target_price: float

    exit_reason: ExitReason
    execution: ExitExecution = ExitExecution.NORMAL

@property
def holding_days(self):
    return self.exit_index - self.entry_index + 1

@property
def return_pct(self):
    return (
        (self.exit_price - self.entry_price)
        / self.entry_price
    ) * 100