from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from types import MappingProxyType
from typing import Mapping

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
from backtesting.portfolio_allocation import (
    PortfolioAllocator,
    AllocationCandidate,
)
from backtesting.portfolio_heat import (
    PortfolioHeat,
    PositionRisk,
)
from backtesting.trade_risk import (
    TradeRiskMetadata,
    resolve_candidate_stop_price,
)
from backtesting.regime_policy import (
    RegimePortfolioDecision,
    RegimePortfolioPolicy,
)
@dataclass(slots=True)
class RejectedTrade:
    trade: Trade
    reason: str

@dataclass(slots=True)
class ReplacementOpportunity:
    decision: CandidateDecision
    event_date: datetime


@dataclass(frozen=True, slots=True)
class CandidatePriorityEvidence:
    """Immutable research-only priority evidence for one accepted candidate."""

    candidate_key: str
    symbol: str
    signal_date: datetime
    entry_date: datetime
    volume_ratio: float | None
    volume_ratio_finite: bool
    q70_quality_score: float
    baseline_signal_score: float | None
    baseline_within_entry_date_ordinal: int
    signal_date_group_key: str
    signal_date_group_size: int
    signal_date_group_slot_ordinals: tuple[int, ...]
    variant_within_signal_date_ordinal: int
    final_simulator_priority_ordinal: int
    ranking_policy_fingerprint: str

    def __post_init__(self) -> None:
        symbol = str(self.symbol).strip().upper()
        key = str(self.candidate_key).strip()
        fingerprint = str(self.ranking_policy_fingerprint).strip()
        if not symbol or not key or not fingerprint:
            raise ValueError("priority evidence requires candidate key, symbol, and policy fingerprint")
        if not isinstance(self.signal_date, datetime) or not isinstance(self.entry_date, datetime):
            raise TypeError("priority evidence dates must be datetime values")
        try:
            raw_volume = None if self.volume_ratio is None else float(self.volume_ratio)
        except (TypeError, ValueError) as exc:
            raise ValueError("volume_ratio must be numeric or None") from exc
        finite = raw_volume is not None and math.isfinite(raw_volume)
        if bool(self.volume_ratio_finite) != finite:
            raise ValueError("volume_ratio_finite does not match volume_ratio")
        quality = float(self.q70_quality_score)
        if not math.isfinite(quality):
            raise ValueError("q70_quality_score must be finite")
        ordinals = (
            self.baseline_within_entry_date_ordinal,
            self.signal_date_group_size,
            self.variant_within_signal_date_ordinal,
            self.final_simulator_priority_ordinal,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in ordinals):
            raise ValueError("priority ordinals and group size must be positive integers")
        slots = tuple(self.signal_date_group_slot_ordinals)
        if len(slots) != self.signal_date_group_size or tuple(sorted(set(slots))) != slots:
            raise ValueError("signal-date subgroup slots must be unique canonical ordinals")
        if self.baseline_within_entry_date_ordinal not in slots:
            raise ValueError("baseline ordinal must belong to the signal-date subgroup slot set")
        if self.final_simulator_priority_ordinal not in slots:
            raise ValueError("final ordinal must preserve the signal-date subgroup slot set")
        expected_group = self.signal_date.date().isoformat()
        if self.signal_date_group_key != expected_group:
            raise ValueError("signal-date group key does not match signal_date")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "candidate_key", key)
        object.__setattr__(self, "volume_ratio", raw_volume)
        object.__setattr__(self, "q70_quality_score", quality)
        object.__setattr__(self, "signal_date_group_slot_ordinals", slots)
        object.__setattr__(self, "ranking_policy_fingerprint", fingerprint)

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
        portfolio_allocator: PortfolioAllocator | None = None,
        max_positions: int = 5,
        lot_size: int = 100,
        ranking_method: RankingMethod | str = (
            RankingMethod.FIRST_COME
        ),
        transaction_cost_config: (
            TransactionCostConfig | None
        ) = None,
        max_portfolio_heat_pct: float | None = None,
        regime_policy: RegimePortfolioPolicy | None = None,
        max_new_positions_per_day: int | None = None,
        maximum_gross_exposure_pct: float | None = None,
        minimum_cash_buffer_pct: float = 0.0,
        candidate_priority_evidence: tuple[CandidatePriorityEvidence, ...] | None = None,
        candidate_priority_policy_fingerprint: str | None = None,
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

        if (
            max_new_positions_per_day is not None
            and max_new_positions_per_day < 1
        ):
            raise ValueError(
                "max_new_positions_per_day must be at least 1"
            )

        if (
            maximum_gross_exposure_pct is not None
            and not 0 < maximum_gross_exposure_pct <= 100
        ):
            raise ValueError(
                "maximum_gross_exposure_pct must be in (0, 100]"
            )

        if not 0 <= minimum_cash_buffer_pct < 100:
            raise ValueError(
                "minimum_cash_buffer_pct must be in [0, 100)"
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

        self.portfolio_allocator = (
            portfolio_allocator
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

        self.regime_policy = (
            regime_policy
        )

        self.max_new_positions_per_day = (
            max_new_positions_per_day
        )
        self.maximum_gross_exposure_pct = (
            maximum_gross_exposure_pct
        )
        self.minimum_cash_buffer_pct = float(
            minimum_cash_buffer_pct
        )
        evidence = None if candidate_priority_evidence is None else tuple(candidate_priority_evidence)
        if evidence is None:
            if candidate_priority_policy_fingerprint is not None:
                raise ValueError("priority fingerprint requires candidate priority evidence")
            self._candidate_priority_by_key: Mapping[str, CandidatePriorityEvidence] | None = None
            self.candidate_priority_policy_fingerprint = None
        else:
            fingerprint = str(candidate_priority_policy_fingerprint or "").strip()
            if not fingerprint:
                raise ValueError("candidate priority evidence requires a policy fingerprint")
            if not all(isinstance(item, CandidatePriorityEvidence) for item in evidence):
                raise TypeError("candidate priority evidence must contain immutable evidence records")
            keys = tuple(item.candidate_key for item in evidence)
            if len(set(keys)) != len(keys):
                raise ValueError("duplicate candidate priority evidence key")
            if any(item.ranking_policy_fingerprint != fingerprint for item in evidence):
                raise ValueError("candidate priority policy fingerprint mismatch")
            self._candidate_priority_by_key = MappingProxyType({item.candidate_key: item for item in evidence})
            self.candidate_priority_policy_fingerprint = fingerprint

    @staticmethod
    def _candidate_key(candidate: Trade) -> str:
        if candidate.signal_date is None:
            raise ValueError("priority-ranked candidate requires signal_date")
        return f"{candidate.symbol.strip().upper()}|{candidate.signal_date.isoformat()}"

    @staticmethod
    def _finite_or_none(value: object) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    @staticmethod
    def _numeric_signature(value: object) -> tuple[str, float | None]:
        if value is None:
            return "none", None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return "invalid", None
        if math.isnan(number):
            return "nan", None
        if math.isinf(number):
            return ("positive_infinity" if number > 0 else "negative_infinity"), None
        return "finite", number

    def _rank_entry_candidates(
        self,
        candidates: list[Trade],
        *,
        event_date: datetime,
    ) -> list[Trade]:
        baseline = rank_candidates(candidates, method=self.ranking_method)
        evidence_by_key = self._candidate_priority_by_key
        if evidence_by_key is None:
            return baseline

        records: list[CandidatePriorityEvidence] = []
        for ordinal, candidate in enumerate(baseline, start=1):
            key = self._candidate_key(candidate)
            try:
                evidence = evidence_by_key[key]
            except KeyError as exc:
                raise ValueError(f"missing candidate priority evidence: {key}") from exc
            if evidence.symbol != candidate.symbol.strip().upper():
                raise ValueError(f"priority evidence symbol mismatch: {key}")
            if evidence.signal_date != candidate.signal_date or evidence.entry_date != candidate.entry_date:
                raise ValueError(f"priority evidence date mismatch: {key}")
            if evidence.entry_date != event_date:
                raise ValueError(f"priority evidence entry group mismatch: {key}")
            if self._numeric_signature(candidate.volume_ratio) != self._numeric_signature(evidence.volume_ratio):
                raise ValueError(f"priority evidence volume ratio mismatch: {key}")
            actual_score = self._finite_or_none(candidate.signal_score)
            if actual_score != evidence.baseline_signal_score:
                raise ValueError(f"priority evidence signal score mismatch: {key}")
            if evidence.baseline_within_entry_date_ordinal != ordinal:
                raise ValueError(f"non-canonical baseline priority ordinal: {key}")
            records.append(evidence)

        expected_ordinals = tuple(range(1, len(baseline) + 1))
        final_ordinals = tuple(sorted(item.final_simulator_priority_ordinal for item in records))
        if final_ordinals != expected_ordinals:
            raise ValueError("final simulator priority ordinals must be contiguous")

        baseline_by_key = {self._candidate_key(candidate): candidate for candidate in baseline}
        groups: dict[str, list[CandidatePriorityEvidence]] = {}
        for evidence in records:
            groups.setdefault(evidence.signal_date_group_key, []).append(evidence)
        for group_key, group in groups.items():
            baseline_slots = tuple(sorted(item.baseline_within_entry_date_ordinal for item in group))
            if any(item.signal_date_group_slot_ordinals != baseline_slots for item in group):
                raise ValueError(f"signal-date subgroup slot-set mismatch: {group_key}")
            if tuple(sorted(item.final_simulator_priority_ordinal for item in group)) != baseline_slots:
                raise ValueError(f"attempted cross-signal-date slot reorder: {group_key}")
            expected_variant = tuple(sorted(
                group,
                key=lambda item: (
                    not item.volume_ratio_finite,
                    -(item.volume_ratio if item.volume_ratio_finite else 0.0),
                    -item.q70_quality_score,
                    item.symbol,
                    item.candidate_key,
                ),
            ))
            if tuple(item.variant_within_signal_date_ordinal for item in expected_variant) != tuple(
                range(1, len(group) + 1)
            ):
                raise ValueError(f"non-canonical volume priority ordering: {group_key}")
            expected_final = dict(zip(
                (item.candidate_key for item in expected_variant),
                baseline_slots,
                strict=True,
            ))
            if any(item.final_simulator_priority_ordinal != expected_final[item.candidate_key] for item in group):
                raise ValueError(f"final priority does not match slot-preserving volume order: {group_key}")

        return [
            baseline_by_key[item.candidate_key]
            for item in sorted(records, key=lambda item: item.final_simulator_priority_ordinal)
        ]

    def _calculate_quantity(
        self,
        candidate: Trade,
    ) -> int:
        quantity_override = getattr(
            candidate,
            "quantity_override",
            None,
        )

        if quantity_override is not None:
            try:
                resolved_quantity = int(
                    quantity_override
                )
            except (TypeError, ValueError):
                return 0

            if resolved_quantity <= 0:
                return 0

            # Luôn làm tròn xuống theo lot_size.
            resolved_quantity = (
                resolved_quantity
                // self.lot_size
                * self.lot_size
            )

            return resolved_quantity

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

    def _allocate_daily_candidates(
        self,
        candidates: list[Trade],
    ) -> list[Trade]:
        """
        Phân bổ quantity cho các candidate cùng ngày.

        Nếu không cấu hình PortfolioAllocator,
        candidate tiếp tục sử dụng PositionSizer cũ.
        """
        if not candidates:
            return []

        # Xóa quantity override cũ nếu cùng object
        # được sử dụng lại.
        for candidate in candidates:
            candidate.quantity_override = None

        if self.portfolio_allocator is None:
            return candidates

        open_position_count = len(
            self.portfolio.open_positions
        )

        available_slots = max(
            0,
            self.max_positions
            - open_position_count,
        )

        if available_slots <= 0:
            return candidates

        eligible_candidates: list[Trade] = []

        for candidate in candidates:
            # Không phân bổ cho mã đang có vị thế.
            if self.portfolio.has_open_position(
                candidate.symbol
            ):
                continue

            regime_decision = (
                self._resolve_regime_decision(
                    candidate
                )
            )

            if regime_decision is not None:
                if (
                    regime_decision.normalized_regime
                    == "UNKNOWN"
                ):
                    continue

                if not (
                    regime_decision.allow_new_positions
                ):
                    continue

                effective_max_positions = min(
                    self.max_positions,
                    regime_decision.max_positions,
                )

                projected_position_count = (
                    open_position_count
                    + len(eligible_candidates)
                )

                if (
                    projected_position_count
                    >= effective_max_positions
                ):
                    continue

            eligible_candidates.append(
                candidate
            )

            if (
                len(eligible_candidates)
                >= available_slots
            ):
                break

        if not eligible_candidates:
            return candidates

        allocation_candidates: list[
            AllocationCandidate
        ] = []

        trade_by_symbol: dict[
            str,
            Trade,
        ] = {}

        for candidate in eligible_candidates:
            symbol = (
                candidate.symbol
                .upper()
                .strip()
            )

            allocation_candidates.append(
                AllocationCandidate(
                    symbol=symbol,
                    entry_price=float(
                        candidate.entry_price
                    ),
                    stop_price=(
                        resolve_candidate_stop_price(
                            candidate
                        )
                    ),
                    atr=getattr(
                        candidate,
                        "atr",
                        None,
                    ),
                    signal_score=(
                        candidate.signal_score
                    ),
                    market_regime=(
                        candidate.market_regime
                    ),
                )
            )

            trade_by_symbol[
                symbol
            ] = candidate

        portfolio_equity = (
            self.portfolio.equity()
        )

        # Ví dụ:
        # position_size_pct = 20%
        # có 2 candidate đủ điều kiện
        # => allocator được sử dụng tối đa 40% equity.
        target_investable_pct = min(
            100.0,
            self.position_size_pct
            * len(allocation_candidates),
        )

        # Không cho allocator sử dụng số vốn
        # lớn hơn lượng cash hiện có.
        cash_investable_pct = (
            self.portfolio.cash
            / portfolio_equity
            * 100
            if portfolio_equity > 0
            else 0.0
        )

        effective_investable_pct = min(
            target_investable_pct,
            cash_investable_pct,
        )

        if effective_investable_pct <= 0:
            return candidates

        try:
            allocations = (
                self.portfolio_allocator.allocate(
                    allocation_candidates,
                    portfolio_equity=(
                        portfolio_equity
                    ),
                    investable_pct=(
                        effective_investable_pct
                    ),
                )
            )
        except ValueError:
            # Thiếu ATR, stop hoặc metadata cần thiết:
            # fallback về PositionSizer hiện tại.
            return candidates

        for allocation in allocations:
            candidate = trade_by_symbol.get(
                allocation.symbol
            )

            if candidate is None:
                continue

            candidate.quantity_override = int(
                allocation.quantity
            )

        return candidates

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

    def _resolve_regime_decision(
        self,
        candidate: Trade,
    ) -> RegimePortfolioDecision | None:
        if self.regime_policy is None:
            return None

        return self.regime_policy.resolve(
            candidate.market_regime
        )

    def _resolve_effective_heat_limit(
        self,
        regime_decision: (
            RegimePortfolioDecision | None
        ),
    ) -> float | None:
        configured_limit = (
            self.portfolio_heat.max_heat_pct
        )

        if regime_decision is None:
            return configured_limit

        regime_limit = (
            regime_decision
            .max_portfolio_heat_pct
        )

        if configured_limit is None:
            return regime_limit

        if regime_limit is None:
            return configured_limit

        return min(
            configured_limit,
            regime_limit,
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

        regime_decision = (
            self._resolve_regime_decision(
                candidate
            )
        )

        if regime_decision is not None:
            if (
                regime_decision
                .normalized_regime
                == "UNKNOWN"
            ):
                self._reject_trade(
                    rejected_trades=(
                        rejected_trades
                    ),
                    candidate=candidate,
                    reason=(
                        "unknown_market_regime"
                    ),
                )

                return

            if not (
                regime_decision
                .allow_new_positions
            ):
                self._reject_trade(
                    rejected_trades=(
                        rejected_trades
                    ),
                    candidate=candidate,
                    reason=(
                        "regime_entries_disabled"
                    ),
                )

                return

            effective_max_positions = min(
                self.max_positions,
                regime_decision.max_positions,
            )
        else:
            effective_max_positions = (
                self.max_positions
            )

        decision = decide_candidate(
            candidate=candidate,
            open_positions=(
                self.portfolio.open_positions
            ),
            max_positions=(
                effective_max_positions
            ),
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

        equity = self.portfolio.equity()
        proposed_value = (
            candidate.entry_price
            * quantity
        )
        current_exposure = sum(
            trade.entry_price * trade.quantity
            for trade in self.portfolio.open_positions
        )

        if (
            self.maximum_gross_exposure_pct is not None
            and equity > 0
            and (
                current_exposure + proposed_value
            ) / equity * 100
            > self.maximum_gross_exposure_pct
        ):
            self._reject_trade(
                rejected_trades=rejected_trades,
                candidate=candidate,
                reason="maximum_gross_exposure",
            )
            return

        estimated_buy_cost_pct = (
            self.transaction_cost_config.buy_commission_pct
            + self.transaction_cost_config.buy_slippage_pct
        )
        estimated_cash_required = proposed_value * (
            1 + estimated_buy_cost_pct / 100
        )
        minimum_cash = (
            equity
            * self.minimum_cash_buffer_pct
            / 100
        )

        if (
            self.portfolio.cash
            - estimated_cash_required
            < minimum_cash
        ):
            self._reject_trade(
                rejected_trades=rejected_trades,
                candidate=candidate,
                reason="minimum_cash_buffer",
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

        effective_heat_limit = (
            self._resolve_effective_heat_limit(
                regime_decision
            )
        )

        if effective_heat_limit is not None:
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

            heat_engine = PortfolioHeat(
                max_heat_pct=(
                    effective_heat_limit
                ),
            )

            heat_decision = (
                heat_engine.decide(
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
                    signal_date=candidate.signal_date,
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
                "regime_policy_enabled": (
                    self.regime_policy
                    is not None
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

        if self._candidate_priority_by_key is not None:
            candidate_keys = tuple(
                self._candidate_key(trade)
                for trade in candidate_trades
                if trade.is_closed
            )
            if len(set(candidate_keys)) != len(candidate_keys):
                raise ValueError("candidate priority keys must be unique")
            evidence_keys = set(self._candidate_priority_by_key)
            missing = sorted(set(candidate_keys) - evidence_keys)
            extra = sorted(evidence_keys - set(candidate_keys))
            if missing:
                raise ValueError("missing candidate priority evidence: " + ", ".join(missing))
            if extra:
                raise ValueError("extra candidate priority evidence: " + ", ".join(extra))

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

            ranked_candidates = self._rank_entry_candidates(
                entry_candidates,
                event_date=event_date,
            )

            allocated_candidates = (
                self._allocate_daily_candidates(
                    ranked_candidates
                )
            )

            if self.max_new_positions_per_day is not None:
                allowed = self.max_new_positions_per_day
                rejected_for_daily_limit = (
                    allocated_candidates[allowed:]
                )
                allocated_candidates = (
                    allocated_candidates[:allowed]
                )

                for candidate in rejected_for_daily_limit:
                    self._reject_trade(
                        rejected_trades=rejected_trades,
                        candidate=candidate,
                        reason="maximum_orders_per_scan",
                    )

            # Các candidate cùng ngày được xếp hạng
            # trước khi danh mục mở vị thế.
            for candidate in (
                allocated_candidates
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
