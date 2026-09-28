from __future__ import annotations

"""Descriptive outcomes for frozen Phase 10B policy counterfactuals."""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import math
from statistics import median, pstdev
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from quantlab.identity import canonical_identity_value, canonical_json
from quantlab.portfolio.construction import BLOCKS
from quantlab.portfolio.outcome_evaluation import (
    COST_GRID_BPS,
    HORIZONS,
    PATH_METRIC_REASON,
    PATH_METRIC_STATUS,
    FrozenForwardOutcome,
    PortfolioOutcomeAvailability,
)
from quantlab.portfolio.risk_policy import RiskPolicy, RiskPolicyPortfolio, RiskPolicyState


CONTRACT = "quantlab.portfolio_risk_policy_outcome_evaluation"
VERSION = "v1"
CASH_RETURN_ASSUMPTION = "ZERO"
SUMMARY_SUBSETS = ("ALL", "APPLIED", "IDENTITY_NO_CHANGE")


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _mean(values: Iterable[float]) -> float | None:
    items = tuple(values); return None if not items else float(sum(items) / len(items))


def _median(values: Iterable[float]) -> float | None:
    items = tuple(values); return None if not items else float(median(items))


def _std(values: Iterable[float]) -> float | None:
    items = tuple(values); return None if not items else float(pstdev(items))


@dataclass(frozen=True, slots=True)
class RiskPolicyOutcomeSpec:
    name: str = "FROZEN_RISK_POLICY_OUTCOMES_V1"
    version: str = "1"
    horizons: tuple[int, ...] = HORIZONS
    cost_grid_bps: tuple[int, ...] = COST_GRID_BPS
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if self.version != "1" or self.horizons != HORIZONS or self.cost_grid_bps != COST_GRID_BPS:
            raise ValueError("unsupported risk-policy outcome specification")
        object.__setattr__(self, "fingerprint", _hash({
            "contract": (CONTRACT, VERSION), "name": self.name,
            "horizons": self.horizons, "policies": tuple(item.value for item in RiskPolicy),
            "budgets": (5, 10, 20),
            "portfolio_return": "sum(transformed_weight_i*stock_forward_return_i_pct)",
            "cash_return": CASH_RETURN_ASSUMPTION,
            "benchmark_headline": "same_date_VNINDEX_forward_return_pct",
            "benchmark_contribution": "transformed_gross_weight*headline_benchmark_return_pct",
            "excess": "portfolio_return_minus_benchmark_contribution",
            "availability": "all_positive_weight_constituents_and_benchmark_required_no_renormalization",
            "zero_weight": "not_economically_active_not_required",
            "cost": "transformation_turnover_only_times_bps_divided_by_100_percentage_points",
            "blocks": BLOCKS, "path_metrics": (PATH_METRIC_STATUS, PATH_METRIC_REASON),
            "restrictions": ("no_parameter_change", "no_ranking", "no_decision", "no_compounding"),
        }))


FROZEN_RISK_POLICY_OUTCOMES_V1 = RiskPolicyOutcomeSpec()


@dataclass(frozen=True, slots=True)
class RiskPolicyOutcomeObservation:
    session_date: str
    policy: RiskPolicy
    policy_state: RiskPolicyState
    policy_fingerprint: str
    requested_budget: int
    horizon_sessions: int
    risky_gross_weight: float
    cash_weight: float
    transformation_turnover: float
    active_constituent_count: int
    availability: PortfolioOutcomeAvailability
    unavailable_constituent_count: int
    target_session: str | None
    portfolio_forward_return_pct: float | None
    benchmark_forward_return_pct: float | None
    portfolio_benchmark_contribution_pct: float | None
    portfolio_excess_return_pct_points: float | None
    source_policy_portfolio_identity: str
    identity: str


