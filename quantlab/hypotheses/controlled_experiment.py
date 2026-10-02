from __future__ import annotations

"""Immutable design contract for future prospective controlled experiments.

The contract binds methodology and provenance.  It does not read evidence,
perform inference, run a strategy, or mutate any research/runtime store.
"""

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from enum import Enum
from hashlib import sha256
from typing import Any, Mapping

from quantlab.identity import canonical_identity_value, canonical_json

from .falsification import (
    DecisionCriterion,
    EvaluationDisposition,
    FalsificationProtocol,
    FalsificationProtocolStatus,
    validate_hypothesis_binding,
)
from .registry import (
    AlphaHypothesis,
    AssessmentState,
    EvidenceOrigin,
    HypothesisStatus,
    MinimumEconomicEffect,
    ResearchBoundaryAssessments,
)


CONTROLLED_EXPERIMENT_CONTRACT = "quantlab.controlled_experiment_design"
CONTROLLED_EXPERIMENT_VERSION = "v1"


class ControlledExperimentStatus(str, Enum):
    DRAFT = "DRAFT"
    FROZEN = "FROZEN"


class ExperimentExecutionStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ExperimentEvidenceValidity(str, Enum):
    NOT_ASSESSED = "NOT_ASSESSED"
    VALID = "VALID"
    INVALID = "INVALID"
    INSUFFICIENT = "INSUFFICIENT"


class EconomicInterpretation(str, Enum):
    NOT_ASSESSED = "NOT_ASSESSED"
    GROSS_ASSOCIATION_ONLY = "GROSS_ASSOCIATION_ONLY"
    BENCHMARK_RELATIVE_ASSOCIATION = "BENCHMARK_RELATIVE_ASSOCIATION"
    FRICTION_SENSITIVITY_ESTIMATE = "FRICTION_SENSITIVITY_ESTIMATE"
    EXECUTABLE_NET_PERFORMANCE = "EXECUTABLE_NET_PERFORMANCE"


class ValidityCheckKind(str, Enum):
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    EVIDENCE_FINGERPRINT_MISMATCH = "EVIDENCE_FINGERPRINT_MISMATCH"
    POST_FREEZE_SPECIFICATION_CHANGE = "POST_FREEZE_SPECIFICATION_CHANGE"
    TEMPORAL_LEAKAGE = "TEMPORAL_LEAKAGE"
    DUPLICATE_OBSERVATION = "DUPLICATE_OBSERVATION"
    INSUFFICIENT_MATURITY = "INSUFFICIENT_MATURITY"
    COVERAGE_FAILURE = "COVERAGE_FAILURE"
    CONTINUITY_GAP = "CONTINUITY_GAP"
    INVALID_STATISTICAL_METHOD = "INVALID_STATISTICAL_METHOD"
    CORPORATE_ACTION_PROVENANCE = "CORPORATE_ACTION_PROVENANCE"
    HISTORICAL_UNIVERSE_LIMITATION = "HISTORICAL_UNIVERSE_LIMITATION"


class ValidityConsequence(str, Enum):
    INVALID_EVIDENCE = "INVALID_EVIDENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    LIMITATION_DISCLOSURE = "LIMITATION_DISCLOSURE"


_REQUIRED_VALIDITY_CHECKS = frozenset(ValidityCheckKind)


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _text(value: Any, *, name: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{name} must be non-empty")
    return result


def _optional_text(value: Any, *, name: str) -> str | None:
    return None if value is None else _text(value, name=name)


def _date(value: Any, *, name: str) -> str:
    text = _text(value, name=name)
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"{name} must use YYYY-MM-DD") from exc


def _optional_date(value: Any, *, name: str) -> str | None:
    return None if value is None else _date(value, name=name)


