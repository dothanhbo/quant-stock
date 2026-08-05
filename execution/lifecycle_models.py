from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from execution.exit_models import (
    ExitReason,
)


@dataclass(frozen=True, slots=True)
class PositionLifecycleState:
    symbol: str
    entry_date: date
    entry_price: float
    initial_quantity: int
    stop_price: float
    take_profit_price: float | None = None
    highest_price: float | None = None
    trailing_stop_price: float | None = None
    trailing_atr_multiplier: float | None = None
    maximum_holding_days: int | None = None
    updated_at: datetime | None = None

    def __post_init__(
        self,
    ) -> None:
        symbol = self.symbol.strip().upper()

        if not symbol:
            raise ValueError(
                "symbol không được để trống."
            )

        object.__setattr__(
            self,
            "symbol",
            symbol,
        )

        if self.entry_price <= 0:
            raise ValueError(
                "entry_price phải lớn hơn 0."
            )

        if self.initial_quantity <= 0:
            raise ValueError(
                "initial_quantity phải lớn hơn 0."
            )

        if not (
            0
            < self.stop_price
            < self.entry_price
        ):
            raise ValueError(
                "stop_price phải nằm trong "
                "(0, entry_price)."
            )

        if (
            self.take_profit_price is not None
            and self.take_profit_price
            <= self.entry_price
        ):
            raise ValueError(
                "take_profit_price phải lớn hơn "
                "entry_price."
            )

        if (
            self.highest_price is not None
            and self.highest_price <= 0
        ):
            raise ValueError(
                "highest_price phải lớn hơn 0."
            )

        if (
            self.trailing_stop_price is not None
            and self.trailing_stop_price <= 0
        ):
            raise ValueError(
                "trailing_stop_price phải lớn hơn 0."
            )

        if (
            self.trailing_atr_multiplier is not None
            and self.trailing_atr_multiplier <= 0
        ):
            raise ValueError(
                "trailing_atr_multiplier phải "
                "lớn hơn 0."
            )

        if (
            self.maximum_holding_days is not None
            and self.maximum_holding_days <= 0
        ):
            raise ValueError(
                "maximum_holding_days phải "
                "lớn hơn 0."
            )


@dataclass(frozen=True, slots=True)
class ClosedPaperTrade:
    symbol: str
    entry_date: date
    exit_date: date
    quantity: int
    entry_price: float
    exit_price: float
    gross_proceeds: float
    commission: float
    realized_pnl: float
    return_pct: float
    holding_days: int
    exit_reason: ExitReason
    order_id: str
    created_at: datetime