@dataclass(frozen=True, slots=True)
class RiskPolicyOutcomeSummary:
    policy: RiskPolicy
    policy_fingerprint: str
    requested_budget: int
    horizon_sessions: int
    subset: str
    total_dates: int
    evaluable_dates: int
    coverage_pct: float | None
    applied_dates: int
    identity_no_change_dates: int
    empty_dates: int
    unavailable_dates: int
    mean_portfolio_return_pct: float | None
    median_portfolio_return_pct: float | None
    portfolio_return_std_pct: float | None
    positive_portfolio_return_rate: float | None
    mean_benchmark_return_pct: float | None
    mean_benchmark_contribution_pct: float | None
    mean_excess_return_pct_points: float | None
    median_excess_return_pct_points: float | None
    excess_return_std_pct_points: float | None
    positive_excess_rate: float | None
    mean_risky_gross_weight: float | None
    mean_cash_weight: float | None
    path_metric_status: str
    path_metric_reason: str
    identity: str


@dataclass(frozen=True, slots=True)
class RiskPolicyOutcomeBlockSummary:
    policy: RiskPolicy
    policy_fingerprint: str
    requested_budget: int
    horizon_sessions: int
    block_name: str
    block_start_date: str
    block_end_date: str
    total_dates: int
    evaluable_dates: int
    mean_portfolio_return_pct: float | None
    median_portfolio_return_pct: float | None
    mean_excess_return_pct_points: float | None
    positive_portfolio_return_rate: float | None
    positive_excess_rate: float | None
    mean_risky_gross_weight: float | None
    identity: str


@dataclass(frozen=True, slots=True)
class RiskPolicyOutcomeContrast:
    requested_budget: int
    horizon_sessions: int
    subset: str
    paired_date_count: int
    mean_portfolio_return_delta_pct_points: float | None
    median_portfolio_return_delta_pct_points: float | None
    mean_excess_return_delta_pct_points: float | None
    median_excess_return_delta_pct_points: float | None
    mean_absolute_return_magnitude_delta_pct_points: float | None
    mean_risky_exposure_delta: float | None
    mean_cash_delta: float | None
    identity: str


@dataclass(frozen=True, slots=True)
class RiskPolicyCostSensitivity:
    policy: RiskPolicy
    policy_fingerprint: str
    requested_budget: int
    horizon_sessions: int
    cost_rate_bps: int
    evaluable_dates: int
    mean_transformation_turnover: float | None
    mean_hypothetical_cost_pct_points: float | None
    mean_gross_portfolio_return_pct: float | None
    mean_net_portfolio_return_pct: float | None
    mean_gross_excess_return_pct_points: float | None
    mean_net_excess_return_pct_points: float | None
    identity: str


@dataclass(frozen=True, slots=True)
class RiskPolicyOutcomeResult:
    contract_name: str
    contract_version: str
    specification_fingerprint: str
    source_identities: Mapping[str, str]
    observations: tuple[RiskPolicyOutcomeObservation, ...]
    summaries: tuple[RiskPolicyOutcomeSummary, ...]
    block_summaries: tuple[RiskPolicyOutcomeBlockSummary, ...]
    contrasts: tuple[RiskPolicyOutcomeContrast, ...]
    cost_sensitivity: tuple[RiskPolicyCostSensitivity, ...]
    limitations: tuple[str, ...]
    identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_identities", MappingProxyType(dict(self.source_identities)))


def _missing_availability(statuses: tuple[str, ...]) -> PortfolioOutcomeAvailability:
    if any(item == "CENSORED_AFTER_DATA_END" for item in statuses): return PortfolioOutcomeAvailability.CENSORED_TARGET
    if any("BENCHMARK" in item for item in statuses): return PortfolioOutcomeAvailability.UNAVAILABLE_BENCHMARK_OUTCOME
    return PortfolioOutcomeAvailability.UNAVAILABLE_CONSTITUENT_OUTCOME