def _timestamp(value: Any, *, name: str) -> str:
    text = _text(value, name=name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{name} must be UTC")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _optional_timestamp(value: Any, *, name: str) -> str | None:
    return None if value is None else _timestamp(value, name=name)


def _tuple_text(values: Any, *, name: str, allow_empty: bool = False) -> tuple[str, ...]:
    if values is None or isinstance(values, str):
        raise TypeError(f"{name} must be a sequence of strings")
    result = tuple(_text(item, name=name) for item in values)
    if not result and not allow_empty:
        raise ValueError(f"{name} must not be empty")
    if len(set(result)) != len(result):
        raise ValueError(f"{name} must not contain duplicates")
    return result


@dataclass(frozen=True, slots=True)
class ExperimentEvidenceSource:
    source_name: str
    contract_name: str
    contract_version: str
    source_identity: str
    source_fingerprint: str
    evidence_origin: EvidenceOrigin
    eligible_after_session: str

    def __post_init__(self) -> None:
        for name in ("source_name", "contract_name", "contract_version", "source_identity", "source_fingerprint"):
            object.__setattr__(self, name, _text(getattr(self, name), name=name.replace("_", " ")))
        object.__setattr__(self, "eligible_after_session", _date(
            self.eligible_after_session, name="eligible-after session",
        ))

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "contract_name": self.contract_name,
            "contract_version": self.contract_version,
            "source_identity": self.source_identity,
            "source_fingerprint": self.source_fingerprint,
            "evidence_origin": self.evidence_origin.value,
            "eligible_after_session": self.eligible_after_session,
        }


@dataclass(frozen=True, slots=True)
class ExperimentTemporalIntegrity:
    historical_discovery_cutoff: str | None
    registration_timestamp_utc: str | None
    protocol_freeze_timestamp_utc: str | None
    prospective_evaluation_start_session: str | None
    evaluation_end_session: str | None
    stopping_rule: str | None
    outcome_maturity_rule: str | None
    observation_eligibility: str | None
    retrospective_exclusion_rule: str | None

    def __post_init__(self) -> None:
        for name in (
            "historical_discovery_cutoff",
            "prospective_evaluation_start_session",
            "evaluation_end_session",
        ):
            object.__setattr__(self, name, _optional_date(getattr(self, name), name=name.replace("_", " ")))
        for name in ("registration_timestamp_utc", "protocol_freeze_timestamp_utc"):
            object.__setattr__(self, name, _optional_timestamp(getattr(self, name), name=name.replace("_", " ")))
        for name in ("stopping_rule", "outcome_maturity_rule", "observation_eligibility", "retrospective_exclusion_rule"):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name=name.replace("_", " ")))
        if self.evaluation_end_session is not None and self.stopping_rule is not None:
            raise ValueError("experiment must declare an evaluation end or stopping rule, not both")
        if self.historical_discovery_cutoff and self.prospective_evaluation_start_session:
            if self.prospective_evaluation_start_session <= self.historical_discovery_cutoff:
                raise ValueError("prospective evaluation must start after the historical discovery cutoff")
        if self.registration_timestamp_utc and self.protocol_freeze_timestamp_utc:
            if self.protocol_freeze_timestamp_utc < self.registration_timestamp_utc:
                raise ValueError("protocol freeze must not predate hypothesis registration")
        if self.protocol_freeze_timestamp_utc and self.prospective_evaluation_start_session:
            freeze_date = self.protocol_freeze_timestamp_utc[:10]
            if self.prospective_evaluation_start_session <= freeze_date:
                raise ValueError("prospective evaluation must begin after protocol freeze")
        if self.evaluation_end_session and self.prospective_evaluation_start_session:
            if self.evaluation_end_session < self.prospective_evaluation_start_session:
                raise ValueError("evaluation end must not predate evaluation start")

    def unresolved_declarations(self) -> tuple[str, ...]:
        required = (
            "historical_discovery_cutoff",
            "registration_timestamp_utc",
            "protocol_freeze_timestamp_utc",
            "prospective_evaluation_start_session",
            "outcome_maturity_rule",
            "observation_eligibility",
            "retrospective_exclusion_rule",
        )
        missing = tuple(name for name in required if getattr(self, name) is None)
        if self.evaluation_end_session is None and self.stopping_rule is None:
            missing = (*missing, "evaluation_end_session_or_stopping_rule")
        return missing

    def as_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class ControlledComparison:
    primary_hypothesis: str | None
    null_hypothesis: str | None
    benchmark: str | None
    primary_outcome: str | None
    primary_horizon_sessions: int | None
    universe: str | None
    sample_eligibility: str | None
    missing_observation_treatment: str | None
    dependence_methodology_fingerprint: str | None
    minimum_economic_effect: MinimumEconomicEffect | None
    support_criterion: DecisionCriterion | None
    falsification_criterion: DecisionCriterion | None

    def __post_init__(self) -> None:
        for name in (
            "primary_hypothesis", "null_hypothesis", "benchmark", "primary_outcome",
            "universe", "sample_eligibility", "missing_observation_treatment",
            "dependence_methodology_fingerprint",
        ):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name=name.replace("_", " ")))
        if self.primary_horizon_sessions is not None and (
            isinstance(self.primary_horizon_sessions, bool) or self.primary_horizon_sessions <= 0
        ):
            raise ValueError("primary horizon must be a positive session count")
        if _criteria_conflict(self.support_criterion, self.falsification_criterion):
            raise ValueError("support and falsification declarations conflict")

    def unresolved_declarations(self) -> tuple[str, ...]:
        return tuple(name for name in self.__dataclass_fields__ if getattr(self, name) is None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "primary_hypothesis": self.primary_hypothesis,
            "null_hypothesis": self.null_hypothesis,
            "benchmark": self.benchmark,
            "primary_outcome": self.primary_outcome,
            "primary_horizon_sessions": self.primary_horizon_sessions,
            "universe": self.universe,
            "sample_eligibility": self.sample_eligibility,
            "missing_observation_treatment": self.missing_observation_treatment,
            "dependence_methodology_fingerprint": self.dependence_methodology_fingerprint,
            "minimum_economic_effect": None if self.minimum_economic_effect is None else self.minimum_economic_effect.as_dict(),
            "support_criterion": None if self.support_criterion is None else self.support_criterion.as_dict(),
            "falsification_criterion": None if self.falsification_criterion is None else self.falsification_criterion.as_dict(),
        }


