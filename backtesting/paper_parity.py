from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

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
from execution.exit_engine import (
    ExitEngine,
    ExitEngineConfig,
)
from execution.exit_models import (
    ExitBar,
    ExitDecision,
    PositionExitState,
)
from execution.order_manager import (
    OrderManager,
)
from execution.paper_broker import (
    PaperBroker,
)
from execution.risk_guard import (
    RiskGuard,
    RiskLimits,
)
from execution.signal_executor import (
    PaperExecutionConfig,
)


@dataclass(frozen=True, slots=True)
class BacktestPaperParityConfig:
    initial_cash: float
    position_sizer: str
    risk_per_trade_pct: float
    atr_stop_multiplier: float
    fixed_fraction_pct: float
    lot_size: int
    commission_rate: float
    slippage_bps: float
    maximum_position_pct: float
    maximum_gross_exposure_pct: float
    maximum_open_positions: int
    maximum_daily_loss_pct: float
    minimum_cash_buffer_pct: float
    sell_tax_rate: float = 0.0

    @classmethod
    def from_paper_config(
        cls,
        paper: PaperExecutionConfig,
        *,
        sell_tax_rate: float = 0.0,
    ) -> "BacktestPaperParityConfig":
        return cls(
            initial_cash=paper.initial_cash,
            position_sizer=paper.position_sizer,
            risk_per_trade_pct=(
                paper.risk_per_trade_pct
            ),
            atr_stop_multiplier=(
                paper.atr_stop_multiplier
            ),
            fixed_fraction_pct=(
                paper.fixed_fraction_pct
            ),
            lot_size=paper.lot_size,
            commission_rate=(
                paper.commission_rate
            ),
            slippage_bps=paper.slippage_bps,
            maximum_position_pct=(
                paper.maximum_position_pct
            ),
            maximum_gross_exposure_pct=(
                paper.maximum_gross_exposure_pct
            ),
            maximum_open_positions=(
                paper.maximum_open_positions
            ),
            maximum_daily_loss_pct=(
                paper.maximum_daily_loss_pct
            ),
            minimum_cash_buffer_pct=(
                paper.minimum_cash_buffer_pct
            ),
            sell_tax_rate=sell_tax_rate,
        )

    @classmethod
    def from_env(
        cls,
        *,
        sell_tax_rate: float = 0.0,
    ) -> "BacktestPaperParityConfig":
        return cls.from_paper_config(
            PaperExecutionConfig.from_env(),
            sell_tax_rate=sell_tax_rate,
        )

    @property
    def commission_pct(
        self,
    ) -> float:
        return (
            self.commission_rate
            * 100
        )

    @property
    def slippage_pct(
        self,
    ) -> float:
        return (
            self.slippage_bps
            / 100
        )

    @property
    def sell_tax_pct(
        self,
    ) -> float:
        return (
            self.sell_tax_rate
            * 100
        )

    def transaction_cost_config(
        self,
    ) -> TransactionCostConfig:
        return TransactionCostConfig(
            buy_commission_pct=(
                self.commission_pct
            ),
            sell_commission_pct=(
                self.commission_pct
            ),
            sell_tax_pct=(
                self.sell_tax_pct
            ),
            buy_slippage_pct=(
                self.slippage_pct
            ),
            sell_slippage_pct=(
                self.slippage_pct
            ),
        )

    def risk_limits(
        self,
    ) -> RiskLimits:
        return RiskLimits(
            maximum_position_pct=(
                self.maximum_position_pct
            ),
            maximum_gross_exposure_pct=(
                self
                .maximum_gross_exposure_pct
            ),
            maximum_open_positions=(
                self.maximum_open_positions
            ),
            maximum_daily_loss_pct=(
                self.maximum_daily_loss_pct
            ),
            minimum_cash_buffer_pct=(
                self.minimum_cash_buffer_pct
            ),
        )

    def build_position_sizer(
        self,
    ) -> PositionSizer:
        if (
            self.position_sizer
            == "fixed_fraction"
        ):
            return FixedFractionSizer(
                position_size_pct=(
                    self.fixed_fraction_pct
                )
            )

        return AtrRiskSizer(
            risk_per_trade_pct=(
                self.risk_per_trade_pct
            ),
            atr_stop_multiplier=(
                self.atr_stop_multiplier
            ),
            max_position_size_pct=(
                self.maximum_position_pct
            ),
        )


@dataclass(frozen=True, slots=True)
class EntryParityResult:
    symbol: str
    reference_price: float
    quantity: int
    paper_fill_price: float
    backtest_fill_price: float
    paper_commission: float
    backtest_commission: float
    paper_cash_after: float
    backtest_cash_after: float

    @property
    def passed(
        self,
    ) -> bool:
        return all(
            (
                math.isclose(
                    self.paper_fill_price,
                    self.backtest_fill_price,
                    abs_tol=0.01,
                ),
                math.isclose(
                    self.paper_commission,
                    self.backtest_commission,
                    abs_tol=0.01,
                ),
                math.isclose(
                    self.paper_cash_after,
                    self.backtest_cash_after,
                    abs_tol=0.01,
                ),
            )
        )


