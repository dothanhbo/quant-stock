from __future__ import annotations

from dataclasses import dataclass

from execution.models import (
    Order,
    OrderSide,
)
from execution.portfolio_state import (
    PortfolioState,
)


@dataclass(frozen=True, slots=True)
class RiskLimits:
    maximum_position_pct: float = 25.0
    maximum_gross_exposure_pct: float = 80.0
    maximum_open_positions: int = 10
    maximum_daily_loss_pct: float = 3.0
    minimum_cash_buffer_pct: float = 5.0
    allow_duplicate_orders: bool = False
    commission_rate: float = 0.0015


@dataclass(slots=True)
class RiskCheckResult:
    approved: bool
    reason: str = ""


class RiskGuard:
    def __init__(
        self,
        limits: RiskLimits | None = None,
    ) -> None:
        self.limits = (
            limits
            or RiskLimits()
        )
        self.kill_switch_enabled = False

    def enable_kill_switch(
        self,
    ) -> None:
        self.kill_switch_enabled = True

    def disable_kill_switch(
        self,
    ) -> None:
        self.kill_switch_enabled = False

    def validate_order(
        self,
        *,
        order: Order,
        estimated_price: float,
        portfolio: PortfolioState,
        duplicate_order_exists: bool,
        daily_realized_pnl: float = 0.0,
    ) -> RiskCheckResult:
        if self.kill_switch_enabled:
            return RiskCheckResult(
                approved=False,
                reason="Kill switch đang bật.",
            )

        if estimated_price <= 0:
            return RiskCheckResult(
                approved=False,
                reason="Giá ước tính không hợp lệ.",
            )

        if (
            duplicate_order_exists
            and not self.limits.allow_duplicate_orders
        ):
            return RiskCheckResult(
                approved=False,
                reason="Phát hiện lệnh trùng.",
            )

        snapshot = portfolio.snapshot()

        if (
            snapshot.equity > 0
            and daily_realized_pnl
            <= -(
                snapshot.equity
                * self.limits.maximum_daily_loss_pct
                / 100
            )
        ):
            return RiskCheckResult(
                approved=False,
                reason="Đã chạm daily loss limit.",
            )

        if order.side == OrderSide.SELL:
            position = portfolio.get_position(
                order.symbol
            )

            if (
                position is None
                or position.quantity
                < order.quantity
            ):
                return RiskCheckResult(
                    approved=False,
                    reason="Không đủ cổ phiếu để bán.",
                )

            return RiskCheckResult(
                approved=True
            )

        order_value = (
            estimated_price
            * order.quantity
        )

        estimated_commission = (
            order_value
            * self.limits.commission_rate
        )

        required_cash = (
            order_value
            + estimated_commission
        )

        if required_cash > portfolio.cash:
            return RiskCheckResult(
                approved=False,
                reason="Không đủ tiền mặt.",
            )

        if snapshot.equity <= 0:
            return RiskCheckResult(
                approved=False,
                reason="Equity không hợp lệ.",
            )

        current_position = (
            portfolio.get_position(
                order.symbol
            )
        )

        current_market_value = (
            current_position.market_value
            if current_position is not None
            else 0.0
        )

        resulting_position_pct = (
            (
                current_market_value
                + order_value
            )
            / snapshot.equity
            * 100
        )

        if (
            resulting_position_pct
            > self.limits.maximum_position_pct
        ):
            return RiskCheckResult(
                approved=False,
                reason=(
                    "Vượt maximum_position_pct."
                ),
            )

        resulting_exposure_pct = (
            (
                snapshot.positions_value
                + order_value
            )
            / snapshot.equity
            * 100
        )

        if (
            resulting_exposure_pct
            > self.limits.maximum_gross_exposure_pct
        ):
            return RiskCheckResult(
                approved=False,
                reason=(
                    "Vượt maximum_gross_exposure_pct."
                ),
            )

        is_new_position = (
            current_position is None
            or current_position.quantity <= 0
        )

        if (
            is_new_position
            and snapshot.open_positions
            >= self.limits.maximum_open_positions
        ):
            return RiskCheckResult(
                approved=False,
                reason="Vượt maximum_open_positions.",
            )

        remaining_cash = (
            portfolio.cash
            - required_cash
        )

        minimum_cash = (
            snapshot.equity
            * self.limits.minimum_cash_buffer_pct
            / 100
        )

        if remaining_cash < minimum_cash:
            return RiskCheckResult(
                approved=False,
                reason="Vi phạm minimum cash buffer.",
            )

        return RiskCheckResult(
            approved=True
        )