def _criteria_conflict(
    support: DecisionCriterion | None,
    falsification: DecisionCriterion | None,
) -> bool:
    if support is None or falsification is None:
        return False
    if support.metric != falsification.metric or support.unit != falsification.unit:
        return False
    if support.as_dict() == falsification.as_dict():
        return True
    if "ABS_GTE" in {support.operator, falsification.operator}:
        return True
    lower_ops = {"GT", "GTE"}
    upper_ops = {"LT", "LTE"}
    if support.operator in lower_ops and falsification.operator in lower_ops:
        return True
    if support.operator in upper_ops and falsification.operator in upper_ops:
        return True
    lower = support if support.operator in lower_ops else falsification
    upper = falsification if falsification.operator in upper_ops else support
    if lower.operator not in lower_ops or upper.operator not in upper_ops:
        return True
    if lower.threshold < upper.threshold:
        return True
    if lower.threshold > upper.threshold:
        return False
    return lower.operator == "GTE" and upper.operator == "LTE"


@dataclass(frozen=True, slots=True)
class ExperimentValidityCheck:
    kind: ValidityCheckKind
    detection_rule: str
    consequence: ValidityConsequence

    def __post_init__(self) -> None:
        object.__setattr__(self, "detection_rule", _text(self.detection_rule, name="validity detection rule"))

    def as_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "detection_rule": self.detection_rule,
            "consequence": self.consequence.value,
        }


