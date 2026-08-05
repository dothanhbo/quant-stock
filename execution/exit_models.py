from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class ExitReason(str, Enum):
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    TRAILING_STOP = "TRAILING_STOP"
    TIME_EXIT = "TIME_EXIT"
    EXIT_SIGNAL = "EXIT_SIGNAL"


class SameBarExitPolicy(str, Enum):
    """
    Rule used when one daily candle touches both stop and target.

    CONSERVATIVE_STOP assumes the stop is hit first.
    OPTIMISTIC_TARGET assumes the target is hit first.
    """
    CONSERVATIVE_STOP = (
        "CONSERVATIVE_STOP"
    )
    OPTIMISTIC_TARGET = (
        "OPTIMISTIC_TARGET"
    )


@dataclass(frozen=True, slots=True)
class ExitBar:
    symbol: str
    valuation_date: date
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    atr: float | None = None

    def __post_init__(
        self,
    ) -> None:
        symbol = (
            self.symbol
            .strip()
            .upper()
        )

        if not symbol:
            raise ValueError(
                "symbol không được để trống."
            )

        object.__setattr__(
            self,
            "symbol",
            symbol,
        )

        prices = {
            "open_price": self.open_price,
            "high_price": self.high_price,
            "low_price": self.low_price,
            "close_price": self.close_price,
        }

        for name, value in prices.items():
            if value <= 0:
                raise ValueError(
                    f"{name} phải lớn hơn 0."
                )

        if self.high_price < self.low_price:
            raise ValueError(
                "high_price không được nhỏ hơn "
                "low_price."
            )

        if not (
            self.low_price
            <= self.open_price
            <= self.high_price
        ):
            raise ValueError(
                "open_price phải nằm trong "
                "biên low/high."
            )

        if not (
            self.low_price
            <= self.close_price
            <= self.high_price
        ):
            raise ValueError(
                "close_price phải nằm trong "
                "biên low/high."
            )

        if (
            self.atr is not None
            and self.atr <= 0
        ):
            raise ValueError(
                "atr phải lớn hơn 0."
            )


@dataclass(frozen=True, slots=True)
class PositionExitState:
    symbol: str
    entry_date: date
    entry_price: float
    quantity: int
    stop_price: float
    take_profit_price: float | None = None
    highest_price: float | None = None
    trailing_stop_price: float | None = None
    trailing_atr_multiplier: float | None = None
    maximum_holding_days: int | None = None

    def __post_init__(
        self,
    ) -> None:
        symbol = (
            self.symbol
            .strip()
            .upper()
        )

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

        if self.quantity <= 0:
            raise ValueError(
                "quantity phải lớn hơn 0."
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
            self.take_profit_price
            is not None
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
            self.trailing_stop_price
            is not None
            and self.trailing_stop_price <= 0
        ):
            raise ValueError(
                "trailing_stop_price phải lớn hơn 0."
            )

        if (
            self.trailing_atr_multiplier
            is not None
            and self.trailing_atr_multiplier <= 0
        ):
            raise ValueError(
                "trailing_atr_multiplier phải "
                "lớn hơn 0."
            )

        if (
            self.maximum_holding_days
            is not None
            and self.maximum_holding_days <= 0
        ):
            raise ValueError(
                "maximum_holding_days phải "
                "lớn hơn 0."
            )

    @property
    def current_highest_price(
        self,
    ) -> float:
        if self.highest_price is None:
            return self.entry_price

        return max(
            self.entry_price,
            self.highest_price,
        )

    @property
    def holding_days(
        self,
    ) -> int:
        return 0


@dataclass(frozen=True, slots=True)
class ExitDecision:
    should_exit: bool
    reason: ExitReason | None
    trigger_price: float | None
    execution_price: float | None
    valuation_date: date
    highest_price: float
    effective_stop_price: float
    trailing_stop_price: float | None
    holding_days: int
    details: str = ""

    @classmethod
    def hold(
        cls,
        *,
        valuation_date: date,
        highest_price: float,
        effective_stop_price: float,
        trailing_stop_price: float | None,
        holding_days: int,
        details: str = "",
    ) -> "ExitDecision":
        return cls(
            should_exit=False,
            reason=None,
            trigger_price=None,
            execution_price=None,
            valuation_date=valuation_date,
            highest_price=highest_price,
            effective_stop_price=(
                effective_stop_price
            ),
            trailing_stop_price=(
                trailing_stop_price
            ),
            holding_days=holding_days,
            details=details,
        )
