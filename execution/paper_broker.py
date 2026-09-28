from __future__ import annotations

from pathlib import Path
from datetime import date

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
from execution.exit_models import ExitReason


class PaperBroker(BrokerInterface):
    def __init__(
        self,
        *,
        initial_cash: float = 100_000_000,
        commission_rate: float = 0.0015,
        slippage_bps: float = 5.0,
        sell_tax_rate: float = 0.0,
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
        if not 0 <= sell_tax_rate < 1:
            raise ValueError("sell_tax_rate phải nằm trong [0, 1).")

        self.commission_rate = (
            commission_rate
        )
        self.slippage_bps = slippage_bps
        self.sell_tax_rate = sell_tax_rate
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
        self._duplicate_execution_intents: set[str] = set()
        self._execution_dispositions: dict[str, str] = {}

    def submit_order(
        self,
        order: Order,
    ) -> Fill | None:
        source_intent_id = order.source_intent_id
        atomic_intent_execution = source_intent_id is not None
        exit_reservation_created = False
        if atomic_intent_execution and self._store is None:
            raise RuntimeError("source-linked execution requires persistent paper storage")
        if source_intent_id is not None:
            existing = self._store.lookup_execution_intent(source_intent_id)
            if existing is not None:
                if existing["status"] == OrderStatus.FILLED.value:
                    if existing["fill"] is None:
                        raise RuntimeError("filled execution intent has no persisted fill")
                    self._restore_persisted_state()
                    self._duplicate_execution_intents.add(source_intent_id)
                    self._execution_dispositions[source_intent_id] = "ALREADY_EXECUTED"
                    return existing["fill"]
                if (
                    existing["status"] in {OrderStatus.REJECTED.value, OrderStatus.CANCELLED.value}
                    and not source_intent_id.startswith("paper_exit:")
                ):
                    return None
                order.client_order_id = existing["client_order_id"]
                if source_intent_id.startswith("paper_exit:"):
                    order.reference_price = float(existing["reference_price"])
                    order.quantity = int(existing["quantity"])
                    order.execution_context = existing.get("execution_context")

        if atomic_intent_execution and source_intent_id.startswith("paper_exit:"):
            reservation = self._store.save_exit_intent(order)
            if reservation["status"] == OrderStatus.FILLED.value:
                completed = self._store.lookup_execution_intent(source_intent_id)
                if completed is None or completed["fill"] is None:
                    raise RuntimeError("filled exit intent has no persisted fill")
                self._restore_persisted_state()
                self._duplicate_execution_intents.add(source_intent_id)
                self._execution_dispositions[source_intent_id] = "ALREADY_EXECUTED"
                return completed["fill"]
            exit_reservation_created = bool(reservation["created"])

        self._orders[
            order.client_order_id
        ] = order
        if not atomic_intent_execution:
            self._persist_order(order)

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

            sell_tax = gross_value * self.sell_tax_rate
            net_cash_flow = self._apply_sell(
                order=order,
                fill_price=fill_price,
                commission=commission + sell_tax,
            )
            commission += sell_tax

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

        if atomic_intent_execution:
            try:
                lifecycle_state = self._entry_lifecycle_from_order(order, fill) if order.execution_context and "lifecycle" in order.execution_context else None
                closed_trade = self._closed_trade_from_order(order, fill) if order.execution_context and "exit" in order.execution_context else None
                disposition, persisted_fill = self._store.save_intent_execution(
                    order, fill, self.portfolio,
                    lifecycle_state=lifecycle_state,
                    closed_trade=closed_trade,
                    delete_lifecycle_symbol=order.symbol if closed_trade is not None else None,
                )
            except Exception:
                self._restore_persisted_state()
                raise
            if disposition == "ALREADY_EXECUTED":
                self._restore_persisted_state()
                self._duplicate_execution_intents.add(str(source_intent_id))
                self._execution_dispositions[str(source_intent_id)] = disposition
                return persisted_fill
            if disposition == "ALREADY_REJECTED":
                self._restore_persisted_state()
                self._execution_dispositions[str(source_intent_id)] = disposition
                return None
            if disposition == "RESUMED" and exit_reservation_created:
                disposition = "EXECUTED"
            self._execution_dispositions[str(source_intent_id)] = disposition
            return persisted_fill

        self._persist_order(order)
        self._persist_fill(fill)
        self._persist_portfolio()

        return fill

    @staticmethod
    def _entry_lifecycle_from_order(order: Order, fill: Fill) -> PositionLifecycleState:
        context = order.execution_context["lifecycle"]
        reference_price = float(order.reference_price or fill.price)
        return PositionLifecycleState(
            symbol=fill.symbol,
            entry_date=date.fromisoformat(str(context["entry_date"])),
            entry_price=fill.price,
            initial_quantity=fill.quantity,
            stop_price=float(context["stop_price"]),
            take_profit_price=(float(context["take_profit_price"]) if context.get("take_profit_price") is not None else None),
            highest_price=max(fill.price, reference_price),
            trailing_atr_multiplier=(float(context["trailing_atr_multiplier"]) if context.get("trailing_atr_multiplier") is not None else None),
            maximum_holding_days=(int(context["maximum_holding_days"]) if context.get("maximum_holding_days") is not None else None),
            entry_order_id=fill.order_id,
            strategy_version=context.get("strategy_version"),
            policy_fingerprint=context.get("policy_fingerprint"),
        )

    @staticmethod
    def _closed_trade_from_order(order: Order, fill: Fill) -> ClosedPaperTrade:
        context = order.execution_context["exit"]
        entry_price = float(context["entry_price"])
        realized_pnl = fill.net_cash_flow - entry_price * fill.quantity
        return ClosedPaperTrade(
            symbol=fill.symbol,
            entry_date=date.fromisoformat(str(context["entry_date"])),
            exit_date=date.fromisoformat(str(context["exit_date"])),
            quantity=fill.quantity,
            entry_price=entry_price,
            exit_price=fill.price,
            gross_proceeds=fill.gross_value,
            commission=fill.commission,
            realized_pnl=realized_pnl,
            return_pct=(realized_pnl / (entry_price * fill.quantity) * 100 if entry_price > 0 else 0.0),
            holding_days=int(context["holding_days"]),
            exit_reason=ExitReason(str(context["exit_reason"])),
            order_id=fill.order_id,
            created_at=fill.created_at,
        )

    def lookup_execution_intent(self, source_intent_id: str) -> dict | None:
        if self._store is None:
            return None
        return self._store.lookup_execution_intent(source_intent_id)

    def consume_duplicate_execution(self, source_intent_id: str) -> bool:
        """Report whether this submit resolved to an already-persisted intent."""
        key = str(source_intent_id)
        if key not in self._duplicate_execution_intents:
            return False
        self._duplicate_execution_intents.remove(key)
        return True

    def consume_execution_disposition(self, source_intent_id: str) -> str | None:
        return self._execution_dispositions.pop(str(source_intent_id), None)

    def _restore_persisted_state(self) -> None:
        if self._store is None:
            return
        self.portfolio = self._store.load_portfolio_state(
            fallback_initial_cash=self.portfolio.initial_cash
        )
        self._orders = self._store.load_orders()
        self._fills = self._store.load_fills()
        self._market_prices = {
            position.symbol: position.market_price
            for position in self.portfolio.get_positions()
        }

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

    def queue_signal(self, signal: dict) -> bool:
        if self._store is None:
            raise RuntimeError("PaperBroker chưa cấu hình database.")
        return self._store.queue_signal(signal)

    def load_pending_signals(self, before_date: str) -> list[dict]:
        if self._store is None:
            return []
        return self._store.load_pending_signals(before_date)

    def complete_pending_signal(
        self, pending_id: int, *, processed_date: str, status: str, reason: str = ""
    ) -> None:
        if self._store is None:
            raise RuntimeError("PaperBroker chưa cấu hình database.")
        self._store.complete_pending_signal(
            pending_id,
            processed_date=processed_date,
            status=status,
            reason=reason,
        )

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
