from __future__ import annotations

"""Evidence-free governance contract for prospective falsification.

This module records methodology chosen before prospective evidence is
evaluated.  It deliberately contains no statistical estimator, resampler,
strategy rule, or evidence-store integration.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import math
from typing import Any

from quantlab.identity import canonical_identity_value, canonical_json

from .registry import (
    AlphaHypothesis,
    AssessmentState,
    EvidenceOrigin,
    ExpectedSign,
    HypothesisStatus,
    MinimumEconomicEffect,
    ProspectiveEvaluationWindow,
    ResearchBoundaryAssessments,
)


FALSIFICATION_PROTOCOL_CONTRACT = "quantlab.prospective_falsification_protocol"
FALSIFICATION_PROTOCOL_VERSION = "v1"


class FalsificationProtocolStatus(str, Enum):
    DRAFT = "DRAFT"
    FROZEN = "FROZEN"


class EvaluationDisposition(str, Enum):
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    INCONCLUSIVE = "INCONCLUSIVE"
    FALSIFIED = "FALSIFIED"
    SUPPORTED = "SUPPORTED"
    INVALID_EVIDENCE = "INVALID_EVIDENCE"


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _text(value: Any, *, name: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{name} must be non-empty")
    return result


def _optional_text(value: Any, *, name: str) -> str | None:
    return None if value is None else _text(value, name=name)


def _utc_timestamp(value: Any, *, name: str) -> str:
    text = _text(value, name=name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{name} must be UTC")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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
class DecisionCriterion:
    metric: str
    operator: str
    threshold: float
    unit: str

    def __post_init__(self) -> None:
        if self.operator not in {"GT", "GTE", "LT", "LTE", "ABS_GTE"}:
            raise ValueError("unsupported decision-criterion operator")
        if isinstance(self.threshold, bool) or not math.isfinite(float(self.threshold)):
            raise ValueError("decision-criterion threshold must be finite")
        object.__setattr__(self, "metric", _text(self.metric, name="decision-criterion metric"))
        object.__setattr__(self, "threshold", float(self.threshold))
        object.__setattr__(self, "unit", _text(self.unit, name="decision-criterion unit"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "operator": self.operator,
            "threshold": self.threshold,
            "unit": self.unit,
        }


@dataclass(frozen=True, slots=True)
class MinimumEvidenceRequirements:
    required_sample_size: int | None
    minimum_coverage_pct: float | None
    missing_data_treatment: str | None
    observation_eligibility: str | None
    maturity_requirement: str | None
    continuity_gap_handling: str | None
    minimum_economic_effect: MinimumEconomicEffect | None
    support_criterion: DecisionCriterion | None
    falsification_criterion: DecisionCriterion | None

    def __post_init__(self) -> None:
        if self.required_sample_size is not None and (
            isinstance(self.required_sample_size, bool) or self.required_sample_size <= 0
        ):
            raise ValueError("required sample size must be positive")
        if self.minimum_coverage_pct is not None:
            coverage = float(self.minimum_coverage_pct)
            if not math.isfinite(coverage) or not 0 < coverage <= 100:
                raise ValueError("minimum coverage percentage must be in (0, 100]")
            object.__setattr__(self, "minimum_coverage_pct", coverage)
        for name in (
            "missing_data_treatment",
            "observation_eligibility",
            "maturity_requirement",
            "continuity_gap_handling",
        ):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name=name.replace("_", " ")))

    def unresolved_declarations(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in (
                "required_sample_size",
                "minimum_coverage_pct",
                "missing_data_treatment",
                "observation_eligibility",
                "maturity_requirement",
                "continuity_gap_handling",
                "minimum_economic_effect",
                "support_criterion",
                "falsification_criterion",
            )
            if getattr(self, name) is None
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "required_sample_size": self.required_sample_size,
            "minimum_coverage_pct": self.minimum_coverage_pct,
            "missing_data_treatment": self.missing_data_treatment,
            "observation_eligibility": self.observation_eligibility,
            "maturity_requirement": self.maturity_requirement,
            "continuity_gap_handling": self.continuity_gap_handling,
            "minimum_economic_effect": None if self.minimum_economic_effect is None else self.minimum_economic_effect.as_dict(),
            "support_criterion": None if self.support_criterion is None else self.support_criterion.as_dict(),
            "falsification_criterion": None if self.falsification_criterion is None else self.falsification_criterion.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class DependenceMethodology:
    overlapping_horizon_treatment: str | None
    same_date_cross_section_treatment: str | None
    serial_dependence_treatment: str | None
    confidence_interval_method: str | None
    block_or_cluster_resampling_unit: str | None
    multiple_testing_family: str | None
    multiplicity_adjustment: str | None
    effective_information_method: str | None

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            object.__setattr__(self, name, _optional_text(getattr(self, name), name=name.replace("_", " ")))

    def unresolved_declarations(self) -> tuple[str, ...]:
        return tuple(name for name in self.__dataclass_fields__ if getattr(self, name) is None)

    def as_dict(self) -> dict[str, str | None]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class EconomicValidityDeclarations:
    gross_association_definition: str | None
    benchmark_relative_definition: str | None
    transaction_friction_treatment: str | None
    executable_net_performance_treatment: str | None
    inherited_limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "gross_association_definition",
            "benchmark_relative_definition",
            "transaction_friction_treatment",
            "executable_net_performance_treatment",
        ):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name=name.replace("_", " ")))
        object.__setattr__(self, "inherited_limitations", _tuple_text(
            self.inherited_limitations, name="inherited limitations", allow_empty=True,
        ))

    def unresolved_declarations(self) -> tuple[str, ...]:
        missing = tuple(
            name
            for name in (
                "gross_association_definition",
                "benchmark_relative_definition",
                "transaction_friction_treatment",
                "executable_net_performance_treatment",
            )
            if getattr(self, name) is None
        )
        return (*missing, *(("inherited_limitations",) if not self.inherited_limitations else ()))

    def as_dict(self) -> dict[str, Any]:
        return {
            "gross_association_definition": self.gross_association_definition,
            "benchmark_relative_definition": self.benchmark_relative_definition,
            "transaction_friction_treatment": self.transaction_friction_treatment,
            "executable_net_performance_treatment": self.executable_net_performance_treatment,
            "inherited_limitations": self.inherited_limitations,
        }


@dataclass(frozen=True, slots=True)
class FalsificationProtocol:
    protocol_id: str
    protocol_version: str
    created_at_utc: str
    revision: int
    hypothesis_id: str
    hypothesis_revision: int
    parent_hypothesis_specification_fingerprint: str
    expected_sign: ExpectedSign
    testable_null: str | None
    evaluation_universe: str | None
    universe_limitations: tuple[str, ...]
    feature_formation_and_availability_timing: str | None
    prospective_evaluation_window: ProspectiveEvaluationWindow | None
    primary_outcome: str | None
    primary_benchmark: str | None
    primary_horizon_sessions: int | None
    evidence_family: str | None
    minimum_evidence: MinimumEvidenceRequirements
    dependence_methodology: DependenceMethodology
    economic_validity: EconomicValidityDeclarations
    status: FalsificationProtocolStatus
    frozen_at_utc: str | None
    parent_revision_identity: str | None
    specification_fingerprint: str = field(init=False)
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        if self.protocol_version != FALSIFICATION_PROTOCOL_VERSION:
            raise ValueError("unsupported falsification-protocol version")
        if isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("protocol revision must be a positive integer")
        if isinstance(self.hypothesis_revision, bool) or self.hypothesis_revision < 1:
            raise ValueError("hypothesis revision must be a positive integer")
        object.__setattr__(self, "protocol_id", _text(self.protocol_id, name="protocol id"))
        object.__setattr__(self, "hypothesis_id", _text(self.hypothesis_id, name="hypothesis id"))
        object.__setattr__(self, "created_at_utc", _utc_timestamp(self.created_at_utc, name="creation timestamp"))
        object.__setattr__(self, "parent_hypothesis_specification_fingerprint", _text(
            self.parent_hypothesis_specification_fingerprint, name="parent hypothesis specification fingerprint",
        ))
        object.__setattr__(self, "parent_revision_identity", _optional_text(
            self.parent_revision_identity, name="parent revision identity",
        ))
        for name in (
            "testable_null",
            "evaluation_universe",
            "feature_formation_and_availability_timing",
            "primary_outcome",
            "primary_benchmark",
            "evidence_family",
        ):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name=name.replace("_", " ")))
        object.__setattr__(self, "universe_limitations", _tuple_text(
            self.universe_limitations, name="universe limitations", allow_empty=True,
        ))
        if self.primary_horizon_sessions is not None and (
            isinstance(self.primary_horizon_sessions, bool) or self.primary_horizon_sessions <= 0
        ):
            raise ValueError("primary horizon must be a positive session count")
        if self.status is FalsificationProtocolStatus.DRAFT:
            if self.frozen_at_utc is not None:
                raise ValueError("draft protocol must not have a freeze timestamp")
        else:
            object.__setattr__(self, "frozen_at_utc", _utc_timestamp(self.frozen_at_utc, name="freeze timestamp"))
            unresolved = self.unresolved_declarations()
            if unresolved:
                raise ValueError(f"frozen protocol has unresolved declarations: {', '.join(unresolved)}")
        specification = self._specification_content()
        fingerprint = _hash(specification)
        identity = _hash({
            "contract": {"name": FALSIFICATION_PROTOCOL_CONTRACT, "version": FALSIFICATION_PROTOCOL_VERSION},
            "specification_fingerprint": fingerprint,
            "status": self.status.value,
            "frozen_at_utc": self.frozen_at_utc,
        })
        object.__setattr__(self, "specification_fingerprint", fingerprint)
        object.__setattr__(self, "identity", identity)

    def unresolved_declarations(self) -> tuple[str, ...]:
        missing = tuple(
            name
            for name in (
                "testable_null",
                "evaluation_universe",
                "feature_formation_and_availability_timing",
                "prospective_evaluation_window",
                "primary_outcome",
                "primary_benchmark",
                "primary_horizon_sessions",
                "evidence_family",
            )
            if getattr(self, name) is None
        )
        if self.expected_sign is ExpectedSign.UNKNOWN:
            missing = (*missing, "expected_sign")
        if not self.universe_limitations:
            missing = (*missing, "universe_limitations")
        return (
            *missing,
            *(f"minimum_evidence.{name}" for name in self.minimum_evidence.unresolved_declarations()),
            *(f"dependence_methodology.{name}" for name in self.dependence_methodology.unresolved_declarations()),
            *(f"economic_validity.{name}" for name in self.economic_validity.unresolved_declarations()),
        )

    def _specification_content(self) -> dict[str, Any]:
        return {
            "contract": {"name": FALSIFICATION_PROTOCOL_CONTRACT, "version": FALSIFICATION_PROTOCOL_VERSION},
            "protocol_id": self.protocol_id,
            "protocol_version": self.protocol_version,
            "created_at_utc": self.created_at_utc,
            "revision": self.revision,
            "hypothesis_id": self.hypothesis_id,
            "hypothesis_revision": self.hypothesis_revision,
            "parent_hypothesis_specification_fingerprint": self.parent_hypothesis_specification_fingerprint,
            "expected_sign": self.expected_sign.value,
            "testable_null": self.testable_null,
            "evaluation_universe": self.evaluation_universe,
            "universe_limitations": self.universe_limitations,
            "feature_formation_and_availability_timing": self.feature_formation_and_availability_timing,
            "prospective_evaluation_window": None if self.prospective_evaluation_window is None else self.prospective_evaluation_window.as_dict(),
            "primary_outcome": self.primary_outcome,
            "primary_benchmark": self.primary_benchmark,
            "primary_horizon_sessions": self.primary_horizon_sessions,
            "evidence_family": self.evidence_family,
            "minimum_evidence": self.minimum_evidence.as_dict(),
            "dependence_methodology": self.dependence_methodology.as_dict(),
            "economic_validity": self.economic_validity.as_dict(),
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


def validate_hypothesis_binding(protocol: FalsificationProtocol, hypothesis: AlphaHypothesis) -> None:
    mismatches: list[str] = []
    comparisons = (
        ("hypothesis_id", protocol.hypothesis_id, hypothesis.hypothesis_id),
        ("hypothesis_revision", protocol.hypothesis_revision, hypothesis.revision),
        (
            "parent_hypothesis_specification_fingerprint",
            protocol.parent_hypothesis_specification_fingerprint,
            hypothesis.specification_fingerprint,
        ),
        ("expected_sign", protocol.expected_sign, hypothesis.expected_sign),
        ("evaluation_universe", protocol.evaluation_universe, hypothesis.universe_definition),
        (
            "feature_formation_and_availability_timing",
            protocol.feature_formation_and_availability_timing,
            hypothesis.feature_availability_timing,
        ),
        ("prospective_evaluation_window", protocol.prospective_evaluation_window, hypothesis.prospective_evaluation_window),
        ("evidence_family", protocol.evidence_family, hypothesis.evidence_family),
    )
    for name, actual, expected in comparisons:
        if actual != expected:
            mismatches.append(name)
    if protocol.primary_horizon_sessions not in hypothesis.evaluation_horizons_sessions:
        mismatches.append("primary_horizon_sessions")
    inherited = set(protocol.universe_limitations) | set(protocol.economic_validity.inherited_limitations)
    required_limitations = set(hypothesis.universe_limitations) | set(hypothesis.methodological_limitations)
    if not required_limitations.issubset(inherited):
        mismatches.append("inherited_limitations")
    if mismatches:
        raise ValueError(f"protocol/hypothesis binding mismatch: {', '.join(mismatches)}")


def freeze_falsification_protocol(
    protocol: FalsificationProtocol,
    hypothesis: AlphaHypothesis,
    *,
    frozen_at_utc: str,
) -> FalsificationProtocol:
    if protocol.status is not FalsificationProtocolStatus.DRAFT:
        raise ValueError("only a draft protocol can be frozen")
    if hypothesis.status is not HypothesisStatus.REGISTERED:
        raise ValueError("protocol can be frozen only for a registered hypothesis")
    validate_hypothesis_binding(protocol, hypothesis)
    unresolved = protocol.unresolved_declarations()
    if unresolved:
        raise ValueError(f"protocol has unresolved declarations: {', '.join(unresolved)}")
    return replace(
        protocol,
        status=FalsificationProtocolStatus.FROZEN,
        frozen_at_utc=frozen_at_utc,
    )


def revise_falsification_protocol(
    protocol: FalsificationProtocol,
    *,
    created_at_utc: str,
    changes: dict[str, Any],
) -> FalsificationProtocol:
    if protocol.status is not FalsificationProtocolStatus.FROZEN:
        raise ValueError("only a frozen protocol can be revised")
    forbidden = {
        "protocol_id", "protocol_version", "revision", "status", "frozen_at_utc",
        "identity", "specification_fingerprint", "parent_revision_identity",
    }
    unsupported = tuple(sorted(set(changes) & forbidden))
    if unsupported:
        raise ValueError(f"revision cannot replace governance fields: {', '.join(unsupported)}")
    unknown = tuple(sorted(set(changes) - set(FalsificationProtocol.__dataclass_fields__)))
    if unknown:
        raise ValueError(f"unknown protocol revision fields: {', '.join(unknown)}")
    return replace(
        protocol,
        **changes,
        created_at_utc=created_at_utc,
        revision=protocol.revision + 1,
        status=FalsificationProtocolStatus.DRAFT,
        frozen_at_utc=None,
        parent_revision_identity=protocol.identity,
    )


@dataclass(frozen=True, slots=True)
class ProspectiveEvidencePreconditions:
    evidence_origin: EvidenceOrigin
    protocol_identity: str
    hypothesis_id: str
    hypothesis_revision: int
    parent_hypothesis_specification_fingerprint: str
    evidence_family: str
    contract_valid: bool
    evidence_usable: bool
    evaluation_window_complete: bool
    mature_eligible_sample_size: int
    coverage_pct: float | None
    continuity_requirement_met: bool | None
    dependence_method_applied: bool | None
    multiplicity_adjustment_applied: bool | None
    statistical_support_criterion_met: bool | None
    economic_effect_criterion_met: bool | None
    falsification_criterion_met: bool | None

    def __post_init__(self) -> None:
        for name in ("protocol_identity", "hypothesis_id", "parent_hypothesis_specification_fingerprint", "evidence_family"):
            object.__setattr__(self, name, _text(getattr(self, name), name=name.replace("_", " ")))
        if isinstance(self.hypothesis_revision, bool) or self.hypothesis_revision < 1:
            raise ValueError("hypothesis revision must be positive")
        if isinstance(self.mature_eligible_sample_size, bool) or self.mature_eligible_sample_size < 0:
            raise ValueError("mature eligible sample size must be non-negative")
        if self.coverage_pct is not None:
            coverage = float(self.coverage_pct)
            if not math.isfinite(coverage) or not 0 <= coverage <= 100:
                raise ValueError("observed coverage must be in [0, 100]")
            object.__setattr__(self, "coverage_pct", coverage)


@dataclass(frozen=True, slots=True)
class FalsificationDecision:
    disposition: EvaluationDisposition
    protocol_identity: str
    protocol_specification_fingerprint: str
    hypothesis_id: str
    hypothesis_revision: int
    reason_codes: tuple[str, ...]
    assessments: ResearchBoundaryAssessments = field(default_factory=ResearchBoundaryAssessments)
    capital_authorized: bool = False
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        if self.assessments.alpha_validated is not AssessmentState.NOT_ASSESSED:
            raise ValueError("falsification disposition cannot set alpha validation")
        if self.assessments.production_ready is not AssessmentState.NOT_ASSESSED:
            raise ValueError("falsification disposition cannot set production readiness")
        if self.capital_authorized:
            raise ValueError("falsification disposition cannot authorize capital")
        reasons = _tuple_text(self.reason_codes, name="decision reason codes")
        object.__setattr__(self, "reason_codes", reasons)
        object.__setattr__(self, "identity", _hash({
            "contract": {"name": FALSIFICATION_PROTOCOL_CONTRACT, "version": FALSIFICATION_PROTOCOL_VERSION},
            "disposition": self.disposition.value,
            "protocol_identity": self.protocol_identity,
            "protocol_specification_fingerprint": self.protocol_specification_fingerprint,
            "hypothesis_id": self.hypothesis_id,
            "hypothesis_revision": self.hypothesis_revision,
            "reason_codes": reasons,
            "assessments": self.assessments.as_dict(),
            "capital_authorized": self.capital_authorized,
        }))


def classify_prospective_evidence(
    protocol: FalsificationProtocol,
    evidence: ProspectiveEvidencePreconditions,
) -> FalsificationDecision:
    invalid: list[str] = []
    if protocol.status is not FalsificationProtocolStatus.FROZEN or protocol.unresolved_declarations():
        invalid.append("PROTOCOL_NOT_FROZEN_AND_COMPLETE")
    if evidence.evidence_origin is not EvidenceOrigin.PROSPECTIVE:
        invalid.append("NON_PROSPECTIVE_EVIDENCE")
    bindings = (
        (evidence.protocol_identity, protocol.identity),
        (evidence.hypothesis_id, protocol.hypothesis_id),
        (evidence.hypothesis_revision, protocol.hypothesis_revision),
        (evidence.parent_hypothesis_specification_fingerprint, protocol.parent_hypothesis_specification_fingerprint),
        (evidence.evidence_family, protocol.evidence_family),
    )
    if any(actual != expected for actual, expected in bindings):
        invalid.append("EVIDENCE_BINDING_MISMATCH")
    if not evidence.contract_valid:
        invalid.append("BROKEN_EVIDENCE_CONTRACT")
    if not evidence.evidence_usable:
        invalid.append("UNUSABLE_EVIDENCE")
    if evidence.dependence_method_applied is False:
        invalid.append("DEPENDENCE_METHOD_NOT_APPLIED")
    if evidence.multiplicity_adjustment_applied is False:
        invalid.append("MULTIPLICITY_ADJUSTMENT_NOT_APPLIED")
    if invalid:
        return _decision(protocol, EvaluationDisposition.INVALID_EVIDENCE, invalid)

    requirements = protocol.minimum_evidence
    insufficient: list[str] = []
    if not evidence.evaluation_window_complete:
        insufficient.append("EVALUATION_WINDOW_IMMATURE")
    if evidence.mature_eligible_sample_size < requirements.required_sample_size:
        insufficient.append("MINIMUM_SAMPLE_NOT_MET")
    if evidence.coverage_pct is None or evidence.coverage_pct < requirements.minimum_coverage_pct:
        insufficient.append("MINIMUM_COVERAGE_NOT_MET")
    if evidence.continuity_requirement_met is not True:
        insufficient.append("CONTINUITY_REQUIREMENT_NOT_MET")
    if insufficient:
        return _decision(protocol, EvaluationDisposition.INSUFFICIENT_EVIDENCE, insufficient)

    if evidence.dependence_method_applied is not True or evidence.multiplicity_adjustment_applied is not True:
        return _decision(protocol, EvaluationDisposition.INVALID_EVIDENCE, ("REQUIRED_INFERENCE_METHOD_UNCONFIRMED",))
    if evidence.falsification_criterion_met is True:
        return _decision(protocol, EvaluationDisposition.FALSIFIED, ("PREDECLARED_FALSIFICATION_CRITERION_MET",))
    if (
        evidence.statistical_support_criterion_met is True
        and evidence.economic_effect_criterion_met is True
        and evidence.falsification_criterion_met is False
    ):
        return _decision(protocol, EvaluationDisposition.SUPPORTED, ("ALL_PREDECLARED_SUPPORT_CRITERIA_MET",))
    return _decision(protocol, EvaluationDisposition.INCONCLUSIVE, ("BETWEEN_PREDECLARED_DECISION_BOUNDARIES",))


def _decision(
    protocol: FalsificationProtocol,
    disposition: EvaluationDisposition,
    reasons: tuple[str, ...] | list[str],
) -> FalsificationDecision:
    return FalsificationDecision(
        disposition=disposition,
        protocol_identity=protocol.identity,
        protocol_specification_fingerprint=protocol.specification_fingerprint,
        hypothesis_id=protocol.hypothesis_id,
        hypothesis_revision=protocol.hypothesis_revision,
        reason_codes=tuple(reasons),
    )


def validate_disposition_transition(
    previous: EvaluationDisposition,
    current: EvaluationDisposition,
) -> None:
    terminal = {
        EvaluationDisposition.FALSIFIED,
        EvaluationDisposition.SUPPORTED,
        EvaluationDisposition.INVALID_EVIDENCE,
    }
    if previous in terminal and current is not previous:
        raise ValueError(f"terminal disposition cannot transition: {previous.value} -> {current.value}")
