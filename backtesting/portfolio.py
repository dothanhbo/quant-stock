from __future__ import annotations

from datetime import datetime

from backtesting.trade import ExitExecution, ExitReason, Trade
from backtesting.transaction_cost import (
    TransactionCostConfig,
    apply_buy_slippage,
    apply_sell_slippage,
    calculate_buy_cost,
    calculate_sell_cost,
)

class PortfolioError(Exception):
    """Base exception for portfolio operations."""

class InsufficientCashError(PortfolioError):
    """Raised when portfolio cash is insufficient to open a position."""


class PositionNotFoundError(PortfolioError):
    """Raised when an open position cannot be found."""


class DuplicatePositionError(PortfolioError):
    """Raised when attempting to open a duplicate symbol position."""


class Portfolio:
    def __init__(
        self,
        initial_cash: float = 1_000_000_000,
        allow_duplicate_symbols: bool = False,
        transaction_cost_config: TransactionCostConfig | None = None,
    ) -> None:
        if initial_cash <= 0:
            raise ValueError("initial_cash must be greater than 0")

        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.allow_duplicate_symbols = allow_duplicate_symbols

        self.open_positions: list[Trade] = []
        self.closed_positions: list[Trade] = []

        self.transaction_cost_config = (
            transaction_cost_config
            or TransactionCostConfig(
                buy_commission_pct=0.0,
                sell_commission_pct=0.0,
                sell_tax_pct=0.0,
            )
        )

    def open_position(
        self,
        symbol: str,
        entry_date: datetime,
        entry_price: float,
        quantity: int,
    ) -> Trade:
        if not symbol or not symbol.strip():
            raise ValueError("symbol must not be empty")

        if entry_price <= 0:
            raise ValueError("entry_price must be greater than 0")

        if quantity <= 0:
            raise ValueError("quantity must be greater than 0")

        symbol = symbol.upper().strip()

        if (
            not self.allow_duplicate_symbols
            and self.get_open_position(symbol) is not None
        ):
            raise DuplicatePositionError(
                f"An open position already exists for {symbol}"
            )

        effective_entry_price = apply_buy_slippage(
            price=entry_price,
            config=self.transaction_cost_config,
        )

        buy_cost = calculate_buy_cost(
            price=effective_entry_price,
            quantity=quantity,
            config=self.transaction_cost_config,
        )

        position_cost = buy_cost.net_value

        if position_cost > self.cash:
            raise InsufficientCashError(
                f"Insufficient cash to open {symbol}: "
                f"required={position_cost:.2f}, available={self.cash:.2f}"
            )

        trade = Trade(
            symbol=symbol,
            entry_date=entry_date,
            entry_price=float(effective_entry_price),
            quantity=quantity,
            buy_commission=buy_cost.commission,
        )

        self.cash -= position_cost
        self.open_positions.append(trade)

        return trade

    def close_position(
        self,
        symbol: str,
        exit_date: datetime,
        exit_price: float,
        reason: ExitReason,
        execution: ExitExecution = ExitExecution.NORMAL,
    ) -> Trade:
        if exit_price <= 0:
            raise ValueError("exit_price must be greater than 0")

        symbol = symbol.upper().strip()
        trade = self.get_open_position(symbol)

        if trade is None:
            raise PositionNotFoundError(
                f"No open position found for {symbol}"
            )

        effective_exit_price = apply_sell_slippage(
            price=exit_price,
            config=self.transaction_cost_config,
        )

        trade.close(
            exit_date=exit_date,
            exit_price=float(effective_exit_price),
            reason=reason,
            execution=execution,
        )

        sell_cost = calculate_sell_cost(
            price=effective_exit_price,
            quantity=trade.quantity,
            config=self.transaction_cost_config,
        ) 

        trade.sell_commission = sell_cost.commission
        trade.sell_tax = sell_cost.tax

        self.cash += sell_cost.net_value

        self.open_positions.remove(trade)
        self.closed_positions.append(trade)

        return trade

    def get_open_position(self, symbol: str) -> Trade | None:
        symbol = symbol.upper().strip()

        for trade in self.open_positions:
            if trade.symbol.upper() == symbol:
                return trade

        return None

    def has_open_position(self, symbol: str) -> bool:
        return self.get_open_position(symbol) is not None

    def market_value(
        self,
        current_prices: dict[str, float] | None = None,
    ) -> float:
        """
        Calculate current market value of all open positions.

        If current_prices is omitted, entry prices are used.
        """
        current_prices = current_prices or {}
        total = 0.0

        for trade in self.open_positions:
            current_price = current_prices.get(
                trade.symbol,
                trade.entry_price,
            )

            total += current_price * trade.quantity

        return total

    def equity(
        self,
        current_prices: dict[str, float] | None = None,
    ) -> float:
        return self.cash + self.market_value(current_prices)

    def unrealized_pnl(
        self,
        current_prices: dict[str, float] | None = None,
    ) -> float:
        current_prices = current_prices or {}
        total = 0.0

        for trade in self.open_positions:
            current_price = current_prices.get(
                trade.symbol,
                trade.entry_price,
            )

            total += (
                current_price - trade.entry_price
            ) * trade.quantity

        return total

    @property
    def realized_pnl(self) -> float:
        return sum(trade.pnl for trade in self.closed_positions)

    @property
    def total_invested_cost(self) -> float:
        return sum(trade.cost for trade in self.open_positions)

    @property
    def total_trades(self) -> int:
        return len(self.closed_positions)

    @property
    def winning_trades(self) -> int:
        return sum(
            1 for trade in self.closed_positions
            if trade.is_win
        )

    @property
    def losing_trades(self) -> int:
        return sum(
            1 for trade in self.closed_positions
            if trade.pnl < 0
        )

    @property
    def win_rate(self) -> float:
        if not self.closed_positions:
            return 0.0

        return (
            self.winning_trades
            / len(self.closed_positions)
        ) * 100

    def summary(
        self,
        current_prices: dict[str, float] | None = None,
    ) -> dict:
        return {
            "initial_cash": self.initial_cash,
            "cash": self.cash,
            "market_value": self.market_value(current_prices),
            "equity": self.equity(current_prices),
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl(current_prices),
            "open_positions": len(self.open_positions),
            "closed_positions": len(self.closed_positions),
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": self.win_rate,
        }