@dataclass(frozen=True, slots=True)
class ControlledExperiment:
    experiment_id: str
    schema_version: str
    created_at_utc: str
    revision: int
    hypothesis_id: str
    hypothesis_revision: int
    hypothesis_specification_fingerprint: str
    falsification_protocol_id: str
    falsification_protocol_revision: int
    falsification_protocol_specification_fingerprint: str
    falsification_protocol_identity: str
    evidence_sources: tuple[ExperimentEvidenceSource, ...]
    expected_evidence_identity_rule: str | None
    temporal_integrity: ExperimentTemporalIntegrity
    controlled_comparison: ControlledComparison
    validity_checks: tuple[ExperimentValidityCheck, ...]
    inherited_limitations: tuple[str, ...]
    status: ControlledExperimentStatus
    frozen_at_utc: str | None
    parent_revision_identity: str | None
    specification_fingerprint: str = field(init=False)
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != CONTROLLED_EXPERIMENT_VERSION:
            raise ValueError("unsupported controlled-experiment schema version")
        if isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("experiment revision must be positive")
        if isinstance(self.hypothesis_revision, bool) or self.hypothesis_revision < 1:
            raise ValueError("hypothesis revision must be positive")
        if isinstance(self.falsification_protocol_revision, bool) or self.falsification_protocol_revision < 1:
            raise ValueError("protocol revision must be positive")
        for name in (
            "experiment_id", "hypothesis_id", "hypothesis_specification_fingerprint",
            "falsification_protocol_id", "falsification_protocol_specification_fingerprint",
            "falsification_protocol_identity",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name=name.replace("_", " ")))
        object.__setattr__(self, "created_at_utc", _timestamp(self.created_at_utc, name="creation timestamp"))
        object.__setattr__(self, "expected_evidence_identity_rule", _optional_text(
            self.expected_evidence_identity_rule, name="expected evidence identity rule",
        ))
        sources = tuple(sorted(self.evidence_sources, key=lambda item: item.source_name))
        if len({item.source_name for item in sources}) != len(sources):
            raise ValueError("duplicate experiment evidence source")
        object.__setattr__(self, "evidence_sources", sources)
        checks = tuple(sorted(self.validity_checks, key=lambda item: item.kind.value))
        if len({item.kind for item in checks}) != len(checks):
            raise ValueError("duplicate experiment validity check")
        object.__setattr__(self, "validity_checks", checks)
        object.__setattr__(self, "inherited_limitations", tuple(sorted(_tuple_text(
            self.inherited_limitations, name="inherited limitations", allow_empty=True,
        ))))
        object.__setattr__(self, "parent_revision_identity", _optional_text(
            self.parent_revision_identity, name="parent revision identity",
        ))
        if self.status is ControlledExperimentStatus.DRAFT:
            if self.frozen_at_utc is not None:
                raise ValueError("draft experiment must not have a freeze timestamp")
        else:
            object.__setattr__(self, "frozen_at_utc", _timestamp(self.frozen_at_utc, name="freeze timestamp"))
            unresolved = self.unresolved_declarations()
            if unresolved:
                raise ValueError(f"frozen experiment has unresolved declarations: {', '.join(unresolved)}")
        specification = self._specification_content()
        fingerprint = _hash(specification)
        object.__setattr__(self, "specification_fingerprint", fingerprint)
        object.__setattr__(self, "identity", _hash({
            "contract": {"name": CONTROLLED_EXPERIMENT_CONTRACT, "version": CONTROLLED_EXPERIMENT_VERSION},
            "specification_fingerprint": fingerprint,
            "status": self.status.value,
            "frozen_at_utc": self.frozen_at_utc,
        }))

    def unresolved_declarations(self) -> tuple[str, ...]:
        missing: tuple[str, ...] = ()
        if not self.evidence_sources:
            missing = (*missing, "evidence_sources")
        elif any(item.evidence_origin is not EvidenceOrigin.PROSPECTIVE for item in self.evidence_sources):
            missing = (*missing, "prospective_evidence_sources")
        if self.expected_evidence_identity_rule is None:
            missing = (*missing, "expected_evidence_identity_rule")
        if set(item.kind for item in self.validity_checks) != _REQUIRED_VALIDITY_CHECKS:
            missing = (*missing, "complete_validity_check_set")
        if not self.inherited_limitations:
            missing = (*missing, "inherited_limitations")
        return (
            *missing,
            *(f"temporal_integrity.{name}" for name in self.temporal_integrity.unresolved_declarations()),
            *(f"controlled_comparison.{name}" for name in self.controlled_comparison.unresolved_declarations()),
        )

    def _specification_content(self) -> dict[str, Any]:
        return {
            "contract": {"name": CONTROLLED_EXPERIMENT_CONTRACT, "version": CONTROLLED_EXPERIMENT_VERSION},
            "experiment_id": self.experiment_id,
            "schema_version": self.schema_version,
            "created_at_utc": self.created_at_utc,
            "revision": self.revision,
            "hypothesis_id": self.hypothesis_id,
            "hypothesis_revision": self.hypothesis_revision,
            "hypothesis_specification_fingerprint": self.hypothesis_specification_fingerprint,
            "falsification_protocol_id": self.falsification_protocol_id,
            "falsification_protocol_revision": self.falsification_protocol_revision,
            "falsification_protocol_specification_fingerprint": self.falsification_protocol_specification_fingerprint,
            "falsification_protocol_identity": self.falsification_protocol_identity,
            "evidence_sources": tuple(item.as_dict() for item in self.evidence_sources),
            "expected_evidence_identity_rule": self.expected_evidence_identity_rule,
            "temporal_integrity": self.temporal_integrity.as_dict(),
            "controlled_comparison": self.controlled_comparison.as_dict(),
            "validity_checks": tuple(item.as_dict() for item in self.validity_checks),
            "inherited_limitations": self.inherited_limitations,
            "parent_revision_identity": self.parent_revision_identity,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            **self._specification_content(),
            "status": self.status.value,
            "frozen_at_utc": self.frozen_at_utc,
            "unresolved_declarations": self.unresolved_declarations(),
            "specification_fingerprint": self.specification_fingerprint,
            "identity": self.identity,
        }


