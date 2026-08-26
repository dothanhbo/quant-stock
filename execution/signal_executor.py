from __future__ import annotations

import math
import os
import sqlite3
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from backtesting.position_sizers import (
    AtrRiskSizer,
    FixedFractionSizer,
    PositionSizer,
    PositionSizingContext,
)
from backtesting.portfolio_heat import (
    PortfolioHeat,
    PositionRisk,
)
from backtesting.regime_policy import (
    RegimePortfolioDecision,
    RegimePortfolioPolicy,
)
from backtesting.trade import Trade
from backtesting.transaction_cost import (
    TransactionCostConfig,
)
from execution.lifecycle_models import (
    PositionLifecycleState,
)
from execution.order_manager import OrderManager
from execution.paper_broker import PaperBroker
from execution.risk_guard import RiskGuard, RiskLimits
from config.trading_policy import TradingPolicy


def _read_bool(
    name: str,
    default: bool,
) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _read_float(
    name: str,
    default: float,
) -> float:
    value = os.getenv(name)

    if value is None:
        return default

    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(
            f"{name} phải là số."
        ) from exc


def _read_int(
    name: str,
    default: int,
) -> int:
    value = os.getenv(name)

    if value is None:
        return default

    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            f"{name} phải là số nguyên."
        ) from exc


@dataclass(frozen=True, slots=True)
class PaperExecutionConfig:
    enabled: bool = False
    database_path: Path = Path(
        "data/paper_trading.db"
    )
    initial_cash: float = 100_000_000

    # Use the same sizing models as backtesting.
    position_sizer: str = "atr_risk"
    risk_per_trade_pct: float = 1.0
    atr_stop_multiplier: float = 2.0
    fixed_fraction_pct: float = 10.0

    maximum_orders_per_scan: int = 3
    lot_size: int = 100
    commission_rate: float = 0.0015
    slippage_bps: float = 5.0
    maximum_position_pct: float = 20.0
    maximum_gross_exposure_pct: float = 80.0
    maximum_open_positions: int = 10
    maximum_daily_loss_pct: float = 3.0
    minimum_cash_buffer_pct: float = 5.0
    sell_tax_rate: float = 0.001
    maximum_order_adtv20_pct: float = 1.0

    @classmethod
    def from_env(
        cls,
    ) -> "PaperExecutionConfig":
        config = cls(
            enabled=_read_bool(
                "PAPER_TRADING_ENABLED",
                False,
            ),
            database_path=Path(
                os.getenv(
                    "PAPER_DATABASE_PATH",
                    "data/paper_trading.db",
                )
            ),
            initial_cash=_read_float(
                "PAPER_INITIAL_CASH",
                100_000_000,
            ),
            position_sizer=os.getenv(
                "PAPER_POSITION_SIZER",
                "atr_risk",
            ).strip().lower(),
            risk_per_trade_pct=_read_float(
                "PAPER_RISK_PER_TRADE_PCT",
                1.0,
            ),
            atr_stop_multiplier=_read_float(
                "PAPER_ATR_STOP_MULTIPLIER",
                2.0,
            ),
            fixed_fraction_pct=_read_float(
                "PAPER_FIXED_FRACTION_PCT",
                10.0,
            ),
            maximum_orders_per_scan=_read_int(
                "PAPER_MAX_ORDERS_PER_SCAN",
                3,
            ),
            lot_size=_read_int(
                "PAPER_LOT_SIZE",
                100,
            ),
            commission_rate=_read_float(
                "PAPER_COMMISSION_RATE",
                0.0015,
            ),
            slippage_bps=_read_float(
                "PAPER_SLIPPAGE_BPS",
                5.0,
            ),
            maximum_position_pct=_read_float(
                "PAPER_MAX_POSITION_PCT",
                20.0,
            ),
            maximum_gross_exposure_pct=_read_float(
                "PAPER_MAX_EXPOSURE_PCT",
                80.0,
            ),
            maximum_open_positions=_read_int(
                "PAPER_MAX_OPEN_POSITIONS",
                10,
            ),
            maximum_daily_loss_pct=_read_float(
                "PAPER_MAX_DAILY_LOSS_PCT",
                3.0,
            ),
            minimum_cash_buffer_pct=_read_float(
                "PAPER_MIN_CASH_BUFFER_PCT",
                5.0,
            ),
            sell_tax_rate=_read_float(
                "PAPER_SELL_TAX_RATE",
                0.001,
            ),
            maximum_order_adtv20_pct=_read_float(
                "PAPER_MAX_ORDER_ADTV20_PCT",
                1.0,
            ),
        )
        config.validate()
        return config

    def validate(
        self,
    ) -> None:
        if self.initial_cash <= 0:
            raise ValueError(
                "PAPER_INITIAL_CASH phải lớn hơn 0."
            )

        if self.position_sizer not in {
            "atr_risk",
            "fixed_fraction",
        }:
            raise ValueError(
                "PAPER_POSITION_SIZER chỉ nhận "
                "'atr_risk' hoặc 'fixed_fraction'."
            )

        if not 0 < self.risk_per_trade_pct <= 100:
            raise ValueError(
                "PAPER_RISK_PER_TRADE_PCT phải "
                "nằm trong (0, 100]."
            )

        if self.atr_stop_multiplier <= 0:
            raise ValueError(
                "PAPER_ATR_STOP_MULTIPLIER phải "
                "lớn hơn 0."
            )

        if not 0 < self.fixed_fraction_pct <= 100:
            raise ValueError(
                "PAPER_FIXED_FRACTION_PCT phải "
                "nằm trong (0, 100]."
            )

        if self.maximum_orders_per_scan <= 0:
            raise ValueError(
                "PAPER_MAX_ORDERS_PER_SCAN phải "
                "lớn hơn 0."
            )

        if self.lot_size <= 0:
            raise ValueError(
                "PAPER_LOT_SIZE phải lớn hơn 0."
            )

        if not 0 <= self.commission_rate < 1:
            raise ValueError("PAPER_COMMISSION_RATE phải nằm trong [0, 1).")
        if self.slippage_bps < 0:
            raise ValueError("PAPER_SLIPPAGE_BPS không được âm.")
        if not 0 < self.maximum_position_pct <= 100:
            raise ValueError("PAPER_MAX_POSITION_PCT phải nằm trong (0, 100].")
        if not 0 < self.maximum_gross_exposure_pct <= 100:
            raise ValueError("PAPER_MAX_EXPOSURE_PCT phải nằm trong (0, 100].")
        if self.maximum_open_positions <= 0:
            raise ValueError("PAPER_MAX_OPEN_POSITIONS phải lớn hơn 0.")
        if not 0 <= self.minimum_cash_buffer_pct < 100:
            raise ValueError("PAPER_MIN_CASH_BUFFER_PCT phải nằm trong [0, 100).")
        if not 0 <= self.sell_tax_rate < 1:
            raise ValueError("PAPER_SELL_TAX_RATE phải nằm trong [0, 1).")
        if not 0 < self.maximum_order_adtv20_pct <= 100:
            raise ValueError(
                "PAPER_MAX_ORDER_ADTV20_PCT phải nằm trong (0, 100]."
            )