def _observation(portfolio: RiskPolicyPortfolio, horizon: int, index: Mapping[tuple[str, str], FrozenForwardOutcome]) -> RiskPolicyOutcomeObservation:
    active = tuple(item for item in portfolio.positions if item.transformed_weight > 0.0)
    if not active:
        availability = PortfolioOutcomeAvailability.EMPTY_PORTFOLIO
        missing = 0; target = stock = benchmark = benchmark_contribution = excess = None
    else:
        matched = tuple(index.get((portfolio.session_date, item.symbol)) for item in active)
        missing = sum(item is None for item in matched)
        statuses = tuple("MISSING_JOINED_OUTCOME" if item is None else item.availability[horizon] for item in matched)
        if missing or any(item != "AVAILABLE" for item in statuses):
            availability = _missing_availability(statuses)
            target = stock = benchmark = benchmark_contribution = excess = None
        else:
            complete = tuple(item for item in matched if item is not None)
            targets = {item.target_sessions[horizon] for item in complete}
            benchmarks = {item.benchmark_returns[horizon] for item in complete}
            if len(targets) != 1 or None in targets: raise ValueError("constituent target sessions do not reconcile")
            if len(benchmarks) != 1 or None in benchmarks: raise ValueError("constituent benchmark outcomes do not reconcile")
            target = next(iter(targets)); benchmark = float(next(iter(benchmarks)))
            stock = sum(position.transformed_weight * float(outcome.stock_returns[horizon]) for position, outcome in zip(active, complete, strict=True))
            benchmark_contribution = portfolio.transformed_gross_weight * benchmark
            excess = stock - benchmark_contribution
            availability = PortfolioOutcomeAvailability.FULLY_EVALUABLE
    payload = {
        "portfolio": portfolio.identity, "horizon": horizon, "availability": availability.value,
        "target": target, "stock": stock, "benchmark": benchmark,
        "benchmark_contribution": benchmark_contribution, "excess": excess,
    }
    return RiskPolicyOutcomeObservation(
        portfolio.session_date, portfolio.policy, portfolio.policy_state, portfolio.policy_fingerprint,
        portfolio.requested_budget, horizon, portfolio.transformed_gross_weight,
        portfolio.transformed_cash_weight, portfolio.transformation_turnover, len(active), availability,
        missing, target, stock, benchmark, benchmark_contribution, excess, portfolio.identity, _hash(payload),
    )


def _subset(items: tuple[RiskPolicyOutcomeObservation, ...], name: str) -> tuple[RiskPolicyOutcomeObservation, ...]:
    if name == "ALL": return items
    state = RiskPolicyState.APPLIED if name == "APPLIED" else RiskPolicyState.IDENTITY_NO_CHANGE
    return tuple(item for item in items if item.policy_state is state)


def _summary(items: tuple[RiskPolicyOutcomeObservation, ...], policy: RiskPolicy, fingerprint: str, budget: int, horizon: int, subset: str) -> RiskPolicyOutcomeSummary:
    selected = _subset(tuple(item for item in items if item.policy is policy and item.requested_budget == budget and item.horizon_sessions == horizon), subset)
    evaluable = tuple(item for item in selected if item.availability is PortfolioOutcomeAvailability.FULLY_EVALUABLE)
    def values(name: str) -> tuple[float, ...]: return tuple(float(getattr(item, name)) for item in evaluable)
    stock, benchmark = values("portfolio_forward_return_pct"), values("benchmark_forward_return_pct")
    contribution, excess = values("portfolio_benchmark_contribution_pct"), values("portfolio_excess_return_pct_points")
    gross, cash = values("risky_gross_weight"), values("cash_weight")
    payload = {"policy": fingerprint, "budget": budget, "horizon": horizon, "subset": subset, "ids": tuple(item.identity for item in selected)}
    return RiskPolicyOutcomeSummary(
        policy, fingerprint, budget, horizon, subset, len(selected), len(evaluable),
        None if not selected else len(evaluable) / len(selected) * 100.0,
        sum(item.policy_state is RiskPolicyState.APPLIED for item in selected),
        sum(item.policy_state is RiskPolicyState.IDENTITY_NO_CHANGE for item in selected),
        sum(item.availability is PortfolioOutcomeAvailability.EMPTY_PORTFOLIO for item in selected),
        len(selected) - len(evaluable) - sum(item.availability is PortfolioOutcomeAvailability.EMPTY_PORTFOLIO for item in selected),
        _mean(stock), _median(stock), _std(stock), None if not stock else sum(value > 0 for value in stock) / len(stock),
        _mean(benchmark), _mean(contribution), _mean(excess), _median(excess), _std(excess),
        None if not excess else sum(value > 0 for value in excess) / len(excess), _mean(gross), _mean(cash),
        PATH_METRIC_STATUS, PATH_METRIC_REASON, _hash(payload),
    )


