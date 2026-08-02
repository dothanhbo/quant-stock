from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtesting.trade import (
    ExitExecution,
    ExitReason,
)


@dataclass(slots=True)
class ExitResult:
    entry_index: int
    exit_index: int

    entry_date: pd.Timestamp
    exit_date: pd.Timestamp

    entry_price: float
    exit_price: float

    stop_price: float
    target_price: float

    exit_reason: ExitReason

    execution: ExitExecution = (
        ExitExecution.NORMAL
    )

    @property
    def holding_days(self) -> int:
        return (
            self.exit_index
            - self.entry_index
            + 1
        )

    @property
    def return_pct(self) -> float:
        return (
            (
                self.exit_price
                - self.entry_price
            )
            / self.entry_price
        ) * 100