from __future__ import annotations

"""Neutral, descriptive sensitivity of frozen portfolio outcomes to hypothetical friction."""

from dataclasses import dataclass, field
from hashlib import sha256
import math
from statistics import mean, median, pstdev
from types import MappingProxyType
from typing import Any, Mapping

from quantlab.identity import canonical_identity_value, canonical_json
from quantlab.portfolio.construction import BLOCKS, BUDGETS, SELECTION_POLICY, WeightingPolicy
from quantlab.portfolio.outcome_evaluation import COST_GRID_BPS, HORIZONS, PATH_METRIC_REASON, PATH_METRIC_STATUS


CONTRACT = "quantlab.execution_friction_sensitivity"
VERSION = "v1"
HYPOTHETICAL_LABEL = "HYPOTHETICAL_FRICTION_SENSITIVITY_NOT_HISTORICAL_COST_ESTIMATE"
TURNOVER_DEFINITION = "Phase6_one_way_weight_turnover = 0.5 * (L1 security-weight change over symbol union + absolute cash-weight change)"
COST_FORMULA = "deduction_percentage_points = Phase6_one_way_weight_turnover * hypothetical_all_in_rate_bps / 100"
SCOPE = "frozen_ADX_ONLY_EQUAL_WEIGHT_portfolios_budgets_5_10_20_horizons_5_10_20"
EXPECTED_PROVENANCE = {
    "phase6_result_identity": "c23fa10a1b577509f1fa5d7a23d543dc63237b719c05269342ae5e7a6e660697",
    "phase6_specification_fingerprint": "7b78fb5ea54dd3986ac4e46cefae93b88734812c9ba72ad3beaeae9e8f68453d",
    "phase65_manifest_sha256": "46bc2f5ff3c99ea14b9798f0c3a116a258274a9ae50fcd95ac164b01845e0e86",
    "phase65_outcome_artifact_sha256": "2bea6502a27cf7caccfbaa174c7be0b708bc7a89e175c26fef8f18293e174712",
    "phase7_result_identity": "3eee3dfffe992d8c8663736c5e96ca7e95b1c47f2bbd97c3ee2ee2e960e25b65",
    "phase7_specification_fingerprint": "818c5a93b28024a36f8991e0cc0b1ceb9275c8a1690e0d4d90989f6a2ae158a0",
    "phase7_manifest_sha256": "0ed2df1a2253b397b7c37aab05c0d4f8164021aee2231afcd0b2417f3229e5b0",
    "phase7_daily_artifact_sha256": "b94bd474d39c31b144515912675fb7b63afe67c5f83dd793c5829fe806d5b986",
    "phase11a_result_identity": "ae1ad7157c598172b335085ea60693bb3cb95ce1471a4a74982d0fdfed9737d7",
    "phase11b_result_identity": "7363f62b313672b04fa5e2e83ab958bb103701d58ccd538ff500e3d8e093d669",
    "phase11c_result_identity": "4fb274958f452884fb6321245d3adaba011e052a8d05c34eb076f785cb7af9b0",
    "phase11d_result_identity": "3bf2fd5184f2a02801ad4e6af17b5d7b2e5411bb0fd816fb67eec88e5551526e",
}


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


class FrictionProvenance:
    CANONICAL = "CANONICAL"
    OPERATIONAL_ASSUMPTION = "OPERATIONAL_ASSUMPTION"
    HYPOTHETICAL_ASSUMPTION = "HYPOTHETICAL_ASSUMPTION"
    EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class FrictionComponentEvidence:
    component: str
    provenance: str
    rate_bps: float | None
    note: str

    def __post_init__(self) -> None:
        if self.provenance not in {
            FrictionProvenance.CANONICAL,
            FrictionProvenance.OPERATIONAL_ASSUMPTION,
            FrictionProvenance.HYPOTHETICAL_ASSUMPTION,
            FrictionProvenance.EVIDENCE_UNAVAILABLE,
        }:
            raise ValueError("invalid friction provenance")
        if self.provenance == FrictionProvenance.EVIDENCE_UNAVAILABLE and self.rate_bps is not None:
            raise ValueError("unavailable component cannot have a rate")
        if self.rate_bps is not None and (not math.isfinite(float(self.rate_bps)) or self.rate_bps < 0):
            raise ValueError("component rate must be finite and nonnegative")
        if not self.component or not self.note:
            raise ValueError("component name and provenance note are required")


