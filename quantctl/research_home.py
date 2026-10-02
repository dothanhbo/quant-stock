from __future__ import annotations

import csv
from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any, Mapping

from quantctl.historical_qualification import qualify_historical_limitations
from quantctl.registry import PROJECT_ROOT


EVIDENCE_START_DATE = "2018-08-07"
EVIDENCE_END_DATE = "2026-09-17"


class EvidenceState(str, Enum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True, slots=True)
class CanonicalArtifactEvidence:
    key: str
    label: str
    state: EvidenceState
    as_of: str | None
    result_identity: str | None
    root_path: Path
    manifest_path: Path
    summary_path: Path
    limitations: tuple[str, ...]
    detail: str


@dataclass(frozen=True, slots=True)
class MonitoringHistoryEvidence:
    state: EvidenceState
    compatible_snapshot_count: int
    result_identity: str | None
    source_path: Path
    latest_session: str | None
    transition_count: int
    message: str
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResearchHomeCatalog:
    artifacts: tuple[CanonicalArtifactEvidence, ...]
    monitoring: MonitoringHistoryEvidence
    evidence_start_date: str = EVIDENCE_START_DATE
    evidence_end_date: str = EVIDENCE_END_DATE
    universe_label: str = "point-in-time database coverage"


@dataclass(frozen=True, slots=True)
class _ArtifactSpec:
    key: str
    label: str
    directory: str
    manifest: str
    summary: str
    identity_path: tuple[str, ...]
    required_summary_columns: frozenset[str]
    as_of_paths: tuple[tuple[str, ...], ...]


_ARTIFACT_SPECS = (
    _ArtifactSpec(
        "factor_evidence",
        "Factor Evidence",
        "quantlab_neutral_panel_factor_evaluation_2018-08-07_2026-09-17",
        "experiment_manifest.json",
        "factor_summary.csv",
        ("evaluation", "result_identity"),
        frozenset({"factor", "horizon_sessions", "outcome_field", "identity"}),
        (("effective_bounds", "outcome_data_through_session"), ("requested_bounds", "end_date")),
    ),
    _ArtifactSpec(
        "candidate_gate",
        "Candidate Gate",
        "quantlab_research_decision_gate_2018-08-07_2026-09-17",
        "research_decision_manifest.json",
        "research_decision_summary.csv",
        ("decision_result_identity",),
        frozenset({"candidate_type", "candidate", "decision", "identity"}),
        (("input_date_range", "end_date"),),
    ),
    _ArtifactSpec(
        "portfolio_evidence",
        "Portfolio Evidence",
        "quantlab_portfolio_research_synthesis_2018-08-07_2026-09-17",
        "portfolio_research_synthesis_manifest.json",
        "portfolio_research_decisions.csv",
        ("result_identity",),
        frozenset({"decision", "identity"}),
        (("temporal_partition", "end_date"),),
    ),
    _ArtifactSpec(
        "risk_gate",
        "Risk Gate",
        "quantlab_risk_policy_decision_2018-08-07_2026-09-17",
        "risk_policy_decision_manifest.json",
        "risk_policy_decision_summary.csv",
        ("result_identity",),
        frozenset({"policy", "decision", "identity"}),
        (),
    ),
)

_MONITORING_DIRECTORY = "quantlab_monitoring_history_2026-09-25_f48bc402c781"
_MONITORING_MANIFEST = "monitoring_longitudinal_manifest.json"
_MONITORING_CATALOG = "monitoring_snapshot_catalog.csv"
_MONITORING_TRANSITIONS = "monitoring_transitions.csv"
_CHANGE_HISTORY_UNAVAILABLE = (
    "Change history unavailable — at least two compatible snapshots are required."
)


