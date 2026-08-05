from __future__ import annotations

from abc import ABC, abstractmethod

from execution.models import (
    Fill,
    Order,
    PortfolioSnapshot,
    Position,
)


class BrokerInterface(ABC):
    @abstractmethod
    def submit_order(
        self,
        order: Order,
    ) -> Fill | None:
        """Submit an order and return a fill when execution succeeds."""

    @abstractmethod
    def cancel_order(
        self,
        client_order_id: str,
    ) -> bool:
        """Cancel a pending order."""

    @abstractmethod
    def get_cash(self) -> float:
        """Return available cash."""

    @abstractmethod
    def get_position(
        self,
        symbol: str,
    ) -> Position | None:
        """Return one position."""

    @abstractmethod
    def get_positions(
        self,
    ) -> list[Position]:
        """Return all open positions."""

    @abstractmethod
    def get_open_orders(
        self,
    ) -> list[Order]:
        """Return pending or accepted orders."""

    @abstractmethod
    def update_market_price(
        self,
        symbol: str,
        price: float,
    ) -> None:
        """Update the latest market price."""

    @abstractmethod
    def get_portfolio_snapshot(
        self,
    ) -> PortfolioSnapshot:
        """Return current portfolio state."""