@dataclass(slots=True)
class PaperSignalExecution:
    symbol: str
    status: str
    quantity: int = 0
    requested_price: float = 0.0
    fill_price: float | None = None
    gross_value: float = 0.0
    commission: float = 0.0
    position_sizer: str = ""
    estimated_position_pct: float = 0.0
    estimated_risk_amount: float = 0.0
    estimated_risk_pct: float = 0.0
    signal_rank: int | None = None
    signal_score: float | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class PaperPositionSummary:
    symbol: str
    quantity: int
    average_price: float
    market_price: float
    cost_basis: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    stop_price: float | None = None
    take_profit_price: float | None = None
    holding_days: int | None = None


@dataclass(frozen=True, slots=True)
class PaperClosedTradeSummary:
    symbol: str
    quantity: int
    entry_price: float
    exit_price: float
    realized_pnl: float
    return_pct: float
    holding_days: int
    exit_reason: str


@dataclass(slots=True)
class PaperExecutionBatchResult:
    enabled: bool
    position_sizer: str = ""
    executions: list[
        PaperSignalExecution
    ] = field(
        default_factory=list
    )
    cash: float = 0.0
    equity: float = 0.0
    gross_exposure_pct: float = 0.0
    open_positions: int = 0
    positions: list[
        PaperPositionSummary
    ] = field(
        default_factory=list
    )
    closed_today: list[
        PaperClosedTradeSummary
    ] = field(
        default_factory=list
    )
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0

    @property
    def filled_count(
        self,
    ) -> int:
        return sum(
            item.status == "FILLED"
            for item in self.executions
        )

    @property
    def skipped_count(
        self,
    ) -> int:
        return sum(
            item.status == "SKIPPED"
            for item in self.executions
        )

    @property
    def rejected_count(
        self,
    ) -> int:
        return sum(
            item.status == "REJECTED"
            for item in self.executions
        )

    @property
    def queued_count(self) -> int:
        return sum(item.status == "QUEUED" for item in self.executions)


