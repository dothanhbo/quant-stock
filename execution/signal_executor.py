from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from backtesting.position_sizers import (
    AtrRiskSizer,
    FixedFractionSizer,
    PositionSizer,
    PositionSizingContext,
)
from backtesting.trade import Trade
from backtesting.transaction_cost import (
    TransactionCostConfig,
)
from execution.order_manager import OrderManager
from execution.paper_broker import PaperBroker
from execution.risk_guard import RiskGuard, RiskLimits


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
    reason: str = ""


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
    ) -> None:
        self.config = config

        self.broker = PaperBroker(
            initial_cash=config.initial_cash,
            commission_rate=(
                config.commission_rate
            ),
            slippage_bps=(
                config.slippage_bps
            ),
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
                sell_tax_pct=0.0,
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

    def execute_signals(
        self,
        signals: Sequence[
            dict[str, Any]
        ],
    ) -> PaperExecutionBatchResult:
        if not self.config.enabled:
            return PaperExecutionBatchResult(
                enabled=False,
                position_sizer=(
                    self.position_sizer.name
                ),
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

            if not symbol:
                executions.append(
                    PaperSignalExecution(
                        symbol="-",
                        status="SKIPPED",
                        position_sizer=(
                            self.position_sizer.name
                        ),
                        reason=(
                            "Signal không có symbol."
                        ),
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
                        reason=(
                            "Đã đạt giới hạn lệnh "
                            "trong một lần scan."
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
                        reason="Đã có vị thế paper.",
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
                            display_entry_price
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
                        reason=(
                            "PositionSizer trả về dưới "
                            "một lô giao dịch."
                        ),
                    )
                )
                continue

            fill = self.order_manager.buy_market(
                symbol=symbol,
                quantity=quantity,
                price=broker_price,
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
                            display_entry_price
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
                        reason=(
                            reason
                            or "Lệnh bị từ chối."
                        ),
                    )
                )
                continue

            submitted_count += 1

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
                )
            )

        snapshot = (
            self.broker.get_portfolio_snapshot()
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
        )

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
