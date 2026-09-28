from __future__ import annotations

"""Causal formation-to-next-session timing and capacity evidence."""

from bisect import bisect_right
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import math
from statistics import median, pstdev
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from quantlab.execution.foundation import (
    NEUTRAL_EXECUTION_FOUNDATION_V1,
    OrderIntentState,
    OrderSide,
    descriptive_participation_pct,
    translate_target_portfolio,
)
from quantlab.identity import canonical_identity_value, canonical_json
from quantlab.portfolio.construction import BLOCKS


CONTRACT = "quantlab.execution_timing_capacity_evidence"
VERSION = "v1"
EXPECTED_PHASE11A_RESULT = "ae1ad7157c598172b335085ea60693bb3cb95ce1471a4a74982d0fdfed9737d7"
EXPECTED_PHASE11A_SPEC = "a9a0ec5bb3d8c424fc935ee439e5463ad134bfd80e800e17a7d983d58ccd9685"
EXPECTED_PHASE11A_FRICTION = "c78a3a933b0d0bcad3b4d4e442176004721fd56451e78be09724d8231d06ae07"
EXPECTED_PHASE6_RESULT = "c23fa10a1b577509f1fa5d7a23d543dc63237b719c05269342ae5e7a6e660697"
EXPECTED_PHASE6_SPEC = "7b78fb5ea54dd3986ac4e46cefae93b88734812c9ba72ad3beaeae9e8f68453d"
NORMALIZED_PORTFOLIO_EQUITY = 1.0
CAPITAL_SEMANTICS = "ONE_CANONICAL_PRICE_NOTIONAL_UNIT_SCALE_LINEAR_TARGET_ACQUISITION"
PARTICIPATION_INTERPRETATION = "DESCRIPTIVE_PARTICIPATION_NOT_FILL_PROBABILITY_OR_MARKET_IMPACT"


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _mean(values: Iterable[float]) -> float | None:
    items = tuple(values); return None if not items else sum(items) / len(items)


def _median(values: Iterable[float]) -> float | None:
    items = tuple(values); return None if not items else float(median(items))


def _std(values: Iterable[float]) -> float | None:
    items = tuple(values); return None if not items else float(pstdev(items))


def _quantile(values: Iterable[float], probability: float) -> float | None:
    items = tuple(sorted(values))
    if not items: return None
    position = (len(items) - 1) * probability; lower = int(position); upper = min(lower + 1, len(items) - 1)
    return float(items[lower] + (items[upper] - items[lower]) * (position - lower))


class TimingEvidenceState(str, Enum):
    AVAILABLE = "AVAILABLE"
    NO_NEXT_MARKET_SESSION = "NO_NEXT_MARKET_SESSION"
    MISSING_FORMATION_REFERENCE = "MISSING_FORMATION_REFERENCE"
    MISSING_NEXT_OPEN = "MISSING_NEXT_OPEN"
    NONPOSITIVE_PRICE = "NONPOSITIVE_PRICE"
    ORDER_INTENT_UNAVAILABLE = "ORDER_INTENT_UNAVAILABLE"


class CapacityEvidenceState(str, Enum):
    AVAILABLE = "AVAILABLE"
    NO_NEXT_MARKET_SESSION = "NO_NEXT_MARKET_SESSION"
    MISSING_VOLUME = "MISSING_VOLUME"
    NONPOSITIVE_VOLUME = "NONPOSITIVE_VOLUME"
    ORDER_INTENT_UNAVAILABLE = "ORDER_INTENT_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class FrozenExecutionTargetPosition:
    symbol: str
    weight: float
    source_position_identity: str

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper(); weight = float(self.weight)
        if not symbol or not math.isfinite(weight) or weight <= 0 or weight > 1: raise ValueError("invalid frozen target position")
        if not self.source_position_identity: raise ValueError("source position identity is required")
        object.__setattr__(self, "symbol", symbol); object.__setattr__(self, "weight", weight)


