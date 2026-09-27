from __future__ import annotations

"""Pure outcome evaluation over frozen portfolio and outcome evidence."""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import csv
import json
import math
from pathlib import Path
from statistics import median, pstdev
from types import MappingProxyType
from typing import Any, Mapping

from quantlab.identity import canonical_identity_value, canonical_json
from .construction import BLOCKS, BUDGETS, SELECTION_POLICY, WeightingPolicy


CONTRACT = "quantlab.neutral_portfolio_outcome_evaluation"
VERSION = "v1"
HORIZONS = (5, 10, 20)
COST_GRID_BPS = (0, 10, 25, 50)
JOIN_KEY = ("session_date", "symbol")
PATH_METRIC_STATUS = "NOT_APPLICABLE"
PATH_METRIC_REASON = "OVERLAPPING_FORWARD_HORIZONS"
COST_LABEL = "HYPOTHETICAL_COST_SENSITIVITY"
CONTRASTS = ((5, 10), (5, 20), (10, 20))


class PortfolioOutcomeAvailability(str, Enum):
    FULLY_EVALUABLE = "FULLY_EVALUABLE"
    EMPTY_PORTFOLIO = "EMPTY_PORTFOLIO"
    UNAVAILABLE_CONSTITUENT_OUTCOME = "UNAVAILABLE_CONSTITUENT_OUTCOME"
    UNAVAILABLE_BENCHMARK_OUTCOME = "UNAVAILABLE_BENCHMARK_OUTCOME"
    CENSORED_TARGET = "CENSORED_TARGET"


class TemporalEvidence(str, Enum):
    SAME_DIRECTION = "SAME_DIRECTION"
    MIXED = "MIXED"
    REVERSAL = "REVERSAL"
    UNDEFINED = "UNDEFINED"


class EvidenceState(str, Enum):
    OUTCOME_EVIDENCE_DEFINED = "OUTCOME_EVIDENCE_DEFINED"
    MIXED_TEMPORAL_EVIDENCE = "MIXED_TEMPORAL_EVIDENCE"
    INSUFFICIENT_OUTCOME_EVIDENCE = "INSUFFICIENT_OUTCOME_EVIDENCE"
    CAUSAL_PATH_NOT_APPLICABLE = "CAUSAL_PATH_NOT_APPLICABLE"


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _mean(values: tuple[float, ...]) -> float | None:
    return None if not values else sum(values) / len(values)


def _median(values: tuple[float, ...]) -> float | None:
    return None if not values else float(median(values))


def _std(values: tuple[float, ...]) -> float | None:
    return None if not values else float(pstdev(values))