def _block(items: tuple[RiskPolicyOutcomeObservation, ...], policy: RiskPolicy, fingerprint: str, budget: int, horizon: int, block: tuple[str, str, str]) -> RiskPolicyOutcomeBlockSummary:
    name, start, end = block
    selected = tuple(item for item in items if item.policy is policy and item.requested_budget == budget and item.horizon_sessions == horizon and start <= item.session_date <= end)
    evaluable = tuple(item for item in selected if item.availability is PortfolioOutcomeAvailability.FULLY_EVALUABLE)
    stock = tuple(float(item.portfolio_forward_return_pct) for item in evaluable)
    excess = tuple(float(item.portfolio_excess_return_pct_points) for item in evaluable)
    payload = {"policy": fingerprint, "budget": budget, "horizon": horizon, "block": block, "ids": tuple(item.identity for item in selected)}
    return RiskPolicyOutcomeBlockSummary(policy, fingerprint, budget, horizon, name, start, end, len(selected), len(evaluable),
        _mean(stock), _median(stock), _mean(excess), None if not stock else sum(v > 0 for v in stock)/len(stock),
        None if not excess else sum(v > 0 for v in excess)/len(excess), _mean(item.risky_gross_weight for item in evaluable), _hash(payload))


def _contrast(items: tuple[RiskPolicyOutcomeObservation, ...], budget: int, horizon: int, subset: str) -> RiskPolicyOutcomeContrast:
    base = {(item.session_date): item for item in items if item.policy is RiskPolicy.NO_RISK_POLICY and item.requested_budget == budget and item.horizon_sessions == horizon and item.availability is PortfolioOutcomeAvailability.FULLY_EVALUABLE}
    scaled = {item.session_date: item for item in items if item.policy is RiskPolicy.VOLATILITY_SCALING and item.requested_budget == budget and item.horizon_sessions == horizon and item.availability is PortfolioOutcomeAvailability.FULLY_EVALUABLE and (subset == "ALL" or item.policy_state is RiskPolicyState.APPLIED)}
    dates = tuple(sorted(set(base) & set(scaled)))
    stock = tuple(float(scaled[d].portfolio_forward_return_pct) - float(base[d].portfolio_forward_return_pct) for d in dates)
    excess = tuple(float(scaled[d].portfolio_excess_return_pct_points) - float(base[d].portfolio_excess_return_pct_points) for d in dates)
    magnitude = tuple(abs(float(scaled[d].portfolio_forward_return_pct)) - abs(float(base[d].portfolio_forward_return_pct)) for d in dates)
    exposure = tuple(scaled[d].risky_gross_weight - base[d].risky_gross_weight for d in dates)
    cash = tuple(scaled[d].cash_weight - base[d].cash_weight for d in dates)
    payload = {"budget": budget, "horizon": horizon, "subset": subset, "pairs": tuple((base[d].identity, scaled[d].identity) for d in dates)}
    return RiskPolicyOutcomeContrast(budget, horizon, subset, len(dates), _mean(stock), _median(stock), _mean(excess), _median(excess), _mean(magnitude), _mean(exposure), _mean(cash), _hash(payload))