def _mapping_value(payload: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = payload
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _identity(payload: Mapping[str, Any], path: tuple[str, ...]) -> str | None:
    value = _mapping_value(payload, path)
    normalized = str(value or "").strip()
    return normalized or None


def _as_of(payload: Mapping[str, Any], paths: tuple[tuple[str, ...], ...]) -> str | None:
    for path in paths:
        if path and path[0] == "temporal_partition":
            partition = payload.get("temporal_partition")
            if isinstance(partition, list):
                dates = tuple(
                    str(item.get("end_date", "")).strip()
                    for item in partition
                    if isinstance(item, Mapping) and str(item.get("end_date", "")).strip()
                )
                if dates:
                    return max(dates)
            continue
        value = _mapping_value(payload, path)
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return EVIDENCE_END_DATE if payload.get("completed") is True else None


def _limitations(payload: Mapping[str, Any]) -> tuple[str, ...]:
    values = payload.get("limitations")
    if not isinstance(values, list):
        return ()
    return tuple(str(item).strip() for item in values if str(item).strip())


def _read_manifest(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("manifest root must be an object")
    return payload


def _read_header(path: Path) -> frozenset[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        header = next(csv.reader(handle), ())
    return frozenset(str(item).strip() for item in header if str(item).strip())


def _read_rows(path: Path) -> tuple[dict[str, str], ...]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return tuple(dict(row) for row in csv.DictReader(handle))


def _inspect_artifact(root: Path, spec: _ArtifactSpec) -> CanonicalArtifactEvidence:
    artifact_root = root / "research_results" / spec.directory
    manifest_path = artifact_root / spec.manifest
    summary_path = artifact_root / spec.summary
    manifest_exists = manifest_path.is_file()
    summary_exists = summary_path.is_file()
    if not manifest_exists and not summary_exists:
        return CanonicalArtifactEvidence(
            spec.key, spec.label, EvidenceState.UNAVAILABLE, None, None,
            artifact_root, manifest_path, summary_path, (), "Canonical artifact is unavailable.",
        )
    if not manifest_exists or not summary_exists:
        return CanonicalArtifactEvidence(
            spec.key, spec.label, EvidenceState.PARTIAL, None, None,
            artifact_root, manifest_path, summary_path, (),
            "Canonical artifact family is incomplete.",
        )
    try:
        manifest = _read_manifest(manifest_path)
        header = _read_header(summary_path)
        result_identity = _identity(manifest, spec.identity_path)
        missing_columns = spec.required_summary_columns - header
        if manifest.get("completed") is not True:
            raise ValueError("manifest is not marked completed")
        if result_identity is None:
            raise ValueError("result identity is missing")
        if missing_columns:
            raise ValueError(f"summary schema is missing: {', '.join(sorted(missing_columns))}")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return CanonicalArtifactEvidence(
            spec.key, spec.label, EvidenceState.UNAVAILABLE, None, None,
            artifact_root, manifest_path, summary_path, (),
            f"Canonical artifact is incompatible: {exc}",
        )
    return CanonicalArtifactEvidence(
        spec.key,
        spec.label,
        EvidenceState.AVAILABLE,
        _as_of(manifest, spec.as_of_paths),
        result_identity,
        artifact_root,
        manifest_path,
        summary_path,
        qualify_historical_limitations(_limitations(manifest)),
        "Canonical persisted evidence is available.",
    )


def _inspect_monitoring(root: Path) -> MonitoringHistoryEvidence:
    source_path = (
        root / "research_results" / _MONITORING_DIRECTORY / _MONITORING_MANIFEST
    )
    if not source_path.is_file():
        return MonitoringHistoryEvidence(
            EvidenceState.UNAVAILABLE, 0, None, source_path, None, 0,
            _CHANGE_HISTORY_UNAVAILABLE, (),
        )
    try:
        payload = _read_manifest(source_path)
        if payload.get("contract") != "quantlab.monitoring_history":
            raise ValueError("unexpected monitoring contract")
        count = int(payload.get("valid_snapshot_count", 0))
        identity = _identity(payload, ("result_identity",))
        if identity is None:
            raise ValueError("result identity is missing")
        catalog_rows = _read_rows(source_path.with_name(_MONITORING_CATALOG))
        valid_rows = tuple(
            row for row in catalog_rows if str(row.get("classification", "")).strip() == "VALID"
        )
        latest_row = valid_rows[-1] if valid_rows else None
        latest_specification = (
            str(latest_row.get("specification_fingerprint", "")).strip()
            if latest_row is not None
            else ""
        )
        compatible_rows = tuple(
            row
            for row in valid_rows
            if str(row.get("specification_fingerprint", "")).strip() == latest_specification
        )
        count = len(compatible_rows)
        latest_session = (
            str(latest_row.get("observed_market_session", "")).strip() or None
            if latest_row is not None
            else None
        )
        transition_rows = _read_rows(source_path.with_name(_MONITORING_TRANSITIONS))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return MonitoringHistoryEvidence(
            EvidenceState.UNAVAILABLE, 0, None, source_path, None, 0,
            _CHANGE_HISTORY_UNAVAILABLE, (),
        )
    comparable_transitions = tuple(
        row
        for row in transition_rows
        if str(row.get("current_identity", "")).strip()
        == str(compatible_rows[-1].get("result_identity", "")).strip()
    ) if count >= 2 else ()
    if count >= 2 and comparable_transitions:
        latest_transition = comparable_transitions[-1]
        message = (
            f"{latest_transition.get('previous_session', 'UNKNOWN')} → "
            f"{latest_transition.get('current_session', 'UNKNOWN')}: "
            f"{latest_transition.get('previous_status', 'UNKNOWN')} → "
            f"{latest_transition.get('current_status', 'UNKNOWN')} "
            f"({len(comparable_transitions)} persisted transition record(s))."
        )
        state = EvidenceState.AVAILABLE
    elif count >= 2:
        message = "Compatible snapshots exist, but persisted change records are unavailable."
        state = EvidenceState.PARTIAL
    else:
        message = _CHANGE_HISTORY_UNAVAILABLE
        state = EvidenceState.INSUFFICIENT
    return MonitoringHistoryEvidence(
        state,
        count,
        identity,
        source_path,
        latest_session,
        len(comparable_transitions),
        message,
        _limitations(payload),
    )


def inspect_research_home_catalog(*, root: Path = PROJECT_ROOT) -> ResearchHomeCatalog:
    """Read the fixed, approved Slice-1 artifact registry without executing research."""

    return ResearchHomeCatalog(
        tuple(_inspect_artifact(root, spec) for spec in _ARTIFACT_SPECS),
        _inspect_monitoring(root),
    )