@dataclass(frozen=True, slots=True)
class ExecutionFrictionSensitivitySpec:
    name: str = "NEUTRAL_EXECUTION_FRICTION_SENSITIVITY_V1"
    version: str = "1"
    budgets: tuple[int, ...] = BUDGETS
    horizons: tuple[int, ...] = HORIZONS
    cost_grid_bps: tuple[int, ...] = COST_GRID_BPS
    cost_grid_provenance: str = FrictionProvenance.HYPOTHETICAL_ASSUMPTION
    blocks: tuple[tuple[str, str, str], ...] = BLOCKS
    formula: str = COST_FORMULA
    turnover_definition: str = TURNOVER_DEFINITION
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if (self.version != "1" or self.budgets != BUDGETS or self.horizons != HORIZONS
                or self.cost_grid_bps != COST_GRID_BPS or self.cost_grid_provenance != FrictionProvenance.HYPOTHETICAL_ASSUMPTION or self.blocks != BLOCKS
                or self.formula != COST_FORMULA or self.turnover_definition != TURNOVER_DEFINITION):
            raise ValueError("Phase 11E scope, grid, turnover, and formula are frozen")
        payload = {
            "contract": [CONTRACT, VERSION], "name": self.name,
            "scope": SCOPE, "selection": SELECTION_POLICY,
            "weighting": WeightingPolicy.EQUAL_WEIGHT.value,
            "budgets": self.budgets, "horizons": self.horizons,
            "grid_bps": self.cost_grid_bps, "blocks": self.blocks,
            "grid_provenance": self.cost_grid_provenance,
            "turnover_definition": self.turnover_definition,
            "cost_formula": self.formula,
            "cost_semantics": "one_way_weight_turnover_cost_per_forward_observation; no compounding across overlapping observations",
            "zero_cost_scope": "turnover_defined_fully_evaluable_phase7_rows_to_match_phase7_cost_artifact; all-evaluable no-friction summary separately reconciled",
            "break_even": "mean gross excess percentage points * 100 / mean one-way turnover; analytical; not searched",
            "path_metrics": [PATH_METRIC_STATUS, PATH_METRIC_REASON],
            "timing_gap": "reference_to_next_open_is_not_slippage_and_is_not_deducted",
            "prohibitions": (
                "no_returns_reconstructed", "no_db_or_market_data_access", "no_fills_or_cost_estimates",
                "no_policy_ranking", "no_budget_horizon_or_cost_selection", "no_portfolio_changes",
                "no_wealth_curve_or_cumulative_metrics", "no_market_impact_estimate",
            ),
        }
        object.__setattr__(self, "fingerprint", _hash(payload))


NEUTRAL_EXECUTION_FRICTION_SENSITIVITY_V1 = ExecutionFrictionSensitivitySpec()
FRICTION_COMPONENT_EVIDENCE = (
    FrictionComponentEvidence("COMMISSION_OR_FEE", FrictionProvenance.OPERATIONAL_ASSUMPTION, None, "Current operational configuration is not treated as a historical fee schedule; no component rate is applied."),
    FrictionComponentEvidence("SELL_SIDE_TAX", FrictionProvenance.OPERATIONAL_ASSUMPTION, None, "Current operational configuration is not treated as a historical tax schedule; no component rate is applied."),
    FrictionComponentEvidence("SPREAD_OR_SLIPPAGE", FrictionProvenance.OPERATIONAL_ASSUMPTION, None, "Operational fixed-bps assumptions are not measured historical spread/slippage; grid is unallocated aggregate sensitivity."),
    FrictionComponentEvidence("MARKET_IMPACT", FrictionProvenance.EVIDENCE_UNAVAILABLE, None, "No order-book, spread, impact, or fill evidence supports a rate."),
)


