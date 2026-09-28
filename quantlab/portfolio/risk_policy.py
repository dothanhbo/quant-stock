from __future__ import annotations

"""Outcome-free counterfactual policies over frozen Phase 6/10A evidence."""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import math
from statistics import median
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from quantlab.identity import canonical_identity_value, canonical_json
from quantlab.portfolio.construction import BLOCKS, WHOLE_SCOPE
from quantlab.portfolio.risk import (
    FrozenRiskPortfolio,
    PortfolioRiskComponentContribution,
    PortfolioRiskObservation,
    RiskEvidenceState,
    SECTOR_EVIDENCE_UNAVAILABLE,
)


CONTRACT = "quantlab.portfolio_risk_policy_research"
VERSION = "v1"
TARGET_ANNUALIZED_VOLATILITY = 0.20
RISK_CONTRIBUTION_POLICY_STATE = "RISK_CONTRIBUTION_POLICY_DEFERRED"


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _mean(values: Iterable[float]) -> float | None:
    items = tuple(values)
    return None if not items else float(sum(items) / len(items))


def _median(values: Iterable[float]) -> float | None:
    items = tuple(values)
    return None if not items else float(median(items))


class RiskPolicy(str, Enum):
    NO_RISK_POLICY = "NO_RISK_POLICY"
    VOLATILITY_SCALING = "VOLATILITY_SCALING"


class RiskPolicyState(str, Enum):
    APPLIED = "APPLIED"
    IDENTITY_NO_CHANGE = "IDENTITY_NO_CHANGE"
    INSUFFICIENT_RISK_EVIDENCE = "INSUFFICIENT_RISK_EVIDENCE"
    EMPTY_PORTFOLIO = "EMPTY_PORTFOLIO"
    POLICY_NOT_APPLICABLE = "POLICY_NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class RiskPolicyDefinition:
    policy: RiskPolicy
    target_annualized_volatility: float | None
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        target = self.target_annualized_volatility
        if self.policy is RiskPolicy.NO_RISK_POLICY and target is not None:
            raise ValueError("identity policy has no volatility target")
        if self.policy is RiskPolicy.VOLATILITY_SCALING and (target is None or not math.isfinite(target) or target <= 0.0):
            raise ValueError("volatility scaling requires a positive finite target")
        object.__setattr__(self, "fingerprint", _hash({
            "contract": (CONTRACT, VERSION), "policy": self.policy.value,
            "target_annualized_volatility": target,
            "multiplier": "min(1,target/positive_defined_annualized_volatility)",
            "transformation": "uniform_scale_frozen_risky_weights_residual_to_cash",
            "unavailable": "preserve_source_with_explicit_non_applied_state",
            "restrictions": ("long_only", "no_leverage", "no_new_security", "no_outcomes", "no_optimization", "no_ranking"),
        }))


@dataclass(frozen=True, slots=True)
class RiskPolicySpec:
    name: str = "NEUTRAL_PORTFOLIO_RISK_POLICY_V1"
    version: str = "1"
    policies: tuple[RiskPolicyDefinition, ...] = (
        RiskPolicyDefinition(RiskPolicy.NO_RISK_POLICY, None),
        RiskPolicyDefinition(RiskPolicy.VOLATILITY_SCALING, TARGET_ANNUALIZED_VOLATILITY),
    )
    deferred_policies: tuple[str, ...] = (RISK_CONTRIBUTION_POLICY_STATE,)
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if self.version != "1" or tuple(item.policy for item in self.policies) != tuple(RiskPolicy):
            raise ValueError("unsupported risk-policy specification")
        object.__setattr__(self, "fingerprint", _hash({
            "contract": (CONTRACT, VERSION), "name": self.name, "version": self.version,
            "policies": tuple(item.fingerprint for item in self.policies),
            "deferred": self.deferred_policies,
            "risk_recomputation": "same_Phase10A_estimator_uniform_scaling_algebra",
            "turnover": "half_L1_security_weight_change_plus_cash_change_from_source_portfolio",
            "blocks": BLOCKS, "sector": SECTOR_EVIDENCE_UNAVAILABLE,
        }))