@dataclass(frozen=True, slots=True)
class FrozenExecutionTargetPortfolio:
    formation_date: str
    requested_budget: int
    positions: tuple[FrozenExecutionTargetPosition, ...]
    source_portfolio_identity: str

    def __post_init__(self) -> None:
        positions = tuple(sorted(self.positions, key=lambda item: item.symbol))
        if self.requested_budget not in (5, 10, 20): raise ValueError("requested budget must be 5, 10, or 20")
        if len({item.symbol for item in positions}) != len(positions): raise ValueError("duplicate target symbol")
        if sum(item.weight for item in positions) > 1 + 1e-12: raise ValueError("target weights exceed one")
        if not self.source_portfolio_identity: raise ValueError("source portfolio identity is required")
        object.__setattr__(self, "positions", positions)


@dataclass(frozen=True, slots=True)
class DailyExecutionMarketEvidence:
    symbol: str
    session_date: str
    open_price: float | None
    close_price: float | None
    volume: float | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", self.symbol.strip().upper())


@dataclass(frozen=True, slots=True)
class ExecutionTimingCapacitySpec:
    name: str = "CAUSAL_EXECUTION_TIMING_CAPACITY_V1"
    version: str = "1"
    normalized_portfolio_equity: float = NORMALIZED_PORTFOLIO_EQUITY
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if self.version != "1" or self.normalized_portfolio_equity != 1.0: raise ValueError("unsupported timing/capacity specification")
        object.__setattr__(self, "fingerprint", _hash({
            "contract": (CONTRACT, VERSION), "name": self.name, "version": self.version,
            "phase11a_specification": NEUTRAL_EXECUTION_FOUNDATION_V1.fingerprint,
            "input": "frozen_Phase6_ADX_ONLY_EQUAL_WEIGHT_targets_budgets_5_10_20",
            "order_state": "target_acquisition_notional_not_rebalance_no_achieved_holdings_or_cash_path",
            "capital": CAPITAL_SEMANTICS, "normalized_equity": self.normalized_portfolio_equity,
            "formation_reference": "exact_same_date_close",
            "next_session": "first_VNINDEX_session_strictly_after_formation_no_t2_fallback",
            "next_open": "NEXT_SESSION_OPEN_REFERENCE_NOT_FILL_PRICE",
            "gap": "next_open_divided_by_formation_close_minus_one",
            "adverse_gap": "BUY_signed_gap_SELL_negative_signed_gap_no_truncation",
            "participation": "absolute_theoretical_acquisition_shares_divided_by_exact_next_session_daily_volume_times_100_per_normalized_equity_unit",
            "participation_interpretation": PARTICIPATION_INTERPRETATION,
            "quantile": "linear_interpolation_type7", "dispersion": "population_std_ddof0",
            "blocks": BLOCKS,
            "restrictions": ("no_fill_model", "no_costs", "no_lot_rounding", "no_t2_fallback", "no_ranking", "no_traded_value_claim"),
        }))


CAUSAL_EXECUTION_TIMING_CAPACITY_V1 = ExecutionTimingCapacitySpec()


@dataclass(frozen=True, slots=True)
class ExecutionTimingObservation:
    formation_date: str
    next_market_session: str | None
    requested_budget: int
    symbol: str
    side: OrderSide | None
    order_state: str
    target_weight: float
    normalized_target_acquisition_notional: float
    theoretical_acquisition_shares_per_equity_unit: float | None
    formation_reference_close: float | None
    next_session_open_reference: float | None
    next_session_daily_volume: float | None
    timing_state: TimingEvidenceState
    capacity_state: CapacityEvidenceState
    reference_to_next_open_gap_decimal: float | None
    adverse_direction_gap_decimal: float | None
    participation_pct_per_normalized_equity_unit: float | None
    participation_interpretation: str
    source_portfolio_identity: str
    source_position_identity: str
    source_order_intent_identity: str
    identity: str


