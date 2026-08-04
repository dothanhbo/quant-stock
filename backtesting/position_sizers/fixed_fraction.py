from __future__ import annotations

from dataclasses import dataclass

from backtesting.position_sizers.base import (
    PositionSizer,
    PositionSizingContext,
)


@dataclass(slots=True, frozen=True)
class FixedFractionSizer(PositionSizer):
    """
    Allocate a fixed percentage of current portfolio equity to each trade.

    This reproduces the PortfolioSimulator sizing logic used before Sprint 3.
    """

    position_size_pct: float = 20.0

    def __post_init__(self) -> None:
        if not 0 < self.position_size_pct <= 100:
            raise ValueError(
                "position_size_pct phải nằm trong khoảng (0, 100]."
            )

    @property
    def name(self) -> str:
        return "fixed_fraction"

    def calculate_quantity(
        self,
        context: PositionSizingContext,
    ) -> int:
        entry_price = float(
            context.candidate.entry_price
        )

        if entry_price <= 0:
            return 0

        allocated_cash = (
            context.equity
            * self.position_size_pct
            / 100
        )

        usable_cash = min(
            allocated_cash,
            context.cash,
        )

        if usable_cash <= 0:
            return 0

        costs = (
            context.transaction_cost_config
        )

        effective_entry_price = (
            entry_price
            * (
                1
                + costs.buy_slippage_pct
                / 100
            )
        )

        buy_fee_rate = (
            costs.buy_commission_pct
            / 100
        )

        total_price_per_share = (
            effective_entry_price
            * (1 + buy_fee_rate)
        )

        if total_price_per_share <= 0:
            return 0

        raw_quantity = int(
            usable_cash
            / total_price_per_share
        )

        return (
            raw_quantity
            // context.lot_size
        ) * context.lot_size