NEUTRAL_PORTFOLIO_RISK_POLICY_V1 = RiskPolicySpec()


@dataclass(frozen=True, slots=True)
class RiskPolicyPosition:
    symbol: str
    original_weight: float
    transformed_weight: float
    source_position_identity: str
    identity: str


@dataclass(frozen=True, slots=True)
class RiskPolicyPortfolio:
    session_date: str
    requested_budget: int
    weighting_policy: str
    policy: RiskPolicy
    policy_fingerprint: str
    policy_state: RiskPolicyState
    transformation_reason: str
    source_portfolio_identity: str
    source_risk_observation_identity: str
    positions: tuple[RiskPolicyPosition, ...]
    original_gross_weight: float
    transformed_gross_weight: float
    transformed_cash_weight: float
    exposure_multiplier: float
    exposure_reduction: float
    transformation_turnover: float
    selected_count: int
    max_position_weight: float
    herfindahl_concentration: float
    effective_n: float | None
    annualized_volatility_before: float | None
    annualized_volatility_after: float | None
    daily_variance_before: float | None
    daily_variance_after: float | None
    mean_pairwise_correlation: float | None
    maximum_component_risk_share_before: float | None
    maximum_component_risk_share_after: float | None
    effective_risk_contributors_before: float | None
    effective_risk_contributors_after: float | None
    component_contribution_share_sum: float | None
    identity: str


@dataclass(frozen=True, slots=True)
class RiskPolicySummary:
    policy: RiskPolicy
    policy_fingerprint: str
    requested_budget: int
    scope_name: str
    scope_start_date: str
    scope_end_date: str
    observation_count: int
    applied_count: int
    identity_no_change_count: int
    unavailable_count: int
    empty_count: int
    application_rate_pct: float | None
    mean_original_gross_weight: float | None
    mean_transformed_gross_weight: float | None
    mean_exposure_reduction: float | None
    median_exposure_reduction: float | None
    mean_transformation_turnover: float | None
    mean_annualized_volatility_before: float | None
    mean_annualized_volatility_after: float | None
    mean_max_position_weight: float | None
    mean_effective_n: float | None
    mean_maximum_component_risk_share_after: float | None
    mean_effective_risk_contributors_after: float | None
    portfolio_identities_sha256: str
    identity: str


@dataclass(frozen=True, slots=True)
class RiskPolicyResult:
    contract_name: str
    contract_version: str
    specification_fingerprint: str
    source_identities: Mapping[str, str]
    policies: tuple[RiskPolicyDefinition, ...]
    portfolios: tuple[RiskPolicyPortfolio, ...]
    summaries: tuple[RiskPolicySummary, ...]
    deferred_policies: tuple[str, ...]
    limitations: tuple[str, ...]
    identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_identities", MappingProxyType(dict(self.source_identities)))


