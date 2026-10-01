from __future__ import annotations

"""Read-only empirical aggregation over existing portfolio diagnostics.

This module deliberately accepts already-materialized historical observations.
It does not load databases, rerun strategies, or turn descriptive evidence into
production policy.
"""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import math
from statistics import mean, median
from types import MappingProxyType
from typing import Any, Iterable, Mapping
import warnings

import pandas as pd

from quantlab.diagnostics.portfolio_execution import (
    PortfolioExecutionDiagnosticsResult,
    evaluate_portfolio_execution_diagnostics,
)
from quantlab.identity import canonical_identity_value, canonical_json


CONTRACT = "quantlab.portfolio_risk_evidence"
VERSION = "v1"


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


class EvidenceSourceStatus(str, Enum):
    EVALUABLE = "EVALUABLE"
    PARTIALLY_EVALUABLE = "PARTIALLY_EVALUABLE"
    NOT_EVALUABLE = "NOT_EVALUABLE"


class EvidenceStrength(str, Enum):
    STRONG = "STRONG"
    MODERATE = "MODERATE"
    WEAK = "WEAK"
    INSUFFICIENT = "INSUFFICIENT"


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _strength(total: int, defined: int) -> EvidenceStrength:
    if total <= 0 or defined <= 0:
        return EvidenceStrength.INSUFFICIENT
    coverage = defined / total
    if total >= 30 and coverage >= 0.80:
        return EvidenceStrength.STRONG
    if total >= 10 and coverage >= 0.60:
        return EvidenceStrength.MODERATE
    return EvidenceStrength.WEAK


@dataclass(frozen=True, slots=True)
class EvidenceMetricSummary:
    metric: str
    observation_count: int
    defined_count: int
    coverage_pct: float
    mean: float | None
    median: float | None
    p90: float | None
    maximum: float | None
    evidence_strength: EvidenceStrength
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "identity", _hash({
            "contract": [CONTRACT, VERSION, "metric"],
            "metric": self.metric,
            "values": {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "identity"},
        }))


@dataclass(frozen=True, slots=True)
class EvidenceCostSummary:
    cost_bps: float
    observation_count: int
    trade_count: int
    mean_gross_pnl: float | None
    mean_net_pnl: float | None
    mean_estimated_cost: float | None
    mean_turnover: float | None
    mean_gross_return_pct: float | None
    mean_net_return_pct: float | None
    mean_break_even_cost_bps: float | None
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "identity", _hash({
            "contract": [CONTRACT, VERSION, "cost"],
            "values": {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "identity"},
        }))


@dataclass(frozen=True, slots=True)
class DrawdownAssociation:
    total_observations: int
    worst_group_count: int
    normal_group_count: int
    worst_group_cutoff: float | None
    worst_mean_concentration: float | None
    normal_mean_concentration: float | None
    worst_mean_correlation: float | None
    normal_mean_correlation: float | None
    worst_mean_beta: float | None
    normal_mean_beta: float | None
    worst_mean_gross_exposure: float | None
    normal_mean_gross_exposure: float | None
    interpretation: str
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "identity", _hash({
            "contract": [CONTRACT, VERSION, "drawdown"],
            "values": {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "identity"},
        }))


@dataclass(frozen=True, slots=True)
class RegimeEvidenceSummary:
    regime: str
    observation_count: int
    mean_concentration: float | None
    mean_correlation: float | None
    mean_beta: float | None
    mean_gross_exposure: float | None
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "identity", _hash({
            "contract": [CONTRACT, VERSION, "regime"],
            "values": {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "identity"},
        }))


@dataclass(frozen=True, slots=True)
class PolicyImplication:
    risk_dimension: str
    empirical_evidence: str
    severity_observed: str
    evidence_strength: EvidenceStrength
    production_control_justified: str
    next_step: str
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        allowed = {
            "YES — EVIDENCE SUPPORTS DESIGN PHASE",
            "NOT YET — MORE EVIDENCE NEEDED",
            "NO MATERIAL ISSUE OBSERVED",
            "NOT EVALUABLE",
        }
        if self.production_control_justified not in allowed:
            raise ValueError("unsupported production-control implication")
        object.__setattr__(self, "identity", _hash({
            "contract": [CONTRACT, VERSION, "policy"],
            "values": {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "identity"},
        }))