@dataclass(frozen=True, slots=True)
class ExitParityResult:
    paper_decision: ExitDecision
    backtest_decision: ExitDecision

    @property
    def passed(
        self,
    ) -> bool:
        return (
            self.paper_decision.should_exit
            == self.backtest_decision.should_exit
            and self.paper_decision.reason
            == self.backtest_decision.reason
            and (
                (
                    self.paper_decision
                    .execution_price
                    is None
                    and self.backtest_decision
                    .execution_price
                    is None
                )
                or math.isclose(
                    float(
                        self.paper_decision
                        .execution_price
                    ),
                    float(
                        self.backtest_decision
                        .execution_price
                    ),
                    abs_tol=0.01,
                )
            )
        )


def calculate_parity_quantity(
    *,
    config: BacktestPaperParityConfig,
    symbol: str,
    entry_price: float,
    stop_price: float,
    atr: float | None,
    cash: float | None = None,
    equity: float | None = None,
) -> int:
    candidate = Trade(
        symbol=symbol,
        entry_date=datetime.now(),
        entry_price=entry_price,
        quantity=1,
        stop_price=stop_price,
        atr=atr,
    )

    context = PositionSizingContext(
        candidate=candidate,
        cash=(
            config.initial_cash
            if cash is None
            else cash
        ),
        equity=(
            config.initial_cash
            if equity is None
            else equity
        ),
        lot_size=config.lot_size,
        transaction_cost_config=(
            config
            .transaction_cost_config()
        ),
    )

    return (
        config.build_position_sizer()
        .calculate_quantity(
            context
        )
    )


def audit_entry_parity(
    *,
    config: BacktestPaperParityConfig,
    symbol: str = "VNM",
    reference_price: float = 60_000.0,
    stop_price: float = 58_000.0,
    atr: float | None = 1_000.0,
) -> EntryParityResult:
    from backtesting.portfolio import (
        Portfolio,
    )

    quantity = calculate_parity_quantity(
        config=config,
        symbol=symbol,
        entry_price=reference_price,
        stop_price=stop_price,
        atr=atr,
    )

    if quantity <= 0:
        raise RuntimeError(
            "PositionSizer trả về quantity <= 0."
        )

    with TemporaryDirectory() as temp_dir:
        broker = PaperBroker(
            initial_cash=config.initial_cash,
            commission_rate=(
                config.commission_rate
            ),
            slippage_bps=(
                config.slippage_bps
            ),
            database_path=(
                Path(temp_dir)
                / "paper.db"
            ),
            restore_state=False,
        )

        manager = OrderManager(
            broker=broker,
            risk_guard=RiskGuard(
                config.risk_limits()
            ),
        )

        paper_fill = manager.buy_market(
            symbol=symbol,
            quantity=quantity,
            price=reference_price,
        )

        if paper_fill is None:
            raise RuntimeError(
                "Paper order bị RiskGuard từ chối."
            )

        backtest_portfolio = Portfolio(
            initial_cash=config.initial_cash,
            transaction_cost_config=(
                config
                .transaction_cost_config()
            ),
        )

        backtest_trade = (
            backtest_portfolio
            .open_position(
                symbol=symbol,
                entry_date=datetime.now(),
                entry_price=reference_price,
                quantity=quantity,
                stop_price=stop_price,
            )
        )

        return EntryParityResult(
            symbol=symbol,
            reference_price=reference_price,
            quantity=quantity,
            paper_fill_price=paper_fill.price,
            backtest_fill_price=(
                backtest_trade.entry_price
            ),
            paper_commission=(
                paper_fill.commission
            ),
            backtest_commission=(
                backtest_trade.buy_commission
            ),
            paper_cash_after=(
                broker.portfolio.cash
            ),
            backtest_cash_after=(
                backtest_portfolio.cash
            ),
        )


def audit_exit_parity(
    *,
    state: PositionExitState,
    bar: ExitBar,
    exit_signal: bool = False,
    config: ExitEngineConfig | None = None,
) -> ExitParityResult:
    # Both environments intentionally use one pure ExitEngine.
    paper_engine = ExitEngine(
        config
    )
    backtest_engine = ExitEngine(
        config
    )

    return ExitParityResult(
        paper_decision=(
            paper_engine.evaluate(
                state=state,
                bar=bar,
                exit_signal=exit_signal,
            )
        ),
        backtest_decision=(
            backtest_engine.evaluate(
                state=state,
                bar=bar,
                exit_signal=exit_signal,
            )
        ),
    )