def _transform(
    portfolio: FrozenRiskPortfolio,
    risk: PortfolioRiskObservation,
    components: tuple[PortfolioRiskComponentContribution, ...],
    definition: RiskPolicyDefinition,
) -> RiskPolicyPortfolio:
    if not portfolio.positions:
        state, reason, multiplier = RiskPolicyState.EMPTY_PORTFOLIO, "source_portfolio_empty", 1.0
    elif definition.policy is RiskPolicy.NO_RISK_POLICY:
        state, reason, multiplier = RiskPolicyState.IDENTITY_NO_CHANGE, "identity_control", 1.0
    elif risk.annualized_portfolio_volatility is None or risk.daily_portfolio_variance is None:
        state, reason, multiplier = RiskPolicyState.INSUFFICIENT_RISK_EVIDENCE, risk.covariance_undefined_reason or "risk_evidence_unavailable", 1.0
    elif risk.annualized_portfolio_volatility <= 0.0:
        state, reason, multiplier = RiskPolicyState.POLICY_NOT_APPLICABLE, "estimated_volatility_not_positive", 1.0
    else:
        multiplier = min(1.0, float(definition.target_annualized_volatility) / risk.annualized_portfolio_volatility)
        state = RiskPolicyState.APPLIED if multiplier < 1.0 - 1e-15 else RiskPolicyState.IDENTITY_NO_CHANGE
        reason = "volatility_above_target_scaled_to_target" if state is RiskPolicyState.APPLIED else "volatility_at_or_below_target"
    positions = tuple(
        RiskPolicyPosition(
            item.symbol, item.weight, item.weight * multiplier, item.position_identity,
            _hash((item.symbol, item.weight, item.weight * multiplier, item.position_identity, definition.fingerprint)),
        ) for item in portfolio.positions
    )
    transformed_gross = sum(item.transformed_weight for item in positions)
    cash = 1.0 - transformed_gross
    if transformed_gross > portfolio.gross_weight + 1e-12 or transformed_gross > 1.0 + 1e-12 or abs(transformed_gross + cash - 1.0) > 1e-12:
        raise ValueError("policy transformation violates exposure reconciliation")
    turnover = 0.5 * (sum(abs(item.transformed_weight - item.original_weight) for item in positions) + abs(cash - portfolio.cash_weight))
    hhi = sum(item.transformed_weight ** 2 for item in positions)
    effective_n = None if hhi <= 0.0 else transformed_gross ** 2 / hhi
    defined = risk.daily_portfolio_variance is not None
    variance_after = None if not defined else risk.daily_portfolio_variance * multiplier * multiplier
    volatility_after = None if risk.annualized_portfolio_volatility is None else risk.annualized_portfolio_volatility * multiplier
    share_sum = None
    if components and defined and risk.daily_portfolio_variance and variance_after is not None and variance_after > 0.0:
        transformed_components = tuple(item.component_variance_contribution * multiplier * multiplier for item in components)
        if abs(sum(transformed_components) - variance_after) > 1e-10:
            raise ValueError("transformed component contributions do not reconcile")
        share_sum = sum(item.component_variance_contribution_share for item in components)
        if abs(share_sum - 1.0) > 1e-10:
            raise ValueError("transformed component contribution shares do not reconcile")
    payload = {
        "source_portfolio": portfolio.portfolio_identity, "source_risk": risk.identity,
        "policy": definition.fingerprint, "state": state.value, "reason": reason,
        "positions": tuple(item.identity for item in positions), "multiplier": multiplier,
        "gross": transformed_gross, "cash": cash, "turnover": turnover,
        "risk": (variance_after, volatility_after, risk.maximum_component_risk_share, risk.effective_risk_contributors, share_sum),
    }
    return RiskPolicyPortfolio(
        portfolio.session_date, portfolio.requested_budget, portfolio.weighting_policy,
        definition.policy, definition.fingerprint, state, reason, portfolio.portfolio_identity,
        risk.identity, positions, portfolio.gross_weight, transformed_gross, cash, multiplier,
        portfolio.gross_weight - transformed_gross, turnover, len(positions),
        max((item.transformed_weight for item in positions), default=0.0), hhi, effective_n,
        risk.annualized_portfolio_volatility, volatility_after, risk.daily_portfolio_variance,
        variance_after, risk.mean_pairwise_correlation, risk.maximum_component_risk_share,
        risk.maximum_component_risk_share, risk.effective_risk_contributors,
        risk.effective_risk_contributors, share_sum, _hash(payload),
    )


