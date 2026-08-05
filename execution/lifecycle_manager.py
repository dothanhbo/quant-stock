from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from execution.exit_engine import (
    ExitEngine,
)
from execution.exit_models import (
    ExitBar,
    PositionExitState,
)
from execution.lifecycle_models import (
    ClosedPaperTrade,
    PositionLifecycleState,
)
from execution.order_manager import (
    OrderManager,
)
from execution.paper_broker import (
    PaperBroker,
)


@dataclass(frozen=True, slots=True)
class LifecycleHold:
    symbol: str
    valuation_date: date
    market_price: float
    highest_price: float
    effective_stop_price: float
    trailing_stop_price: float | None
    unrealized_pnl: float
    unrealized_pnl_pct: float


@dataclass(frozen=True, slots=True)
class LifecycleExit:
    symbol: str
    valuation_date: date
    quantity: int
    reference_exit_price: float
    fill_price: float
    realized_pnl: float
    return_pct: float
    holding_days: int
    reason: str
    order_id: str


@dataclass(slots=True)
class LifecycleRunResult:
    valuation_date: date
    held: list[LifecycleHold] = field(
        default_factory=list
    )
    exited: list[LifecycleExit] = field(
        default_factory=list
    )
    missing_prices: list[str] = field(
        default_factory=list
    )
    missing_states: list[str] = field(
        default_factory=list
    )
    rejected_exits: list[str] = field(
        default_factory=list
    )
    cash: float = 0.0
    equity: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    open_positions: int = 0