def freeze_controlled_experiment(
    experiment: ControlledExperiment,
    hypothesis: AlphaHypothesis,
    protocol: FalsificationProtocol,
    *,
    frozen_at_utc: str,
) -> ControlledExperiment:
    if experiment.status is not ControlledExperimentStatus.DRAFT:
        raise ValueError("only a draft experiment can be frozen")
    if hypothesis.status is not HypothesisStatus.REGISTERED:
        raise ValueError("experiment requires a registered hypothesis")
    if protocol.status is not FalsificationProtocolStatus.FROZEN:
        raise ValueError("experiment requires a frozen falsification protocol")
    validate_hypothesis_binding(protocol, hypothesis)
    mismatches: list[str] = []
    bindings = (
        ("hypothesis_id", experiment.hypothesis_id, hypothesis.hypothesis_id),
        ("hypothesis_revision", experiment.hypothesis_revision, hypothesis.revision),
        ("hypothesis_specification_fingerprint", experiment.hypothesis_specification_fingerprint, hypothesis.specification_fingerprint),
        ("falsification_protocol_id", experiment.falsification_protocol_id, protocol.protocol_id),
        ("falsification_protocol_revision", experiment.falsification_protocol_revision, protocol.revision),
        (
            "falsification_protocol_specification_fingerprint",
            experiment.falsification_protocol_specification_fingerprint,
            protocol.specification_fingerprint,
        ),
        ("falsification_protocol_identity", experiment.falsification_protocol_identity, protocol.identity),
    )
    mismatches.extend(name for name, actual, expected in bindings if actual != expected)
    temporal = experiment.temporal_integrity
    temporal_bindings = (
        ("historical_discovery_cutoff", temporal.historical_discovery_cutoff, hypothesis.historical_discovery_cutoff),
        ("registration_timestamp_utc", temporal.registration_timestamp_utc, hypothesis.registration_timestamp_utc),
        ("protocol_freeze_timestamp_utc", temporal.protocol_freeze_timestamp_utc, protocol.frozen_at_utc),
    )
    mismatches.extend(name for name, actual, expected in temporal_bindings if actual != expected)
    if protocol.prospective_evaluation_window is not None:
        window = protocol.prospective_evaluation_window
        if temporal.prospective_evaluation_start_session <= window.start_after_session:
            mismatches.append("prospective_evaluation_start_session")
        if temporal.evaluation_end_session is not None and temporal.evaluation_end_session > window.end_session:
            mismatches.append("evaluation_end_session")
        if any(source.eligible_after_session != window.start_after_session for source in experiment.evidence_sources):
            mismatches.append("evidence_source_eligible_after_session")
    expected_comparison = _comparison_from_protocol(hypothesis, protocol)
    if experiment.controlled_comparison != expected_comparison:
        mismatches.append("controlled_comparison")
    required_limitations = (
        set(hypothesis.universe_limitations)
        | set(hypothesis.methodological_limitations)
        | set(protocol.economic_validity.inherited_limitations)
    )
    if not required_limitations.issubset(set(experiment.inherited_limitations)):
        mismatches.append("inherited_limitations")
    if any(source.evidence_origin is not EvidenceOrigin.PROSPECTIVE for source in experiment.evidence_sources):
        mismatches.append("prospective_evidence_sources")
    if mismatches:
        raise ValueError(f"experiment binding mismatch: {', '.join(dict.fromkeys(mismatches))}")
    unresolved = experiment.unresolved_declarations()
    if unresolved:
        raise ValueError(f"experiment has unresolved declarations: {', '.join(unresolved)}")
    freeze_time = _timestamp(frozen_at_utc, name="experiment freeze timestamp")
    if freeze_time < protocol.frozen_at_utc:
        raise ValueError("experiment freeze must not predate protocol freeze")
    if freeze_time[:10] >= temporal.prospective_evaluation_start_session:
        raise ValueError("experiment must freeze before prospective evaluation starts")
    return replace(
        experiment,
        status=ControlledExperimentStatus.FROZEN,
        frozen_at_utc=freeze_time,
    )