def _finite(value: Any, *, name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _optional_float(value: Any) -> float | None:
    return None if value in (None, "") else _finite(value, name="optional numeric value")


@dataclass(frozen=True, slots=True)
class FrozenOutcomePosition:
    symbol: str
    selection_rank: int
    weight: float
    position_identity: str


@dataclass(frozen=True, slots=True)
class FrozenOutcomePortfolio:
    session_date: str
    requested_budget: int
    weighting_policy: str
    selection_identity: str
    eligible_cross_section_count: int
    selected_count: int
    gross_weight: float
    cash_weight: float
    one_way_weight_turnover: float | None
    portfolio_identity: str
    positions: tuple[FrozenOutcomePosition, ...]


@dataclass(frozen=True, slots=True)
class FrozenForwardOutcome:
    session_date: str
    symbol: str
    benchmark_symbol: str
    target_sessions: Mapping[int, str | None]
    stock_returns: Mapping[int, float | None]
    benchmark_returns: Mapping[int, float | None]
    excess_returns: Mapping[int, float | None]
    availability: Mapping[int, str]

    def __post_init__(self) -> None:
        expected = set(HORIZONS)
        for name in (
            "target_sessions", "stock_returns", "benchmark_returns",
            "excess_returns", "availability",
        ):
            if set(getattr(self, name)) != expected:
                raise ValueError(f"{name} must contain the frozen horizons")
        if not self.symbol or self.symbol != self.symbol.strip().upper():
            raise ValueError("outcome symbol must be normalized uppercase")
        for horizon in HORIZONS:
            target = self.target_sessions[horizon]
            stock = self.stock_returns[horizon]
            benchmark = self.benchmark_returns[horizon]
            excess = self.excess_returns[horizon]
            status = self.availability[horizon]
            if target is not None and target <= self.session_date:
                raise ValueError("outcome target session must be after formation")
            if status == "AVAILABLE":
                if target is None or stock is None or benchmark is None or excess is None:
                    raise ValueError("available outcome must contain target and returns")
                values = tuple(
                    _finite(value, name=f"available {horizon}-session return")
                    for value in (stock, benchmark, excess)
                )
                if not math.isclose(values[2], values[0] - values[1], rel_tol=1e-12, abs_tol=1e-12):
                    raise ValueError("available outcome excess return does not reconcile")
            elif any(value is not None for value in (stock, benchmark, excess)):
                raise ValueError("unavailable outcome returns must remain undefined")
        for name in (
            "target_sessions", "stock_returns", "benchmark_returns",
            "excess_returns", "availability",
        ):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))


