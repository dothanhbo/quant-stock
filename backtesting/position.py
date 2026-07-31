from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class PositionStatus(Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


@dataclass
class Position:
    symbol: str

    quantity: int

    entry_date: datetime
    entry_price: float

    stop_price: float
    target_price: float

    exit_date: Optional[datetime] = None
    exit_price: Optional[float] = None

    status: PositionStatus = PositionStatus.OPEN

    @property
    def is_open(self) -> bool:
        return self.status == PositionStatus.OPEN

    @property
    def unrealized_pnl(self) -> float:
        if self.exit_price is None:
            return 0.0

        return (self.exit_price - self.entry_price) * self.quantity

    def close(
        self,
        exit_date: datetime,
        exit_price: float,
    ):
        self.exit_date = exit_date
        self.exit_price = exit_price
        self.status = PositionStatus.CLOSED