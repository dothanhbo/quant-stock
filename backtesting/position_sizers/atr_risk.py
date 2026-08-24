from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from backtesting.position_sizers.base import (
    PositionSizer,
    PositionSizingContext,
)


@dataclass(slots=True, frozen=True)
class AtrRiskSizer(PositionSizer):
    """
    Size each trade from a fixed portfolio-risk budget.

    Quantity is based on:

        risk_budget / stop_distance

    where:

        risk_budget = equity * risk_per_trade_pct
        stop_distance = ATR * atr_stop_multiplier

    The resulting quantity is capped by:
    - available cash,
    - max_position_size_pct,
    - configured lot size.
    """

    risk_per_trade_pct: float = 1.0
    atr_stop_multiplier: float = 2.0
    max_position_size_pct: float = 20.0
    atr_attribute: str = "atr"
    use_candidate_stop: bool = True

    def __post_init__(self) -> None:
        if not 0 < self.risk_per_trade_pct <= 100:
            raise ValueError(
                "risk_per_trade_pct phải nằm trong khoảng (0, 100]."
            )

        if self.atr_stop_multiplier <= 0:
            raise ValueError(
                "atr_stop_multiplier phải lớn hơn 0."
            )

        if not 0 < self.max_position_size_pct <= 100:
            raise ValueError(
                "max_position_size_pct phải nằm trong khoảng (0, 100]."
            )

        if not self.atr_attribute.strip():
            raise ValueError(
                "atr_attribute không được rỗng."
            )

    @property
    def name(self) -> str:
        return "atr_risk"

    def _get_atr(
        self,
        context: PositionSizingContext,
    ) -> float | None:
        value = getattr(
            context.candidate,
            self.atr_attribute,
            None,
        )

        try:
            atr = float(value)

        except (
            TypeError,
            ValueError,
        ):
            return None

        if (
            not isfinite(atr)
            or atr <= 0
        ):
            return None

        return atr

    def _max_affordable_quantity(
        self,
        context: PositionSizingContext,
    ) -> int:
        entry_price = float(
            context.candidate.entry_price
        )

        if entry_price <= 0:
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

        max_position_cash = (
            context.equity
            * self.max_position_size_pct
            / 100
        )

        usable_cash = min(
            context.cash,
            max_position_cash,
        )

        raw_quantity = int(
            usable_cash
            / total_price_per_share
        )

        return (
            raw_quantity
            // context.lot_size
        ) * context.lot_size

    def calculate_quantity(
        self,
        context: PositionSizingContext,
    ) -> int:
        atr = self._get_atr(
            context
        )

        if atr is None:
            return 0

        stop_distance = None
        if self.use_candidate_stop:
            try:
                entry_price = float(context.candidate.entry_price)
                stop_price = float(context.candidate.stop_price)
                candidate_distance = entry_price - stop_price
                if isfinite(candidate_distance) and candidate_distance > 0:
                    stop_distance = candidate_distance
            except (TypeError, ValueError):
                stop_distance = None

        if stop_distance is None:
            stop_distance = atr * self.atr_stop_multiplier

        if stop_distance <= 0:
            return 0

        risk_budget = (
            context.equity
            * self.risk_per_trade_pct
            / 100
        )

        raw_risk_quantity = int(
            risk_budget
            / stop_distance
        )

        risk_quantity = (
            raw_risk_quantity
            // context.lot_size
        ) * context.lot_size

        affordable_quantity = (
            self._max_affordable_quantity(
                context
            )
        )

        return max(
            min(
                risk_quantity,
                affordable_quantity,
            ),
            0,
        )