@dataclass(frozen=True, slots=True)
class FrozenPortfolioOutcomeObservation:
    session_date: str
    requested_budget: int
    horizon_sessions: int
    availability: str
    one_way_weight_turnover: float | None
    portfolio_stock_return_pct: float | None
    portfolio_excess_return_pct_points: float | None
    source_identity: str

    def __post_init__(self) -> None:
        if self.requested_budget not in BUDGETS or self.horizon_sessions not in HORIZONS:
            raise ValueError("observation is outside the frozen Phase 6/7 dimensions")
        if not self.session_date or not self.source_identity:
            raise ValueError("date and source identity are required")
        if self.availability not in {
            "FULLY_EVALUABLE", "EMPTY_PORTFOLIO", "UNAVAILABLE_CONSTITUENT_OUTCOME",
            "UNAVAILABLE_BENCHMARK_OUTCOME", "CENSORED_TARGET",
        }:
            raise ValueError("unknown Phase 7 availability state")
        if self.availability == "FULLY_EVALUABLE":
            if self.portfolio_stock_return_pct is None or self.portfolio_excess_return_pct_points is None:
                raise ValueError("fully evaluable Phase 7 row requires stock and excess returns")
            for name, value in (("stock return", self.portfolio_stock_return_pct), ("excess return", self.portfolio_excess_return_pct_points)):
                if not math.isfinite(float(value)):
                    raise ValueError(f"{name} must be finite")
        elif self.portfolio_stock_return_pct is not None or self.portfolio_excess_return_pct_points is not None:
            raise ValueError("unavailable Phase 7 rows must not contain returns")
        if self.one_way_weight_turnover is not None:
            value = float(self.one_way_weight_turnover)
            if not math.isfinite(value) or value < 0 or value > 1 + 1e-12:
                raise ValueError("Phase 6 one-way weight turnover must be in [0, 1]")
            object.__setattr__(self, "one_way_weight_turnover", value)


@dataclass(frozen=True, slots=True)
class ExecutionFrictionSensitivitySummary:
    scope_name: str
    scope_start_date: str
    scope_end_date: str
    requested_budget: int
    horizon_sessions: int
    hypothetical_all_in_cost_rate_bps: int
    phase7_evaluable_date_count: int
    turnover_defined_date_count: int
    included_date_count: int
    mean_gross_stock_return_pct: float | None
    median_gross_stock_return_pct: float | None
    mean_net_stock_return_pct: float | None
    median_net_stock_return_pct: float | None
    stock_return_population_std_pct: float | None
    positive_net_stock_return_rate: float | None
    mean_gross_excess_return_pct_points: float | None
    median_gross_excess_return_pct_points: float | None
    mean_net_excess_return_pct_points: float | None
    median_net_excess_return_pct_points: float | None
    excess_return_population_std_pct_points: float | None
    positive_net_excess_rate: float | None
    mean_cost_deduction_pct_points: float | None
    median_cost_deduction_pct_points: float | None
    label: str = HYPOTHETICAL_LABEL
    path_metric_status: str = PATH_METRIC_STATUS
    path_metric_reason: str = PATH_METRIC_REASON
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        if self.label != HYPOTHETICAL_LABEL or self.path_metric_status != PATH_METRIC_STATUS or self.path_metric_reason != PATH_METRIC_REASON:
            raise ValueError("Phase 11E descriptive-only labels are frozen")
        object.__setattr__(self, "identity", _hash({key: value for key, value in _asdict(self).items() if key != "identity"}))


@dataclass(frozen=True, slots=True)
class ExecutionFrictionBreakEven:
    scope_name: str
    scope_start_date: str
    scope_end_date: str
    requested_budget: int
    horizon_sessions: int
    included_date_count: int
    mean_gross_excess_return_pct_points: float | None
    mean_one_way_weight_turnover: float | None
    descriptive_break_even_friction_bps: float | None
    undefined_reason: str | None
    label: str = "DESCRIPTIVE_BREAK_EVEN_FRICTION"
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        if self.label != "DESCRIPTIVE_BREAK_EVEN_FRICTION":
            raise ValueError("break-even label is frozen")
        if self.descriptive_break_even_friction_bps is not None and (
            not math.isfinite(self.descriptive_break_even_friction_bps) or self.descriptive_break_even_friction_bps < 0
        ):
            raise ValueError("break-even friction must be finite and nonnegative")
        object.__setattr__(self, "identity", _hash({key: value for key, value in _asdict(self).items() if key != "identity"}))