class PaperSignalExecutor:
    """
    Execute scanner signals through the same PositionSizer interface
    used by the portfolio backtester.

    Scanner prices are stored in thousand VND. Broker and sizing cash
    calculations use actual VND, so price-like inputs are converted once
    at this boundary.
    """

    PRICE_SCALE = 1000.0

    def __init__(
        self,
        config: PaperExecutionConfig,
        *,
        position_sizer: PositionSizer | None = None,
        regime_policy: (
            RegimePortfolioPolicy | None
        ) = None,
    ) -> None:
        self.config = config
        self.policy = TradingPolicy.from_env()
        self.regime_policy = (
            regime_policy
            or RegimePortfolioPolicy()
        )

        self.broker = PaperBroker(
            initial_cash=config.initial_cash,
            commission_rate=(
                config.commission_rate
            ),
            slippage_bps=(
                config.slippage_bps
            ),
            sell_tax_rate=config.sell_tax_rate,
            database_path=(
                config.database_path
            ),
            restore_state=True,
        )

        self.order_manager = OrderManager(
            broker=self.broker,
            risk_guard=RiskGuard(
                RiskLimits(
                    maximum_position_pct=(
                        config.maximum_position_pct
                    ),
                    maximum_gross_exposure_pct=(
                        config.maximum_gross_exposure_pct
                    ),
                    maximum_open_positions=(
                        config.maximum_open_positions
                    ),
                    maximum_daily_loss_pct=(
                        config.maximum_daily_loss_pct
                    ),
                    minimum_cash_buffer_pct=(
                        config.minimum_cash_buffer_pct
                    ),
                    commission_rate=config.commission_rate,
                )
            ),
        )

        self.position_sizer = (
            position_sizer
            or self._build_position_sizer()
        )

        self.transaction_cost_config = (
            TransactionCostConfig(
                buy_commission_pct=(
                    config.commission_rate
                    * 100
                ),
                sell_commission_pct=(
                    config.commission_rate
                    * 100
                ),
                sell_tax_pct=config.sell_tax_rate * 100,
                buy_slippage_pct=(
                    config.slippage_bps
                    / 100
                ),
                sell_slippage_pct=(
                    config.slippage_bps
                    / 100
                ),
            )
        )

    @classmethod
    def from_env(
        cls,
    ) -> "PaperSignalExecutor":
        return cls(
            PaperExecutionConfig.from_env()
        )

    def queue_signals(
        self,
        signals: Sequence[dict[str, Any]],
        *,
        report_date: str | date | None = None,
    ) -> PaperExecutionBatchResult:
        """Persist close-T signals for execution at the next session open."""
        result = self.execute_signals([], report_date=report_date)
        if not self.config.enabled:
            return result
        for signal_rank, signal in enumerate(
            signals,
            start=1,
        ):
            symbol = str(signal.get("symbol", "")).strip().upper()
            try:
                regime_decision = (
                    self.regime_policy.resolve(
                        signal.get("regime")
                    )
                )
                regime_reason = (
                    self._regime_entry_rejection_reason(
                        regime_decision
                    )
                )

                if regime_reason is not None:
                    result.executions.append(
                        PaperSignalExecution(
                            symbol=symbol or "-",
                            status="SKIPPED",
                            position_sizer=(
                                self.position_sizer.name
                            ),
                            signal_rank=signal_rank,
                            signal_score=self._safe_float(
                                signal.get("score")
                            ),
                            reason=regime_reason,
                        )
                    )
                    continue

                queued_signal = dict(signal)
                queued_signal.setdefault(
                    "signal_rank",
                    signal_rank,
                )
                inserted = self.broker.queue_signal(
                    queued_signal
                )
                result.executions.append(
                    PaperSignalExecution(
                        symbol=symbol or "-",
                        status="QUEUED" if inserted else "SKIPPED",
                        position_sizer=self.position_sizer.name,
                        signal_rank=signal_rank,
                        signal_score=self._safe_float(
                            signal.get("score")
                        ),
                        reason=(
                            "Chờ khớp tại open phiên kế tiếp."
                            if inserted
                            else "Signal đã có trong hàng đợi."
                        ),
                    )
                )
            except Exception as error:
                result.executions.append(
                    PaperSignalExecution(
                        symbol=symbol or "-",
                        status="REJECTED",
                        position_sizer=self.position_sizer.name,
                        signal_rank=signal_rank,
                        signal_score=self._safe_float(
                            signal.get("score")
                        ),
                        reason=f"Không queue được signal: {error}",
                    )
                )
        return result

    def execute_pending_signals(
        self,
        *,
        valuation_date: str | date,
        market_database_path: str | Path = "data/market.db",
    ) -> PaperExecutionBatchResult:
        """Fill signals only at the first VNINDEX session after signal date."""
        resolved_date = self._resolve_report_date(valuation_date)
        pending = self.broker.load_pending_signals(resolved_date.isoformat())
        if not pending:
            return self.execute_signals([], report_date=resolved_date)

        due: list[dict[str, Any]] = []
        freshness_results: list[PaperSignalExecution] = []
        with sqlite3.connect(market_database_path) as connection:
            for signal_rank, signal in enumerate(
                pending,
                start=1,
            ):
                symbol = str(signal["symbol"]).strip().upper()
                signal.setdefault(
                    "signal_rank",
                    signal_rank,
                )
                pending_id = int(signal["_pending_id"])
                signal_date = str(signal.get("date", ""))[:10]
                expected_date = self._load_next_market_session(
                    connection,
                    signal_date=signal_date,
                )

                if expected_date is None:
                    reason = (
                        "Không xác định được phiên VNINDEX kế tiếp "
                        f"sau signal {signal_date}."
                    )
                    self.broker.complete_pending_signal(
                        pending_id,
                        processed_date=resolved_date.isoformat(),
                        status="REJECTED",
                        reason=reason,
                    )
                    freshness_results.append(PaperSignalExecution(
                        symbol=symbol,
                        status="REJECTED",
                        position_sizer=self.position_sizer.name,
                        signal_rank=self._safe_int(
                            signal.get("signal_rank")
                        ),
                        signal_score=self._safe_float(
                            signal.get("score")
                        ),
                        reason=reason,
                    ))
                    continue

                if resolved_date.isoformat() < expected_date:
                    # The next actual market session has not arrived yet.
                    continue

                if resolved_date.isoformat() > expected_date:
                    reason = (
                        "MISSED_EXECUTION: signal "
                        f"{signal_date} chỉ hợp lệ tại open {expected_date}; "
                        f"không khớp bù tại {resolved_date.isoformat()}."
                    )
                    self.broker.complete_pending_signal(
                        pending_id,
                        processed_date=resolved_date.isoformat(),
                        status="MISSED_EXECUTION",
                        reason=reason,
                    )
                    freshness_results.append(PaperSignalExecution(
                        symbol=symbol,
                        status="SKIPPED",
                        position_sizer=self.position_sizer.name,
                        signal_rank=self._safe_int(
                            signal.get("signal_rank")
                        ),
                        signal_score=self._safe_float(
                            signal.get("score")
                        ),
                        reason=reason,
                    ))
                    continue

                adtv20 = self._load_adtv20_at_signal(
                    connection,
                    symbol=symbol,
                    signal_date=signal_date,
                )
                if adtv20 is None:
                    reason = (
                        "Thiếu 20 phiên close/volume hợp lệ để tính ADTV20 "
                        f"tại ngày signal {signal_date}."
                    )
                    self.broker.complete_pending_signal(
                        pending_id,
                        processed_date=resolved_date.isoformat(),
                        status="REJECTED",
                        reason=reason,
                    )
                    freshness_results.append(PaperSignalExecution(
                        symbol=symbol,
                        status="REJECTED",
                        position_sizer=self.position_sizer.name,
                        signal_rank=self._safe_int(
                            signal.get("signal_rank")
                        ),
                        signal_score=self._safe_float(
                            signal.get("score")
                        ),
                        reason=reason,
                    ))
                    continue

                signal["adtv20"] = adtv20
                due.append(signal)

            symbols = sorted({
                str(item["symbol"]).strip().upper()
                for item in due
            })
            rows: list[tuple[Any, Any]] = []
            if symbols:
                placeholders = ",".join("?" for _ in symbols)
                query = f"""
                    SELECT symbol, open FROM prices
                    WHERE date(time) = ? AND symbol IN ({placeholders})
                """
                rows = connection.execute(
                    query,
                    (resolved_date.isoformat(), *symbols),
                ).fetchall()
        opens = {str(symbol).upper(): float(value) for symbol, value in rows}

        executable: list[dict[str, Any]] = []
        executable_ids: list[int] = []
        for signal in due:
            symbol = str(signal["symbol"]).strip().upper()
            pending_id = int(signal.pop("_pending_id"))
            open_price = opens.get(symbol)
            atr = self._read_positive_float(signal, ("atr",))
            if open_price is None or open_price <= 0 or atr is None:
                reason = "Thiếu open hoặc ATR của phiên khớp."
                self.broker.complete_pending_signal(
                    pending_id,
                    processed_date=resolved_date.isoformat(),
                    status="REJECTED",
                    reason=reason,
                )
                freshness_results.append(PaperSignalExecution(
                    symbol=symbol,
                    status="REJECTED",
                    position_sizer=self.position_sizer.name,
                    signal_rank=self._safe_int(
                        signal.get("signal_rank")
                    ),
                    signal_score=self._safe_float(
                        signal.get("score")
                    ),
                    reason=reason,
                ))
                continue
            stop, target = self.policy.calculate_levels(
                entry_price=open_price,
                atr=atr,
            )
            signal.update({
                "date": resolved_date.isoformat(),
                "entry": open_price,
                "stop_loss": stop,
                "take_profit": target,
                "execution_timing": "next_open",
            })
            executable.append(signal)
            executable_ids.append(pending_id)

        execution_result = self.execute_signals(
            executable,
            report_date=resolved_date,
            market_database_path=(
                market_database_path
            ),
        )
        for pending_id, execution in zip(
            executable_ids,
            execution_result.executions,
        ):
            self.broker.complete_pending_signal(
                pending_id,
                processed_date=resolved_date.isoformat(),
                status=execution.status,
                reason=execution.reason,
            )
        execution_result.executions = (
            freshness_results + execution_result.executions
        )
        return execution_result

    @staticmethod
    def _load_next_market_session(
        connection: sqlite3.Connection,
        *,
        signal_date: str,
    ) -> str | None:
        row = connection.execute(
            """
            SELECT date(time)
            FROM prices
            WHERE symbol = 'VNINDEX' AND date(time) > date(?)
            GROUP BY date(time)
            ORDER BY date(time)
            LIMIT 1
            """,
            (signal_date,),
        ).fetchone()
        return str(row[0]) if row is not None else None

    @classmethod
    def _load_adtv20_at_signal(
        cls,
        connection: sqlite3.Connection,
        *,
        symbol: str,
        signal_date: str,
    ) -> float | None:
        rows = connection.execute(
            """
            SELECT close, volume
            FROM prices
            WHERE symbol = ?
              AND date(time) <= date(?)
              AND close > 0
              AND volume > 0
            ORDER BY date(time) DESC
            LIMIT 20
            """,
            (symbol, signal_date),
        ).fetchall()
        if len(rows) < 20:
            return None
        values = [float(close) * float(volume) for close, volume in rows]
        if not all(math.isfinite(value) and value > 0 for value in values):
            return None
        return sum(values) / len(values) * cls.PRICE_SCALE

    def _clip_quantity_to_exposure(
        self,
        *,
        quantity: int,
        price: float,
    ) -> int:
        if quantity <= 0 or price <= 0:
            return 0

        snapshot = (
            self.broker
            .get_portfolio_snapshot()
        )

        if snapshot.equity <= 0:
            return 0

        maximum_exposure_value = (
            snapshot.equity
            * self.config.maximum_gross_exposure_pct
            / 100
        )

        remaining_exposure_value = (
            maximum_exposure_value
            - snapshot.positions_value
        )

        if remaining_exposure_value <= 0:
            return 0

        max_quantity_by_exposure = int(
            remaining_exposure_value
            / price
        )

        max_quantity_by_exposure = (
            max_quantity_by_exposure
            // self.config.lot_size
        ) * self.config.lot_size

        return max(
            min(
                quantity,
                max_quantity_by_exposure,
            ),
            0,
        )

    def _clip_quantity_to_liquidity(
        self,
        *,
        quantity: int,
        price: float,
        adtv20: float,
    ) -> int:
        if quantity <= 0 or price <= 0 or adtv20 <= 0:
            return 0
        maximum_order_value = (
            adtv20 * self.config.maximum_order_adtv20_pct / 100
        )
        maximum_quantity = int(maximum_order_value / price)
        maximum_quantity = (
            maximum_quantity // self.config.lot_size
        ) * self.config.lot_size
        return max(min(quantity, maximum_quantity), 0)

    def execute_signals(
        self,
        signals: Sequence[
            dict[str, Any]
        ],
        *,
        report_date: str | date | None = None,
        market_database_path: (
            str | Path | None
        ) = None,
    ) -> PaperExecutionBatchResult:
        if not self.config.enabled:
            return PaperExecutionBatchResult(
                enabled=False,
                position_sizer=(
                    self.position_sizer.name
                ),
            )

        inferred_report_date = report_date

        if inferred_report_date is None and signals:
            signal_date = signals[0].get("date")
            if signal_date:
                inferred_report_date = str(signal_date)[:10]

        resolved_report_date = (
            self._resolve_report_date(
                inferred_report_date
            )
        )
        market_sessions = (
            self._load_market_sessions(
                valuation_date=(
                    resolved_report_date
                ),
                market_database_path=(
                    market_database_path
                ),
            )
        )

        executions: list[
            PaperSignalExecution
        ] = []
        submitted_count = 0

        for signal in signals:
            symbol = str(
                signal.get(
                    "symbol",
                    "",
                )
            ).strip().upper()
            signal_rank = self._safe_int(
                signal.get("signal_rank")
            )
            signal_score = self._safe_float(
                signal.get("score")
            )

            if not symbol:
                executions.append(
                    PaperSignalExecution(
                        symbol="-",
                        status="SKIPPED",
                        position_sizer=(
                            self.position_sizer.name
                        ),
                        signal_rank=signal_rank,
                        signal_score=signal_score,
                        reason=(
                            "Signal không có symbol."
                        ),
                    )
                )
                continue

            regime_decision = (
                self.regime_policy.resolve(
                    signal.get("regime")
                )
            )
            regime_reason = (
                self._regime_entry_rejection_reason(
                    regime_decision
                )
            )

            if regime_reason is not None:
                executions.append(
                    PaperSignalExecution(
                        symbol=symbol,
                        status="SKIPPED",
                        position_sizer=(
                            self.position_sizer.name
                        ),
                        signal_rank=signal_rank,
                        signal_score=signal_score,
                        reason=regime_reason,
                    )
                )
                continue

            if (
                submitted_count
                >= self.config.maximum_orders_per_scan
            ):
                executions.append(
                    PaperSignalExecution(
                        symbol=symbol,
                        status="SKIPPED",
                        position_sizer=(
                            self.position_sizer.name
                        ),
                        signal_rank=signal_rank,
                        signal_score=signal_score,
                        reason=(
                            "Đã đạt giới hạn lệnh mua mới "
                            "trong ngày "
                            f"(tối đa {self.config.maximum_orders_per_scan} lệnh)."
                        ),
                    )
                )
                continue

            current_position = (
                self.broker.get_position(
                    symbol
                )
            )

            if (
                current_position is not None
                and current_position.quantity > 0
            ):
                executions.append(
                    PaperSignalExecution(
                        symbol=symbol,
                        status="SKIPPED",
                        position_sizer=(
                            self.position_sizer.name
                        ),
                        signal_rank=signal_rank,
                        signal_score=signal_score,
                        reason="Đã có vị thế paper.",
                    )
                )
                continue

            if (
                regime_decision.max_positions
                < self.config.maximum_open_positions
                and self.broker
                .get_portfolio_snapshot()
                .open_positions
                >= regime_decision.max_positions
            ):
                executions.append(
                    PaperSignalExecution(
                        symbol=symbol,
                        status="SKIPPED",
                        position_sizer=(
                            self.position_sizer.name
                        ),
                        signal_rank=signal_rank,
                        signal_score=signal_score,
                        reason=(
                            f"Regime {regime_decision.normalized_regime}: "
                            "đã đạt giới hạn "
                            f"{regime_decision.max_positions} vị thế."
                        ),
                    )
                )
                continue

            display_entry_price = (
                self._read_positive_float(
                    signal,
                    (
                        "entry",
                        "entry_price",
                        "close",
                    ),
                )
            )

            if display_entry_price is None:
                executions.append(
                    PaperSignalExecution(
                        symbol=symbol,
                        status="SKIPPED",
                        position_sizer=(
                            self.position_sizer.name
                        ),
                        signal_rank=signal_rank,
                        signal_score=signal_score,
                        reason=(
                            "Signal không có entry hợp lệ."
                        ),
                    )
                )
                continue

            broker_price = (
                display_entry_price
                * self.PRICE_SCALE
            )

            candidate = self._build_candidate(
                signal=signal,
                symbol=symbol,
                broker_price=broker_price,
            )

            sizing_context = (
                PositionSizingContext(
                    candidate=candidate,
                    cash=self.broker.get_cash(),
                    equity=(
                        self.broker
                        .get_portfolio_snapshot()
                        .equity
                    ),
                    lot_size=self.config.lot_size,
                    transaction_cost_config=(
                        self.transaction_cost_config
                    ),
                )
            )

            quantity = (
                self.position_sizer
                .calculate_quantity(
                    sizing_context
                )
            )

            quantity = self._clip_quantity_to_exposure(
                quantity=quantity,
                price=broker_price,
            )

            quantity_before_liquidity = quantity
            adtv20 = self._read_positive_float(signal, ("adtv20",))
            if adtv20 is not None:
                quantity = self._clip_quantity_to_liquidity(
                    quantity=quantity,
                    price=broker_price,
                    adtv20=adtv20,
                )

            (
                estimated_position_pct,
                estimated_risk_amount,
                estimated_risk_pct,
            ) = self._calculate_sizing_metrics(
                candidate=candidate,
                quantity=quantity,
                equity=sizing_context.equity,
            )

            if quantity <= 0:
                executions.append(
                    PaperSignalExecution(
                        symbol=symbol,
                        status="SKIPPED",
                        requested_price=(
                            broker_price
                        ),
                        position_sizer=(
                            self.position_sizer.name
                        ),
                        estimated_position_pct=(
                            estimated_position_pct
                        ),
                        estimated_risk_amount=(
                            estimated_risk_amount
                        ),
                        estimated_risk_pct=(
                            estimated_risk_pct
                        ),
                        signal_rank=signal_rank,
                        signal_score=signal_score,
                        reason=(
                            "Giới hạn thanh khoản "
                            f"{self.config.maximum_order_adtv20_pct:g}% "
                            "ADTV20 không đủ một lô giao dịch."
                            if quantity_before_liquidity > 0
                            and adtv20 is not None
                            else "PositionSizer trả về dưới một lô giao dịch."
                        ),
                    )
                )
                continue

            regime_heat_reason = (
                self._regime_heat_rejection_reason(
                    candidate=candidate,
                    quantity=quantity,
                    decision=regime_decision,
                    portfolio_equity=(
                        sizing_context.equity
                    ),
                )
            )

            if regime_heat_reason is not None:
                executions.append(
                    PaperSignalExecution(
                        symbol=symbol,
                        status="SKIPPED",
                        quantity=quantity,
                        requested_price=broker_price,
                        position_sizer=(
                            self.position_sizer.name
                        ),
                        estimated_position_pct=(
                            estimated_position_pct
                        ),
                        estimated_risk_amount=(
                            estimated_risk_amount
                        ),
                        estimated_risk_pct=(
                            estimated_risk_pct
                        ),
                        signal_rank=signal_rank,
                        signal_score=signal_score,
                        reason=regime_heat_reason,
                    )
                )
                continue

            daily_realized_pnl = sum(
                trade.realized_pnl
                for trade in self.broker.get_closed_trades()
                if trade.exit_date == resolved_report_date
            )
            fill = self.order_manager.buy_market(
                symbol=symbol,
                quantity=quantity,
                price=broker_price,
                daily_realized_pnl=daily_realized_pnl,
            )

            if fill is None:
                rejected_order = next(
                    (
                        order
                        for order
                        in reversed(
                            self.broker.get_orders()
                        )
                        if order.symbol == symbol
                    ),
                    None,
                )

                reason = (
                    rejected_order.rejection_reason
                    if rejected_order is not None
                    else "Risk Guard từ chối lệnh."
                )

                executions.append(
                    PaperSignalExecution(
                        symbol=symbol,
                        status="REJECTED",
                        quantity=quantity,
                        requested_price=(
                            broker_price
                        ),
                        position_sizer=(
                            self.position_sizer.name
                        ),
                        estimated_position_pct=(
                            estimated_position_pct
                        ),
                        estimated_risk_amount=(
                            estimated_risk_amount
                        ),
                        estimated_risk_pct=(
                            estimated_risk_pct
                        ),
                        signal_rank=signal_rank,
                        signal_score=signal_score,
                        reason=(
                            reason
                            or "Lệnh bị từ chối."
                        ),
                    )
                )
                continue

            submitted_count += 1

            self._initialize_filled_position(
                signal=signal,
                candidate=candidate,
                fill=fill,
                broker_price=broker_price,
                report_date=resolved_report_date,
            )

            executions.append(
                PaperSignalExecution(
                    symbol=symbol,
                    status="FILLED",
                    quantity=fill.quantity,
                    requested_price=(
                        display_entry_price
                    ),
                    fill_price=fill.price,
                    gross_value=fill.gross_value,
                    commission=fill.commission,
                    position_sizer=(
                        self.position_sizer.name
                    ),
                    estimated_position_pct=(
                        estimated_position_pct
                    ),
                    estimated_risk_amount=(
                        estimated_risk_amount
                    ),
                    estimated_risk_pct=(
                        estimated_risk_pct
                    ),
                    signal_rank=signal_rank,
                    signal_score=signal_score,
                )
            )

        snapshot = (
            self.broker.get_portfolio_snapshot()
        )

        positions = [
            self._build_position_summary(
                position=position,
                report_date=resolved_report_date,
                market_sessions=market_sessions,
            )
            for position
            in self.broker.get_positions()
            if position.quantity > 0
        ]

        closed_today = (
            self._load_closed_trade_summaries(
                report_date=resolved_report_date
            )
        )

        return PaperExecutionBatchResult(
            enabled=True,
            position_sizer=(
                self.position_sizer.name
            ),
            executions=executions,
            cash=snapshot.cash,
            equity=snapshot.equity,
            gross_exposure_pct=(
                snapshot.gross_exposure_pct
            ),
            open_positions=(
                snapshot.open_positions
            ),
            positions=positions,
            closed_today=closed_today,
            realized_pnl=snapshot.realized_pnl,
            unrealized_pnl=snapshot.unrealized_pnl,
        )

    @staticmethod
    def _regime_entry_rejection_reason(
        decision: RegimePortfolioDecision,
    ) -> str | None:
        if decision.normalized_regime == "UNKNOWN":
            return (
                "Không mở lệnh: signal thiếu hoặc "
                "không nhận diện được market regime."
            )

        if not decision.allow_new_positions:
            return (
                f"Regime {decision.normalized_regime}: "
                "policy không cho mở vị thế mới."
            )

        return None

    def _regime_heat_rejection_reason(
        self,
        *,
        candidate: Trade,
        quantity: int,
        decision: RegimePortfolioDecision,
        portfolio_equity: float,
    ) -> str | None:
        heat_limit = (
            decision.max_portfolio_heat_pct
        )

        if heat_limit is None:
            return None

        if (
            candidate.stop_price is None
            or candidate.stop_price <= 0
        ):
            return (
                f"Regime {decision.normalized_regime}: "
                "không tính được portfolio heat vì "
                "signal thiếu stop hợp lệ."
            )

        position_risks: list[PositionRisk] = []

        for position in self.broker.get_positions():
            if position.quantity <= 0:
                continue

            lifecycle = (
                self.broker
                .get_position_lifecycle(
                    position.symbol
                )
            )

            if lifecycle is None:
                return (
                    f"Regime {decision.normalized_regime}: "
                    "không tính được portfolio heat vì "
                    f"{position.symbol} thiếu lifecycle."
                )

            position_risks.append(
                PositionRisk.from_prices(
                    symbol=position.symbol,
                    entry_price=(
                        lifecycle.entry_price
                    ),
                    stop_price=(
                        lifecycle.stop_price
                    ),
                    quantity=position.quantity,
                    portfolio_equity=(
                        portfolio_equity
                    ),
                )
            )

        proposed_risk = PositionRisk.from_prices(
            symbol=candidate.symbol,
            entry_price=candidate.entry_price,
            stop_price=candidate.stop_price,
            quantity=quantity,
            portfolio_equity=portfolio_equity,
        )
        heat_decision = PortfolioHeat(
            max_heat_pct=heat_limit
        ).decide(
            portfolio_equity=portfolio_equity,
            position_risks=position_risks,
            proposed_risk=proposed_risk,
        )

        if heat_decision.allowed:
            return None

        return (
            f"Regime {decision.normalized_regime}: "
            "portfolio heat dự kiến "
            f"{heat_decision.projected_heat_pct:.2f}% "
            f"vượt giới hạn {heat_limit:.2f}%."
        )

    def _initialize_filled_position(
        self,
        *,
        signal: dict[str, Any],
        candidate: Trade,
        fill,
        broker_price: float,
        report_date: date,
    ) -> None:
        stop_price = candidate.stop_price

        take_profit_display = self._read_positive_float(
            signal,
            (
                "take_profit",
                "take_profit_price",
            ),
        )
        take_profit_price = (
            take_profit_display
            * self.PRICE_SCALE
            if take_profit_display is not None
            else None
        )

        if (
            stop_price is None
            or stop_price <= 0
            or stop_price >= fill.price
        ):
            raise RuntimeError(
                f"{fill.symbol}: không thể tạo lifecycle "
                "vì stop price không hợp lệ."
            )

        if (
            take_profit_price is not None
            and take_profit_price <= fill.price
        ):
            raise RuntimeError(
                f"{fill.symbol}: không thể tạo lifecycle "
                "vì take-profit không lớn hơn giá khớp."
            )

        state = PositionLifecycleState(
            symbol=fill.symbol,
            entry_date=report_date,
            entry_price=fill.price,
            initial_quantity=fill.quantity,
            stop_price=float(stop_price),
            take_profit_price=(
                float(take_profit_price)
                if take_profit_price is not None
                else None
            ),
            highest_price=max(
                float(fill.price),
                float(broker_price),
            ),
            trailing_atr_multiplier=(
                self.policy
                .trailing_atr_multiplier
            ),
            maximum_holding_days=(
                self.policy.maximum_holding_days
            ),
            updated_at=datetime.now(
                timezone.utc
            ),
        )

        self.broker.save_position_lifecycle(
            state
        )

        # PaperBroker sets market_price=fill_price at execution.
        # For end-of-day reporting, mark the new position using
        # the scanner's latest known market/reference close instead.
        self.broker.update_market_price(
            fill.symbol,
            broker_price,
            persist_snapshot=True,
        )

    def _build_position_summary(
        self,
        *,
        position,
        report_date: date,
        market_sessions: list[date] | None = None,
    ) -> PaperPositionSummary:
        lifecycle = (
            self.broker.get_position_lifecycle(
                position.symbol
            )
        )

        stop_price = None
        take_profit_price = None
        holding_days = None

        if lifecycle is not None:
            stop_candidates = [
                lifecycle.stop_price,
                lifecycle.trailing_stop_price,
            ]
            valid_stops = [
                float(value)
                for value in stop_candidates
                if value is not None
                and float(value) > 0
            ]

            if valid_stops:
                stop_price = max(
                    valid_stops
                )

            if (
                lifecycle.take_profit_price
                is not None
                and lifecycle.take_profit_price > 0
            ):
                take_profit_price = float(
                    lifecycle.take_profit_price
                )

            if market_sessions:
                holding_days = max(
                    0,
                    bisect_right(
                        market_sessions,
                        report_date,
                    )
                    - bisect_right(
                        market_sessions,
                        lifecycle.entry_date,
                    ),
                )
            else:
                holding_days = max(
                    0,
                    (
                        report_date
                        - lifecycle.entry_date
                    ).days,
                )

        return PaperPositionSummary(
            symbol=position.symbol,
            quantity=position.quantity,
            average_price=position.average_price,
            market_price=position.market_price,
            cost_basis=position.cost_basis,
            market_value=position.market_value,
            unrealized_pnl=position.unrealized_pnl,
            unrealized_pnl_pct=(
                position.unrealized_pnl_pct
            ),
            stop_price=stop_price,
            take_profit_price=(
                take_profit_price
            ),
            holding_days=holding_days,
        )

    @staticmethod
    def _load_market_sessions(
        *,
        valuation_date: date,
        market_database_path: (
            str | Path | None
        ),
    ) -> list[date] | None:
        database_path = Path(
            market_database_path
            or os.getenv(
                "MARKET_DATABASE_PATH",
                "data/market.db",
            )
        )

        if not database_path.exists():
            return None

        try:
            with sqlite3.connect(
                database_path
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
        except sqlite3.Error:
            return None

        return [
            date.fromisoformat(str(row[0]))
            for row in rows
            if row[0] is not None
        ] or None

    def _load_closed_trade_summaries(
        self,
        *,
        report_date: date,
    ) -> list[
        PaperClosedTradeSummary
    ]:
        summaries: list[
            PaperClosedTradeSummary
        ] = []

        for trade in self.broker.get_closed_trades():
            exit_date = trade.exit_date

            if isinstance(
                exit_date,
                datetime,
            ):
                exit_date = exit_date.date()

            if exit_date != report_date:
                continue

            reason = trade.exit_reason

            if hasattr(
                reason,
                "value",
            ):
                reason = reason.value

            summaries.append(
                PaperClosedTradeSummary(
                    symbol=trade.symbol,
                    quantity=trade.quantity,
                    entry_price=trade.entry_price,
                    exit_price=trade.exit_price,
                    realized_pnl=(
                        trade.realized_pnl
                    ),
                    return_pct=trade.return_pct,
                    holding_days=(
                        trade.holding_days
                    ),
                    exit_reason=str(reason),
                )
            )

        return summaries

    @staticmethod
    def _resolve_report_date(
        value: str | date | None,
    ) -> date:
        if isinstance(
            value,
            datetime,
        ):
            return value.date()

        if isinstance(
            value,
            date,
        ):
            return value

        if value:
            return date.fromisoformat(
                str(value)[:10]
            )

        return datetime.now(
            timezone.utc
        ).date()

    def _build_position_sizer(
        self,
    ) -> PositionSizer:
        if (
            self.config.position_sizer
            == "fixed_fraction"
        ):
            return FixedFractionSizer(
                position_size_pct=(
                    self.config
                    .fixed_fraction_pct
                )
            )

        return AtrRiskSizer(
            risk_per_trade_pct=(
                self.config
                .risk_per_trade_pct
            ),
            atr_stop_multiplier=(
                self.config
                .atr_stop_multiplier
            ),
            max_position_size_pct=(
                self.config
                .maximum_position_pct
            ),
        )

    def _build_candidate(
        self,
        *,
        signal: dict[str, Any],
        symbol: str,
        broker_price: float,
    ) -> Trade:
        atr_display = self._read_positive_float(
            signal,
            ("atr",),
        )

        atr_vnd = (
            atr_display
            * self.PRICE_SCALE
            if atr_display is not None
            else None
        )

        stop_display = self._read_positive_float(
            signal,
            (
                "stop_loss",
                "stop_price",
            ),
        )

        stop_vnd = (
            stop_display
            * self.PRICE_SCALE
            if stop_display is not None
            else None
        )

        risk_per_share = (
            broker_price - stop_vnd
            if (
                stop_vnd is not None
                and 0 < stop_vnd < broker_price
            )
            else None
        )

        return Trade(
            symbol=symbol,
            entry_date=self._read_signal_date(
                signal
            ),
            entry_price=broker_price,
            quantity=1,
            signal_score=self._safe_float(
                signal.get("score")
            ),
            relative_strength=self._safe_float(
                signal.get(
                    "relative_strength_20d"
                )
            ),
            adx=self._safe_float(
                signal.get("adx")
            ),
            volume_ratio=self._safe_float(
                signal.get("volume_ratio")
            ),
            atr=atr_vnd,
            market_regime=(
                str(
                    signal.get(
                        "regime",
                        "",
                    )
                )
                or None
            ),
            entry_model=(
                str(
                    signal.get(
                        "entry_model",
                        "",
                    )
                )
                or None
            ),
            stop_price=stop_vnd,
            risk_per_share=risk_per_share,
        )

    @staticmethod
    def _calculate_sizing_metrics(
        *,
        candidate: Trade,
        quantity: int,
        equity: float,
    ) -> tuple[float, float, float]:
        if (
            quantity <= 0
            or equity <= 0
        ):
            return 0.0, 0.0, 0.0

        position_value = (
            candidate.entry_price
            * quantity
        )

        position_pct = (
            position_value
            / equity
            * 100
        )

        risk_per_share = (
            candidate.risk_per_share
            if (
                candidate.risk_per_share
                is not None
                and candidate.risk_per_share > 0
            )
            else 0.0
        )

        risk_amount = (
            risk_per_share
            * quantity
        )

        risk_pct = (
            risk_amount
            / equity
            * 100
        )

        return (
            position_pct,
            risk_amount,
            risk_pct,
        )

    @staticmethod
    def _read_signal_date(
        signal: dict[str, Any],
    ) -> datetime:
        value = signal.get("date")

        if value:
            try:
                return datetime.fromisoformat(
                    str(value)
                ).replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                pass

        return datetime.now(
            timezone.utc
        )

    @staticmethod
    def _read_positive_float(
        signal: dict[str, Any],
        keys: tuple[str, ...],
    ) -> float | None:
        for key in keys:
            value = signal.get(key)

            try:
                number = float(value)
            except (TypeError, ValueError):
                continue

            if number > 0:
                return number

        return None

    @staticmethod
    def _safe_float(
        value: object,
    ) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_int(
        value: object,
    ) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
