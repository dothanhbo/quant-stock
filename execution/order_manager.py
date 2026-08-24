from __future__ import annotations

from execution.broker_interface import (
    BrokerInterface,
)
from execution.models import (
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    utc_now,
)
from execution.risk_guard import (
    RiskGuard,
)


class OrderManager:
    def __init__(
        self,
        *,
        broker: BrokerInterface,
        risk_guard: RiskGuard,
    ) -> None:
        self.broker = broker
        self.risk_guard = risk_guard

    def submit_order(
        self,
        order: Order,
        *,
        estimated_price: float,
        daily_realized_pnl: float = 0.0,
    ) -> Fill | None:
        duplicate_exists = (
            self._duplicate_order_exists(
                order
            )
        )

        portfolio = getattr(
            self.broker,
            "portfolio",
            None,
        )

        if portfolio is None:
            raise TypeError(
                "Broker hiện tại không cung cấp "
                "PortfolioState cho RiskGuard."
            )

        risk_result = (
            self.risk_guard.validate_order(
                order=order,
                estimated_price=(
                    estimated_price
                ),
                portfolio=portfolio,
                duplicate_order_exists=(
                    duplicate_exists
                ),
                daily_realized_pnl=(
                    daily_realized_pnl
                ),
            )
        )

        if not risk_result.approved:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = (
                risk_result.reason
            )
            order.updated_at = utc_now()

            record_order = getattr(
                self.broker,
                "record_order",
                None,
            )

            if callable(record_order):
                record_order(
                    order
                )

            return None

        order.reference_price = (
            estimated_price
        )
        order.status = OrderStatus.ACCEPTED
        order.updated_at = utc_now()

        return self.broker.submit_order(
            order
        )

    def buy_market(
        self,
        *,
        symbol: str,
        quantity: int,
        price: float,
        daily_realized_pnl: float = 0.0,
    ) -> Fill | None:
        order = Order(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=quantity,
            order_type=OrderType.MARKET,
            reference_price=price,
        )

        return self.submit_order(
            order,
            estimated_price=price,
            daily_realized_pnl=daily_realized_pnl,
        )

    def sell_market(
        self,
        *,
        symbol: str,
        quantity: int,
        price: float,
    ) -> Fill | None:
        order = Order(
            symbol=symbol,
            side=OrderSide.SELL,
            quantity=quantity,
            order_type=OrderType.MARKET,
            reference_price=price,
        )

        return self.submit_order(
            order,
            estimated_price=price,
        )

    def _duplicate_order_exists(
        self,
        order: Order,
    ) -> bool:
        return any(
            existing.symbol
            == order.symbol
            and existing.side
            == order.side
            for existing
            in self.broker.get_open_orders()
        )
