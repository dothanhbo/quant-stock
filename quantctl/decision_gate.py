from __future__ import annotations

"""Immutable read-only projection of canonical Quant Lab decision artifacts."""

import csv
from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any, Mapping

from quantctl.historical_qualification import qualify_historical_limitations
from quantctl.registry import PROJECT_ROOT
from quantctl.research_status import (
    ProductionPolicySnapshot,
    ProductionReadinessStatus,
    ResearchDecisionStatus,
    inspect_production_policy,
    inspect_research_frontier,
)


class DecisionCandidateType(str, Enum):
    FACTOR = "Factor"
    SELECTION_POLICY = "Selection policy"
    PORTFOLIO_CONFIGURATION = "Portfolio configuration"
    RISK_POLICY = "Risk policy"


class DecisionArtifactState(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    INCOMPATIBLE = "INCOMPATIBLE"


@dataclass(frozen=True, slots=True)
class DecisionEvidenceDimension:
    dimension: str
    status: str
    reason_code: str
    rationale: str
    supporting_source: Path
    source_identity: str | None
    availability: str
    decision_relevant: bool | None
    observation_summary: str | None
    identity: str


@dataclass(frozen=True, slots=True)
class DecisionGateCandidate:
    key: str
    candidate_type: DecisionCandidateType
    name: str
    display_name: str
    research_stage: str
    disposition: str
    decision_reason: str
    candidate_identity: str
    decision_identity: str
    artifact_identity: str
    as_of: str | None
    evidence_population: str
    dimensions: tuple[DecisionEvidenceDimension, ...]
    blockers: tuple[str, ...]
    insufficient_dimensions: tuple[str, ...]
    mixed_or_uncertain_dimensions: tuple[str, ...]
    limitations: tuple[str, ...]
    manifest_path: Path
    summary_path: Path
    evidence_path: Path
    contract_bound_next_step: str | None


@dataclass(frozen=True, slots=True)
class DecisionArtifactStatus:
    family: str
    state: DecisionArtifactState
    detail: str
    root_path: Path


@dataclass(frozen=True, slots=True)
class DecisionGateCatalog:
    candidates: tuple[DecisionGateCandidate, ...]
    artifacts: tuple[DecisionArtifactStatus, ...]
    production: ProductionPolicySnapshot
    production_replacement: ResearchDecisionStatus
    readiness: ProductionReadinessStatus
    current_research_stage: str
    as_of: str | None

    def candidates_for(self, candidate_type: DecisionCandidateType) -> tuple[DecisionGateCandidate, ...]:
        return tuple(item for item in self.candidates if item.candidate_type is candidate_type)

    def candidate(self, key: str) -> DecisionGateCandidate | None:
        return next((item for item in self.candidates if item.key == key), None)


@dataclass(frozen=True, slots=True)
class DecisionContextResolution:
    candidate: DecisionGateCandidate | None
    notice: str | None


@dataclass(frozen=True, slots=True)
class _FamilySpec:
    family: str
    directory: str
    manifest: str
    summary: str
    evidence: str
    summary_columns: frozenset[str]
    evidence_columns: frozenset[str]


_FACTOR_POLICY = _FamilySpec(
    "Factor / selection policy",
    "quantlab_research_decision_gate_2018-08-07_2026-09-17",
    "research_decision_manifest.json",
    "research_decision_summary.csv",
    "research_decision_evidence.csv",
    frozenset({
        "candidate_type", "candidate", "decision", "reason_codes",
        "candidate_identity", "identity",
    }),
    frozenset({
        "candidate_type", "candidate", "dimension", "status", "defined",
        "decision_relevant", "source_artifact", "source_identity", "reason_code",
        "rationale", "identity",
    }),
)
_PORTFOLIO = _FamilySpec(
    "Portfolio configuration",
    "quantlab_portfolio_research_synthesis_2018-08-07_2026-09-17",
    "portfolio_research_synthesis_manifest.json",
    "portfolio_research_decisions.csv",
    "portfolio_research_evidence.csv",
    frozenset({"requested_budget", "horizon_sessions", "decision", "reason_code", "identity"}),
    frozenset({"requested_budget", "horizon_sessions", "dimension", "status", "reason_code", "identity"}),
)
_RISK = _FamilySpec(
    "Risk policy",
    "quantlab_risk_policy_decision_2018-08-07_2026-09-17",
    "risk_policy_decision_manifest.json",
    "risk_policy_decision_summary.csv",
    "risk_policy_decision_evidence.csv",
    frozenset({"policy", "policy_fingerprint", "decision", "reason_code", "identity"}),
    frozenset({"policy", "dimension", "status", "decision_relevant", "reason_code", "identity"}),
)
_SPECS = (_FACTOR_POLICY, _PORTFOLIO, _RISK)


def _rows(path: Path) -> tuple[dict[str, str], ...]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("CSV header is missing")
        return tuple(dict(row) for row in reader)


def _manifest(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or payload.get("completed") is not True:
        raise ValueError("manifest is not a completed object")
    return payload


def _require_columns(rows: tuple[dict[str, str], ...], required: frozenset[str], *, name: str) -> None:
    columns = frozenset(rows[0]) if rows else frozenset()
    missing = required - columns
    if missing:
        raise ValueError(f"{name} schema is missing: {', '.join(sorted(missing))}")


def _text(row: Mapping[str, str], field: str) -> str:
    return str(row.get(field, "")).strip()


def _bool(row: Mapping[str, str], field: str) -> bool | None:
    value = _text(row, field).lower()
    if value in {"true", "1", "yes"}:
        return True
    if value in {"false", "0", "no"}:
        return False
    return None


def _limitations(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    values = manifest.get("limitations")
    if not isinstance(values, list):
        return qualify_historical_limitations(())
    return qualify_historical_limitations(
        str(value).strip() for value in values if str(value).strip()
    )


def _as_of(manifest: Mapping[str, Any]) -> str | None:
    value = manifest.get("input_date_range")
    if isinstance(value, Mapping) and str(value.get("end_date", "")).strip():
        return str(value["end_date"]).strip()
    partition = manifest.get("temporal_partition")
    if isinstance(partition, list):
        dates = tuple(
            str(item.get("end_date", "")).strip()
            for item in partition
            if isinstance(item, Mapping) and str(item.get("end_date", "")).strip()
        )
        if dates:
            return max(dates)
    return None


def _artifact_identity(manifest: Mapping[str, Any]) -> str:
    return str(
        manifest.get("decision_result_identity")
        or manifest.get("result_identity")
        or manifest.get("specification_fingerprint")
        or ""
    ).strip()


def _source_path(root: Path, family_root: Path, manifest: Mapping[str, Any], row: Mapping[str, str]) -> Path:
    source = _text(row, "source_artifact")
    if not source:
        return family_root
    input_root = str(manifest.get("input_canonical_root", "")).strip()
    if input_root:
        return (root / "research_results" / Path(input_root).name / source).resolve()
    return (family_root / source).resolve()


def _dimension(
    *,
    row: Mapping[str, str],
    root: Path,
    family_root: Path,
    manifest: Mapping[str, Any],
    fallback_source: Path,
) -> DecisionEvidenceDimension:
    defined = _bool(row, "defined")
    status = _text(row, "status") or "UNAVAILABLE"
    if defined is False:
        availability = "UNAVAILABLE"
    elif status == "NOT_APPLICABLE":
        availability = "NOT_APPLICABLE"
    else:
        availability = "AVAILABLE"
    source = _source_path(root, family_root, manifest, row)
    if source == family_root:
        source = fallback_source
    return DecisionEvidenceDimension(
        _text(row, "dimension"),
        status,
        _text(row, "reason_code") or "UNAVAILABLE",
        _text(row, "rationale") or "No additional rationale is persisted for this dimension.",
        source,
        _text(row, "source_identity") or None,
        availability,
        _bool(row, "decision_relevant"),
        _text(row, "observation_summary") or None,
        _text(row, "identity"),
    )


def _classify_dimensions(dimensions: tuple[DecisionEvidenceDimension, ...]) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    blockers = tuple(
        item.dimension for item in dimensions if item.status in {"ADVERSE", "REJECTED"}
    )
    insufficient = tuple(
        item.dimension
        for item in dimensions
        if item.status in {"INSUFFICIENT", "UNAVAILABLE"} or item.availability == "UNAVAILABLE"
    )
    uncertain = tuple(
        item.dimension
        for item in dimensions
        if item.status in {
            "MIXED", "NEUTRAL", "NOT_APPLICABLE", "TRADE_OFF_PRESENT", "CONTROL_REFERENCE",
        }
    )
    return blockers, insufficient, uncertain


def _next_step(decision: str) -> str | None:
    if decision in {"ADVANCE_TO_FORWARD_VALIDATION", "ELIGIBLE_FOR_FUTURE_FORWARD_PROTOCOL"}:
        return decision
    return None


def _factor_policy_candidates(
    root: Path,
    family_root: Path,
    manifest: Mapping[str, Any],
    summaries: tuple[dict[str, str], ...],
    evidence_rows: tuple[dict[str, str], ...],
    spec: _FamilySpec,
) -> tuple[DecisionGateCandidate, ...]:
    artifact_identity = _artifact_identity(manifest)
    as_of = _as_of(manifest)
    limitations = _limitations(manifest)
    output: list[DecisionGateCandidate] = []
    for row in summaries:
        row_type = _text(row, "candidate_type").lower()
        if row_type not in {"factor", "policy"}:
            continue
        candidate_type = (
            DecisionCandidateType.FACTOR if row_type == "factor" else DecisionCandidateType.SELECTION_POLICY
        )
        name = _text(row, "candidate")
        dimensions = tuple(
            _dimension(
                row=item,
                root=root,
                family_root=family_root,
                manifest=manifest,
                fallback_source=family_root / spec.evidence,
            )
            for item in evidence_rows
            if _text(item, "candidate_type").lower() == row_type and _text(item, "candidate") == name
        )
        blockers, insufficient, uncertain = _classify_dimensions(dimensions)
        decision = _text(row, "decision")
        output.append(
            DecisionGateCandidate(
                f"{row_type}:{name}", candidate_type, name, name,
                "Factor and policy decision gate", decision,
                _text(row, "reason_codes") or "UNAVAILABLE",
                _text(row, "candidate_identity"), _text(row, "identity"), artifact_identity,
                as_of, "Complete point-in-time neutral research population",
                dimensions, blockers, insufficient, uncertain, limitations,
                family_root / spec.manifest, family_root / spec.summary, family_root / spec.evidence,
                _next_step(decision),
            )
        )
    return tuple(output)


def _portfolio_candidates(
    family_root: Path,
    manifest: Mapping[str, Any],
    summaries: tuple[dict[str, str], ...],
    evidence_rows: tuple[dict[str, str], ...],
    spec: _FamilySpec,
) -> tuple[DecisionGateCandidate, ...]:
    output: list[DecisionGateCandidate] = []
    for row in summaries:
        budget, horizon = _text(row, "requested_budget"), _text(row, "horizon_sessions")
        dimensions = tuple(
            _dimension(
                row=item,
                root=family_root.parent.parent,
                family_root=family_root,
                manifest=manifest,
                fallback_source=family_root / spec.evidence,
            )
            for item in evidence_rows
            if _text(item, "requested_budget") == budget and _text(item, "horizon_sessions") == horizon
        )
        blockers, insufficient, uncertain = _classify_dimensions(dimensions)
        decision = _text(row, "decision")
        name = f"budget_{budget} / horizon_{horizon}"
        output.append(
            DecisionGateCandidate(
                f"portfolio:{budget}:{horizon}", DecisionCandidateType.PORTFOLIO_CONFIGURATION,
                name, f"Budget {budget} · {horizon} sessions", "Portfolio research synthesis",
                decision, _text(row, "reason_code"), _text(row, "identity"), _text(row, "identity"),
                _artifact_identity(manifest), _as_of(manifest),
                "Historical overlapping forward-outcome observations for the ADX_ONLY neutral control",
                dimensions, blockers, insufficient, uncertain, _limitations(manifest),
                family_root / spec.manifest, family_root / spec.summary, family_root / spec.evidence,
                _next_step(decision),
            )
        )
    return tuple(output)


def _risk_candidates(
    family_root: Path,
    manifest: Mapping[str, Any],
    summaries: tuple[dict[str, str], ...],
    evidence_rows: tuple[dict[str, str], ...],
    spec: _FamilySpec,
) -> tuple[DecisionGateCandidate, ...]:
    output: list[DecisionGateCandidate] = []
    for row in summaries:
        policy = _text(row, "policy")
        dimensions = tuple(
            _dimension(
                row=item,
                root=family_root.parent.parent,
                family_root=family_root,
                manifest=manifest,
                fallback_source=family_root / spec.evidence,
            )
            for item in evidence_rows
            if _text(item, "policy") == policy
        )
        blockers, insufficient, uncertain = _classify_dimensions(dimensions)
        decision = _text(row, "decision")
        output.append(
            DecisionGateCandidate(
                f"risk:{policy}", DecisionCandidateType.RISK_POLICY, policy, policy,
                "Risk policy decision evaluation", decision, _text(row, "reason_code"),
                _text(row, "policy_fingerprint"), _text(row, "identity"), _artifact_identity(manifest),
                _as_of(manifest), "Frozen historical risk-policy evidence; not prospective confirmation",
                dimensions, blockers, insufficient, uncertain, _limitations(manifest),
                family_root / spec.manifest, family_root / spec.summary, family_root / spec.evidence,
                _next_step(decision),
            )
        )
    return tuple(output)


def _read_family(root: Path, spec: _FamilySpec) -> tuple[tuple[DecisionGateCandidate, ...], DecisionArtifactStatus]:
    family_root = (root / "research_results" / spec.directory).resolve()
    paths = (family_root / spec.manifest, family_root / spec.summary, family_root / spec.evidence)
    if not any(path.is_file() for path in paths):
        return (), DecisionArtifactStatus(spec.family, DecisionArtifactState.UNAVAILABLE, "Canonical decision artifact is unavailable.", family_root)
    if not all(path.is_file() for path in paths):
        return (), DecisionArtifactStatus(spec.family, DecisionArtifactState.INCOMPATIBLE, "Canonical decision artifact family is incomplete.", family_root)
    try:
        manifest = _manifest(paths[0])
        summaries = _rows(paths[1])
        evidence_rows = _rows(paths[2])
        _require_columns(summaries, spec.summary_columns, name="summary")
        _require_columns(evidence_rows, spec.evidence_columns, name="evidence")
        if not _artifact_identity(manifest):
            raise ValueError("artifact identity is missing")
        if spec is _FACTOR_POLICY:
            candidates = _factor_policy_candidates(root, family_root, manifest, summaries, evidence_rows, spec)
        elif spec is _PORTFOLIO:
            candidates = _portfolio_candidates(family_root, manifest, summaries, evidence_rows, spec)
        else:
            candidates = _risk_candidates(family_root, manifest, summaries, evidence_rows, spec)
        if not candidates:
            raise ValueError("no supported canonical candidates")
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return (), DecisionArtifactStatus(spec.family, DecisionArtifactState.INCOMPATIBLE, f"Canonical decision artifact is incompatible: {exc}", family_root)
    return candidates, DecisionArtifactStatus(spec.family, DecisionArtifactState.AVAILABLE, "Canonical persisted decisions are available.", family_root)


def inspect_decision_gate_catalog(
    *,
    root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
) -> DecisionGateCatalog:
    resolved_root = Path(root).resolve()
    candidates: list[DecisionGateCandidate] = []
    artifacts: list[DecisionArtifactStatus] = []
    for spec in _SPECS:
        found, status = _read_family(resolved_root, spec)
        candidates.extend(found)
        artifacts.append(status)
    frontier = inspect_research_frontier(root=resolved_root)
    ordered = tuple(sorted(candidates, key=lambda item: (item.candidate_type.value, item.display_name.casefold())))
    return DecisionGateCatalog(
        ordered,
        tuple(artifacts),
        inspect_production_policy(root=resolved_root, environ=environ),
        frontier.production_replacement,
        frontier.readiness,
        frontier.latest_stage,
        frontier.as_of,
    )


def resolve_decision_context(
    catalog: DecisionGateCatalog,
    context: Mapping[str, object] | None,
) -> DecisionContextResolution:
    if not context:
        return DecisionContextResolution(None, None)
    candidate_type = str(context.get("candidate_type", "")).strip().lower()
    key: str | None = None
    if candidate_type == "factor":
        name = str(context.get("candidate", context.get("factor", ""))).strip()
        key = f"factor:{name}" if name else None
    elif candidate_type in {"selection_policy", "policy"}:
        name = str(context.get("candidate", context.get("selection_policy", ""))).strip()
        key = f"policy:{name}" if name else None
    elif candidate_type in {"portfolio", "portfolio_configuration"}:
        budget = str(context.get("budget", "")).strip()
        horizon = str(context.get("horizon_sessions", context.get("horizon", ""))).strip()
        key = f"portfolio:{budget}:{horizon}" if budget and horizon else None
    elif candidate_type in {"risk", "risk_policy"}:
        name = str(context.get("candidate", context.get("risk_policy", ""))).strip()
        key = f"risk:{name}" if name else None
    if key is None:
        return DecisionContextResolution(None, "The carried context does not identify a supported persisted candidate.")
    candidate = catalog.candidate(key)
    if candidate is None:
        return DecisionContextResolution(
            None,
            f"The carried candidate `{key}` is not represented by a compatible canonical decision artifact; no substitute was selected.",
        )
    return DecisionContextResolution(candidate, None)


__all__ = (
    "DecisionArtifactState",
    "DecisionArtifactStatus",
    "DecisionCandidateType",
    "DecisionContextResolution",
    "DecisionEvidenceDimension",
    "DecisionGateCandidate",
    "DecisionGateCatalog",
    "inspect_decision_gate_catalog",
    "resolve_decision_context",
)
