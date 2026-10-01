from __future__ import annotations

import csv
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Mapping

from quantctl.registry import PROJECT_ROOT


class PortfolioEvidenceState(str, Enum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    INCOMPATIBLE = "INCOMPATIBLE"


@dataclass(frozen=True, slots=True)
class EvidenceDimension:
    name: str
    state: PortfolioEvidenceState
    detail: str


@dataclass(frozen=True, slots=True)
class StructureEvidence:
    mean_selected_count: float | None
    mean_fill_ratio: float | None
    max_single_name_weight: float | None
    effective_holdings: float | None
    herfindahl_concentration: float | None
    one_way_turnover: float | None
    risk_policy_transformation_turnover: float | None


@dataclass(frozen=True, slots=True)
class RiskEvidence:
    annualized_volatility: float | None
    aggregate_pairwise_correlation: float | None
    benchmark_beta: float | None
    maximum_component_risk_share: float | None
    effective_risk_contributors: float | None


@dataclass(frozen=True, slots=True)
class OutcomeEvidence:
    coverage_pct: float | None
    mean_gross_return_pct: float | None
    mean_gross_excess_return_pct_points: float | None
    positive_excess_rate: float | None
    identity: str | None


@dataclass(frozen=True, slots=True)
class RiskPolicyOutcomeProfile:
    risk_policy: str
    coverage_pct: float | None
    mean_gross_return_pct: float | None
    mean_gross_excess_return_pct_points: float | None
    positive_excess_rate: float | None


@dataclass(frozen=True, slots=True)
class CostSensitivityPoint:
    cost_rate_bps: int
    mean_gross_excess_return_pct_points: float | None
    mean_net_excess_return_pct_points: float | None
    mean_cost_deduction_pct_points: float | None
    label: str
    identity: str


@dataclass(frozen=True, slots=True)
class ExecutionEvidence:
    timing_coverage_pct: float | None
    capacity_coverage_pct: float | None
    mean_reference_gap_decimal: float | None
    mean_adverse_gap_decimal: float | None
    mean_normalized_participation_pct: float | None
    p95_normalized_participation_pct: float | None
    identity: str | None


@dataclass(frozen=True, slots=True)
class ExecutionCapability:
    area: str
    capability: str
    state: str
    evidence: str
    limitation: str


@dataclass(frozen=True, slots=True)
class RiskProfile:
    budget: int
    annualized_volatility: float | None
    aggregate_pairwise_correlation: float | None
    benchmark_beta: float | None
    maximum_component_risk_share: float | None
    effective_risk_contributors: float | None


@dataclass(frozen=True, slots=True)
class ArtifactProvenance:
    family: str
    state: PortfolioEvidenceState
    path: Path
    result_identity: str | None
    as_of: str | None
    limitations: tuple[str, ...]
    detail: str


@dataclass(frozen=True, slots=True)
class PortfolioRiskEvidence:
    state: PortfolioEvidenceState
    selection_policy: str
    budget: int
    horizon_sessions: int
    risk_policy: str
    structure: StructureEvidence | None
    risk: RiskEvidence | None
    outcome: OutcomeEvidence | None
    risk_policy_outcomes: tuple[RiskPolicyOutcomeProfile, ...]
    cost_sensitivity: tuple[CostSensitivityPoint, ...]
    execution: ExecutionEvidence | None
    execution_capabilities: tuple[ExecutionCapability, ...]
    risk_profiles: tuple[RiskProfile, ...]
    dimensions: tuple[EvidenceDimension, ...]
    provenance: tuple[ArtifactProvenance, ...]
    limitations: tuple[str, ...]
    detail: str


@dataclass(frozen=True, slots=True)
class _StructureRow:
    policy: str
    budget: int
    selected_count: float | None
    fill_ratio: float | None
    max_weight: float | None
    effective_n: float | None
    herfindahl: float | None
    turnover: float | None
    identity: str


@dataclass(frozen=True, slots=True)
class _RiskRow:
    budget: int
    volatility: float | None
    correlation: float | None
    beta: float | None
    max_weight: float | None
    effective_n: float | None
    max_risk_share: float | None
    effective_risk_contributors: float | None
    identity: str


@dataclass(frozen=True, slots=True)
class _PolicyRiskRow:
    policy: str
    budget: int
    transformation_turnover: float | None
    volatility: float | None
    max_weight: float | None
    effective_n: float | None
    max_risk_share: float | None
    effective_risk_contributors: float | None
    identity: str


@dataclass(frozen=True, slots=True)
class _OutcomeRow:
    policy: str
    budget: int
    horizon: int
    coverage: float | None
    gross_return: float | None
    excess_return: float | None
    positive_excess_rate: float | None
    identity: str


@dataclass(frozen=True, slots=True)
class _ExecutionRow:
    budget: int
    timing_coverage: float | None
    capacity_coverage: float | None
    gap: float | None
    adverse_gap: float | None
    participation: float | None
    p95_participation: float | None
    identity: str


@dataclass(frozen=True, slots=True)
class PortfolioRiskCatalog:
    selection_policies: tuple[str, ...]
    budgets: tuple[int, ...]
    horizons: tuple[int, ...]
    risk_policies: tuple[str, ...]
    structures: tuple[_StructureRow, ...]
    risks: tuple[_RiskRow, ...]
    policy_risks: tuple[_PolicyRiskRow, ...]
    outcomes: tuple[_OutcomeRow, ...]
    costs: tuple[tuple[str, int, int, CostSensitivityPoint], ...]
    executions: tuple[_ExecutionRow, ...]
    capabilities: tuple[ExecutionCapability, ...]
    provenance: tuple[ArtifactProvenance, ...]


_ROOTS = {
    "portfolio_structure": ("quantlab_portfolio_construction_2018-08-07_2026-09-17", "portfolio_construction_manifest.json"),
    "portfolio_outcome": ("quantlab_portfolio_outcome_evaluation_2018-08-07_2026-09-17", "portfolio_outcome_manifest.json"),
    "portfolio_risk": ("quantlab_portfolio_risk_2018-08-07_2026-09-17", "portfolio_risk_manifest.json"),
    "risk_policy": ("quantlab_portfolio_risk_policy_2018-08-07_2026-09-17", "portfolio_risk_policy_manifest.json"),
    "risk_policy_outcome": ("quantlab_portfolio_risk_policy_outcome_2018-08-07_2026-09-17", "portfolio_risk_policy_outcome_manifest.json"),
    "execution_foundation": ("quantlab_execution_foundation", "execution_contract_manifest.json"),
    "execution_timing_capacity": ("quantlab_execution_timing_capacity_2018-08-07_2026-09-17", "execution_timing_capacity_manifest.json"),
    "execution_friction": ("quantlab_execution_friction_sensitivity_2018-08-07_2026-09-17", "execution_friction_sensitivity_manifest.json"),
    "price_provenance": ("quantlab_execution_price_provenance_2018_2026", "execution_price_provenance_manifest.json"),
    "provider_provenance": ("quantlab_execution_provider_provenance_2018_2026", "execution_provider_provenance_manifest.json"),
}


def _read_csv_file(path: Path) -> tuple[dict[str, str], ...]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return tuple(dict(row) for row in csv.DictReader(handle))


@lru_cache(maxsize=64)
def _read_csv_cached(path_text: str, modified_ns: int, size: int) -> tuple[dict[str, str], ...]:
    del modified_ns, size
    return _read_csv_file(Path(path_text))


def _read_csv(path: Path, required: frozenset[str]) -> tuple[dict[str, str], ...]:
    stat = path.stat()
    rows = _read_csv_cached(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    if not rows:
        raise ValueError(f"empty persisted artifact: {path.name}")
    missing = required - frozenset(rows[0])
    if missing:
        raise ValueError(f"{path.name} is missing columns: {', '.join(sorted(missing))}")
    return rows


def _text(row: Mapping[str, Any], key: str) -> str:
    return str(row.get(key, "")).strip()


def _integer(row: Mapping[str, Any], key: str) -> int:
    value = _text(row, key)
    return int(float(value)) if value else 0


def _float(row: Mapping[str, Any], key: str) -> float | None:
    value = _text(row, key)
    if not value or value.lower() in {"nan", "none", "null", "<na>"}:
        return None
    return float(value)


def _manifest_provenance(root: Path, family: str) -> ArtifactProvenance:
    directory, filename = _ROOTS[family]
    path = root / "research_results" / directory / filename
    if not path.is_file():
        return ArtifactProvenance(
            family, PortfolioEvidenceState.UNAVAILABLE, path, None, None, (),
            "Required canonical manifest is unavailable.",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping) or payload.get("completed") is not True:
            raise ValueError("manifest is incomplete")
        identity = str(payload.get("result_identity", "")).strip()
        if not identity:
            raise ValueError("result identity is missing")
        period = payload.get("period")
        as_of = str(period.get("end_date", "")).strip() if isinstance(period, Mapping) else None
        limitations = payload.get("limitations")
        normalized = tuple(str(item).strip() for item in limitations if str(item).strip()) if isinstance(limitations, list) else ()
        return ArtifactProvenance(
            family, PortfolioEvidenceState.AVAILABLE, path, identity,
            as_of or "2026-09-17", normalized, "Canonical persisted artifact family.",
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return ArtifactProvenance(
            family, PortfolioEvidenceState.UNAVAILABLE, path, None, None, (),
            f"Canonical manifest is incompatible: {exc}",
        )


def _root(root: Path, family: str) -> Path:
    return root / "research_results" / _ROOTS[family][0]


def _rows_if_available(
    provenance: ArtifactProvenance,
    path: Path,
    required: frozenset[str],
) -> tuple[dict[str, str], ...]:
    if provenance.state is PortfolioEvidenceState.UNAVAILABLE or not path.is_file():
        return ()
    try:
        return _read_csv(path, required)
    except (OSError, UnicodeError, TypeError, ValueError):
        return ()


def inspect_portfolio_risk_catalog(*, root: Path = PROJECT_ROOT) -> PortfolioRiskCatalog:
    provenance = tuple(_manifest_provenance(root, family) for family in _ROOTS)
    by_family = {item.family: item for item in provenance}

    structure_rows = _rows_if_available(
        by_family["portfolio_structure"],
        _root(root, "portfolio_structure") / "portfolio_construction_summary.csv",
        frozenset({
            "candidate_source", "requested_budget", "scope_name", "mean_selected_count",
            "mean_fill_ratio", "mean_effective_n", "mean_max_single_name_weight",
            "mean_herfindahl", "mean_one_way_weight_turnover", "identity",
        }),
    )
    structures = tuple(
        _StructureRow(
            _text(row, "candidate_source"), _integer(row, "requested_budget"),
            _float(row, "mean_selected_count"), _float(row, "mean_fill_ratio"),
            _float(row, "mean_max_single_name_weight"), _float(row, "mean_effective_n"),
            _float(row, "mean_herfindahl"), _float(row, "mean_one_way_weight_turnover"),
            _text(row, "identity"),
        )
        for row in structure_rows if _text(row, "scope_name") == "whole_period"
    )

    risk_rows = _rows_if_available(
        by_family["portfolio_risk"],
        _root(root, "portfolio_risk") / "portfolio_risk_summary.csv",
        frozenset({
            "requested_budget", "scope_name", "mean_annualized_portfolio_volatility",
            "mean_pairwise_correlation", "mean_benchmark_beta", "mean_max_single_name_weight",
            "mean_effective_n", "mean_maximum_component_risk_share",
            "mean_effective_risk_contributors", "identity",
        }),
    )
    risks = tuple(
        _RiskRow(
            _integer(row, "requested_budget"), _float(row, "mean_annualized_portfolio_volatility"),
            _float(row, "mean_pairwise_correlation"), _float(row, "mean_benchmark_beta"),
            _float(row, "mean_max_single_name_weight"), _float(row, "mean_effective_n"),
            _float(row, "mean_maximum_component_risk_share"),
            _float(row, "mean_effective_risk_contributors"), _text(row, "identity"),
        )
        for row in risk_rows if _text(row, "scope_name") == "whole_period"
    )

    policy_rows = _rows_if_available(
        by_family["risk_policy"],
        _root(root, "risk_policy") / "portfolio_risk_policy_summary.csv",
        frozenset({
            "policy", "requested_budget", "scope_name", "mean_transformation_turnover",
            "mean_annualized_volatility_after", "mean_max_position_weight", "mean_effective_n",
            "mean_maximum_component_risk_share_after", "mean_effective_risk_contributors_after",
            "identity",
        }),
    )
    policy_risks = tuple(
        _PolicyRiskRow(
            _text(row, "policy"), _integer(row, "requested_budget"),
            _float(row, "mean_transformation_turnover"),
            _float(row, "mean_annualized_volatility_after"),
            _float(row, "mean_max_position_weight"), _float(row, "mean_effective_n"),
            _float(row, "mean_maximum_component_risk_share_after"),
            _float(row, "mean_effective_risk_contributors_after"), _text(row, "identity"),
        )
        for row in policy_rows if _text(row, "scope_name") == "whole_period"
    )

    outcome_rows = _rows_if_available(
        by_family["risk_policy_outcome"],
        _root(root, "risk_policy_outcome") / "portfolio_risk_policy_outcome_summary.csv",
        frozenset({
            "policy", "requested_budget", "horizon_sessions", "subset", "coverage_pct",
            "mean_portfolio_return_pct", "mean_excess_return_pct_points",
            "positive_excess_rate", "identity",
        }),
    )
    outcomes = tuple(
        _OutcomeRow(
            _text(row, "policy"), _integer(row, "requested_budget"),
            _integer(row, "horizon_sessions"), _float(row, "coverage_pct"),
            _float(row, "mean_portfolio_return_pct"),
            _float(row, "mean_excess_return_pct_points"),
            _float(row, "positive_excess_rate"), _text(row, "identity"),
        )
        for row in outcome_rows if _text(row, "subset") == "ALL"
    )

    costs: list[tuple[str, int, int, CostSensitivityPoint]] = []
    friction_rows = _rows_if_available(
        by_family["execution_friction"],
        _root(root, "execution_friction") / "execution_friction_sensitivity_summary.csv",
        frozenset({
            "scope_name", "requested_budget", "horizon_sessions",
            "hypothetical_all_in_cost_rate_bps", "mean_gross_excess_return_pct_points",
            "mean_net_excess_return_pct_points", "mean_cost_deduction_pct_points",
            "label", "identity",
        }),
    )
    costs.extend(
        (
            "NO_RISK_POLICY", _integer(row, "requested_budget"),
            _integer(row, "horizon_sessions"),
            CostSensitivityPoint(
                _integer(row, "hypothetical_all_in_cost_rate_bps"),
                _float(row, "mean_gross_excess_return_pct_points"),
                _float(row, "mean_net_excess_return_pct_points"),
                _float(row, "mean_cost_deduction_pct_points"),
                _text(row, "label"), _text(row, "identity"),
            ),
        )
        for row in friction_rows if _text(row, "scope_name") == "whole_period"
    )
    policy_cost_rows = _rows_if_available(
        by_family["risk_policy_outcome"],
        _root(root, "risk_policy_outcome") / "portfolio_risk_policy_cost_sensitivity.csv",
        frozenset({
            "policy", "requested_budget", "horizon_sessions", "cost_rate_bps",
            "mean_gross_excess_return_pct_points", "mean_net_excess_return_pct_points",
            "mean_hypothetical_cost_pct_points", "identity",
        }),
    )
    costs.extend(
        (
            _text(row, "policy"), _integer(row, "requested_budget"),
            _integer(row, "horizon_sessions"),
            CostSensitivityPoint(
                _integer(row, "cost_rate_bps"),
                _float(row, "mean_gross_excess_return_pct_points"),
                _float(row, "mean_net_excess_return_pct_points"),
                _float(row, "mean_hypothetical_cost_pct_points"),
                "HYPOTHETICAL_RISK_POLICY_TRANSFORMATION_COST", _text(row, "identity"),
            ),
        )
        for row in policy_cost_rows if _text(row, "policy") != "NO_RISK_POLICY"
    )

    execution_rows = _rows_if_available(
        by_family["execution_timing_capacity"],
        _root(root, "execution_timing_capacity") / "execution_timing_capacity_summary.csv",
        frozenset({
            "requested_budget", "scope_name", "observation_count", "timing_available_count",
            "capacity_available_count", "mean_gap_decimal", "mean_adverse_gap_decimal",
            "mean_participation_pct_per_normalized_equity_unit",
            "p95_participation_pct_per_normalized_equity_unit", "identity",
        }),
    )
    executions = tuple(
        _ExecutionRow(
            _integer(row, "requested_budget"),
            100.0 * _integer(row, "timing_available_count") / max(1, _integer(row, "observation_count")),
            100.0 * _integer(row, "capacity_available_count") / max(1, _integer(row, "observation_count")),
            _float(row, "mean_gap_decimal"), _float(row, "mean_adverse_gap_decimal"),
            _float(row, "mean_participation_pct_per_normalized_equity_unit"),
            _float(row, "p95_participation_pct_per_normalized_equity_unit"),
            _text(row, "identity"),
        )
        for row in execution_rows if _text(row, "scope_name") == "whole_period"
    )

    capabilities: list[ExecutionCapability] = []
    capability_counts: dict[str, int] = {}
    capability_sources = (
        ("execution_foundation", "execution_capability_matrix.csv", "area", "capability", "state", "evidence", "limitation"),
        ("price_provenance", "execution_price_provenance_capability.csv", None, "operation", "state", "reason", "reason"),
        ("provider_provenance", "execution_provider_provenance_capability.csv", None, "capability", "state", "basis", "basis"),
    )
    for family, filename, area_key, capability_key, state_key, evidence_key, limitation_key in capability_sources:
        path = _root(root, family) / filename
        rows = _rows_if_available(
            by_family[family], path,
            frozenset({capability_key, state_key, evidence_key, limitation_key})
            | (frozenset({area_key}) if area_key else frozenset()),
        )
        capability_counts[family] = len(rows)
        capabilities.extend(
            ExecutionCapability(
                _text(row, area_key) if area_key else family,
                _text(row, capability_key), _text(row, state_key),
                _text(row, evidence_key), _text(row, limitation_key),
            )
            for row in rows
        )

    required_presence = {
        "portfolio_structure": bool(structures),
        "portfolio_risk": bool(risks),
        "risk_policy": bool(policy_risks),
        "risk_policy_outcome": bool(outcomes),
        "execution_friction": bool(friction_rows),
        "execution_timing_capacity": bool(executions),
        "execution_foundation": capability_counts.get("execution_foundation", 0) > 0,
        "price_provenance": capability_counts.get("price_provenance", 0) > 0,
        "provider_provenance": capability_counts.get("provider_provenance", 0) > 0,
    }
    provenance = tuple(
        item
        if item.state is PortfolioEvidenceState.UNAVAILABLE
        or required_presence.get(item.family, True)
        else ArtifactProvenance(
            item.family,
            PortfolioEvidenceState.UNAVAILABLE,
            item.path,
            item.result_identity,
            item.as_of,
            item.limitations,
            "Required compact summary is missing or schema-incompatible.",
        )
        for item in provenance
    )

    selection_policies = tuple(sorted({item.policy for item in structures}))
    budgets = tuple(sorted({item.budget for item in structures}))
    horizons = tuple(sorted({item.horizon for item in outcomes}))
    risk_policies = tuple(sorted({item.policy for item in policy_risks}, key=lambda item: (item != "NO_RISK_POLICY", item)))
    return PortfolioRiskCatalog(
        selection_policies, budgets, horizons, risk_policies,
        tuple(sorted(structures, key=lambda item: (item.policy, item.budget))),
        tuple(sorted(risks, key=lambda item: item.budget)),
        tuple(sorted(policy_risks, key=lambda item: (item.policy, item.budget))),
        tuple(sorted(outcomes, key=lambda item: (item.policy, item.budget, item.horizon))),
        tuple(sorted(costs, key=lambda item: (item[0], item[1], item[2], item[3].cost_rate_bps))),
        tuple(sorted(executions, key=lambda item: item.budget)),
        tuple(sorted(capabilities, key=lambda item: (item.area, item.capability))),
        provenance,
    )


def _incompatible(
    catalog: PortfolioRiskCatalog,
    policy: str,
    budget: int,
    horizon: int,
    risk_policy: str,
    detail: str,
) -> PortfolioRiskEvidence:
    return PortfolioRiskEvidence(
        PortfolioEvidenceState.INCOMPATIBLE, policy, budget, horizon, risk_policy,
        None, None, None, (), (), None, (), (), (), catalog.provenance, (), detail,
    )


def select_portfolio_risk_evidence(
    catalog: PortfolioRiskCatalog,
    *,
    selection_policy: str,
    budget: int,
    horizon_sessions: int,
    risk_policy: str,
) -> PortfolioRiskEvidence:
    if selection_policy not in catalog.selection_policies:
        return _incompatible(catalog, selection_policy, budget, horizon_sessions, risk_policy, "Selection policy is unsupported by canonical Phase 6 portfolios.")
    if budget not in catalog.budgets:
        return _incompatible(catalog, selection_policy, budget, horizon_sessions, risk_policy, "Budget is unsupported by canonical portfolio evidence.")
    if horizon_sessions not in catalog.horizons:
        return _incompatible(catalog, selection_policy, budget, horizon_sessions, risk_policy, "Horizon is unsupported by canonical outcome evidence.")
    if risk_policy not in catalog.risk_policies:
        return _incompatible(catalog, selection_policy, budget, horizon_sessions, risk_policy, "Risk-policy variant is unsupported by persisted evidence.")

    source_structure = next((item for item in catalog.structures if (item.policy, item.budget) == (selection_policy, budget)), None)
    source_risk = next((item for item in catalog.risks if item.budget == budget), None)
    policy_risk = next((item for item in catalog.policy_risks if (item.policy, item.budget) == (risk_policy, budget)), None)
    source_outcome = next((item for item in catalog.outcomes if (item.policy, item.budget, item.horizon) == (risk_policy, budget, horizon_sessions)), None)
    source_execution = next((item for item in catalog.executions if item.budget == budget), None)
    selected_costs = tuple(
        item for policy, row_budget, horizon, item in catalog.costs
        if (policy, row_budget, horizon) == (risk_policy, budget, horizon_sessions)
    )
    if source_structure is None:
        return PortfolioRiskEvidence(
            PortfolioEvidenceState.UNAVAILABLE, selection_policy, budget, horizon_sessions,
            risk_policy, None, None, None, (), (), None, (), (), (), catalog.provenance, (),
            "Required canonical portfolio-structure evidence is unavailable or incompatible.",
        )

    structure = StructureEvidence(
        source_structure.selected_count, source_structure.fill_ratio,
        policy_risk.max_weight if policy_risk is not None else source_structure.max_weight,
        policy_risk.effective_n if policy_risk is not None else source_structure.effective_n,
        source_structure.herfindahl if risk_policy == "NO_RISK_POLICY" else None,
        source_structure.turnover,
        policy_risk.transformation_turnover if policy_risk is not None else None,
    )
    if risk_policy == "NO_RISK_POLICY" and source_risk is not None:
        risk = RiskEvidence(
            source_risk.volatility, source_risk.correlation, source_risk.beta,
            source_risk.max_risk_share, source_risk.effective_risk_contributors,
        )
    elif policy_risk is not None:
        risk = RiskEvidence(
            policy_risk.volatility, None, None, policy_risk.max_risk_share,
            policy_risk.effective_risk_contributors,
        )
    else:
        risk = None
    outcome = None if source_outcome is None else OutcomeEvidence(
        source_outcome.coverage, source_outcome.gross_return, source_outcome.excess_return,
        source_outcome.positive_excess_rate, source_outcome.identity,
    )
    outcome_profiles = tuple(
        RiskPolicyOutcomeProfile(
            item.policy, item.coverage, item.gross_return, item.excess_return,
            item.positive_excess_rate,
        )
        for item in catalog.outcomes
        if (item.budget, item.horizon) == (budget, horizon_sessions)
    )
    execution = None if source_execution is None else ExecutionEvidence(
        source_execution.timing_coverage, source_execution.capacity_coverage,
        source_execution.gap, source_execution.adverse_gap, source_execution.participation,
        source_execution.p95_participation, source_execution.identity,
    )
    risk_profiles: list[RiskProfile] = []
    for row_budget in catalog.budgets:
        raw = next((item for item in catalog.risks if item.budget == row_budget), None)
        transformed = next((item for item in catalog.policy_risks if (item.policy, item.budget) == (risk_policy, row_budget)), None)
        if risk_policy == "NO_RISK_POLICY" and raw is not None:
            risk_profiles.append(RiskProfile(row_budget, raw.volatility, raw.correlation, raw.beta, raw.max_risk_share, raw.effective_risk_contributors))
        elif transformed is not None:
            risk_profiles.append(RiskProfile(row_budget, transformed.volatility, None, None, transformed.max_risk_share, transformed.effective_risk_contributors))

    dimensions = (
        EvidenceDimension("Concentration", PortfolioEvidenceState.AVAILABLE, "Persisted equal-weight portfolio structure and concentration summaries."),
        EvidenceDimension("Aggregate correlation", PortfolioEvidenceState.AVAILABLE if risk and risk.aggregate_pairwise_correlation is not None else PortfolioEvidenceState.UNAVAILABLE, "Aggregate average pairwise correlation only; no pairwise matrix artifact."),
        EvidenceDimension("Benchmark beta", PortfolioEvidenceState.AVAILABLE if risk and risk.benchmark_beta is not None else PortfolioEvidenceState.UNAVAILABLE, "Historical descriptive beta; not a production limit."),
        EvidenceDimension("Hypothetical cost sensitivity", PortfolioEvidenceState.AVAILABLE if selected_costs else PortfolioEvidenceState.UNAVAILABLE, "Persisted hypothetical arithmetic; not realized execution cost."),
        EvidenceDimension("Timing reference evidence", PortfolioEvidenceState.PARTIAL if execution else PortfolioEvidenceState.UNAVAILABLE, "Next-session open is a reference, not a fill price."),
        EvidenceDimension("Normalized volume participation", PortfolioEvidenceState.PARTIAL if execution else PortfolioEvidenceState.UNAVAILABLE, "Scale-linear participation per normalized equity unit; not monetary capacity."),
        EvidenceDimension("Sector concentration", PortfolioEvidenceState.UNAVAILABLE, "No canonical point-in-time sector identity exists."),
        EvidenceDimension("Top-N concentration", PortfolioEvidenceState.UNAVAILABLE, "No canonical top-N concentration summary is persisted."),
        EvidenceDimension("Pairwise correlation matrix", PortfolioEvidenceState.UNAVAILABLE, "Only aggregate correlation summaries are persisted."),
        EvidenceDimension("Actual fill probability / market impact", PortfolioEvidenceState.UNAVAILABLE, "Daily OHLCV cannot establish fills, queue priority, or impact."),
    )
    relevant_provenance = catalog.provenance
    limitations = tuple(
        dict.fromkeys(
            limit
            for item in relevant_provenance
            if item.state is PortfolioEvidenceState.AVAILABLE
            for limit in item.limitations
        )
    )
    core_missing = risk is None or outcome is None or execution is None
    state = PortfolioEvidenceState.PARTIAL if core_missing or any(item.state is not PortfolioEvidenceState.AVAILABLE for item in dimensions) else PortfolioEvidenceState.AVAILABLE
    return PortfolioRiskEvidence(
        state, selection_policy, budget, horizon_sessions, risk_policy, structure, risk,
        outcome, outcome_profiles, selected_costs, execution, catalog.capabilities, tuple(risk_profiles),
        dimensions, relevant_provenance, limitations,
        "Persisted descriptive portfolio, risk, outcome, cost, and execution evidence only.",
    )
