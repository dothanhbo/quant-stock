from __future__ import annotations

"""Immutable governance contract for pre-registered alpha hypotheses.

The registry records research intent and evidence boundaries.  It performs no
factor calculation, outcome evaluation, strategy selection, or production
promotion.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from quantlab.identity import canonical_identity_value, canonical_json


ALPHA_HYPOTHESIS_REGISTRY_CONTRACT = "quantlab.alpha_hypothesis_registry"
ALPHA_HYPOTHESIS_REGISTRY_VERSION = "v1"


class HypothesisStatus(str, Enum):
    DRAFT = "DRAFT"
    REGISTERED = "REGISTERED"
    EVALUATING = "EVALUATING"
    INCONCLUSIVE = "INCONCLUSIVE"
    FALSIFIED = "FALSIFIED"
    SUPPORTED = "SUPPORTED"


class ExpectedSign(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NON_MONOTONIC = "NON_MONOTONIC"
    UNKNOWN = "UNKNOWN"


class EvidenceOrigin(str, Enum):
    RETROSPECTIVE = "RETROSPECTIVE"
    PROSPECTIVE = "PROSPECTIVE"


class AssessmentState(str, Enum):
    NOT_ASSESSED = "NOT_ASSESSED"
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"


_ALLOWED_TRANSITIONS: Mapping[HypothesisStatus, frozenset[HypothesisStatus]] = MappingProxyType({
    HypothesisStatus.DRAFT: frozenset({HypothesisStatus.REGISTERED}),
    HypothesisStatus.REGISTERED: frozenset({HypothesisStatus.EVALUATING}),
    HypothesisStatus.EVALUATING: frozenset({
        HypothesisStatus.INCONCLUSIVE,
        HypothesisStatus.FALSIFIED,
        HypothesisStatus.SUPPORTED,
    }),
    HypothesisStatus.INCONCLUSIVE: frozenset({HypothesisStatus.EVALUATING}),
    HypothesisStatus.FALSIFIED: frozenset(),
    HypothesisStatus.SUPPORTED: frozenset(),
})


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _text(value: Any, *, name: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{name} must be non-empty")
    return result


def _optional_text(value: Any, *, name: str) -> str | None:
    if value is None:
        return None
    return _text(value, name=name)


def _utc_timestamp(value: Any, *, name: str) -> str:
    text = _text(value, name=name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{name} must be UTC")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _date(value: Any, *, name: str) -> str:
    text = _text(value, name=name)
    try:
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date") from exc


def _tuple_text(values: Any, *, name: str, allow_empty: bool = False) -> tuple[str, ...]:
    if isinstance(values, str) or values is None:
        raise TypeError(f"{name} must be a sequence of strings")
    result = tuple(_text(value, name=name) for value in values)
    if not allow_empty and not result:
        raise ValueError(f"{name} must not be empty")
    if len(set(result)) != len(result):
        raise ValueError(f"{name} must not contain duplicates")
    return result


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    origin: EvidenceOrigin
    source_path: str
    source_identity: str
    source_fingerprint: str | None
    observed_through_date: str
    evidence_started_at_utc: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_path", _text(self.source_path, name="evidence source path"))
        object.__setattr__(self, "source_identity", _text(self.source_identity, name="evidence source identity"))
        object.__setattr__(self, "source_fingerprint", _optional_text(self.source_fingerprint, name="evidence source fingerprint"))
        object.__setattr__(self, "observed_through_date", _date(self.observed_through_date, name="evidence observed-through date"))
        if self.origin is EvidenceOrigin.RETROSPECTIVE and self.evidence_started_at_utc is not None:
            raise ValueError("retrospective evidence must not claim a prospective start")
        if self.origin is EvidenceOrigin.PROSPECTIVE:
            object.__setattr__(self, "evidence_started_at_utc", _utc_timestamp(self.evidence_started_at_utc, name="prospective evidence start"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "origin": self.origin.value,
            "source_path": self.source_path,
            "source_identity": self.source_identity,
            "source_fingerprint": self.source_fingerprint,
            "observed_through_date": self.observed_through_date,
            "evidence_started_at_utc": self.evidence_started_at_utc,
        }


@dataclass(frozen=True, slots=True)
class ProspectiveEvaluationWindow:
    start_after_session: str
    end_session: str

    def __post_init__(self) -> None:
        start = _date(self.start_after_session, name="prospective start-after session")
        end = _date(self.end_session, name="prospective end session")
        if start >= end:
            raise ValueError("prospective end session must be after start-after session")
        object.__setattr__(self, "start_after_session", start)
        object.__setattr__(self, "end_session", end)

    def as_dict(self) -> dict[str, str]:
        return {"start_after_session": self.start_after_session, "end_session": self.end_session}


@dataclass(frozen=True, slots=True)
class MinimumEconomicEffect:
    metric: str
    operator: str
    threshold: float
    unit: str

    def __post_init__(self) -> None:
        if self.operator not in {"GT", "GTE", "LT", "LTE", "ABS_GTE"}:
            raise ValueError("unsupported economic-effect operator")
        if isinstance(self.threshold, bool) or not math.isfinite(float(self.threshold)):
            raise ValueError("economic-effect threshold must be finite")
        object.__setattr__(self, "metric", _text(self.metric, name="economic-effect metric"))
        object.__setattr__(self, "threshold", float(self.threshold))
        object.__setattr__(self, "unit", _text(self.unit, name="economic-effect unit"))

    def as_dict(self) -> dict[str, Any]:
        return {"metric": self.metric, "operator": self.operator, "threshold": self.threshold, "unit": self.unit}


@dataclass(frozen=True, slots=True)
class ResearchBoundaryAssessments:
    research_gate_pass: AssessmentState = AssessmentState.NOT_ASSESSED
    alpha_validated: AssessmentState = AssessmentState.NOT_ASSESSED
    production_ready: AssessmentState = AssessmentState.NOT_ASSESSED

    def as_dict(self) -> dict[str, str]:
        return {
            "research_gate_pass": self.research_gate_pass.value,
            "alpha_validated": self.alpha_validated.value,
            "production_ready": self.production_ready.value,
        }


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    from_status: HypothesisStatus | None
    to_status: HypothesisStatus
    occurred_at_utc: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "occurred_at_utc", _utc_timestamp(self.occurred_at_utc, name="lifecycle timestamp"))
        object.__setattr__(self, "reason", _text(self.reason, name="lifecycle reason"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "from_status": None if self.from_status is None else self.from_status.value,
            "to_status": self.to_status.value,
            "occurred_at_utc": self.occurred_at_utc,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class AlphaHypothesis:
    hypothesis_id: str
    registry_schema_version: str
    created_at_utc: str
    revision: int
    research_question: str
    factor_policy_reference: str
    factor_policy_version: str | None
    expected_relationship: str | None
    expected_sign: ExpectedSign
    universe_definition: str | None
    universe_limitations: tuple[str, ...]
    feature_availability_timing: str | None
    outcome_definition: str | None
    evaluation_horizons_sessions: tuple[int, ...]
    historical_discovery_cutoff: str | None
    registration_timestamp_utc: str | None
    evidence_references: tuple[EvidenceReference, ...]
    evidence_family: str | None
    prospective_evaluation_window: ProspectiveEvaluationWindow | None
    minimum_sample_count: int | None
    minimum_coverage_pct: float | None
    minimum_economic_effect: MinimumEconomicEffect | None
    falsification_conditions: tuple[str, ...]
    methodological_limitations: tuple[str, ...]
    dependencies: tuple[str, ...]
    status: HypothesisStatus
    assessments: ResearchBoundaryAssessments
    parent_revision_identity: str | None
    lifecycle_history: tuple[LifecycleEvent, ...]
    specification_fingerprint: str = field(init=False)
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        if self.registry_schema_version != ALPHA_HYPOTHESIS_REGISTRY_VERSION:
            raise ValueError("unsupported alpha-hypothesis registry schema version")
        if isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("hypothesis revision must be a positive integer")
        object.__setattr__(self, "hypothesis_id", _text(self.hypothesis_id, name="hypothesis id"))
        object.__setattr__(self, "created_at_utc", _utc_timestamp(self.created_at_utc, name="creation timestamp"))
        object.__setattr__(self, "research_question", _text(self.research_question, name="research question"))
        object.__setattr__(self, "factor_policy_reference", _text(self.factor_policy_reference, name="factor/policy reference"))
        for name in (
            "factor_policy_version", "expected_relationship", "universe_definition",
            "feature_availability_timing", "outcome_definition", "evidence_family",
            "parent_revision_identity",
        ):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name=name.replace("_", " ")))
        object.__setattr__(self, "universe_limitations", _tuple_text(self.universe_limitations, name="universe limitations", allow_empty=True))
        object.__setattr__(self, "falsification_conditions", _tuple_text(self.falsification_conditions, name="falsification conditions", allow_empty=True))
        object.__setattr__(self, "methodological_limitations", _tuple_text(self.methodological_limitations, name="methodological limitations", allow_empty=True))
        object.__setattr__(self, "dependencies", _tuple_text(self.dependencies, name="dependencies", allow_empty=True))
        horizons = tuple(self.evaluation_horizons_sessions)
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in horizons):
            raise ValueError("evaluation horizons must be positive integers")
        if tuple(sorted(set(horizons))) != horizons:
            raise ValueError("evaluation horizons must be unique and ascending")
        object.__setattr__(self, "evaluation_horizons_sessions", horizons)
        if self.historical_discovery_cutoff is not None:
            object.__setattr__(self, "historical_discovery_cutoff", _date(self.historical_discovery_cutoff, name="historical discovery cutoff"))
        if self.registration_timestamp_utc is not None:
            object.__setattr__(self, "registration_timestamp_utc", _utc_timestamp(self.registration_timestamp_utc, name="registration timestamp"))
        if self.minimum_sample_count is not None and (isinstance(self.minimum_sample_count, bool) or self.minimum_sample_count <= 0):
            raise ValueError("minimum sample count must be positive")
        if self.minimum_coverage_pct is not None:
            coverage = float(self.minimum_coverage_pct)
            if not math.isfinite(coverage) or not 0 < coverage <= 100:
                raise ValueError("minimum coverage percentage must be in (0, 100]")
            object.__setattr__(self, "minimum_coverage_pct", coverage)
        evidence = tuple(self.evidence_references)
        if len({(item.origin, item.source_identity) for item in evidence}) != len(evidence):
            raise ValueError("duplicate evidence reference")
        object.__setattr__(self, "evidence_references", evidence)
        history = tuple(self.lifecycle_history)
        if not history or history[-1].to_status is not self.status:
            raise ValueError("lifecycle history must end at the current status")
        if history[0].from_status is not None or history[0].to_status is not HypothesisStatus.DRAFT:
            raise ValueError("lifecycle history must begin with DRAFT creation")
        for previous, current in zip(history, history[1:]):
            if current.from_status is not previous.to_status or current.to_status not in _ALLOWED_TRANSITIONS[previous.to_status]:
                raise ValueError("lifecycle history contains an invalid transition")
        object.__setattr__(self, "lifecycle_history", history)
        if self.status is not HypothesisStatus.DRAFT:
            self._validate_registered_specification()
        if any(item.origin is EvidenceOrigin.PROSPECTIVE for item in evidence):
            if self.registration_timestamp_utc is None:
                raise ValueError("prospective evidence requires a registration timestamp")
            for item in evidence:
                if item.origin is EvidenceOrigin.PROSPECTIVE and item.evidence_started_at_utc < self.registration_timestamp_utc:
                    raise ValueError("prospective evidence cannot predate registration")
        specification = self._specification_content()
        fingerprint = _hash(specification)
        identity = _hash({
            "contract": {"name": ALPHA_HYPOTHESIS_REGISTRY_CONTRACT, "version": ALPHA_HYPOTHESIS_REGISTRY_VERSION},
            "specification_fingerprint": fingerprint,
            "status": self.status.value,
            "assessments": self.assessments.as_dict(),
            "lifecycle_history": tuple(item.as_dict() for item in history),
        })
        object.__setattr__(self, "specification_fingerprint", fingerprint)
        object.__setattr__(self, "identity", identity)

    def _validate_registered_specification(self) -> None:
        required = {
            "factor_policy_version": self.factor_policy_version,
            "expected_relationship": self.expected_relationship,
            "universe_definition": self.universe_definition,
            "feature_availability_timing": self.feature_availability_timing,
            "outcome_definition": self.outcome_definition,
            "historical_discovery_cutoff": self.historical_discovery_cutoff,
            "registration_timestamp_utc": self.registration_timestamp_utc,
            "evidence_family": self.evidence_family,
            "prospective_evaluation_window": self.prospective_evaluation_window,
            "minimum_sample_count": self.minimum_sample_count,
            "minimum_coverage_pct": self.minimum_coverage_pct,
            "minimum_economic_effect": self.minimum_economic_effect,
        }
        missing = tuple(name for name, value in required.items() if value is None)
        if missing or self.expected_sign is ExpectedSign.UNKNOWN or not self.evaluation_horizons_sessions:
            raise ValueError(f"registered hypothesis has incomplete declarations: {', '.join(missing) or 'sign/horizons'}")
        if not self.falsification_conditions or not self.methodological_limitations:
            raise ValueError("registered hypothesis requires falsification conditions and methodological limitations")

    def _specification_content(self) -> dict[str, Any]:
        return {
            "contract": {"name": ALPHA_HYPOTHESIS_REGISTRY_CONTRACT, "version": ALPHA_HYPOTHESIS_REGISTRY_VERSION},
            "hypothesis_id": self.hypothesis_id,
            "registry_schema_version": self.registry_schema_version,
            "created_at_utc": self.created_at_utc,
            "revision": self.revision,
            "research_question": self.research_question,
            "factor_policy_reference": self.factor_policy_reference,
            "factor_policy_version": self.factor_policy_version,
            "expected_relationship": self.expected_relationship,
            "expected_sign": self.expected_sign.value,
            "universe_definition": self.universe_definition,
            "universe_limitations": self.universe_limitations,
            "feature_availability_timing": self.feature_availability_timing,
            "outcome_definition": self.outcome_definition,
            "evaluation_horizons_sessions": self.evaluation_horizons_sessions,
            "historical_discovery_cutoff": self.historical_discovery_cutoff,
            "registration_timestamp_utc": self.registration_timestamp_utc,
            "evidence_references": tuple(item.as_dict() for item in self.evidence_references),
            "evidence_family": self.evidence_family,
            "prospective_evaluation_window": None if self.prospective_evaluation_window is None else self.prospective_evaluation_window.as_dict(),
            "minimum_sample_count": self.minimum_sample_count,
            "minimum_coverage_pct": self.minimum_coverage_pct,
            "minimum_economic_effect": None if self.minimum_economic_effect is None else self.minimum_economic_effect.as_dict(),
            "falsification_conditions": self.falsification_conditions,
            "methodological_limitations": self.methodological_limitations,
            "dependencies": self.dependencies,
            "parent_revision_identity": self.parent_revision_identity,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            **self._specification_content(),
            "status": self.status.value,
            "assessments": self.assessments.as_dict(),
            "lifecycle_history": [item.as_dict() for item in self.lifecycle_history],
            "specification_fingerprint": self.specification_fingerprint,
            "identity": self.identity,
        }


@dataclass(frozen=True, slots=True)
class AlphaHypothesisRegistry:
    records: tuple[AlphaHypothesis, ...]
    contract_name: str = ALPHA_HYPOTHESIS_REGISTRY_CONTRACT
    contract_version: str = ALPHA_HYPOTHESIS_REGISTRY_VERSION
    registry_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if self.contract_name != ALPHA_HYPOTHESIS_REGISTRY_CONTRACT or self.contract_version != ALPHA_HYPOTHESIS_REGISTRY_VERSION:
            raise ValueError("unsupported alpha-hypothesis registry contract")
        records = tuple(sorted(self.records, key=lambda item: (item.hypothesis_id, item.revision)))
        keys = tuple((item.hypothesis_id, item.revision) for item in records)
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate hypothesis identity/revision")
        identities = tuple(item.identity for item in records)
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate hypothesis content identity")
        by_id: dict[str, list[AlphaHypothesis]] = {}
        for item in records:
            by_id.setdefault(item.hypothesis_id, []).append(item)
        for revisions in by_id.values():
            expected = list(range(1, len(revisions) + 1))
            if [item.revision for item in revisions] != expected:
                raise ValueError("hypothesis revisions must be contiguous from 1")
            for previous, current in zip(revisions, revisions[1:]):
                if current.parent_revision_identity != previous.identity:
                    raise ValueError("hypothesis revision parent identity mismatch")
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "registry_fingerprint", _hash({
            "contract": {"name": self.contract_name, "version": self.contract_version},
            "record_identities": identities,
        }))

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract_name": self.contract_name,
            "contract_version": self.contract_version,
            "records": [item.as_dict() for item in self.records],
            "registry_fingerprint": self.registry_fingerprint,
        }


def transition_hypothesis(
    hypothesis: AlphaHypothesis,
    to_status: HypothesisStatus,
    *,
    occurred_at_utc: str,
    reason: str,
    assessments: ResearchBoundaryAssessments | None = None,
) -> AlphaHypothesis:
    if to_status not in _ALLOWED_TRANSITIONS[hypothesis.status]:
        raise ValueError(f"invalid hypothesis transition: {hypothesis.status.value} -> {to_status.value}")
    registration = hypothesis.registration_timestamp_utc
    if to_status is HypothesisStatus.REGISTERED:
        registration = _utc_timestamp(occurred_at_utc, name="registration timestamp")
    return replace(
        hypothesis,
        status=to_status,
        registration_timestamp_utc=registration,
        assessments=hypothesis.assessments if assessments is None else assessments,
        lifecycle_history=(*hypothesis.lifecycle_history, LifecycleEvent(hypothesis.status, to_status, occurred_at_utc, reason)),
    )


def revise_hypothesis(
    hypothesis: AlphaHypothesis,
    *,
    created_at_utc: str,
    reason: str,
    changes: Mapping[str, Any],
) -> AlphaHypothesis:
    forbidden = {"hypothesis_id", "registry_schema_version", "revision", "status", "identity", "specification_fingerprint", "lifecycle_history", "parent_revision_identity", "registration_timestamp_utc", "assessments"}
    unsupported = tuple(sorted(set(changes) & forbidden))
    if unsupported:
        raise ValueError(f"revision cannot replace governance fields: {', '.join(unsupported)}")
    unknown = tuple(sorted(set(changes) - set(AlphaHypothesis.__dataclass_fields__)))
    if unknown:
        raise ValueError(f"unknown hypothesis revision fields: {', '.join(unknown)}")
    retrospective = tuple(item for item in hypothesis.evidence_references if item.origin is EvidenceOrigin.RETROSPECTIVE)
    return replace(
        hypothesis,
        **dict(changes),
        created_at_utc=created_at_utc,
        revision=hypothesis.revision + 1,
        status=HypothesisStatus.DRAFT,
        registration_timestamp_utc=None,
        evidence_references=retrospective,
        assessments=ResearchBoundaryAssessments(),
        parent_revision_identity=hypothesis.identity,
        lifecycle_history=(LifecycleEvent(None, HypothesisStatus.DRAFT, created_at_utc, reason),),
    )


def _optional_window(value: Mapping[str, Any] | None) -> ProspectiveEvaluationWindow | None:
    return None if value is None else ProspectiveEvaluationWindow(**value)


def _optional_effect(value: Mapping[str, Any] | None) -> MinimumEconomicEffect | None:
    return None if value is None else MinimumEconomicEffect(**value)


def _record_from_dict(value: Mapping[str, Any]) -> AlphaHypothesis:
    supplied_fingerprint = value.get("specification_fingerprint")
    supplied_identity = value.get("identity")
    record = AlphaHypothesis(
        hypothesis_id=value["hypothesis_id"],
        registry_schema_version=value["registry_schema_version"],
        created_at_utc=value["created_at_utc"],
        revision=value["revision"],
        research_question=value["research_question"],
        factor_policy_reference=value["factor_policy_reference"],
        factor_policy_version=value.get("factor_policy_version"),
        expected_relationship=value.get("expected_relationship"),
        expected_sign=ExpectedSign(value.get("expected_sign", "UNKNOWN")),
        universe_definition=value.get("universe_definition"),
        universe_limitations=tuple(value.get("universe_limitations", ())),
        feature_availability_timing=value.get("feature_availability_timing"),
        outcome_definition=value.get("outcome_definition"),
        evaluation_horizons_sessions=tuple(value.get("evaluation_horizons_sessions", ())),
        historical_discovery_cutoff=value.get("historical_discovery_cutoff"),
        registration_timestamp_utc=value.get("registration_timestamp_utc"),
        evidence_references=tuple(EvidenceReference(
            origin=EvidenceOrigin(item["origin"]),
            source_path=item["source_path"],
            source_identity=item["source_identity"],
            source_fingerprint=item.get("source_fingerprint"),
            observed_through_date=item["observed_through_date"],
            evidence_started_at_utc=item.get("evidence_started_at_utc"),
        ) for item in value.get("evidence_references", ())),
        evidence_family=value.get("evidence_family"),
        prospective_evaluation_window=_optional_window(value.get("prospective_evaluation_window")),
        minimum_sample_count=value.get("minimum_sample_count"),
        minimum_coverage_pct=value.get("minimum_coverage_pct"),
        minimum_economic_effect=_optional_effect(value.get("minimum_economic_effect")),
        falsification_conditions=tuple(value.get("falsification_conditions", ())),
        methodological_limitations=tuple(value.get("methodological_limitations", ())),
        dependencies=tuple(value.get("dependencies", ())),
        status=HypothesisStatus(value["status"]),
        assessments=ResearchBoundaryAssessments(**{
            key: AssessmentState(item)
            for key, item in value.get("assessments", {}).items()
        }),
        parent_revision_identity=value.get("parent_revision_identity"),
        lifecycle_history=tuple(LifecycleEvent(
            None if item.get("from_status") is None else HypothesisStatus(item["from_status"]),
            HypothesisStatus(item["to_status"]), item["occurred_at_utc"], item["reason"],
        ) for item in value["lifecycle_history"]),
    )
    if supplied_fingerprint is not None and supplied_fingerprint != record.specification_fingerprint:
        raise ValueError("persisted hypothesis specification fingerprint mismatch")
    if supplied_identity is not None and supplied_identity != record.identity:
        raise ValueError("persisted hypothesis identity mismatch")
    return record


def load_alpha_hypothesis_registry(path: str | Path) -> AlphaHypothesisRegistry:
    source = Path(path).expanduser().resolve()
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("contract_name") != ALPHA_HYPOTHESIS_REGISTRY_CONTRACT or payload.get("contract_version") != ALPHA_HYPOTHESIS_REGISTRY_VERSION:
        raise ValueError("unsupported persisted alpha-hypothesis registry")
    registry = AlphaHypothesisRegistry(tuple(_record_from_dict(item) for item in payload.get("records", ())))
    supplied = payload.get("registry_fingerprint")
    if supplied is not None and supplied != registry.registry_fingerprint:
        raise ValueError("persisted registry fingerprint mismatch")
    return registry
