from __future__ import annotations

import sqlite3
import hashlib
import json
from bisect import bisect_right
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
    PositionLifecycleState,
)
from execution.order_manager import (
    OrderManager,
)
from execution.paper_broker import (
    PaperBroker,
)
from core.paths import resolve_market_database_path


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
        market_database_path: str | Path | None = None,
        price_scale: float = 1000.0,
        atr_period: int = 14,
        default_trailing_atr_multiplier: (
            float | None
        ) = None,
    ) -> None:
        self.broker = broker
        self.order_manager = order_manager
        self.exit_engine = exit_engine
        self.market_database_path = resolve_market_database_path(
            market_database_path
        )
        self.price_scale = price_scale
        self.atr_period = int(atr_period)
        self.default_trailing_atr_multiplier = (
            default_trailing_atr_multiplier
        )

        if self.price_scale <= 0:
            raise ValueError(
                "price_scale phải lớn hơn 0."
            )

        if self.atr_period <= 0:
            raise ValueError(
                "atr_period phải lớn hơn 0."
            )

        if (
            self.default_trailing_atr_multiplier
            is not None
            and self.default_trailing_atr_multiplier
            <= 0
        ):
            raise ValueError(
                "default_trailing_atr_multiplier "
                "phải lớn hơn 0 hoặc None."
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
        market_sessions = (
            self._load_market_sessions(
                valuation_date=resolved_date,
            )
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

            if resolved_date < lifecycle.entry_date:
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

            pending_sells = [
                order for order in self.broker.get_open_orders()
                if order.symbol == symbol and order.side.value == "SELL"
            ]
            if pending_sells:
                if len(pending_sells) != 1:
                    rejected_exits.append(symbol)
                    continue
                pending = pending_sells[0]
                context = pending.execution_context or {}
                exit_context = context.get("exit")
                if (
                    not pending.source_intent_id
                    or not pending.source_intent_id.startswith("paper_exit:")
                    or not exit_context
                    or pending.reference_price is None
                ):
                    # Legacy or incomplete open sells lack enough durable
                    # evidence to resume safely; do not create a new sell.
                    rejected_exits.append(symbol)
                    continue
                fill = self.order_manager.sell_market(
                    symbol=symbol,
                    quantity=pending.quantity,
                    price=float(pending.reference_price),
                    source_intent_id=pending.source_intent_id,
                    execution_context=context,
                )
                if fill is None:
                    rejected_exits.append(symbol)
                    continue
                entry_price = float(exit_context["entry_price"])
                realized_pnl = fill.net_cash_flow - entry_price * fill.quantity
                return_pct = (
                    realized_pnl / (entry_price * fill.quantity) * 100
                    if entry_price > 0 else 0.0
                )
                exited.append(LifecycleExit(
                    symbol=symbol,
                    valuation_date=date.fromisoformat(str(exit_context["exit_date"])),
                    quantity=fill.quantity,
                    reference_exit_price=float(pending.reference_price),
                    fill_price=fill.price,
                    realized_pnl=realized_pnl,
                    return_pct=return_pct,
                    holding_days=int(exit_context["holding_days"]),
                    reason=str(exit_context["exit_reason"]),
                    order_id=fill.order_id,
                ))
                continue

            holding_sessions = (
                self._count_holding_sessions(
                    sessions=market_sessions,
                    entry_date=lifecycle.entry_date,
                    valuation_date=resolved_date,
                )
            )
            trailing_atr_multiplier = (
                lifecycle.trailing_atr_multiplier
                if lifecycle.trailing_atr_multiplier
                is not None
                else self
                .default_trailing_atr_multiplier
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
                        trailing_atr_multiplier
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
                holding_sessions=holding_sessions,
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
                            trailing_atr_multiplier
                        ),
                        maximum_holding_days=(
                            lifecycle
                            .maximum_holding_days
                        ),
                        entry_order_id=lifecycle.entry_order_id,
                        strategy_version=lifecycle.strategy_version,
                        policy_fingerprint=lifecycle.policy_fingerprint,
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
                source_intent_id=self._exit_intent_id(
                    lifecycle=lifecycle,
                    valuation_date=resolved_date,
                    quantity=quantity,
                    reason=decision.reason.value,
                ),
                execution_context={
                    "exit": {
                        "entry_date": lifecycle.entry_date.isoformat(),
                        "entry_price": previous_average_price,
                        "exit_date": resolved_date.isoformat(),
                        "holding_days": decision.holding_days,
                        "exit_reason": decision.reason.value,
                    },
                },
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

            # Source-linked SELL persistence atomically writes the close
            # record and removes active lifecycle state with its fill.

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

    @staticmethod
    def _exit_intent_id(
        *,
        lifecycle: PositionLifecycleState,
        valuation_date: date,
        quantity: int,
        reason: str,
    ) -> str:
        identity = {
            "entry_order_id": lifecycle.entry_order_id,
            "symbol": lifecycle.symbol,
            "entry_date": lifecycle.entry_date.isoformat(),
            "entry_price": lifecycle.entry_price,
            "initial_quantity": lifecycle.initial_quantity,
            "stop_price": lifecycle.stop_price,
            "take_profit_price": lifecycle.take_profit_price,
            "trailing_stop_price": lifecycle.trailing_stop_price,
            "updated_at": lifecycle.updated_at.isoformat() if lifecycle.updated_at else None,
            "exit_date": valuation_date.isoformat(),
            "quantity": quantity,
            "reason": reason,
        }
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return "paper_exit:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()

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
                time,
                open,
                high,
                low,
                close
            FROM prices
            WHERE symbol IN ({placeholders})
              AND date(time) <= date(?)
              AND open IS NOT NULL
              AND high IS NOT NULL
              AND low IS NOT NULL
              AND close IS NOT NULL
            ORDER BY symbol, time
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

        history_by_symbol: dict[
            str,
            dict[str, tuple[float, float, float, float]],
        ] = {}

        for (
            symbol,
            time_value,
            open_price,
            high_price,
            low_price,
            close_price,
        ) in rows:
            normalized_symbol = (
                str(symbol).strip().upper()
            )
            trading_date = str(time_value)[:10]
            history_by_symbol.setdefault(
                normalized_symbol,
                {},
            )[trading_date] = (
                float(open_price),
                float(high_price),
                float(low_price),
                float(close_price),
            )

        bars: dict[str, ExitBar] = {}
        valuation_text = valuation_date.isoformat()

        for symbol, daily_rows in (
            history_by_symbol.items()
        ):
            current = daily_rows.get(
                valuation_text
            )

            if current is None:
                continue

            ordered_history = [
                daily_rows[trading_date]
                for trading_date in sorted(
                    daily_rows
                )
            ]
            atr = self._calculate_wilder_atr(
                ordered_history,
                period=self.atr_period,
            )
            (
                open_price,
                high_price,
                low_price,
                close_price,
            ) = current

            bars[symbol] = ExitBar(
                symbol=symbol,
                valuation_date=valuation_date,
                open_price=(
                    open_price * self.price_scale
                ),
                high_price=(
                    high_price * self.price_scale
                ),
                low_price=(
                    low_price * self.price_scale
                ),
                close_price=(
                    close_price * self.price_scale
                ),
                atr=(
                    atr * self.price_scale
                    if atr is not None
                    else None
                ),
            )

        return bars

    def _load_market_sessions(
        self,
        *,
        valuation_date: date,
    ) -> list[date]:
        with sqlite3.connect(
            self.market_database_path
        ) as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT date(time)
                FROM prices
                WHERE symbol = 'VNINDEX'
                  AND date(time) <= date(?)
                ORDER BY date(time)
                """,
                (valuation_date.isoformat(),),
            ).fetchall()

        return [
            date.fromisoformat(str(row[0]))
            for row in rows
            if row[0] is not None
        ]

    @staticmethod
    def _count_holding_sessions(
        *,
        sessions: list[date],
        entry_date: date,
        valuation_date: date,
    ) -> int:
        return max(
            0,
            bisect_right(
                sessions,
                valuation_date,
            )
            - bisect_right(
                sessions,
                entry_date,
            ),
        )

    @staticmethod
    def _calculate_wilder_atr(
        rows: list[
            tuple[float, float, float, float]
        ],
        *,
        period: int,
    ) -> float | None:
        if len(rows) < period:
            return None

        previous_close: float | None = None
        atr: float | None = None

        for (
            _open_price,
            high_price,
            low_price,
            close_price,
        ) in rows:
            true_range = high_price - low_price

            if previous_close is not None:
                true_range = max(
                    true_range,
                    abs(high_price - previous_close),
                    abs(low_price - previous_close),
                )

            atr = (
                true_range
                if atr is None
                else atr
                + (true_range - atr) / period
            )
            previous_close = close_price

        return atr