@dataclass(frozen=True, slots=True)
class PortfolioRiskEvidenceSourceResult:
    source_name: str
    source_status: EvidenceSourceStatus
    diagnostic_configuration: Mapping[str, Any]
    observation_count: int
    evaluable_observation_count: int
    concentration_summaries: Mapping[str, EvidenceMetricSummary]
    correlation_summaries: Mapping[str, EvidenceMetricSummary]
    correlation_threshold_summaries: Mapping[str, EvidenceMetricSummary]
    beta_summaries: Mapping[str, EvidenceMetricSummary]
    sector_summaries: Mapping[str, EvidenceMetricSummary]
    drawdown_association: DrawdownAssociation
    cost_sensitivity: tuple[EvidenceCostSummary, ...]
    regime_segments: tuple[RegimeEvidenceSummary, ...]
    provenance: Mapping[str, Any]
    warnings: tuple[str, ...]
    limitations: tuple[str, ...]
    policy_implications: tuple[PolicyImplication, ...]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic_configuration", _freeze(self.diagnostic_configuration))
        for name in ("concentration_summaries", "correlation_summaries", "correlation_threshold_summaries", "beta_summaries", "sector_summaries"):
            object.__setattr__(self, name, MappingProxyType(dict(sorted(getattr(self, name).items()))))
        object.__setattr__(self, "cost_sensitivity", tuple(self.cost_sensitivity))
        object.__setattr__(self, "regime_segments", tuple(self.regime_segments))
        object.__setattr__(self, "provenance", _freeze(self.provenance))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "limitations", tuple(self.limitations))
        object.__setattr__(self, "policy_implications", tuple(self.policy_implications))
        object.__setattr__(self, "identity", _hash({
            "contract": [CONTRACT, VERSION],
            "source": self.source_name,
            "status": self.source_status,
            "diagnostic_configuration": self.diagnostic_configuration,
            "observations": [self.observation_count, self.evaluable_observation_count],
            "concentration": {key: value.identity for key, value in self.concentration_summaries.items()},
            "correlation": {key: value.identity for key, value in self.correlation_summaries.items()},
            "correlation_thresholds": {key: value.identity for key, value in self.correlation_threshold_summaries.items()},
            "beta": {key: value.identity for key, value in self.beta_summaries.items()},
            "sector": {key: value.identity for key, value in self.sector_summaries.items()},
            "drawdown": self.drawdown_association.identity,
            "costs": tuple(item.identity for item in self.cost_sensitivity),
            "regimes": tuple(item.identity for item in self.regime_segments),
            "provenance": self.provenance,
            "warnings": self.warnings,
            "limitations": self.limitations,
            "policies": tuple(item.identity for item in self.policy_implications),
        }))


@dataclass(frozen=True, slots=True)
class PortfolioRiskEvidenceResult:
    sources: tuple[PortfolioRiskEvidenceSourceResult, ...]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        sources = tuple(sorted(self.sources, key=lambda item: item.source_name))
        if len({item.source_name for item in sources}) != len(sources):
            raise ValueError("evidence source names must be unique")
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "identity", _hash({
            "contract": [CONTRACT, VERSION],
            "sources": tuple(item.identity for item in sources),
        }))


def _summary(metric: str, values: Iterable[float | None], total: int) -> EvidenceMetricSummary:
    defined = tuple(float(item) for item in values if item is not None and math.isfinite(float(item)))
    ordered = sorted(defined)
    p90 = None
    if ordered:
        position = (len(ordered) - 1) * 0.90
        lower, upper = math.floor(position), math.ceil(position)
        p90 = ordered[lower] if lower == upper else ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return EvidenceMetricSummary(
        metric, total, len(defined), len(defined) / total * 100.0 if total else 0.0,
        mean(defined) if defined else None, median(defined) if defined else None,
        p90, max(defined) if defined else None, _strength(total, len(defined)),
    )


def _value(diagnostic: PortfolioExecutionDiagnosticsResult, path: str) -> float | None:
    current: Any = diagnostic
    for component in path.split("."):
        current = getattr(current, component)
    try:
        return float(current) if current is not None and math.isfinite(float(current)) else None
    except (TypeError, ValueError):
        return None