def _comparison_from_protocol(
    hypothesis: AlphaHypothesis,
    protocol: FalsificationProtocol,
) -> ControlledComparison:
    return ControlledComparison(
        primary_hypothesis=hypothesis.expected_relationship,
        null_hypothesis=protocol.testable_null,
        benchmark=protocol.primary_benchmark,
        primary_outcome=protocol.primary_outcome,
        primary_horizon_sessions=protocol.primary_horizon_sessions,
        universe=protocol.evaluation_universe,
        sample_eligibility=protocol.minimum_evidence.observation_eligibility,
        missing_observation_treatment=protocol.minimum_evidence.missing_data_treatment,
        dependence_methodology_fingerprint=_hash(protocol.dependence_methodology.as_dict()),
        minimum_economic_effect=protocol.minimum_evidence.minimum_economic_effect,
        support_criterion=protocol.minimum_evidence.support_criterion,
        falsification_criterion=protocol.minimum_evidence.falsification_criterion,
    )


def revise_controlled_experiment(
    experiment: ControlledExperiment,
    *,
    created_at_utc: str,
    changes: Mapping[str, Any],
) -> ControlledExperiment:
    if experiment.status is not ControlledExperimentStatus.FROZEN:
        raise ValueError("only a frozen experiment can be revised")
    forbidden = {
        "experiment_id", "schema_version", "revision", "status", "frozen_at_utc",
        "identity", "specification_fingerprint", "parent_revision_identity",
    }
    unsupported = tuple(sorted(set(changes) & forbidden))
    if unsupported:
        raise ValueError(f"revision cannot replace governance fields: {', '.join(unsupported)}")
    unknown = tuple(sorted(set(changes) - set(ControlledExperiment.__dataclass_fields__)))
    if unknown:
        raise ValueError(f"unknown experiment revision fields: {', '.join(unknown)}")
    return replace(
        experiment,
        **dict(changes),
        created_at_utc=created_at_utc,
        revision=experiment.revision + 1,
        status=ControlledExperimentStatus.DRAFT,
        frozen_at_utc=None,
        parent_revision_identity=experiment.identity,
    )


