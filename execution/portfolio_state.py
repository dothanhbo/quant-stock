from __future__ import annotations

from dataclasses import dataclass, field

from execution.models import (
    PortfolioSnapshot,
    Position,
)


@dataclass(slots=True)
class PortfolioState:
    initial_cash: float
    cash: float
    positions: dict[str, Position] = field(
        default_factory=dict
    )
    realized_pnl: float = 0.0

    def get_position(
        self,
        symbol: str,
    ) -> Position | None:
        return self.positions.get(
            symbol.strip().upper()
        )

    def get_positions(
        self,
    ) -> list[Position]:
        return [
            position
            for position
            in self.positions.values()
            if position.quantity > 0
        ]

    def remove_empty_positions(
        self,
    ) -> None:
        empty_symbols = [
            symbol
            for symbol, position
            in self.positions.items()
            if position.quantity <= 0
        ]

        for symbol in empty_symbols:
            self.positions.pop(
                symbol,
                None,
            )

    def snapshot(
        self,
    ) -> PortfolioSnapshot:
        positions = self.get_positions()

        positions_value = sum(
            position.market_value
            for position in positions
        )

        unrealized_pnl = sum(
            position.unrealized_pnl
            for position in positions
        )

        equity = (
            self.cash
            + positions_value
        )

        gross_exposure_pct = (
            positions_value
            / equity
            * 100
            if equity > 0
            else 0.0
        )

        return PortfolioSnapshot(
            cash=self.cash,
            positions_value=positions_value,
            equity=equity,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=unrealized_pnl,
            gross_exposure_pct=(
                gross_exposure_pct
            ),
            open_positions=len(
                positions
            ),
        )