@dataclass(frozen=True, slots=True)
class ExecutionTimingSummary:
    requested_budget: int
    scope_name: str
    scope_start_date: str
    scope_end_date: str
    observation_count: int
    timing_available_count: int
    no_next_market_session_count: int
    missing_formation_reference_count: int
    missing_next_open_count: int
    nonpositive_price_count: int
    order_intent_unavailable_count: int
    capacity_available_count: int
    missing_volume_count: int
    nonpositive_volume_count: int
    mean_gap_decimal: float | None
    median_gap_decimal: float | None
    gap_population_std_decimal: float | None
    positive_gap_count: int
    zero_gap_count: int
    negative_gap_count: int
    mean_adverse_gap_decimal: float | None
    median_adverse_gap_decimal: float | None
    mean_participation_pct_per_normalized_equity_unit: float | None
    median_participation_pct_per_normalized_equity_unit: float | None
    p95_participation_pct_per_normalized_equity_unit: float | None
    maximum_participation_pct_per_normalized_equity_unit: float | None
    included_observation_identities_sha256: str
    identity: str


@dataclass(frozen=True, slots=True)
class ExecutionTimingCapacityResult:
    contract_name: str
    contract_version: str
    specification_fingerprint: str
    source_identities: Mapping[str, str]
    market_snapshot_identity: str
    market_logical_content_fingerprint: str
    observations: tuple[ExecutionTimingObservation, ...]
    summaries: tuple[ExecutionTimingSummary, ...]
    block_summaries: tuple[ExecutionTimingSummary, ...]
    limitations: tuple[str, ...]
    identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_identities", MappingProxyType(dict(sorted(self.source_identities.items()))))


def adverse_direction_gap(side: OrderSide, signed_gap: float) -> float:
    return float(signed_gap) if side is OrderSide.BUY else -float(signed_gap)


def _validate_sources(sources: Mapping[str, str]) -> None:
    expected = {
        "phase11a_result_identity": EXPECTED_PHASE11A_RESULT,
        "phase11a_specification_fingerprint": EXPECTED_PHASE11A_SPEC,
        "phase11a_friction_fingerprint": EXPECTED_PHASE11A_FRICTION,
        "phase6_result_identity": EXPECTED_PHASE6_RESULT,
        "phase6_specification_fingerprint": EXPECTED_PHASE6_SPEC,
    }
    if dict(sources) != expected: raise ValueError("frozen Phase 11A/Phase 6 provenance mismatch")


def _summary(items: tuple[ExecutionTimingObservation, ...], budget: int, scope: tuple[str, str, str]) -> ExecutionTimingSummary:
    name, start, end = scope
    selected = tuple(item for item in items if item.requested_budget == budget and start <= item.formation_date <= end)
    gaps = tuple(float(item.reference_to_next_open_gap_decimal) for item in selected if item.reference_to_next_open_gap_decimal is not None)
    adverse = tuple(float(item.adverse_direction_gap_decimal) for item in selected if item.adverse_direction_gap_decimal is not None)
    participation = tuple(float(item.participation_pct_per_normalized_equity_unit) for item in selected if item.participation_pct_per_normalized_equity_unit is not None)
    ids = tuple(item.identity for item in selected); ids_hash = _hash(ids)
    values = {
        "budget": budget, "scope": scope, "ids": ids, "gaps": (_mean(gaps), _median(gaps), _std(gaps)),
        "participation": (_mean(participation), _median(participation), _quantile(participation, .95), max(participation, default=None)),
    }
    return ExecutionTimingSummary(
        budget, name, start, end, len(selected), sum(item.timing_state is TimingEvidenceState.AVAILABLE for item in selected),
        sum(item.timing_state is TimingEvidenceState.NO_NEXT_MARKET_SESSION for item in selected),
        sum(item.timing_state is TimingEvidenceState.MISSING_FORMATION_REFERENCE for item in selected),
        sum(item.timing_state is TimingEvidenceState.MISSING_NEXT_OPEN for item in selected),
        sum(item.timing_state is TimingEvidenceState.NONPOSITIVE_PRICE for item in selected),
        sum(item.timing_state is TimingEvidenceState.ORDER_INTENT_UNAVAILABLE for item in selected),
        sum(item.capacity_state is CapacityEvidenceState.AVAILABLE for item in selected),
        sum(item.capacity_state is CapacityEvidenceState.MISSING_VOLUME for item in selected),
        sum(item.capacity_state is CapacityEvidenceState.NONPOSITIVE_VOLUME for item in selected),
        _mean(gaps), _median(gaps), _std(gaps), sum(value > 0 for value in gaps), sum(value == 0 for value in gaps), sum(value < 0 for value in gaps),
        _mean(adverse), _median(adverse), _mean(participation), _median(participation), _quantile(participation, .95), max(participation, default=None), ids_hash, _hash(values),
    )