def _cost(items: tuple[RiskPolicyOutcomeObservation, ...], policy: RiskPolicy, fingerprint: str, budget: int, horizon: int, bps: int) -> RiskPolicyCostSensitivity:
    selected = tuple(item for item in items if item.policy is policy and item.requested_budget == budget and item.horizon_sessions == horizon and item.availability is PortfolioOutcomeAvailability.FULLY_EVALUABLE)
    costs = tuple(item.transformation_turnover * bps / 100.0 for item in selected)
    stock = tuple(float(item.portfolio_forward_return_pct) for item in selected); excess = tuple(float(item.portfolio_excess_return_pct_points) for item in selected)
    payload = {"policy": fingerprint, "budget": budget, "horizon": horizon, "bps": bps, "ids": tuple(item.identity for item in selected), "costs": costs}
    return RiskPolicyCostSensitivity(policy, fingerprint, budget, horizon, bps, len(selected), _mean(item.transformation_turnover for item in selected), _mean(costs), _mean(stock), _mean(v-c for v,c in zip(stock,costs,strict=True)), _mean(excess), _mean(v-c for v,c in zip(excess,costs,strict=True)), _hash(payload))


def evaluate_risk_policy_outcomes(
    portfolios: Iterable[RiskPolicyPortfolio], outcomes: Iterable[FrozenForwardOutcome], *,
    source_identities: Mapping[str, str], spec: RiskPolicyOutcomeSpec = FROZEN_RISK_POLICY_OUTCOMES_V1,
) -> RiskPolicyOutcomeResult:
    frozen = tuple(sorted(portfolios, key=lambda item: (item.session_date, item.requested_budget, item.policy.value)))
    outcome_tuple = tuple(sorted(outcomes, key=lambda item: (item.session_date, item.symbol)))
    outcome_index = {(item.session_date, item.symbol): item for item in outcome_tuple}
    if len(outcome_index) != len(outcome_tuple): raise ValueError("duplicate outcome key")
    policy_fingerprints = {item.policy: item.policy_fingerprint for item in frozen}
    if set(policy_fingerprints) != set(RiskPolicy): raise ValueError("exactly the two frozen policies are required")
    observations = tuple(_observation(item, horizon, outcome_index) for item in frozen for horizon in spec.horizons)
    summaries = tuple(_summary(observations, policy, policy_fingerprints[policy], budget, horizon, subset)
        for policy in RiskPolicy for budget in (5,10,20) for horizon in spec.horizons
        for subset in (("ALL",) if policy is RiskPolicy.NO_RISK_POLICY else SUMMARY_SUBSETS))
    blocks = tuple(_block(observations, policy, policy_fingerprints[policy], budget, horizon, block)
        for policy in RiskPolicy for budget in (5,10,20) for horizon in spec.horizons for block in BLOCKS)
    contrasts = tuple(_contrast(observations, budget, horizon, subset) for budget in (5,10,20) for horizon in spec.horizons for subset in ("ALL","APPLIED"))
    costs = tuple(_cost(observations, policy, policy_fingerprints[policy], budget, horizon, bps)
        for policy in RiskPolicy for budget in (5,10,20) for horizon in spec.horizons for bps in spec.cost_grid_bps)
    sources = MappingProxyType(dict(source_identities))
    limitations = (
        "overlapping forward outcomes are descriptive observations, not an executable return path",
        "cash return is fixed at zero; no deposit interest or financing cost is modeled",
        "cost sensitivity deducts Phase 10B transformation turnover only and is non-compounded",
        "no policy, budget, horizon, or cost level is ranked, selected, or optimized",
    )
    payload = {"contract": (CONTRACT,VERSION), "spec": spec.fingerprint, "sources": dict(sources),
               "observations": tuple(item.identity for item in observations), "summaries": tuple(item.identity for item in summaries),
               "blocks": tuple(item.identity for item in blocks), "contrasts": tuple(item.identity for item in contrasts),
               "costs": tuple(item.identity for item in costs), "limitations": limitations}
    return RiskPolicyOutcomeResult(CONTRACT, VERSION, spec.fingerprint, sources, observations, summaries, blocks, contrasts, costs, limitations, _hash(payload))
