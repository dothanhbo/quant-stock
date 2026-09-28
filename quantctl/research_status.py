from __future__ import annotations

import csv
from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any, Mapping

from config.paper_store import (
    Q70_STRATEGY_IDENTITY,
    configured_paper_environment,
    resolve_active_paper_store,
)
from quantctl.registry import PROJECT_ROOT


FRAMEWORK_NAME = "Neutral Quant Lab"


class ResearchDecisionLevel(str, Enum):
    FACTOR = "FACTOR"
    POLICY = "POLICY"
    PORTFOLIO = "PORTFOLIO"
    RISK = "RISK"
    PRODUCTION = "PRODUCTION"


class ResearchDecision(str, Enum):
    ADVANCE = "ADVANCE"
    ADVANCE_TO_FORWARD_VALIDATION = "ADVANCE_TO_FORWARD_VALIDATION"
    ELIGIBLE_FOR_FUTURE_FORWARD_PROTOCOL = "ELIGIBLE_FOR_FUTURE_FORWARD_PROTOCOL"
    CONTROL_BASELINE = "CONTROL_BASELINE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    HOLD = "HOLD"
    REJECT_FOR_NOW = "REJECT_FOR_NOW"
    APPROVED = "APPROVED"
    NONE_APPROVED = "NONE_APPROVED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ResearchDecisionStatus:
    name: str
    level: ResearchDecisionLevel
    decision: ResearchDecision
    source_artifact: Path | None
    as_of: str | None
    note: str = ""


@dataclass(frozen=True, slots=True)
class ResearchFrontierSnapshot:
    framework: str
    generation_identity: str
    latest_stage: str
    factor_decisions: tuple[ResearchDecisionStatus, ...]
    policy_decisions: tuple[ResearchDecisionStatus, ...]
    portfolio_risk_decisions: tuple[ResearchDecisionStatus, ...]
    production_replacement: ResearchDecisionStatus
    source_artifacts: tuple[Path, ...]
    as_of: str | None
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProductionPolicySnapshot:
    deployed_strategy_identity: str
    active_store: str
    database_path: Path
    role: str
    configured: bool
    source_evidence: str


@dataclass(frozen=True, slots=True)
class _ArtifactSpec:
    stage: str
    level: ResearchDecisionLevel
    root_name: str
    manifest_name: str
    summary_name: str
    name_fields: tuple[str, ...]


_ARTIFACT_SPECS = (
    _ArtifactSpec(
        "Factor and policy decision gate",
        ResearchDecisionLevel.FACTOR,
        "quantlab_research_decision_gate_2018-08-07_2026-09-17",
        "research_decision_manifest.json",
        "research_decision_summary.csv",
        ("candidate",),
    ),
    _ArtifactSpec(
        "Portfolio research synthesis",
        ResearchDecisionLevel.PORTFOLIO,
        "quantlab_portfolio_research_synthesis_2018-08-07_2026-09-17",
        "portfolio_research_synthesis_manifest.json",
        "portfolio_research_decisions.csv",
        ("requested_budget", "horizon_sessions"),
    ),
    _ArtifactSpec(
        "Risk policy decision evaluation",
        ResearchDecisionLevel.RISK,
        "quantlab_risk_policy_decision_2018-08-07_2026-09-17",
        "risk_policy_decision_manifest.json",
        "risk_policy_decision_summary.csv",
        ("policy",),
    ),
)


def _manifest_as_of(manifest: Mapping[str, Any]) -> str | None:
    for key in ("as_of", "as_of_date", "end_date"):
        value = manifest.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("input_date_range", "period"):
        value = manifest.get(key)
        if isinstance(value, Mapping):
            end_date = value.get("end_date")
            if isinstance(end_date, str) and end_date.strip():
                return end_date.strip()
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


def _decision(value: str) -> ResearchDecision:
    normalized = value.strip().upper()
    try:
        return ResearchDecision(normalized)
    except ValueError:
        return ResearchDecision.UNKNOWN


def _row_level(spec: _ArtifactSpec, row: Mapping[str, str]) -> ResearchDecisionLevel:
    if spec.level is not ResearchDecisionLevel.FACTOR:
        return spec.level
    candidate_type = str(row.get("candidate_type", "")).strip().lower()
    return ResearchDecisionLevel.FACTOR if candidate_type == "factor" else ResearchDecisionLevel.POLICY


def _row_name(spec: _ArtifactSpec, row: Mapping[str, str]) -> str:
    values = tuple(str(row.get(field, "")).strip() for field in spec.name_fields)
    if spec.level is ResearchDecisionLevel.PORTFOLIO and len(values) == 2:
        return f"budget_{values[0]} / horizon_{values[1]}"
    return " / ".join(value for value in values if value)


