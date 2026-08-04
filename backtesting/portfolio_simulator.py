from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from backtesting.portfolio import (
    InsufficientCashError,
    Portfolio,
)
from backtesting.ranking import (
    RankingMethod,
    parse_ranking_method,
    rank_candidates,
)
from backtesting.trade import Trade
from backtesting.transaction_cost import (
    TransactionCostConfig,
)
from backtesting.decision_engine import (
    CandidateDecision,
    DecisionAction,
    decide_candidate,
)
from backtesting.position_sizers import (
    FixedFractionSizer,
    PositionSizer,
    PositionSizingContext,
)
from backtesting.portfolio_heat import (
    PortfolioHeat,
    PositionRisk,
)
from backtesting.trade_risk import (
    TradeRiskMetadata,
    resolve_candidate_stop_price,
)
@dataclass(slots=True)
class RejectedTrade:
    trade: Trade
    reason: str

@dataclass(slots=True)
class ReplacementOpportunity:
    decision: CandidateDecision
    event_date: datetime

@dataclass(slots=True)
class PortfolioSimulationResult:
    executed_trades: list[Trade]
    rejected_trades: list[RejectedTrade]
    replacement_opportunities: list[
        ReplacementOpportunity
    ]
    equity_curve: pd.DataFrame
    final_cash: float
    final_market_value: float
    final_equity: float
    final_open_positions: int


