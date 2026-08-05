from __future__ import annotations

from pathlib import Path

from execution.broker_interface import (
    BrokerInterface,
)
from execution.models import (
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PortfolioSnapshot,
    utc_now,
)
from execution.persistence import (
    PaperTradingStore,
)
from execution.portfolio_state import (
    PortfolioState,
)
from execution.lifecycle_models import (
    ClosedPaperTrade,
    PositionLifecycleState,
)


class PaperBroker(BrokerInterface):
    def __init__(
        self,
        *,
        initial_cash: float = 100_000_000,
        commission_rate: float = 0.0015,
        slippage_bps: float = 5.0,
        database_path: str | Path | None = None,
        restore_state: bool = True,
    ) -> None:
        if initial_cash <= 0:
            raise ValueError(
                "initial_cash phải lớn hơn 0."
            )

        if commission_rate < 0:
            raise ValueError(
                "commission_rate không được âm."
            )

        if slippage_bps < 0:
            raise ValueError(
                "slippage_bps không được âm."
            )

        self.commission_rate = (
            commission_rate
        )
        self.slippage_bps = slippage_bps
        self._store = (
            PaperTradingStore(
                database_path
            )
            if database_path is not None
            else None
        )

        if (
            self._store is not None
            and restore_state
            and self._store.has_state()
        ):
            self.portfolio = (
                self._store.load_portfolio_state(
                    fallback_initial_cash=(
                        initial_cash
                    )
                )
            )
            self._orders = (
                self._store.load_orders()
            )
            self._fills = (
                self._store.load_fills()
            )
        else:
            self.portfolio = PortfolioState(
                initial_cash=initial_cash,
                cash=initial_cash,
            )
            self._orders: dict[
                str,
                Order,
            ] = {}
            self._fills: list[Fill] = []

            if self._store is not None:
                self._store.save_initial_cash(
                    initial_cash
                )
                self._store.save_portfolio_state(
                    self.portfolio
                )

        self._market_prices: dict[
            str,
            float,
        ] = {
            position.symbol: (
                position.market_price
            )
            for position
            in self.portfolio.get_positions()
        }

    def submit_order(
        self,
        order: Order,
    ) -> Fill | None:
        self._orders[
            order.client_order_id
        ] = order
        self._persist_order(
            order
        )

        market_price = self._resolve_order_price(
            order
        )

        if market_price is None:
            self._reject_order(
                order,
                "Không có giá thị trường hợp lệ.",
            )
            self._persist_order(
                order
            )
            return None

        fill_price = self._apply_slippage(
            market_price,
            order.side,
        )

        gross_value = (
            fill_price
            * order.quantity
        )

        commission = (
            gross_value
            * self.commission_rate
        )

        slippage_cost = (
            abs(
                fill_price
                - market_price
            )
            * order.quantity
        )

        if order.side == OrderSide.BUY:
            total_cost = (
                gross_value
                + commission
            )

            if total_cost > self.portfolio.cash:
                self._reject_order(
                    order,
                    "Không đủ tiền mặt.",
                )
                self._persist_order(
                    order
                )
                return None

            self._apply_buy(
                order=order,
                fill_price=fill_price,
                commission=commission,
            )

            net_cash_flow = -total_cost

        else:
            position = (
                self.portfolio.get_position(
                    order.symbol
                )
            )

            if (
                position is None
                or position.quantity
                < order.quantity
            ):
                self._reject_order(
                    order,
                    "Không đủ cổ phiếu để bán.",
                )
                self._persist_order(
                    order
                )
                return None

            net_cash_flow = self._apply_sell(
                order=order,
                fill_price=fill_price,
                commission=commission,
            )

        order.status = OrderStatus.FILLED
        order.filled_quantity = (
            order.quantity
        )
        order.average_fill_price = (
            fill_price
        )
        order.updated_at = utc_now()

        fill = Fill(
            order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            gross_value=gross_value,
            commission=commission,
            slippage_cost=slippage_cost,
            net_cash_flow=net_cash_flow,
        )

        self._fills.append(
            fill
        )

        self._persist_order(
            order
        )
        self._persist_fill(
            fill
        )
        self._persist_portfolio()

        return fill

    def record_order(
        self,
        order: Order,
    ) -> None:
        self._orders[
            order.client_order_id
        ] = order
        self._persist_order(
            order
        )

    def cancel_order(
        self,
        client_order_id: str,
    ) -> bool:
        order = self._orders.get(
            client_order_id
        )

        if order is None:
            return False

        if order.status not in {
            OrderStatus.PENDING,
            OrderStatus.ACCEPTED,
        }:
            return False

        order.status = OrderStatus.CANCELLED
        order.updated_at = utc_now()
        self._persist_order(
            order
        )
        return True

    def get_cash(self) -> float:
        return self.portfolio.cash

    def get_position(
        self,
        symbol: str,
    ) -> Position | None:
        return self.portfolio.get_position(
            symbol
        )

    def get_positions(
        self,
    ) -> list[Position]:
        return self.portfolio.get_positions()

    def get_open_orders(
        self,
    ) -> list[Order]:
        return [
            order
            for order
            in self._orders.values()
            if order.status
            in {
                OrderStatus.PENDING,
                OrderStatus.ACCEPTED,
                OrderStatus.PARTIALLY_FILLED,
            }
        ]

    def get_orders(
        self,
    ) -> list[Order]:
        return list(
            self._orders.values()
        )

    def get_fills(
        self,
    ) -> list[Fill]:
        return list(
            self._fills
        )

    def update_market_price(
        self,
        symbol: str,
        price: float,
        *,
        persist_snapshot: bool = True,
    ) -> None:
        symbol = symbol.strip().upper()

        if price <= 0:
            raise ValueError(
                "price phải lớn hơn 0."
            )

        self._market_prices[
            symbol
        ] = price

        position = (
            self.portfolio.get_position(
                symbol
            )
        )

        if position is not None:
            position.market_price = price

        if persist_snapshot:
            self._persist_portfolio()

    def get_portfolio_snapshot(
        self,
    ) -> PortfolioSnapshot:
        return self.portfolio.snapshot()


    def save_position_lifecycle(
        self,
        state: PositionLifecycleState,
    ) -> None:
        if self._store is None:
            raise RuntimeError(
                "PaperBroker chưa cấu hình database."
            )

        self._store.save_position_lifecycle(
            state
        )

    def get_position_lifecycle(
        self,
        symbol: str,
    ) -> PositionLifecycleState | None:
        if self._store is None:
            return None

        return self._store.get_position_lifecycle(
            symbol
        )

    def delete_position_lifecycle(
        self,
        symbol: str,
    ) -> None:
        if self._store is not None:
            self._store.delete_position_lifecycle(
                symbol
            )

    def record_closed_trade(
        self,
        trade: ClosedPaperTrade,
    ) -> None:
        if self._store is None:
            raise RuntimeError(
                "PaperBroker chưa cấu hình database."
            )

        self._store.save_closed_trade(
            trade
        )

    def get_closed_trades(
        self,
    ) -> list[ClosedPaperTrade]:
        if self._store is None:
            return []

        return self._store.load_closed_trades()

    def persist_portfolio_state(
        self,
    ) -> None:
        """
        Persist positions and one portfolio snapshot after a
        batch market-price update.
        """
        self._persist_portfolio()

    def reset_paper_account(
        self,
        *,
        initial_cash: float | None = None,
    ) -> None:
        cash = (
            initial_cash
            if initial_cash is not None
            else self.portfolio.initial_cash
        )

        if cash <= 0:
            raise ValueError(
                "initial_cash phải lớn hơn 0."
            )

        self.portfolio = PortfolioState(
            initial_cash=cash,
            cash=cash,
        )
        self._orders.clear()
        self._fills.clear()
        self._market_prices.clear()

        if self._store is not None:
            self._store.reset()
            self._store.save_initial_cash(
                cash
            )
            self._store.save_portfolio_state(
                self.portfolio
            )

    def _resolve_order_price(
        self,
        order: Order,
    ) -> float | None:
        if (
            order.order_type
            == OrderType.LIMIT
        ):
            return order.limit_price

        if (
            order.reference_price is not None
            and order.reference_price > 0
        ):
            return order.reference_price

        return self._market_prices.get(
            order.symbol
        )

    def _apply_slippage(
        self,
        market_price: float,
        side: OrderSide,
    ) -> float:
        slippage_rate = (
            self.slippage_bps
            / 10_000
        )

        multiplier = (
            1 + slippage_rate
            if side == OrderSide.BUY
            else 1 - slippage_rate
        )

        return (
            market_price
            * multiplier
        )

    def _apply_buy(
        self,
        *,
        order: Order,
        fill_price: float,
        commission: float,
    ) -> None:
        total_cost = (
            fill_price
            * order.quantity
            + commission
        )

        position = (
            self.portfolio.get_position(
                order.symbol
            )
        )

        if position is None:
            position = Position(
                symbol=order.symbol,
                quantity=0,
                average_price=0.0,
                market_price=fill_price,
            )
            self.portfolio.positions[
                order.symbol
            ] = position

        previous_cost = (
            position.average_price
            * position.quantity
        )

        new_cost = (
            fill_price
            * order.quantity
            + commission
        )

        new_quantity = (
            position.quantity
            + order.quantity
        )

        position.average_price = (
            previous_cost
            + new_cost
        ) / new_quantity

        position.quantity = new_quantity
        position.market_price = fill_price
        self.portfolio.cash -= total_cost

    def _apply_sell(
        self,
        *,
        order: Order,
        fill_price: float,
        commission: float,
    ) -> float:
        position = self.portfolio.get_position(
            order.symbol
        )

        if position is None:
            raise RuntimeError(
                "Không tìm thấy position."
            )

        gross_value = (
            fill_price
            * order.quantity
        )

        net_proceeds = (
            gross_value
            - commission
        )

        cost_basis = (
            position.average_price
            * order.quantity
        )

        realized_pnl = (
            net_proceeds
            - cost_basis
        )

        position.quantity -= (
            order.quantity
        )
        position.market_price = (
            fill_price
        )
        position.realized_pnl += (
            realized_pnl
        )

        self.portfolio.realized_pnl += (
            realized_pnl
        )
        self.portfolio.cash += (
            net_proceeds
        )
        self.portfolio.remove_empty_positions()

        return net_proceeds

    def _persist_order(
        self,
        order: Order,
    ) -> None:
        if self._store is not None:
            self._store.save_order(
                order
            )

    def _persist_fill(
        self,
        fill: Fill,
    ) -> None:
        if self._store is not None:
            self._store.save_fill(
                fill
            )

    def _persist_portfolio(
        self,
    ) -> None:
        if self._store is not None:
            self._store.save_portfolio_state(
                self.portfolio
            )

    @staticmethod
    def _reject_order(
        order: Order,
        reason: str,
    ) -> None:
        order.status = OrderStatus.REJECTED
        order.rejection_reason = reason
        order.updated_at = utc_now()