def _summary(items: tuple[RiskPolicyPortfolio, ...], definition: RiskPolicyDefinition, budget: int, scope: tuple[str, str, str]) -> RiskPolicySummary:
    name, start, end = scope
    included = tuple(item for item in items if item.policy is definition.policy and item.requested_budget == budget and start <= item.session_date <= end)
    def values(name: str) -> tuple[float, ...]:
        return tuple(float(value) for item in included if (value := getattr(item, name)) is not None)
    ids = tuple(item.identity for item in included)
    applied = sum(item.policy_state is RiskPolicyState.APPLIED for item in included)
    payload = {"policy": definition.fingerprint, "budget": budget, "scope": scope, "ids": ids,
               "counts": tuple((state.value, sum(item.policy_state is state for item in included)) for state in RiskPolicyState)}
    return RiskPolicySummary(
        definition.policy, definition.fingerprint, budget, name, start, end, len(included), applied,
        sum(item.policy_state is RiskPolicyState.IDENTITY_NO_CHANGE for item in included),
        sum(item.policy_state in {RiskPolicyState.INSUFFICIENT_RISK_EVIDENCE, RiskPolicyState.POLICY_NOT_APPLICABLE} for item in included),
        sum(item.policy_state is RiskPolicyState.EMPTY_PORTFOLIO for item in included),
        None if not included else applied / len(included) * 100.0,
        _mean(values("original_gross_weight")), _mean(values("transformed_gross_weight")),
        _mean(values("exposure_reduction")), _median(values("exposure_reduction")),
        _mean(values("transformation_turnover")), _mean(values("annualized_volatility_before")),
        _mean(values("annualized_volatility_after")), _mean(values("max_position_weight")),
        _mean(values("effective_n")), _mean(values("maximum_component_risk_share_after")),
        _mean(values("effective_risk_contributors_after")), _hash(ids), _hash(payload),
    )


def evaluate_portfolio_risk_policies(
    portfolios: Iterable[FrozenRiskPortfolio],
    risk_observations: Iterable[PortfolioRiskObservation],
    risk_components: Iterable[PortfolioRiskComponentContribution],
    *, source_identities: Mapping[str, str],
    spec: RiskPolicySpec = NEUTRAL_PORTFOLIO_RISK_POLICY_V1,
) -> RiskPolicyResult:
    frozen = tuple(sorted(portfolios, key=lambda item: (item.session_date, item.requested_budget)))
    risks = tuple(sorted(risk_observations, key=lambda item: (item.session_date, item.requested_budget)))
    risk_index = {(item.session_date, item.requested_budget): item for item in risks}
    if len(risk_index) != len(risks) or set(risk_index) != {(item.session_date, item.requested_budget) for item in frozen}:
        raise ValueError("Phase 6 portfolios and Phase 10A risk observations must have identical keys")
    component_index: dict[tuple[str, int], list[PortfolioRiskComponentContribution]] = {}
    for item in risk_components:
        component_index.setdefault((item.session_date, item.requested_budget), []).append(item)
    transformed: list[RiskPolicyPortfolio] = []
    for portfolio in frozen:
        key = (portfolio.session_date, portfolio.requested_budget); risk = risk_index[key]
        if risk.source_portfolio_identity != portfolio.portfolio_identity:
            raise ValueError("Phase 10A risk provenance does not match Phase 6 portfolio")
        components = tuple(sorted(component_index.get(key, ()), key=lambda item: item.symbol))
        for definition in spec.policies:
            transformed.append(_transform(portfolio, risk, components, definition))
    transformed_tuple = tuple(transformed)
    summaries = tuple(
        _summary(transformed_tuple, definition, budget, scope)
        for definition in spec.policies for budget in (5, 10, 20) for scope in (WHOLE_SCOPE, *BLOCKS)
    )
    sources = MappingProxyType(dict(source_identities))
    limitations = (
        "counterfactual structural risk research only; no realized outcomes or performance evidence",
        "20% annualized target is one predeclared neutral convention, not an optimized threshold",
        RISK_CONTRIBUTION_POLICY_STATE + ": deterministic enforcement would require arbitrary nonlinear reweighting semantics",
        "sector policy unavailable because canonical point-in-time sector evidence is unavailable",
        "no policy, budget, or transformed portfolio is ranked or selected",
    )
    payload = {"contract": (CONTRACT, VERSION), "spec": spec.fingerprint, "sources": dict(sources),
               "portfolios": tuple(item.identity for item in transformed_tuple),
               "summaries": tuple(item.identity for item in summaries), "limitations": limitations}
    return RiskPolicyResult(CONTRACT, VERSION, spec.fingerprint, sources, spec.policies, transformed_tuple, summaries, spec.deferred_policies, limitations, _hash(payload))