@dataclass(frozen=True, slots=True)
class PortfolioOutcomeInput:
    source_identities: Mapping[str, str]
    portfolios: tuple[FrozenOutcomePortfolio, ...]
    outcomes: tuple[FrozenForwardOutcome, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_identities", MappingProxyType(dict(self.source_identities)))
        keys = tuple((item.session_date, item.requested_budget) for item in self.portfolios)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("portfolios must be uniquely ordered by date and budget")
        outcome_keys = tuple((item.session_date, item.symbol) for item in self.outcomes)
        if outcome_keys != tuple(sorted(outcome_keys)) or len(outcome_keys) != len(set(outcome_keys)):
            raise ValueError("outcomes must be uniquely ordered by date and symbol")


@dataclass(frozen=True, slots=True)
class PortfolioOutcomeSpec:
    name: str = "NEUTRAL_PORTFOLIO_OUTCOMES_V1"
    version: str = "1"
    budgets: tuple[int, ...] = BUDGETS
    horizons: tuple[int, ...] = HORIZONS
    cost_grid_bps: tuple[int, ...] = COST_GRID_BPS
    blocks: tuple[tuple[str, str, str], ...] = BLOCKS
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if self.version != "1" or self.budgets != BUDGETS or self.horizons != HORIZONS:
            raise ValueError("unsupported portfolio-outcome specification")
        if self.cost_grid_bps != COST_GRID_BPS or self.blocks != BLOCKS:
            raise ValueError("cost grid and temporal blocks are frozen")
        object.__setattr__(self, "fingerprint", _hash({
            "contract": {"name": CONTRACT, "version": VERSION},
            "name": self.name,
            "budgets": self.budgets,
            "horizons": self.horizons,
            "join_key": JOIN_KEY,
            "aggregation": "sum_frozen_weight_times_constituent_return",
            "benchmark": "same_date_VNINDEX_return_times_frozen_gross_weight",
            "missingness": "all_constituents_required_no_weight_renormalization_empty_undefined",
            "cash": "no_risk_free_return",
            "blocks": self.blocks,
            "contrasts": CONTRASTS,
            "cost_grid_bps": self.cost_grid_bps,
            "turnover_cost": "one_way_weight_turnover_times_bps_divided_by_100_percentage_points",
            "overlap": PATH_METRIC_REASON,
            "path_metrics": PATH_METRIC_STATUS,
            "restrictions": (
                "no_selection_or_weight_changes", "no_ranking_or_winner",
                "no_budget_horizon_or_cost_optimization", "no_compounded_path_metrics",
            ),
        }))


NEUTRAL_PORTFOLIO_OUTCOMES_V1 = PortfolioOutcomeSpec()


@dataclass(frozen=True, slots=True)
class DailyPortfolioOutcome:
    session_date: str
    requested_budget: int
    horizon_sessions: int
    selected_count: int
    underfilled: bool
    gross_weight: float
    cash_weight: float
    one_way_weight_turnover: float | None
    availability: PortfolioOutcomeAvailability
    unavailable_constituent_count: int
    target_session: str | None
    portfolio_stock_return_pct: float | None
    portfolio_benchmark_return_pct: float | None
    portfolio_excess_return_pct_points: float | None
    source_portfolio_identity: str
    identity: str


@dataclass(frozen=True, slots=True)
class PortfolioOutcomeSummary:
    requested_budget: int
    horizon_sessions: int
    total_dates: int
    evaluable_dates: int
    empty_dates: int
    underfilled_dates: int
    unavailable_constituent_dates: int
    unavailable_benchmark_dates: int
    censored_dates: int
    mean_stock_return_pct: float | None
    median_stock_return_pct: float | None
    stock_return_std_pct: float | None
    minimum_stock_return_pct: float | None
    maximum_stock_return_pct: float | None
    positive_stock_return_rate: float | None
    mean_benchmark_return_pct: float | None
    median_benchmark_return_pct: float | None
    mean_excess_return_pct_points: float | None
    median_excess_return_pct_points: float | None
    excess_return_std_pct_points: float | None
    minimum_excess_return_pct_points: float | None
    maximum_excess_return_pct_points: float | None
    positive_excess_rate: float | None
    evidence_state: EvidenceState
    path_metric_status: str
    path_metric_reason: str
    identity: str


@dataclass(frozen=True, slots=True)
class PortfolioOutcomeBlockSummary:
    requested_budget: int
    horizon_sessions: int
    block_name: str
    block_start_date: str
    block_end_date: str
    total_dates: int
    evaluable_dates: int
    mean_stock_return_pct: float | None
    median_stock_return_pct: float | None
    mean_benchmark_return_pct: float | None
    mean_excess_return_pct_points: float | None
    median_excess_return_pct_points: float | None
    positive_stock_return_rate: float | None
    positive_excess_rate: float | None
    temporal_evidence: TemporalEvidence
    identity: str


@dataclass(frozen=True, slots=True)
class PortfolioOutcomeContrast:
    scope_name: str
    horizon_sessions: int
    lower_budget: int
    higher_budget: int
    paired_date_count: int
    mean_stock_delta_pct_points: float | None
    median_stock_delta_pct_points: float | None
    mean_excess_delta_pct_points: float | None
    median_excess_delta_pct_points: float | None
    positive_stock_delta_rate: float | None
    positive_excess_delta_rate: float | None
    identity: str


@dataclass(frozen=True, slots=True)
class PortfolioCostSensitivity:
    requested_budget: int
    horizon_sessions: int
    cost_rate_bps: int
    evaluable_date_count: int
    turnover_defined_date_count: int
    mean_gross_stock_return_pct: float | None
    mean_estimated_turnover_cost_pct_points: float | None
    mean_net_stock_return_pct: float | None
    mean_gross_excess_return_pct_points: float | None
    mean_net_excess_return_pct_points: float | None
    label: str
    identity: str


@dataclass(frozen=True, slots=True)
class PortfolioOutcomeEvaluationResult:
    contract_name: str
    contract_version: str
    specification_fingerprint: str
    source_identities: Mapping[str, str]
    daily_outcomes: tuple[DailyPortfolioOutcome, ...]
    summaries: tuple[PortfolioOutcomeSummary, ...]
    block_summaries: tuple[PortfolioOutcomeBlockSummary, ...]
    contrasts: tuple[PortfolioOutcomeContrast, ...]
    cost_sensitivity: tuple[PortfolioCostSensitivity, ...]
    limitations: tuple[str, ...]
    identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_identities", MappingProxyType(dict(self.source_identities)))


def _availability_for_missing(statuses: tuple[str, ...]) -> PortfolioOutcomeAvailability:
    if any(item == "CENSORED_AFTER_DATA_END" for item in statuses):
        return PortfolioOutcomeAvailability.CENSORED_TARGET
    if any("BENCHMARK" in item for item in statuses):
        return PortfolioOutcomeAvailability.UNAVAILABLE_BENCHMARK_OUTCOME
    return PortfolioOutcomeAvailability.UNAVAILABLE_CONSTITUENT_OUTCOME


def _daily(
    portfolio: FrozenOutcomePortfolio,
    horizon: int,
    outcome_index: Mapping[tuple[str, str], FrozenForwardOutcome],
) -> DailyPortfolioOutcome:
    if not portfolio.positions:
        availability = PortfolioOutcomeAvailability.EMPTY_PORTFOLIO
        missing = 0
        target = stock = benchmark = excess = None
    else:
        matched = tuple(outcome_index.get((portfolio.session_date, item.symbol)) for item in portfolio.positions)
        missing = sum(item is None for item in matched)
        statuses = tuple(
            "MISSING_JOINED_OUTCOME" if item is None else item.availability[horizon]
            for item in matched
        )
        if missing or any(item != "AVAILABLE" for item in statuses):
            availability = _availability_for_missing(statuses)
            target = stock = benchmark = excess = None
        else:
            complete = tuple(item for item in matched if item is not None)
            targets = {item.target_sessions[horizon] for item in complete}
            benchmarks = {item.benchmark_returns[horizon] for item in complete}
            if len(targets) != 1 or None in targets:
                raise ValueError("constituent target sessions do not reconcile")
            if len(benchmarks) != 1 or None in benchmarks:
                raise ValueError("constituent benchmark outcomes do not reconcile")
            target = next(iter(targets))
            if target <= portfolio.session_date:
                raise ValueError("portfolio target session is not after formation")
            stock = sum(
                position.weight * float(outcome.stock_returns[horizon])
                for position, outcome in zip(portfolio.positions, complete, strict=True)
            )
            benchmark = portfolio.gross_weight * float(next(iter(benchmarks)))
            excess = stock - benchmark
            weighted_excess = sum(
                position.weight * float(outcome.excess_returns[horizon])
                for position, outcome in zip(portfolio.positions, complete, strict=True)
            )
            if not math.isclose(excess, weighted_excess, rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError("weighted portfolio excess does not reconcile")
            availability = PortfolioOutcomeAvailability.FULLY_EVALUABLE
    payload = {
        "date": portfolio.session_date, "budget": portfolio.requested_budget,
        "horizon": horizon, "portfolio": portfolio.portfolio_identity,
        "availability": availability.value, "missing": missing, "target": target,
        "stock": stock, "benchmark": benchmark, "excess": excess,
        "underfilled": portfolio.selected_count < portfolio.requested_budget,
    }
    return DailyPortfolioOutcome(
        portfolio.session_date, portfolio.requested_budget, horizon,
        portfolio.selected_count, portfolio.selected_count < portfolio.requested_budget,
        portfolio.gross_weight, portfolio.cash_weight, portfolio.one_way_weight_turnover,
        availability, missing, target, stock, benchmark, excess,
        portfolio.portfolio_identity, _hash(payload),
    )


def _rates(values: tuple[float, ...]) -> float | None:
    return None if not values else sum(item > 0.0 for item in values) / len(values)


def _summary(items: tuple[DailyPortfolioOutcome, ...], budget: int, horizon: int) -> PortfolioOutcomeSummary:
    evaluable = tuple(item for item in items if item.availability is PortfolioOutcomeAvailability.FULLY_EVALUABLE)
    stock = tuple(float(item.portfolio_stock_return_pct) for item in evaluable)
    benchmark = tuple(float(item.portfolio_benchmark_return_pct) for item in evaluable)
    excess = tuple(float(item.portfolio_excess_return_pct_points) for item in evaluable)
    block_means = []
    for _, start, end in BLOCKS:
        values = tuple(float(item.portfolio_excess_return_pct_points) for item in evaluable if start <= item.session_date <= end)
        if values:
            block_means.append(sum(values) / len(values))
    state = (
        EvidenceState.INSUFFICIENT_OUTCOME_EVIDENCE if not evaluable
        else EvidenceState.MIXED_TEMPORAL_EVIDENCE
        if any(value > 0 for value in block_means) and any(value < 0 for value in block_means)
        else EvidenceState.OUTCOME_EVIDENCE_DEFINED
    )
    values = {
        "budget": budget, "horizon": horizon,
        "daily": tuple(item.identity for item in items), "state": state.value,
    }
    return PortfolioOutcomeSummary(
        budget, horizon, len(items), len(evaluable),
        sum(item.availability is PortfolioOutcomeAvailability.EMPTY_PORTFOLIO for item in items),
        sum(item.underfilled for item in items),
        sum(item.availability is PortfolioOutcomeAvailability.UNAVAILABLE_CONSTITUENT_OUTCOME for item in items),
        sum(item.availability is PortfolioOutcomeAvailability.UNAVAILABLE_BENCHMARK_OUTCOME for item in items),
        sum(item.availability is PortfolioOutcomeAvailability.CENSORED_TARGET for item in items),
        _mean(stock), _median(stock), _std(stock), min(stock, default=None), max(stock, default=None), _rates(stock),
        _mean(benchmark), _median(benchmark), _mean(excess), _median(excess), _std(excess),
        min(excess, default=None), max(excess, default=None), _rates(excess), state,
        PATH_METRIC_STATUS, PATH_METRIC_REASON, _hash(values),
    )


def _temporal(stock: tuple[float, ...], excess: tuple[float, ...]) -> TemporalEvidence:
    if not stock or not excess:
        return TemporalEvidence.UNDEFINED
    a, b = sum(stock) / len(stock), sum(excess) / len(excess)
    if a * b < 0:
        return TemporalEvidence.REVERSAL
    if a == 0.0 or b == 0.0:
        return TemporalEvidence.MIXED
    return TemporalEvidence.SAME_DIRECTION


def _block(items: tuple[DailyPortfolioOutcome, ...], budget: int, horizon: int, block: tuple[str, str, str]) -> PortfolioOutcomeBlockSummary:
    name, start, end = block
    selected = tuple(item for item in items if start <= item.session_date <= end)
    evaluable = tuple(item for item in selected if item.availability is PortfolioOutcomeAvailability.FULLY_EVALUABLE)
    stock = tuple(float(item.portfolio_stock_return_pct) for item in evaluable)
    benchmark = tuple(float(item.portfolio_benchmark_return_pct) for item in evaluable)
    excess = tuple(float(item.portfolio_excess_return_pct_points) for item in evaluable)
    temporal = _temporal(stock, excess)
    payload = {"budget": budget, "horizon": horizon, "block": block, "daily": tuple(item.identity for item in selected), "temporal": temporal.value}
    return PortfolioOutcomeBlockSummary(
        budget, horizon, name, start, end, len(selected), len(evaluable),
        _mean(stock), _median(stock), _mean(benchmark), _mean(excess), _median(excess),
        _rates(stock), _rates(excess), temporal, _hash(payload),
    )


def _contrast(items: tuple[DailyPortfolioOutcome, ...], horizon: int, lower: int, higher: int, scope: tuple[str, str, str]) -> PortfolioOutcomeContrast:
    name, start, end = scope
    index = {(item.session_date, item.requested_budget): item for item in items if item.horizon_sessions == horizon and start <= item.session_date <= end}
    stock_delta: list[float] = []
    excess_delta: list[float] = []
    for signal_date in sorted({key[0] for key in index}):
        left, right = index.get((signal_date, lower)), index.get((signal_date, higher))
        if not left or not right or left.availability is not PortfolioOutcomeAvailability.FULLY_EVALUABLE or right.availability is not PortfolioOutcomeAvailability.FULLY_EVALUABLE:
            continue
        stock_delta.append(float(right.portfolio_stock_return_pct) - float(left.portfolio_stock_return_pct))
        excess_delta.append(float(right.portfolio_excess_return_pct_points) - float(left.portfolio_excess_return_pct_points))
    stock, excess = tuple(stock_delta), tuple(excess_delta)
    payload = {"scope": scope, "horizon": horizon, "lower": lower, "higher": higher, "stock": stock, "excess": excess}
    return PortfolioOutcomeContrast(
        name, horizon, lower, higher, len(stock), _mean(stock), _median(stock),
        _mean(excess), _median(excess), _rates(stock), _rates(excess), _hash(payload),
    )


def _cost(items: tuple[DailyPortfolioOutcome, ...], budget: int, horizon: int, bps: int) -> PortfolioCostSensitivity:
    evaluable = tuple(item for item in items if item.availability is PortfolioOutcomeAvailability.FULLY_EVALUABLE)
    compatible = tuple(item for item in evaluable if item.one_way_weight_turnover is not None)
    gross_stock = tuple(float(item.portfolio_stock_return_pct) for item in compatible)
    gross_excess = tuple(float(item.portfolio_excess_return_pct_points) for item in compatible)
    costs = tuple(float(item.one_way_weight_turnover) * bps / 100.0 for item in compatible)
    net_stock = tuple(value - cost for value, cost in zip(gross_stock, costs, strict=True))
    net_excess = tuple(value - cost for value, cost in zip(gross_excess, costs, strict=True))
    payload = {"budget": budget, "horizon": horizon, "bps": bps, "daily": tuple(item.identity for item in compatible), "costs": costs}
    return PortfolioCostSensitivity(
        budget, horizon, bps, len(evaluable), len(compatible), _mean(gross_stock),
        _mean(costs), _mean(net_stock), _mean(gross_excess), _mean(net_excess),
        COST_LABEL, _hash(payload),
    )


def evaluate_portfolio_outcomes(
    source: PortfolioOutcomeInput,
    spec: PortfolioOutcomeSpec = NEUTRAL_PORTFOLIO_OUTCOMES_V1,
) -> PortfolioOutcomeEvaluationResult:
    if not isinstance(source, PortfolioOutcomeInput):
        raise TypeError("source must be PortfolioOutcomeInput")
    if not isinstance(spec, PortfolioOutcomeSpec):
        raise TypeError("spec must be PortfolioOutcomeSpec")
    outcome_index = {(item.session_date, item.symbol): item for item in source.outcomes}
    daily = tuple(
        _daily(portfolio, horizon, outcome_index)
        for portfolio in source.portfolios for horizon in spec.horizons
    )
    summaries = tuple(
        _summary(tuple(item for item in daily if item.requested_budget == budget and item.horizon_sessions == horizon), budget, horizon)
        for budget in spec.budgets for horizon in spec.horizons
    )
    blocks = tuple(
        _block(tuple(item for item in daily if item.requested_budget == budget and item.horizon_sessions == horizon), budget, horizon, block)
        for budget in spec.budgets for horizon in spec.horizons for block in spec.blocks
    )
    scopes = (("whole_period", BLOCKS[0][1], BLOCKS[-1][2]), *BLOCKS)
    contrasts = tuple(
        _contrast(daily, horizon, lower, higher, scope)
        for horizon in spec.horizons for scope in scopes for lower, higher in CONTRASTS
    )
    costs = tuple(
        _cost(tuple(item for item in daily if item.requested_budget == budget and item.horizon_sessions == horizon), budget, horizon, bps)
        for budget in spec.budgets for horizon in spec.horizons for bps in spec.cost_grid_bps
    )
    limitations = (
        "consecutive-session forward outcomes overlap and are not an executable return path",
        "CAGR Sharpe Sortino drawdown cumulative PnL and annualized path metrics are not applicable",
        "cost sensitivity is hypothetical formation-turnover arithmetic, not Vietnam execution cost",
        "missing constituent outcomes invalidate the portfolio observation without weight renormalization",
        "empty portfolios have undefined outcomes and cash earns no invented risk-free return",
        "budget contrasts are paired same-date descriptions with no ranking or winner",
        "database coverage is not necessarily historical VN100 membership",
    )
    payload = {
        "contract": {"name": CONTRACT, "version": VERSION},
        "specification": spec.fingerprint, "sources": source.source_identities,
        "daily": tuple(item.identity for item in daily),
        "summaries": tuple(item.identity for item in summaries),
        "blocks": tuple(item.identity for item in blocks),
        "contrasts": tuple(item.identity for item in contrasts),
        "costs": tuple(item.identity for item in costs), "limitations": limitations,
    }
    return PortfolioOutcomeEvaluationResult(
        CONTRACT, VERSION, spec.fingerprint, source.source_identities, daily,
        summaries, blocks, contrasts, costs, limitations, _hash(payload),
    )


def load_portfolio_outcome_input(
    phase6_root: str | Path,
    phase65_root: str | Path,
) -> PortfolioOutcomeInput:
    """Load frozen artifacts only; never access market data or reconstruct inputs."""
    p6, p65 = Path(phase6_root).resolve(), Path(phase65_root).resolve()
    manifest6_path, manifest65_path = p6 / "portfolio_construction_manifest.json", p65 / "point_in_time_forward_outcomes_manifest.json"
    manifest6 = json.loads(manifest6_path.read_text(encoding="utf-8"))
    manifest65 = json.loads(manifest65_path.read_text(encoding="utf-8"))
    if manifest6.get("completed") is not True or manifest6.get("budgets") != [5, 10, 20]:
        raise ValueError("Phase 6 manifest is incomplete or has unsupported budgets")
    if manifest6.get("candidate_source") != SELECTION_POLICY or manifest6.get("weighting_policies") != [WeightingPolicy.EQUAL_WEIGHT.value]:
        raise ValueError("Phase 6 scenario is not frozen ADX_ONLY / EQUAL_WEIGHT")
    declared_outcome_rows = manifest65.get("row_count")
    if (
        manifest65.get("completed") is not True
        or manifest65.get("horizons") != [5, 10, 20]
        or isinstance(declared_outcome_rows, bool)
        or not isinstance(declared_outcome_rows, int)
        or declared_outcome_rows <= 0
    ):
        raise ValueError("Phase 6.5 manifest is incomplete or unsupported")
    outcome_path = p65 / manifest65["artifact"]["filename"]
    actual_outcome_hash = _file_hash(outcome_path)
    if actual_outcome_hash != manifest65["artifact"]["sha256"]:
        raise ValueError("Phase 6.5 outcome artifact SHA-256 mismatch")
    portfolios_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    with (p6 / "portfolio_construction_by_date.csv").open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (row["session_date"], int(row["requested_budget"]))
            if key in portfolios_by_key:
                raise ValueError("duplicate Phase 6 daily portfolio")
            if key[1] not in BUDGETS or row["candidate_source"] != SELECTION_POLICY:
                raise ValueError("Phase 6 daily portfolio has an unsupported scenario")
            if row["weighting_policy"] != WeightingPolicy.EQUAL_WEIGHT.value:
                raise ValueError("Phase 6 daily portfolio has an unsupported weighting policy")
            portfolios_by_key[key] = row
    expected_daily_rows = manifest6.get("artifacts", {}).get("portfolio_construction_by_date.csv")
    if expected_daily_rows is not None and len(portfolios_by_key) != expected_daily_rows:
        raise ValueError("Phase 6 daily portfolio row count does not match manifest")
    positions_by_key: dict[tuple[str, int], list[FrozenOutcomePosition]] = {}
    position_row_count = 0
    with (p6 / "portfolio_positions.csv").open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            position_row_count += 1
            key = (row["session_date"], int(row["requested_budget"]))
            if row["candidate_source"] != SELECTION_POLICY or row["weighting_policy"] != WeightingPolicy.EQUAL_WEIGHT.value:
                raise ValueError("Phase 6 position has an unsupported scenario")
            positions_by_key.setdefault(key, []).append(FrozenOutcomePosition(
                row["symbol"].strip().upper(), int(row["selection_rank"]),
                _finite(row["weight"], name="position weight"), row["position_identity"],
            ))
            if key not in portfolios_by_key or row["portfolio_identity"] != portfolios_by_key[key]["identity"]:
                raise ValueError("Phase 6 position does not match its frozen portfolio")
    expected_position_rows = manifest6.get("artifacts", {}).get("portfolio_positions.csv")
    if expected_position_rows is not None and position_row_count != expected_position_rows:
        raise ValueError("Phase 6 position row count does not match manifest")
    portfolios: list[FrozenOutcomePortfolio] = []
    for key, row in sorted(portfolios_by_key.items()):
        positions = tuple(sorted(positions_by_key.get(key, ()), key=lambda item: item.selection_rank))
        if len(positions) != int(row["selected_count"]):
            raise ValueError("Phase 6 selected count does not match positions")
        gross = _finite(row["gross_weight"], name="gross weight")
        if not math.isclose(sum(item.weight for item in positions), gross, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError("Phase 6 frozen position weights do not reconcile")
        portfolios.append(FrozenOutcomePortfolio(
            key[0], key[1], row["weighting_policy"], row["selection_identity"],
            int(row["eligible_cross_section_count"]), int(row["selected_count"]),
            gross, _finite(row["cash_weight"], name="cash weight"),
            _optional_float(row["one_way_weight_turnover"]), row["identity"], positions,
        ))
    outcomes: list[FrozenForwardOutcome] = []
    with outcome_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            outcomes.append(FrozenForwardOutcome(
                row["session_date"], row["symbol"].strip().upper(), row["benchmark_symbol"],
                {h: row[f"target_session_{h}"] or None for h in HORIZONS},
                {h: _optional_float(row[f"stock_forward_return_{h}_pct"]) for h in HORIZONS},
                {h: _optional_float(row[f"benchmark_forward_return_{h}_pct"]) for h in HORIZONS},
                {h: _optional_float(row[f"excess_forward_return_{h}_pct_points"]) for h in HORIZONS},
                {h: row[f"outcome_{h}__availability"] for h in HORIZONS},
            ))
    if len(outcomes) != declared_outcome_rows:
        raise ValueError("Phase 6.5 outcome row count does not match manifest")
    source_ids = {
        "phase6_manifest_sha256": _file_hash(manifest6_path),
        "phase6_manifest_identity": _hash(manifest6),
        "phase6_result_identity": manifest6["result_identity"],
        "phase65_manifest_sha256": _file_hash(manifest65_path),
        "phase65_outcome_artifact_sha256": actual_outcome_hash,
        "phase65_outcome_row_count": str(declared_outcome_rows),
        "observation_identity": manifest65["observation_index"]["identity"],
        "observation_content_identity": manifest65["observation_index"]["content_identity"],
        "outcome_panel_identity": manifest65["outcome_panel"]["identity"],
        "outcome_content_identity": manifest65["outcome_panel"]["content_identity"],
    }
    return PortfolioOutcomeInput(source_ids, tuple(portfolios), tuple(outcomes))