@dataclass(frozen=True, slots=True)
class ControlledExperimentResult:
    experiment_identity: str
    experiment_specification_fingerprint: str
    execution_status: ExperimentExecutionStatus
    evidence_validity: ExperimentEvidenceValidity
    statistical_disposition: EvaluationDisposition | None
    economic_interpretation: EconomicInterpretation
    economic_interpretation_details: str | None
    assessments: ResearchBoundaryAssessments
    evidence_identities: tuple[str, ...]
    validity_findings: tuple[str, ...]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "experiment_identity", _text(self.experiment_identity, name="experiment identity"))
        object.__setattr__(self, "experiment_specification_fingerprint", _text(
            self.experiment_specification_fingerprint, name="experiment specification fingerprint",
        ))
        object.__setattr__(self, "economic_interpretation_details", _optional_text(
            self.economic_interpretation_details, name="economic interpretation details",
        ))
        identities = tuple(sorted(_tuple_text(self.evidence_identities, name="evidence identities", allow_empty=True)))
        findings = tuple(sorted(_tuple_text(self.validity_findings, name="validity findings", allow_empty=True)))
        object.__setattr__(self, "evidence_identities", identities)
        object.__setattr__(self, "validity_findings", findings)
        if self.execution_status is not ExperimentExecutionStatus.COMPLETED:
            if self.statistical_disposition is not None:
                raise ValueError("incomplete experiment cannot have a statistical disposition")
        elif self.evidence_validity is ExperimentEvidenceValidity.NOT_ASSESSED:
            raise ValueError("completed experiment requires an evidence-validity assessment")
        if self.execution_status is ExperimentExecutionStatus.COMPLETED:
            expected = {
                ExperimentEvidenceValidity.INVALID: EvaluationDisposition.INVALID_EVIDENCE,
                ExperimentEvidenceValidity.INSUFFICIENT: EvaluationDisposition.INSUFFICIENT_EVIDENCE,
            }
            if self.evidence_validity in expected and self.statistical_disposition is not expected[self.evidence_validity]:
                raise ValueError("evidence validity and statistical disposition conflict")
            if self.evidence_validity is ExperimentEvidenceValidity.VALID and self.statistical_disposition not in {
                EvaluationDisposition.INCONCLUSIVE,
                EvaluationDisposition.FALSIFIED,
                EvaluationDisposition.SUPPORTED,
            }:
                raise ValueError("valid completed evidence requires a substantive statistical disposition")
            if self.evidence_validity is ExperimentEvidenceValidity.VALID and not identities:
                raise ValueError("valid completed evidence requires at least one evidence identity")
        if self.economic_interpretation is not EconomicInterpretation.NOT_ASSESSED:
            if self.execution_status is not ExperimentExecutionStatus.COMPLETED:
                raise ValueError("economic interpretation requires completed execution")
            if self.evidence_validity is not ExperimentEvidenceValidity.VALID:
                raise ValueError("economic interpretation requires valid evidence")
            if self.economic_interpretation_details is None:
                raise ValueError("economic interpretation requires explicit details")
        object.__setattr__(self, "identity", _hash({
            "contract": {"name": CONTROLLED_EXPERIMENT_CONTRACT, "version": CONTROLLED_EXPERIMENT_VERSION},
            "experiment_identity": self.experiment_identity,
            "experiment_specification_fingerprint": self.experiment_specification_fingerprint,
            "execution_status": self.execution_status.value,
            "evidence_validity": self.evidence_validity.value,
            "statistical_disposition": None if self.statistical_disposition is None else self.statistical_disposition.value,
            "economic_interpretation": self.economic_interpretation.value,
            "economic_interpretation_details": self.economic_interpretation_details,
            "assessments": self.assessments.as_dict(),
            "evidence_identities": identities,
            "validity_findings": findings,
        }))


__all__ = (
    "CONTROLLED_EXPERIMENT_CONTRACT",
    "CONTROLLED_EXPERIMENT_VERSION",
    "ControlledComparison",
    "ControlledExperiment",
    "ControlledExperimentResult",
    "ControlledExperimentStatus",
    "EconomicInterpretation",
    "ExperimentEvidenceSource",
    "ExperimentEvidenceValidity",
    "ExperimentExecutionStatus",
    "ExperimentTemporalIntegrity",
    "ExperimentValidityCheck",
    "ValidityCheckKind",
    "ValidityConsequence",
    "freeze_controlled_experiment",
    "revise_controlled_experiment",
)