def _read_artifact(
    root: Path,
    spec: _ArtifactSpec,
) -> tuple[tuple[ResearchDecisionStatus, ...], Path | None, str | None, tuple[str, ...], str | None]:
    artifact_root = root / "research_results" / spec.root_name
    manifest_path = artifact_root / spec.manifest_name
    summary_path = artifact_root / spec.summary_name
    if not manifest_path.is_file() and not summary_path.is_file():
        return (), None, None, (f"{spec.stage}: canonical artifact unavailable",), None
    if not manifest_path.is_file() or not summary_path.is_file():
        return (), None, None, (f"{spec.stage}: incomplete canonical artifact pair",), None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("completed") is not True:
            raise ValueError("manifest is not marked completed")
        with summary_path.open(encoding="utf-8", newline="") as handle:
            rows = tuple(dict(row) for row in csv.DictReader(handle))
    except (OSError, json.JSONDecodeError, ValueError) as error:
        return (), None, None, (f"{spec.stage}: unreadable canonical artifact ({error})",), None

    as_of = _manifest_as_of(manifest)
    statuses: dict[tuple[ResearchDecisionLevel, str], ResearchDecisionStatus] = {}
    conflicts: set[tuple[ResearchDecisionLevel, str]] = set()
    warnings: list[str] = []
    for row in rows:
        level = _row_level(spec, row)
        name = _row_name(spec, row)
        if not name or not str(row.get("decision", "")).strip():
            warnings.append(f"{spec.stage}: ignored malformed decision row")
            continue
        status = ResearchDecisionStatus(
            name=name,
            level=level,
            decision=_decision(str(row["decision"])),
            source_artifact=summary_path,
            as_of=as_of,
            note=str(row.get("reason_code") or row.get("reason_codes") or "").strip(),
        )
        key = (level, name.casefold())
        previous = statuses.get(key)
        if previous is not None and previous.decision is not status.decision:
            conflicts.add(key)
        else:
            statuses[key] = status
    for key in conflicts:
        previous = statuses[key]
        statuses[key] = ResearchDecisionStatus(
            previous.name,
            previous.level,
            ResearchDecision.UNKNOWN,
            summary_path,
            as_of,
            "Conflicting decisions in the canonical artifact",
        )
        warnings.append(f"{spec.stage}: conflicting decisions for {previous.name}")
    generation = str(
        manifest.get("decision_result_identity")
        or manifest.get("result_identity")
        or manifest.get("specification_fingerprint")
        or ""
    ).strip() or None
    ordered = tuple(sorted(statuses.values(), key=lambda item: (item.level.value, item.name.casefold())))
    return ordered, manifest_path, as_of, tuple(warnings), generation


def inspect_research_frontier(*, root: Path = PROJECT_ROOT) -> ResearchFrontierSnapshot:
    decisions: list[ResearchDecisionStatus] = []
    sources: list[Path] = []
    warnings: list[str] = []
    as_of_dates: list[str] = []
    generation_identities: list[str] = []
    latest_stage = "UNKNOWN — canonical decision artifacts unavailable"
    any_artifact = False
    conflict = False
    for spec in _ARTIFACT_SPECS:
        found, source, as_of, artifact_warnings, generation = _read_artifact(root, spec)
        decisions.extend(found)
        warnings.extend(artifact_warnings)
        if source is not None:
            any_artifact = True
            sources.append(source)
            latest_stage = spec.stage
        if as_of:
            as_of_dates.append(as_of)
        if generation:
            generation_identities.append(generation)
        if any("conflicting decisions" in item for item in artifact_warnings):
            conflict = True

    factor = tuple(item for item in decisions if item.level is ResearchDecisionLevel.FACTOR)
    policy = tuple(item for item in decisions if item.level is ResearchDecisionLevel.POLICY)
    portfolio_risk = tuple(
        item for item in decisions
        if item.level in {ResearchDecisionLevel.PORTFOLIO, ResearchDecisionLevel.RISK}
    )
    if not any_artifact or conflict:
        replacement_decision = ResearchDecision.UNKNOWN
        replacement_note = (
            "Canonical decision evidence is unavailable or conflicting; no replacement is inferred."
        )
    else:
        replacement_decision = ResearchDecision.NONE_APPROVED
        replacement_note = (
            "No current canonical artifact explicitly authorizes a production replacement."
        )
    replacement = ResearchDecisionStatus(
        "Production replacement",
        ResearchDecisionLevel.PRODUCTION,
        replacement_decision,
        None,
        max(as_of_dates, default=None),
        replacement_note,
    )
    return ResearchFrontierSnapshot(
        framework=FRAMEWORK_NAME,
        generation_identity=" | ".join(generation_identities) if generation_identities else "UNKNOWN",
        latest_stage=latest_stage,
        factor_decisions=factor,
        policy_decisions=policy,
        portfolio_risk_decisions=portfolio_risk,
        production_replacement=replacement,
        source_artifacts=tuple(sources),
        as_of=max(as_of_dates, default=None),
        warnings=tuple(warnings),
    )


def inspect_production_policy(
    *,
    root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
) -> ProductionPolicySnapshot:
    configured = configured_paper_environment(root=root, environ=environ)
    resolved = resolve_active_paper_store(configured)
    role = "FROZEN_BASELINE" if resolved.strategy_identity == Q70_STRATEGY_IDENTITY else "DEPLOYED_BASELINE"
    return ProductionPolicySnapshot(
        deployed_strategy_identity=resolved.strategy_identity,
        active_store=resolved.display_name,
        database_path=(root / resolved.database_path).resolve()
        if not resolved.database_path.is_absolute()
        else resolved.database_path.resolve(),
        role=role,
        configured=bool(str(configured.get("PAPER_STRATEGY_VERSION", "")).strip()),
        source_evidence="PAPER_STRATEGY_VERSION and canonical paper-store resolver",
    )