class PaperLifecycleManager:
    """
    Daily manager for open paper positions.

    It evaluates exact daily OHLC data and always sends exits
    through OrderManager -> PaperBroker. It never edits cash or
    position quantities directly.
    """

    def __init__(
        self,
        *,
        broker: PaperBroker,
        order_manager: OrderManager,
        exit_engine: ExitEngine,
        market_database_path: str | Path = (
            "data/market.db"
        ),
        price_scale: float = 1000.0,
    ) -> None:
        self.broker = broker
        self.order_manager = order_manager
        self.exit_engine = exit_engine
        self.market_database_path = Path(
            market_database_path
        )
        self.price_scale = price_scale

        if self.price_scale <= 0:
            raise ValueError(
                "price_scale phải lớn hơn 0."
            )

    def run(
        self,
        *,
        valuation_date: str | date | None = None,
        exit_signals: set[str] | None = None,
    ) -> LifecycleRunResult:
        resolved_date = self._resolve_date(
            valuation_date
        )
        exit_signals = {
            symbol.strip().upper()
            for symbol in (
                exit_signals or set()
            )
        }

        held: list[LifecycleHold] = []
        exited: list[LifecycleExit] = []
        missing_prices: list[str] = []
        missing_states: list[str] = []
        rejected_exits: list[str] = []

        positions = self.broker.get_positions()
        bars = self._load_bars(
            symbols=[
                position.symbol
                for position in positions
            ],
            valuation_date=resolved_date,
        )

        for position in positions:
            symbol = position.symbol
            lifecycle = (
                self.broker
                .get_position_lifecycle(
                    symbol
                )
            )

            if lifecycle is None:
                missing_states.append(
                    symbol
                )
                continue

            bar = bars.get(
                symbol
            )

            if bar is None:
                missing_prices.append(
                    symbol
                )
                continue

            if resolved_date <= lifecycle.entry_date:
                self.broker.update_market_price(
                    symbol,
                    bar.close_price,
                    persist_snapshot=False,
                )

                refreshed = (
                    self.broker.get_position(
                        symbol
                    )
                )

                if refreshed is None:
                    raise RuntimeError(
                        f"Không tìm thấy vị thế {symbol}."
                    )

                held.append(
                    LifecycleHold(
                        symbol=symbol,
                        valuation_date=resolved_date,
                        market_price=refreshed.market_price,
                        highest_price=max(
                            lifecycle.highest_price
                            or lifecycle.entry_price,
                            lifecycle.entry_price,
                        ),
                        effective_stop_price=(
                            lifecycle.stop_price
                        ),
                        trailing_stop_price=(
                            lifecycle.trailing_stop_price
                        ),
                        unrealized_pnl=(
                            refreshed.unrealized_pnl
                        ),
                        unrealized_pnl_pct=(
                            refreshed.unrealized_pnl_pct
                        ),
                    )
                )

                continue

            previous_average_price = (
                position.average_price
            )
            quantity = position.quantity

            self.broker.update_market_price(
                symbol,
                bar.close_price,
                persist_snapshot=False,
            )

            decision = self.exit_engine.evaluate(
                state=PositionExitState(
                    symbol=symbol,
                    entry_date=(
                        lifecycle.entry_date
                    ),
                    entry_price=(
                        lifecycle.entry_price
                    ),
                    quantity=quantity,
                    stop_price=(
                        lifecycle.stop_price
                    ),
                    take_profit_price=(
                        lifecycle
                        .take_profit_price
                    ),
                    highest_price=(
                        lifecycle.highest_price
                    ),
                    trailing_stop_price=(
                        lifecycle
                        .trailing_stop_price
                    ),
                    trailing_atr_multiplier=(
                        lifecycle
                        .trailing_atr_multiplier
                    ),
                    maximum_holding_days=(
                        lifecycle
                        .maximum_holding_days
                    ),
                ),
                bar=bar,
                exit_signal=(
                    symbol in exit_signals
                ),
            )

            if not decision.should_exit:
                self.broker.save_position_lifecycle(
                    PositionLifecycleState(
                        symbol=symbol,
                        entry_date=(
                            lifecycle.entry_date
                        ),
                        entry_price=(
                            lifecycle.entry_price
                        ),
                        initial_quantity=(
                            lifecycle
                            .initial_quantity
                        ),
                        stop_price=(
                            lifecycle.stop_price
                        ),
                        take_profit_price=(
                            lifecycle
                            .take_profit_price
                        ),
                        highest_price=(
                            decision.highest_price
                        ),
                        trailing_stop_price=(
                            decision
                            .trailing_stop_price
                        ),
                        trailing_atr_multiplier=(
                            lifecycle
                            .trailing_atr_multiplier
                        ),
                        maximum_holding_days=(
                            lifecycle
                            .maximum_holding_days
                        ),
                        updated_at=datetime.now(
                            timezone.utc
                        ),
                    )
                )

                refreshed = (
                    self.broker.get_position(
                        symbol
                    )
                )

                if refreshed is None:
                    raise RuntimeError(
                        f"Không tìm thấy {symbol} "
                        "sau mark-to-market."
                    )

                held.append(
                    LifecycleHold(
                        symbol=symbol,
                        valuation_date=(
                            resolved_date
                        ),
                        market_price=(
                            refreshed.market_price
                        ),
                        highest_price=(
                            decision.highest_price
                        ),
                        effective_stop_price=(
                            decision
                            .effective_stop_price
                        ),
                        trailing_stop_price=(
                            decision
                            .trailing_stop_price
                        ),
                        unrealized_pnl=(
                            refreshed
                            .unrealized_pnl
                        ),
                        unrealized_pnl_pct=(
                            refreshed
                            .unrealized_pnl_pct
                        ),
                    )
                )
                continue

            if (
                decision.execution_price is None
                or decision.reason is None
            ):
                raise RuntimeError(
                    "ExitDecision không đầy đủ."
                )

            fill = self.order_manager.sell_market(
                symbol=symbol,
                quantity=quantity,
                price=(
                    decision.execution_price
                ),
            )

            if fill is None:
                rejected_exits.append(
                    symbol
                )
                continue

            realized_pnl = (
                fill.net_cash_flow
                - previous_average_price
                * fill.quantity
            )
            return_pct = (
                realized_pnl
                / (
                    previous_average_price
                    * fill.quantity
                )
                * 100
                if previous_average_price > 0
                else 0.0
            )

            closed_trade = ClosedPaperTrade(
                symbol=symbol,
                entry_date=(
                    lifecycle.entry_date
                ),
                exit_date=resolved_date,
                quantity=fill.quantity,
                entry_price=(
                    previous_average_price
                ),
                exit_price=fill.price,
                gross_proceeds=(
                    fill.gross_value
                ),
                commission=fill.commission,
                realized_pnl=realized_pnl,
                return_pct=return_pct,
                holding_days=(
                    decision.holding_days
                ),
                exit_reason=decision.reason,
                order_id=fill.order_id,
                created_at=fill.created_at,
            )

            self.broker.record_closed_trade(
                closed_trade
            )
            self.broker.delete_position_lifecycle(
                symbol
            )

            exited.append(
                LifecycleExit(
                    symbol=symbol,
                    valuation_date=(
                        resolved_date
                    ),
                    quantity=fill.quantity,
                    reference_exit_price=(
                        decision.execution_price
                    ),
                    fill_price=fill.price,
                    realized_pnl=realized_pnl,
                    return_pct=return_pct,
                    holding_days=(
                        decision.holding_days
                    ),
                    reason=(
                        decision.reason.value
                    ),
                    order_id=fill.order_id,
                )
            )

        self.broker.persist_portfolio_state()
        snapshot = (
            self.broker
            .get_portfolio_snapshot()
        )

        return LifecycleRunResult(
            valuation_date=resolved_date,
            held=held,
            exited=exited,
            missing_prices=missing_prices,
            missing_states=missing_states,
            rejected_exits=rejected_exits,
            cash=snapshot.cash,
            equity=snapshot.equity,
            realized_pnl=(
                snapshot.realized_pnl
            ),
            unrealized_pnl=(
                snapshot.unrealized_pnl
            ),
            open_positions=(
                snapshot.open_positions
            ),
        )

    def _resolve_date(
        self,
        valuation_date: str | date | None,
    ) -> date:
        if isinstance(
            valuation_date,
            date,
        ):
            return valuation_date

        if isinstance(
            valuation_date,
            str,
        ):
            return date.fromisoformat(
                valuation_date
            )

        if not self.market_database_path.exists():
            raise FileNotFoundError(
                "Không tìm thấy market database: "
                f"{self.market_database_path}"
            )

        with sqlite3.connect(
            self.market_database_path
        ) as connection:
            row = connection.execute(
                """
                SELECT MAX(
                    substr(time, 1, 10)
                )
                FROM prices
                """
            ).fetchone()

        if row is None or row[0] is None:
            raise RuntimeError(
                "Không xác định được ngày "
                "dữ liệu thị trường."
            )

        return date.fromisoformat(
            str(row[0])
        )

    def _load_bars(
        self,
        *,
        symbols: list[str],
        valuation_date: date,
    ) -> dict[str, ExitBar]:
        if not symbols:
            return {}

        placeholders = ", ".join(
            "?"
            for _ in symbols
        )

        query = f"""
            SELECT
                symbol,
                open,
                high,
                low,
                close
            FROM prices
            WHERE symbol IN ({placeholders})
              AND substr(time, 1, 10) = ?
              AND open IS NOT NULL
              AND high IS NOT NULL
              AND low IS NOT NULL
              AND close IS NOT NULL
        """

        with sqlite3.connect(
            self.market_database_path
        ) as connection:
            rows = connection.execute(
                query,
                [
                    *symbols,
                    valuation_date.isoformat(),
                ],
            ).fetchall()

        return {
            str(symbol).strip().upper(): ExitBar(
                symbol=str(symbol),
                valuation_date=valuation_date,
                open_price=(
                    float(open_price)
                    * self.price_scale
                ),
                high_price=(
                    float(high_price)
                    * self.price_scale
                ),
                low_price=(
                    float(low_price)
                    * self.price_scale
                ),
                close_price=(
                    float(close_price)
                    * self.price_scale
                ),
                # ATR is not stored in the raw prices table.
                # Existing trailing levels remain active; creating
                # a new ATR trailing level will be wired to prepared
                # indicator data in the next step.
                atr=None,
            )
            for (
                symbol,
                open_price,
                high_price,
                low_price,
                close_price,
            ) in rows
        }