class PortfolioSimulator:
    def __init__(
        self,
        initial_cash: float,
        position_size_pct: float = 20.0,
        position_sizer: PositionSizer | None = None,
        max_positions: int = 5,
        lot_size: int = 100,
        ranking_method: RankingMethod | str = (
            RankingMethod.FIRST_COME
        ),
        transaction_cost_config: (
            TransactionCostConfig | None
        ) = None,
        max_portfolio_heat_pct: float | None = None,
    ) -> None:
        if initial_cash <= 0:
            raise ValueError(
                "initial_cash must be greater than 0"
            )

        if not 0 < position_size_pct <= 100:
            raise ValueError(
                "position_size_pct must be between "
                "0 and 100"
            )

        if max_positions < 1:
            raise ValueError(
                "max_positions must be at least 1"
            )

        if lot_size < 1:
            raise ValueError(
                "lot_size must be at least 1"
            )

        self.transaction_cost_config = (
            transaction_cost_config
            or TransactionCostConfig(
                buy_commission_pct=0.0,
                sell_commission_pct=0.0,
                sell_tax_pct=0.0,
            )
        )

        self.portfolio = Portfolio(
            initial_cash=initial_cash,
            allow_duplicate_symbols=False,
            transaction_cost_config=(
                self.transaction_cost_config
            ),
        )

        self.position_size_pct = float(
            position_size_pct
        )

        self.position_sizer = (
            position_sizer
            or FixedFractionSizer(
                position_size_pct=position_size_pct,
            )
        )

        self.max_positions = int(
            max_positions
        )

        self.lot_size = int(
            lot_size
        )

        self.ranking_method = (
            parse_ranking_method(
                ranking_method
            )
        )

        self.portfolio_heat = PortfolioHeat(
            max_heat_pct=max_portfolio_heat_pct,
        )

    def _calculate_quantity(
        self,
        candidate: Trade,
    ) -> int:
        context = PositionSizingContext(
            candidate=candidate,
            cash=self.portfolio.cash,
            equity=self.portfolio.equity(),
            lot_size=self.lot_size,
            transaction_cost_config=(
                self.transaction_cost_config
            ),
        )

        return (
            self.position_sizer
            .calculate_quantity(
                context
            )
        )

    def _build_open_position_risks(
        self,
        *,
        portfolio_equity: float,
    ) -> list[PositionRisk]:
        position_risks: list[
            PositionRisk
        ] = []

        for trade in (
            self.portfolio.open_positions
        ):
            stop_price = getattr(
                trade,
                "stop_price",
                None,
            )

            if stop_price is None:
                continue

            position_risks.append(
                PositionRisk.from_prices(
                    symbol=trade.symbol,
                    entry_price=(
                        trade.entry_price
                    ),
                    stop_price=stop_price,
                    quantity=trade.quantity,
                    portfolio_equity=(
                        portfolio_equity
                    ),
                )
            )

        return position_risks

    def _current_heat_pct(
        self,
    ) -> float:
        equity = self.portfolio.equity()

        position_risks = (
            self._build_open_position_risks(
                portfolio_equity=equity,
            )
        )

        return (
            self.portfolio_heat
            .snapshot(
                portfolio_equity=equity,
                position_risks=(
                    position_risks
                ),
            )
            .current_heat_pct
        )

    def _reject_trade(
        self,
        *,
        rejected_trades: list[RejectedTrade],
        candidate: Trade,
        reason: str,
    ) -> None:
        rejected_trades.append(
            RejectedTrade(
                trade=candidate,
                reason=reason,
            )
        )

    def _close_candidate(
        self,
        *,
        candidate: Trade,
        active_trades: dict[int, Trade],
        executed_trades: list[Trade],
    ) -> None:
        candidate_id = id(
            candidate
        )

        active_trade = (
            active_trades.get(
                candidate_id
            )
        )

        if active_trade is None:
            return

        closed_trade = (
            self.portfolio.close_position(
                symbol=active_trade.symbol,
                exit_date=candidate.exit_date,
                exit_price=candidate.exit_price,
                reason=candidate.exit_reason,
                execution=candidate.execution,
            )
        )

        executed_trades.append(
            closed_trade
        )

        del active_trades[
            candidate_id
        ]

    def _open_candidate(
        self,
        *,
        candidate: Trade,
        event_date: datetime,
        active_trades: dict[int, Trade],
        rejected_trades: list[RejectedTrade],
        replacement_opportunities: list[
            ReplacementOpportunity
        ],
    ) -> None:

        decision = decide_candidate(
            candidate=candidate,
            open_positions=(
                self.portfolio.open_positions
            ),
            max_positions=self.max_positions,
            ranking_method=self.ranking_method,
            replacement_threshold=0.0,
            allow_duplicate_symbols=False,
        )

        if (
            decision.action
            == DecisionAction.WOULD_REPLACE
        ):
            replacement_opportunities.append(
                ReplacementOpportunity(
                    decision=decision,
                    event_date=event_date,
                )
            )

            self._reject_trade(
                rejected_trades=rejected_trades,
                candidate=candidate,
                reason="would_replace",
            )

            return

        if (
            decision.action
            == DecisionAction.REJECT
        ):
            self._reject_trade(
                rejected_trades=rejected_trades,
                candidate=candidate,
                reason=decision.reason,
            )

            return

        quantity = self._calculate_quantity(
            candidate
        )

        if quantity <= 0:
            self._reject_trade(
                rejected_trades=(
                    rejected_trades
                ),
                candidate=candidate,
                reason="insufficient_cash",
            )

            return

        stop_price = (
            resolve_candidate_stop_price(
                candidate,
            )
        )

        risk_metadata = None

        if stop_price is not None:
            risk_metadata = (
                TradeRiskMetadata.build(
                    entry_price=(
                        candidate.entry_price
                    ),
                    stop_price=stop_price,
                    quantity=quantity,
                    portfolio_equity=(
                        self.portfolio.equity()
                    ),
                )
            )

        if (
            self.portfolio_heat.max_heat_pct
            is not None
        ):
            if risk_metadata is None:
                self._reject_trade(
                    rejected_trades=(
                        rejected_trades
                    ),
                    candidate=candidate,
                    reason="missing_stop_price",
                )

                return

            portfolio_equity = (
                self.portfolio.equity()
            )

            existing_risks = (
                self._build_open_position_risks(
                    portfolio_equity=(
                        portfolio_equity
                    ),
                )
            )

            proposed_risk = (
                PositionRisk.from_prices(
                    symbol=candidate.symbol,
                    entry_price=(
                        candidate.entry_price
                    ),
                    stop_price=(
                        risk_metadata.stop_price
                    ),
                    quantity=quantity,
                    portfolio_equity=(
                        portfolio_equity
                    ),
                )
            )

            heat_decision = (
                self.portfolio_heat.decide(
                    portfolio_equity=(
                        portfolio_equity
                    ),
                    position_risks=(
                        existing_risks
                    ),
                    proposed_risk=(
                        proposed_risk
                    ),
                )
            )

            if not heat_decision.allowed:
                self._reject_trade(
                    rejected_trades=(
                        rejected_trades
                    ),
                    candidate=candidate,
                    reason=(
                        heat_decision.reason
                    ),
                )

                return

        try:
            opened_trade = (
                self.portfolio.open_position(
                    symbol=candidate.symbol,
                    entry_date=(
                        candidate.entry_date
                    ),
                    entry_price=(
                        candidate.entry_price
                    ),
                    quantity=quantity,
                    signal_score=(
                        candidate.signal_score
                    ),
                    relative_strength=(
                        candidate.relative_strength
                    ),
                    adx=candidate.adx,
                    volume_ratio=(
                        candidate.volume_ratio
                    ),
                    atr=getattr(
                        candidate,
                        "atr",
                        None,
                    ),
                    stop_price=(
                        risk_metadata.stop_price
                        if risk_metadata
                        is not None
                        else None
                    ),
                    risk_per_share=(
                        risk_metadata
                        .risk_per_share
                        if risk_metadata
                        is not None
                        else None
                    ),
                    risk_amount=(
                        risk_metadata.risk_amount
                        if risk_metadata
                        is not None
                        else None
                    ),
                    risk_pct=(
                        risk_metadata.risk_pct
                        if risk_metadata
                        is not None
                        else None
                    ),
                    market_regime=(
                        candidate.market_regime
                    ),
                    entry_model=(
                        candidate.entry_model
                    ),
                )
            )

        except InsufficientCashError:
            self._reject_trade(
                rejected_trades=(
                    rejected_trades
                ),
                candidate=candidate,
                reason="insufficient_cash",
            )

            return

        active_trades[
            id(
                candidate
            )
        ] = opened_trade

    def _append_equity_row(
        self,
        *,
        equity_rows: list[dict],
        event_date: datetime,
    ) -> None:
        equity_rows.append(
            {
                "date": event_date,
                "cash": (
                    self.portfolio.cash
                ),
                "market_value": (
                    self.portfolio
                    .market_value()
                ),
                "equity": (
                    self.portfolio.equity()
                ),
                "open_positions": len(
                    self.portfolio
                    .open_positions
                ),
                "closed_positions": len(
                    self.portfolio
                    .closed_positions
                ),
                "portfolio_heat_pct": (
                    self._current_heat_pct()
                ),
                "max_portfolio_heat_pct": (
                    self.portfolio_heat
                    .max_heat_pct
                ),
            }
        )

    def simulate(
        self,
        candidate_trades: list[Trade],
    ) -> PortfolioSimulationResult:
        executed_trades: list[
            Trade
        ] = []

        rejected_trades: list[
            RejectedTrade
        ] = []

        replacement_opportunities: list[
            ReplacementOpportunity
        ] = []

        equity_rows: list[
            dict
        ] = []

        events: list[
            tuple[
                datetime,
                int,
                Trade,
            ]
        ] = []

        for trade in candidate_trades:
            if not trade.is_closed:
                continue

            if (
                trade.entry_date
                == trade.exit_date
            ):
                events.append(
                    (
                        trade.entry_date,
                        1,
                        trade,
                    )
                )

                events.append(
                    (
                        trade.exit_date,
                        2,
                        trade,
                    )
                )

            else:
                events.append(
                    (
                        trade.exit_date,
                        0,
                        trade,
                    )
                )

                events.append(
                    (
                        trade.entry_date,
                        1,
                        trade,
                    )
                )

        events.sort(
            key=lambda item: (
                item[0],
                item[1],
                item[2].symbol,
            )
        )

        grouped_events: dict[
            datetime,
            list[
                tuple[
                    datetime,
                    int,
                    Trade,
                ]
            ],
        ] = {}

        for event in events:
            event_date = event[0]

            grouped_events.setdefault(
                event_date,
                [],
            ).append(
                event
            )

        active_trades: dict[
            int,
            Trade,
        ] = {}

        for event_date in sorted(
            grouped_events
        ):
            daily_events = (
                grouped_events[
                    event_date
                ]
            )

            normal_exit_candidates = [
                candidate
                for (
                    _,
                    event_type,
                    candidate,
                ) in daily_events
                if event_type == 0
            ]

            entry_candidates = [
                candidate
                for (
                    _,
                    event_type,
                    candidate,
                ) in daily_events
                if event_type == 1
            ]

            same_day_exit_candidates = [
                candidate
                for (
                    _,
                    event_type,
                    candidate,
                ) in daily_events
                if event_type == 2
            ]

            # Đóng các vị thế cũ trước để giải phóng
            # tiền và slot trong danh mục.
            for candidate in (
                normal_exit_candidates
            ):
                self._close_candidate(
                    candidate=candidate,
                    active_trades=(
                        active_trades
                    ),
                    executed_trades=(
                        executed_trades
                    ),
                )

            ranked_candidates = (
                rank_candidates(
                    entry_candidates,
                    method=(
                        self.ranking_method
                    ),
                )
            )

            # Các candidate cùng ngày được xếp hạng
            # trước khi danh mục mở vị thế.
            for candidate in (
                ranked_candidates
            ):
                self._open_candidate(
                    candidate=candidate,
                    event_date=event_date,
                    active_trades=(
                        active_trades
                    ),
                    rejected_trades=(
                        rejected_trades
                    ),
                    replacement_opportunities=(
                        replacement_opportunities
                    ),
                )

            # Trường hợp entry và exit cùng ngày:
            # phải mở trước rồi mới đóng.
            for candidate in (
                same_day_exit_candidates
            ):
                self._close_candidate(
                    candidate=candidate,
                    active_trades=(
                        active_trades
                    ),
                    executed_trades=(
                        executed_trades
                    ),
                )

            self._append_equity_row(
                equity_rows=equity_rows,
                event_date=event_date,
            )

        if active_trades:
            raise RuntimeError(
                f"{len(active_trades)} "
                "vị thế vẫn đang mở."
            )

        equity_curve = pd.DataFrame(
            equity_rows
        )

        if not equity_curve.empty:
            equity_curve = (
                equity_curve
                .sort_values(
                    "date"
                )
                .reset_index(
                    drop=True
                )
            )

            equity_curve[
                "equity_peak"
            ] = (
                equity_curve[
                    "equity"
                ]
                .cummax()
            )

            equity_curve[
                "drawdown_pct"
            ] = (
                (
                    equity_curve[
                        "equity"
                    ]
                    / equity_curve[
                        "equity_peak"
                    ]
                )
                - 1
            ) * 100

        return PortfolioSimulationResult(
            executed_trades=(
                executed_trades
            ),
            rejected_trades=(
                rejected_trades
            ),
            replacement_opportunities=(
                replacement_opportunities
            ),
            equity_curve=equity_curve,
            final_cash=(
                self.portfolio.cash
            ),
            final_market_value=(
                self.portfolio
                .market_value()
            ),
            final_equity=(
                self.portfolio.equity()
            ),
            final_open_positions=len(
                self.portfolio
                .open_positions
            ),
        )