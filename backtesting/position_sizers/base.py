from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from backtesting.trade import Trade
from backtesting.transaction_cost import TransactionCostConfig


@dataclass(slots=True, frozen=True)
class PositionSizingContext:
    """Input required by a position-sizing model."""

    candidate: Trade
    cash: float
    equity: float
    lot_size: int
    transaction_cost_config: TransactionCostConfig

    def __post_init__(self) -> None:
        if self.cash < 0:
            raise ValueError("cash không được âm.")

        if self.equity < 0:
            raise ValueError("equity không được âm.")

        if self.lot_size < 1:
            raise ValueError("lot_size phải từ 1 trở lên.")


class PositionSizer(ABC):
    """Interface for portfolio position-sizing models."""

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def calculate_quantity(
        self,
        context: PositionSizingContext,
    ) -> int:
        """Return an executable quantity rounded to the configured lot size."""
        raise NotImplementedError