@dataclass(frozen=True, slots=True)
class ExecutionFrictionSensitivityResult:
    contract_name: str
    contract_version: str
    specification_fingerprint: str
    source_identities: Mapping[str, str]
    summaries: tuple[ExecutionFrictionSensitivitySummary, ...]
    block_summaries: tuple[ExecutionFrictionSensitivitySummary, ...]
    break_even: tuple[ExecutionFrictionBreakEven, ...]
    friction_components: tuple[FrictionComponentEvidence, ...]
    limitations: tuple[str, ...]
    identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_identities", MappingProxyType(dict(sorted(self.source_identities.items()))))


def _asdict(record: Any) -> dict[str, Any]:
    return {name: getattr(record, name) for name in record.__dataclass_fields__ if name != "identity"}


def _mean(values: tuple[float, ...]) -> float | None:
    return None if not values else float(mean(values))


def _median(values: tuple[float, ...]) -> float | None:
    return None if not values else float(median(values))


def _rate(values: tuple[float, ...]) -> float | None:
    return None if not values else sum(value > 0 for value in values) / len(values)


def _summary(
    observations: tuple[FrozenPortfolioOutcomeObservation, ...],
    budget: int,
    horizon: int,
    bps: int,
    scope: tuple[str, str, str],
) -> ExecutionFrictionSensitivitySummary:
    scope_name, start, end = scope
    in_scope = tuple(item for item in observations if start <= item.session_date <= end)
    evaluable = tuple(item for item in in_scope if item.availability == "FULLY_EVALUABLE")
    turnover_defined = tuple(item for item in evaluable if item.one_way_weight_turnover is not None)
    # Match Phase 7's cost-sensitivity cohort. At zero bps, undefined turnover still means zero deduction,
    # but the paired Phase 7 cost artifact itself uses turnover-defined records; use that common cohort here.
    included = turnover_defined
    if not bps:
        included = turnover_defined
    stocks = tuple(float(item.portfolio_stock_return_pct) for item in included)
    excess = tuple(float(item.portfolio_excess_return_pct_points) for item in included)
    deductions = tuple(float(item.one_way_weight_turnover) * bps / 100.0 for item in included)
    net_stock = tuple(value - cost for value, cost in zip(stocks, deductions, strict=True))
    net_excess = tuple(value - cost for value, cost in zip(excess, deductions, strict=True))
    return ExecutionFrictionSensitivitySummary(
        scope_name, start, end, budget, horizon, bps, len(evaluable), len(turnover_defined), len(included),
        _mean(stocks), _median(stocks), _mean(net_stock), _median(net_stock),
        None if not stocks else float(pstdev(stocks)), _rate(net_stock),
        _mean(excess), _median(excess), _mean(net_excess), _median(net_excess),
        None if not excess else float(pstdev(excess)), _rate(net_excess),
        _mean(deductions), _median(deductions),
    )


def _break_even(
    observations: tuple[FrozenPortfolioOutcomeObservation, ...], budget: int, horizon: int,
    scope: tuple[str, str, str],
) -> ExecutionFrictionBreakEven:
    scope_name, start, end = scope
    usable = tuple(
        item for item in observations
        if start <= item.session_date <= end
        and item.availability == "FULLY_EVALUABLE"
        and item.one_way_weight_turnover is not None
    )
    gross = _mean(tuple(float(item.portfolio_excess_return_pct_points) for item in usable))
    turnover = _mean(tuple(float(item.one_way_weight_turnover) for item in usable))
    if not usable:
        be, reason = None, "no_fully_evaluable_turnover_defined_observations"
    elif turnover is None or turnover <= 0:
        be, reason = None, "mean_turnover_is_zero_or_undefined"
    elif gross is None or gross < 0:
        be, reason = None, "gross_mean_excess_is_negative_no_nonnegative_break_even_rate"
    else:
        be, reason = float(gross) * 100.0 / turnover, None
    return ExecutionFrictionBreakEven(scope_name, start, end, budget, horizon, len(usable), gross, turnover, be, reason)