def _portfolio_value(diagnostic: PortfolioExecutionDiagnosticsResult, path: str) -> float | None:
    """Do not relabel an empty trade-only source as a zero-risk portfolio."""
    if diagnostic.concentration.position_count <= 0:
        return None
    return _value(diagnostic, path)


def _liquidity_value(diagnostic: PortfolioExecutionDiagnosticsResult) -> float | None:
    """Capacity coverage is undefined until the source verifies price/value units."""
    if diagnostic.concentration.position_count <= 0 or not diagnostic.liquidity.units_known:
        return None
    return _value(diagnostic, "liquidity.coverage_pct")


def _mean_defined(values: Iterable[float | None]) -> float | None:
    defined = tuple(value for value in values if value is not None and math.isfinite(float(value)))
    return mean(defined) if defined else None


def _finite_or_none(value: Any) -> float | None:
    """Return a finite float without turning missing evidence into zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _drawdown_association(items: tuple[tuple[float, PortfolioExecutionDiagnosticsResult], ...]) -> DrawdownAssociation:
    if not items:
        return DrawdownAssociation(0, 0, 0, None, None, None, None, None, None, None, None, None, "no drawdown observations")
    ordered = sorted(items, key=lambda item: item[0])
    worst_count = max(1, math.ceil(len(ordered) * 0.10))
    worst = ordered[:worst_count]
    normal = ordered[worst_count:]
    def avg(group: list[tuple[float, PortfolioExecutionDiagnosticsResult]], path: str) -> float | None:
        return _mean_defined(_portfolio_value(item[1], path) for item in group)
    return DrawdownAssociation(
        len(ordered), len(worst), len(normal), worst[-1][0],
        avg(worst, "concentration.herfindahl_concentration"), avg(normal, "concentration.herfindahl_concentration"),
        avg(worst, "correlation.average_pairwise_correlation"), avg(normal, "correlation.average_pairwise_correlation"),
        avg(worst, "beta.weighted_portfolio_beta"), avg(normal, "beta.weighted_portfolio_beta"),
        avg(worst, "concentration.gross_exposure"), avg(normal, "concentration.gross_exposure"),
        "descriptive worst-drawdown-decile comparison; association is not causal",
    )


def _policy(dimension: str, summary: EvidenceMetricSummary, *, next_step: str) -> PolicyImplication:
    if summary.evidence_strength is EvidenceStrength.INSUFFICIENT:
        decision = "NOT EVALUABLE"
    else:
        decision = "NOT YET — MORE EVIDENCE NEEDED"
    evidence = f"{summary.defined_count}/{summary.observation_count} observations defined; mean={summary.mean!r}, p90={summary.p90!r}, max={summary.maximum!r}"
    severity = "DESCRIPTIVE PRESENCE" if summary.defined_count else "UNOBSERVED"
    return PolicyImplication(dimension, evidence, severity, summary.evidence_strength, decision, next_step)


def _pair_threshold_share(diagnostic: PortfolioExecutionDiagnosticsResult, threshold: float) -> float | None:
    pairs = tuple(diagnostic.correlation.pairwise_correlations.values())
    if not pairs:
        return None
    return sum(value >= threshold for value in pairs) / len(pairs) * 100.0


def evaluate_portfolio_risk_evidence(
    *,
    source_name: str,
    observations: Iterable[Mapping[str, Any]],
    source_status: EvidenceSourceStatus = EvidenceSourceStatus.EVALUABLE,
    lookback_sessions: int = 60,
    minimum_observations: int = 20,
    correlation_threshold: float = 0.80,
    top_n: int = 3,
    cost_grid_bps: Iterable[float] = (0.0, 10.0, 25.0, 50.0, 100.0),
) -> PortfolioRiskEvidenceSourceResult:
    if not str(source_name).strip():
        raise ValueError("source_name is required")
    if lookback_sessions < 2 or minimum_observations < 2 or minimum_observations > lookback_sessions:
        raise ValueError("invalid lookback/minimum observation configuration")
    if not math.isfinite(float(correlation_threshold)) or not -1.0 <= float(correlation_threshold) <= 1.0:
        raise ValueError("correlation_threshold must be between -1 and 1")
    if top_n < 1:
        raise ValueError("top_n must be positive")
    source_status = EvidenceSourceStatus(source_status)
    normalized_cost_grid = tuple(sorted({float(value) for value in cost_grid_bps}))
    if any(not math.isfinite(value) or value < 0.0 for value in normalized_cost_grid):
        raise ValueError("cost_grid_bps must be finite and nonnegative")
    raw = tuple(observations)
    diagnostics: list[tuple[str, PortfolioExecutionDiagnosticsResult, Mapping[str, Any]]] = []
    for item in raw:
        date = pd.Timestamp(item["observation_date"]).date().isoformat()
        # The underlying diagnostics deliberately preserve zero-variance pairs
        # as undefined.  NumPy emits a runtime warning while producing that
        # expected NaN; contain only that warning without altering its result.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=RuntimeWarning, message="invalid value encountered in divide")
            diagnostic = evaluate_portfolio_execution_diagnostics(
                weights=item["weights"], daily_returns=item["daily_returns"], benchmark_returns=item.get("benchmark_returns"),
                trades=item.get("trades", ()), sector_by_symbol=item.get("sector_by_symbol"),
                daily_traded_value=item.get("daily_traded_value"), price_units_known=bool(item.get("price_units_known", False)),
                volume_units_known=bool(item.get("volume_units_known", False)), lookback_sessions=lookback_sessions,
                minimum_observations=minimum_observations, correlation_threshold=correlation_threshold,
                top_n=top_n, cost_grid_bps=normalized_cost_grid, initial_equity=item.get("initial_equity"), provenance=item.get("provenance"),
            )
        diagnostics.append((date, diagnostic, item))
    # Date is the primary observation key; diagnostic identity makes same-date
    # records deterministic without retaining or depending on input order.
    diagnostics.sort(key=lambda item: (item[0], item[1].identity))
    total = len(diagnostics)
    concentration = {name: _summary(name, (_portfolio_value(item[1], path) for item in diagnostics), total) for name, path in (
        ("largest_position_weight", "concentration.largest_position_weight"),
        ("top_n_concentration", "concentration.top_n_concentration"),
        ("herfindahl_concentration", "concentration.herfindahl_concentration"),
        ("gross_exposure", "concentration.gross_exposure"),
        ("cash_share", "concentration.cash_share"),
        ("unknown_sector_share_of_gross", "concentration.unknown_sector_share_of_gross"),
        ("effective_sector_count", "sector_crowding.effective_sector_count"),
    )}
    correlations = {name: _summary(name, (_portfolio_value(item[1], path) for item in diagnostics), total) for name, path in (
        ("average_pairwise_correlation", "correlation.average_pairwise_correlation"),
        ("maximum_pairwise_correlation", "correlation.maximum_pairwise_correlation"),
        ("pair_coverage_pct", "correlation.pair_coverage_pct"),
        ("high_correlation_pair_share", "sector_crowding.high_correlation_pair_share"),
    )}
    correlation_threshold_summaries = {
        f"pairs_at_or_above_{threshold:.2f}": _summary(
            f"pairs_at_or_above_{threshold:.2f}",
            (_pair_threshold_share(item[1], threshold) for item in diagnostics),
            total,
        )
        for threshold in (0.50, 0.70, 0.85)
    }
    betas = {name: _summary(name, (_portfolio_value(item[1], path) for item in diagnostics), total) for name, path in (
        ("weighted_portfolio_beta", "beta.weighted_portfolio_beta"),
        ("weighted_portfolio_correlation", "beta.weighted_portfolio_correlation"),
    )}
    position_betas = tuple(
        float(beta)
        for _, diagnostic, _ in diagnostics
        for beta in diagnostic.beta.per_symbol_beta.values()
        if _finite_or_none(beta) is not None
    )
    betas["per_position_beta"] = _summary("per_position_beta", position_betas, len(position_betas))
    sectors = {name: _summary(name, (_portfolio_value(item[1], path) for item in diagnostics), total) for name, path in (
        ("largest_sector_weight", "sector_crowding.largest_sector_weight"),
        ("high_correlation_pair_share", "sector_crowding.high_correlation_pair_share"),
    )}
    drawdowns = tuple(
        (drawdown, item[1])
        for item in diagnostics
        for drawdown in (_finite_or_none(item[2].get("drawdown")),)
        if drawdown is not None
    )
    cost_rows: list[EvidenceCostSummary] = []
    if diagnostics:
        for cost_bps in normalized_cost_grid:
            points = [
                next(point for point in item[1].cost_sensitivity if point.cost_bps == cost_bps)
                for item in diagnostics
            ]
            cost_rows.append(EvidenceCostSummary(
                cost_bps, len(points), sum(item.trade_count for item in points),
                _mean_defined(item.gross_pnl for item in points), _mean_defined(item.net_pnl for item in points),
                _mean_defined(item.estimated_cost for item in points), _mean_defined(item.turnover for item in points),
                _mean_defined(item.gross_return_pct for item in points), _mean_defined(item.net_return_pct for item in points),
                _mean_defined(item[1].break_even_cost_bps for item in diagnostics),
            ))
    regime_values: dict[str, list[PortfolioExecutionDiagnosticsResult]] = {}
    for _, diagnostic, item in diagnostics:
        regime = str(item.get("regime", "UNKNOWN"))
        regime_values.setdefault(regime, []).append(diagnostic)
    regimes = tuple(RegimeEvidenceSummary(
        regime, len(items), _mean_defined(_portfolio_value(item, "concentration.herfindahl_concentration") for item in items),
        _mean_defined(_portfolio_value(item, "correlation.average_pairwise_correlation") for item in items),
        _mean_defined(_portfolio_value(item, "beta.weighted_portfolio_beta") for item in items),
        _mean_defined(_portfolio_value(item, "concentration.gross_exposure") for item in items),
    ) for regime, items in sorted(regime_values.items()))
    first_provenance = diagnostics[0][1].provenance if diagnostics else {"classification": "LEGACY_UNKNOWN"}
    source_warnings = tuple(sorted({warning for _, diagnostic, _ in diagnostics for warning in diagnostic.warnings}))
    limitations = (
        "descriptive evidence only; no production control, ranking, sizing, or rejection is applied",
        "correlation and beta use complete pairwise observations without zero filling",
        "sector and liquidity conclusions are limited by supplied local mappings and unit provenance",
        "drawdown comparisons are associations and do not establish causality",
    )
    implications = (
        _policy("concentration", concentration["herfindahl_concentration"], next_step="retain distribution and review across independent histories"),
        _policy("correlation", correlations["average_pairwise_correlation"], next_step="increase independent portfolio observations before policy design"),
        _policy("beta", betas["weighted_portfolio_beta"], next_step="validate benchmark coverage and regime samples"),
        _policy("sector_crowding", sectors["largest_sector_weight"], next_step="obtain point-in-time sector mapping if sector controls are considered"),
        _policy("cost_sensitivity", _summary("cost_sensitivity", (item.mean_estimated_cost for item in cost_rows), len(cost_rows)), next_step="retain source-specific cost sensitivity"),
        _policy("liquidity", _summary("liquidity", (_liquidity_value(item[1]) for item in diagnostics), total), next_step="obtain defensible price/value units before capacity conclusions"),
    )
    return PortfolioRiskEvidenceSourceResult(
        str(source_name).strip(), source_status,
        {"lookback_sessions": lookback_sessions, "minimum_observations": minimum_observations,
         "correlation_threshold": correlation_threshold, "top_n": top_n, "cost_grid_bps": normalized_cost_grid},
        total, sum(item[1].concentration.position_count > 0 for item in diagnostics),
        concentration, correlations, correlation_threshold_summaries, betas, sectors, _drawdown_association(drawdowns), tuple(cost_rows), regimes,
        first_provenance, source_warnings, limitations, implications,
    )


def evaluate_portfolio_risk_evidence_sources(
    sources: Iterable[Mapping[str, Any]],
    **kwargs: Any,
) -> PortfolioRiskEvidenceResult:
    results = tuple(evaluate_portfolio_risk_evidence(**source, **kwargs) for source in sources)
    return PortfolioRiskEvidenceResult(results)


__all__ = [
    "CONTRACT", "VERSION", "EvidenceSourceStatus", "EvidenceStrength", "EvidenceMetricSummary",
    "EvidenceCostSummary", "DrawdownAssociation", "RegimeEvidenceSummary", "PolicyImplication",
    "PortfolioRiskEvidenceSourceResult", "PortfolioRiskEvidenceResult",
    "evaluate_portfolio_risk_evidence", "evaluate_portfolio_risk_evidence_sources",
]