def evaluate_execution_timing_capacity(
    portfolios: Iterable[FrozenExecutionTargetPortfolio],
    market_evidence: Iterable[DailyExecutionMarketEvidence],
    *,
    source_identities: Mapping[str, str],
    market_snapshot_identity: str,
    market_logical_content_fingerprint: str,
    spec: ExecutionTimingCapacitySpec = CAUSAL_EXECUTION_TIMING_CAPACITY_V1,
) -> ExecutionTimingCapacityResult:
    _validate_sources(source_identities)
    if not market_snapshot_identity or not market_logical_content_fingerprint: raise ValueError("market snapshot provenance is required")
    frozen = tuple(sorted(portfolios, key=lambda item: (item.formation_date, item.requested_budget)))
    keys = tuple((item.formation_date, item.requested_budget) for item in frozen)
    if len(keys) != len(set(keys)): raise ValueError("duplicate frozen target portfolio")
    rows = tuple(sorted(market_evidence, key=lambda item: (item.session_date, item.symbol)))
    index = {(item.symbol, item.session_date): item for item in rows}
    if len(index) != len(rows): raise ValueError("duplicate market evidence row")
    sessions = tuple(sorted({item.session_date for item in rows if item.symbol == "VNINDEX"}))
    observations: list[ExecutionTimingObservation] = []
    for portfolio in frozen:
        next_index = bisect_right(sessions, portfolio.formation_date)
        next_session = sessions[next_index] if next_index < len(sessions) else None
        formation_prices = {position.symbol: (index.get((position.symbol, portfolio.formation_date)).close_price if index.get((position.symbol, portfolio.formation_date)) is not None else None) for position in portfolio.positions}
        translated = translate_target_portfolio(
            formation_date=portfolio.formation_date, reference_price_date=portfolio.formation_date,
            portfolio_equity=spec.normalized_portfolio_equity, current_cash=spec.normalized_portfolio_equity,
            current_holdings={}, target_weights={item.symbol: item.weight for item in portfolio.positions},
            reference_prices=formation_prices,
        )
        intent_index = {item.symbol: item for item in translated.intents}
        for position in portfolio.positions:
            intent = intent_index[position.symbol]; formation = index.get((position.symbol, portfolio.formation_date))
            next_row = None if next_session is None else index.get((position.symbol, next_session))
            formation_close = None if formation is None else formation.close_price
            next_open = None if next_row is None else next_row.open_price
            volume = None if next_row is None else next_row.volume
            if next_session is None:
                timing_state = TimingEvidenceState.NO_NEXT_MARKET_SESSION
            elif intent.state is OrderIntentState.MISSING_REFERENCE_PRICE:
                timing_state = TimingEvidenceState.MISSING_FORMATION_REFERENCE
            elif intent.state is OrderIntentState.NONPOSITIVE_REFERENCE_PRICE:
                timing_state = TimingEvidenceState.NONPOSITIVE_PRICE
            elif intent.state is not OrderIntentState.AVAILABLE:
                timing_state = TimingEvidenceState.ORDER_INTENT_UNAVAILABLE
            elif next_open is None:
                timing_state = TimingEvidenceState.MISSING_NEXT_OPEN
            elif not math.isfinite(float(next_open)) or float(next_open) <= 0:
                timing_state = TimingEvidenceState.NONPOSITIVE_PRICE
            else:
                timing_state = TimingEvidenceState.AVAILABLE
            if timing_state is TimingEvidenceState.AVAILABLE:
                gap = float(next_open) / float(formation_close) - 1.0
                adverse = adverse_direction_gap(intent.side, gap) if intent.side is not None else None
            else:
                gap = adverse = None
            if next_session is None:
                capacity_state = CapacityEvidenceState.NO_NEXT_MARKET_SESSION
            elif intent.state is not OrderIntentState.AVAILABLE or intent.theoretical_share_delta is None:
                capacity_state = CapacityEvidenceState.ORDER_INTENT_UNAVAILABLE
            elif volume is None:
                capacity_state = CapacityEvidenceState.MISSING_VOLUME
            elif not math.isfinite(float(volume)) or float(volume) <= 0:
                capacity_state = CapacityEvidenceState.NONPOSITIVE_VOLUME
            else:
                capacity_state = CapacityEvidenceState.AVAILABLE
            participation = None if capacity_state is not CapacityEvidenceState.AVAILABLE else descriptive_participation_pct(abs(intent.theoretical_share_delta), float(volume))
            payload = {
                "portfolio": portfolio.source_portfolio_identity, "position": position.source_position_identity,
                "order_intent": intent.identity, "next_session": next_session, "formation_close": formation_close,
                "next_open": next_open, "volume": volume, "timing_state": timing_state.value,
                "capacity_state": capacity_state.value, "gap": gap, "adverse": adverse,
                "participation": participation, "specification": spec.fingerprint,
            }
            observations.append(ExecutionTimingObservation(
                portfolio.formation_date, next_session, portfolio.requested_budget, position.symbol, intent.side,
                "TARGET_ACQUISITION_NOT_REBALANCE", position.weight, position.weight * spec.normalized_portfolio_equity,
                intent.theoretical_share_delta, formation_close, next_open, volume, timing_state, capacity_state,
                gap, adverse, participation, PARTICIPATION_INTERPRETATION, portfolio.source_portfolio_identity,
                position.source_position_identity, intent.identity, _hash(payload),
            ))
    observation_tuple = tuple(sorted(observations, key=lambda item: (item.formation_date, item.requested_budget, item.symbol)))
    whole = ("whole_period", "2018-08-07", "2026-09-17")
    summaries = tuple(_summary(observation_tuple, budget, whole) for budget in (5, 10, 20))
    blocks = tuple(_summary(observation_tuple, budget, block) for budget in (5, 10, 20) for block in BLOCKS)
    sources = MappingProxyType(dict(sorted(source_identities.items())))
    limitations = (
        "Phase 6 provides target weights but no achieved share holdings, realized cash, or fill path; observations are target acquisitions, not rebalances",
        "normalized equity is one canonical price-notional unit and participation is a scale-linear coefficient, not a production-capital estimate",
        "next-session open is reference evidence, not a fill or execution price",
        "daily volume participation is not fill probability, market impact, spread, queue, or tradability evidence",
        "no lot, tick, price-band, commission, tax, slippage, settlement, or corporate-action model is applied",
    )
    payload = {"contract": (CONTRACT, VERSION), "specification": spec.fingerprint, "sources": dict(sources), "market_snapshot": market_snapshot_identity, "market_logical": market_logical_content_fingerprint, "observations": tuple(item.identity for item in observation_tuple), "summaries": tuple(item.identity for item in summaries), "blocks": tuple(item.identity for item in blocks), "limitations": limitations}
    return ExecutionTimingCapacityResult(CONTRACT, VERSION, spec.fingerprint, sources, market_snapshot_identity, market_logical_content_fingerprint, observation_tuple, summaries, blocks, limitations, _hash(payload))