def evaluate_execution_friction_sensitivity(
    observations: tuple[FrozenPortfolioOutcomeObservation, ...],
    source_identities: Mapping[str, str],
    spec: ExecutionFrictionSensitivitySpec = NEUTRAL_EXECUTION_FRICTION_SENSITIVITY_V1,
) -> ExecutionFrictionSensitivityResult:
    """Re-project persisted Phase 7 daily returns with Phase 6 weight-turnover deductions only."""
    if not isinstance(spec, ExecutionFrictionSensitivitySpec):
        raise TypeError("spec must be ExecutionFrictionSensitivitySpec")
    required_sources = {
        *EXPECTED_PROVENANCE,
    }
    if not required_sources.issubset(source_identities) or any(not source_identities[key] for key in required_sources):
        raise ValueError("complete Phase 6/6.5/7 and Phase 11A-D source identities are required")
    mismatches = [key for key, expected in EXPECTED_PROVENANCE.items() if source_identities[key] != expected]
    if mismatches:
        raise ValueError(f"frozen upstream provenance mismatch: {mismatches[0]}")
    items = tuple(sorted(observations, key=lambda item: (item.session_date, item.requested_budget, item.horizon_sessions)))
    keys = tuple((item.session_date, item.requested_budget, item.horizon_sessions) for item in items)
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate Phase 7 daily portfolio outcome key")
    if not items:
        raise ValueError("Phase 7 daily portfolio outcome observations are required")
    summaries: list[ExecutionFrictionSensitivitySummary] = []
    blocks: list[ExecutionFrictionSensitivitySummary] = []
    break_evens: list[ExecutionFrictionBreakEven] = []
    whole = ("whole_period", min(item.session_date for item in items), max(item.session_date for item in items))
    scopes = (whole, *spec.blocks)
    for budget in spec.budgets:
        for horizon in spec.horizons:
            group = tuple(item for item in items if item.requested_budget == budget and item.horizon_sessions == horizon)
            for bps in spec.cost_grid_bps:
                summaries.append(_summary(group, budget, horizon, bps, whole))
                blocks.extend(_summary(group, budget, horizon, bps, block) for block in spec.blocks)
            break_evens.extend(_break_even(group, budget, horizon, scope) for scope in scopes)
    limitations = (
        HYPOTHETICAL_LABEL,
        "Phase 6 one_way_weight_turnover is used exactly; Phase 11B TARGET_ACQUISITION_NOTIONAL and acquisition shares are not turnover inputs.",
        "Aggregate hypothetical one-way friction is not allocated to commission, sell tax, spread/slippage, or market impact components.",
        "Reference-to-next-open gaps are descriptive and are not treated as slippage or deducted.",
        "Forward horizons overlap; path metrics are NOT_APPLICABLE because OVERLAPPING_FORWARD_HORIZONS.",
        "No compounded costs, wealth path, CAGR, Sharpe, Sortino, Calmar, drawdown, fills, or executable-cost claim is produced.",
        "Phase 11 V1 HISTORICAL_MONETARY_CAPACITY_RESEARCH = DEFERRED.",
        "No policy, budget, horizon, cost rate, or execution strategy is selected.",
    )
    summary_tuple, block_tuple, break_tuple = tuple(summaries), tuple(blocks), tuple(break_evens)
    ordered_sources = dict(sorted(source_identities.items()))
    identity = _hash({
        "contract": [CONTRACT, VERSION], "spec": spec.fingerprint, "sources": ordered_sources,
        "summaries": tuple(row.identity for row in summary_tuple),
        "blocks": tuple(row.identity for row in block_tuple),
        "break_even": tuple(row.identity for row in break_tuple),
        "components": tuple((item.component, item.provenance, item.rate_bps, item.note) for item in FRICTION_COMPONENT_EVIDENCE),
        "limitations": limitations,
    })
    return ExecutionFrictionSensitivityResult(
        CONTRACT, VERSION, spec.fingerprint, MappingProxyType(ordered_sources),
        summary_tuple, block_tuple, break_tuple, FRICTION_COMPONENT_EVIDENCE,
        limitations, identity,
    )
