from __future__ import annotations

"""Deterministic governance decisions over frozen Phase 10A/B/C evidence."""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Mapping

from quantlab.identity import canonical_identity_value, canonical_json


CONTRACT = "quantlab.risk_policy_decision_gate"
VERSION = "v1"
DECISION_GATE_TIMING = "POST_EVIDENCE_FORMALIZATION"
FORWARD_PROTOCOL_V1 = "QV-FWD-V1-8c60839fa29fc04e"

EXPECTED_PHASE10A_RESULT = "8a9aa6e11af005bb422d0065783af8beee22a94d20f0c35359825e8d9996eaee"
EXPECTED_PHASE10A_SPEC = "cea5b328d89a196dc11081c52e9b37378b017cdbfbb5ca3f2c494dd109882a25"
EXPECTED_PHASE10B_RESULT = "d0c0ca9db3473f6eeb61d6eb84b8a9683003ca8ae3513bc183188549e13d65fe"
EXPECTED_PHASE10B_SPEC = "9a0634ce2c5c604ddcbe64c49c96a0674ac00b8ed17ca4168426b7775b637414"
EXPECTED_PHASE10C_RESULT = "825388876f8655fdc7830c706ff0764d9b1018d2d2ced76a59fe54261e6cee49"
EXPECTED_PHASE10C_SPEC = "f8006fdfeecebb253bd9d5d769bbbd593f007213b613ab24144c5f90861520ab"
EXPECTED_POLICY_FINGERPRINTS = MappingProxyType({
    "NO_RISK_POLICY": "0384b8e21699e02f274a4bb60858097bcde44eca9cd2f6ae9ac5dcba36525c8d",
    "VOLATILITY_SCALING": "354067781f3906791f34d9b20329bac82907d4a18949a0108b04ad7f7e871aff",
})

DIMENSIONS = (
    "PROVENANCE_CONTRACT_INTEGRITY",
    "STRUCTURAL_VALIDITY",
    "DECLARED_RISK_PURPOSE_EFFECT",
    "EVIDENCE_COVERAGE",
    "ECONOMIC_TRADE_OFF",
    "TEMPORAL_CONSISTENCY",
    "PATH_EVIDENCE",
)

LIMITATIONS = (
    "the decision gate was formalized after Phase 10C evidence existed and is not independent prospective confirmation",
    "Phase 10B policy parameters and Phase 10C evidence remain frozen and immutable",
    "overlapping forward observations do not define an executable return path, Sharpe ratio, drawdown, or CAGR",
    "economic evidence is descriptive and does not rank or select a policy",
    "eligibility permits only design of a separate future protocol with a new identity and activation cutoff",
    "Forward V1 is immutable and is not modified, backfilled, or reinterpreted",
    "no production, paper-trading, scanner, execution, or live authorization is granted",
)


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


class RiskPolicyDecision(str, Enum):
    CONTROL_BASELINE = "CONTROL_BASELINE"
    REJECT_FOR_NOW = "REJECT_FOR_NOW"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    HOLD = "HOLD"
    ELIGIBLE_FOR_FUTURE_FORWARD_PROTOCOL = "ELIGIBLE_FOR_FUTURE_FORWARD_PROTOCOL"


class DecisionEvidenceStatus(str, Enum):
    SUPPORTIVE = "SUPPORTIVE"
    ADVERSE = "ADVERSE"
    TRADE_OFF_PRESENT = "TRADE_OFF_PRESENT"
    NO_MATERIAL_TRADE_OFF_EVIDENCE = "NO_MATERIAL_TRADE_OFF_EVIDENCE"
    INSUFFICIENT = "INSUFFICIENT"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    CONTROL_REFERENCE = "CONTROL_REFERENCE"


@dataclass(frozen=True, slots=True)
class RiskPolicyDecisionSpec:
    name: str = "NEUTRAL_RISK_POLICY_DECISION_GATE_V1"
    version: str = "1"
    decision_gate_timing: str = DECISION_GATE_TIMING
    dimensions: tuple[str, ...] = DIMENSIONS
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if self.version != "1" or self.decision_gate_timing != DECISION_GATE_TIMING or self.dimensions != DIMENSIONS:
            raise ValueError("unsupported risk-policy decision specification")
        object.__setattr__(self, "fingerprint", _hash({
            "contract": (CONTRACT, VERSION), "name": self.name, "version": self.version,
            "timing": self.decision_gate_timing, "dimensions": self.dimensions,
            "policy_purposes": {
                "NO_RISK_POLICY": "structural_control_baseline_no_risk_reduction_requirement",
                "VOLATILITY_SCALING": "reduce_exposure_and_ex_ante_volatility_above_frozen_20pct_target_without_leverage_or_relative_weight_change",
            },
            "coverage": "complete_frozen_summary_grids_with_defined_evidence_in_every_frozen_temporal_block_no_outcome_tuned_threshold",
            "economic": "descriptive_trade_off_only_no_outperformance_requirement",
            "path": "NOT_APPLICABLE_OVERLAPPING_FORWARD_HORIZONS",
            "precedence": (
                "provenance_mismatch=>fail_closed_before_decision",
                "material_structural_or_mechanical_failure=>REJECT_FOR_NOW",
                "required_evidence_unavailable=>INSUFFICIENT_EVIDENCE",
                "valid_mechanics_and_complete_frozen_evidence_without_declared_purpose_contradiction=>ELIGIBLE_FOR_FUTURE_FORWARD_PROTOCOL",
                "otherwise=>HOLD",
                "NO_RISK_POLICY=>CONTROL_BASELINE",
            ),
            "zero_tolerance": "mechanical inequalities use fixed floating_point_tolerance_1e-12_only",
            "restrictions": ("no_scoring", "no_ranking", "no_winner", "no_post_hoc_numerical_outcome_thresholds", *LIMITATIONS),
        }))


NEUTRAL_RISK_POLICY_DECISION_GATE_V1 = RiskPolicyDecisionSpec()


@dataclass(frozen=True, slots=True)
class PolicyDecisionFacts:
    policy: str
    policy_fingerprint: str
    structural_observation_count: int
    structural_failure_count: int
    applied_count: int
    defined_applied_risk_count: int
    mechanical_failure_count: int
    complete_summary_grid: bool
    evaluable_summary_count: int
    expected_summary_count: int
    temporal_blocks_with_evidence: int
    expected_temporal_blocks: int
    temporal_mechanical_failure_count: int
    economic_contrast_defined_count: int
    economic_positive_delta_count: int
    economic_zero_delta_count: int
    economic_negative_delta_count: int
    path_metric_status: str
    path_metric_reason: str


@dataclass(frozen=True, slots=True)
class RiskPolicyDecisionInput:
    phase10a_result_identity: str
    phase10a_specification_fingerprint: str
    phase10b_result_identity: str
    phase10b_specification_fingerprint: str
    phase10c_result_identity: str
    phase10c_specification_fingerprint: str
    source_manifest_hashes: Mapping[str, str]
    policy_fingerprints: Mapping[str, str]
    policy_facts: tuple[PolicyDecisionFacts, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_manifest_hashes", MappingProxyType(dict(sorted(self.source_manifest_hashes.items()))))
        object.__setattr__(self, "policy_fingerprints", MappingProxyType(dict(sorted(self.policy_fingerprints.items()))))
        object.__setattr__(self, "policy_facts", tuple(sorted(self.policy_facts, key=lambda item: item.policy)))


@dataclass(frozen=True, slots=True)
class RiskPolicyDecisionEvidence:
    policy: str
    dimension: str
    status: DecisionEvidenceStatus
    decision_relevant: bool
    reason_code: str
    observation_summary: Mapping[str, Any]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        summary = MappingProxyType(dict(sorted(self.observation_summary.items())))
        object.__setattr__(self, "observation_summary", summary)
        object.__setattr__(self, "identity", _hash({
            "contract": (CONTRACT, VERSION), "policy": self.policy, "dimension": self.dimension,
            "status": self.status.value, "decision_relevant": self.decision_relevant,
            "reason_code": self.reason_code, "observation_summary": dict(summary),
        }))


@dataclass(frozen=True, slots=True)
class RiskPolicyGovernanceDecision:
    policy: str
    policy_fingerprint: str
    role: str
    decision: RiskPolicyDecision
    reason_code: str
    evidence_identities: tuple[str, ...]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "identity", _hash({
            "contract": (CONTRACT, VERSION), "policy": self.policy,
            "policy_fingerprint": self.policy_fingerprint, "role": self.role,
            "decision": self.decision.value, "reason_code": self.reason_code,
            "evidence_identities": self.evidence_identities,
        }))


@dataclass(frozen=True, slots=True)
class RiskPolicyDecisionResult:
    contract_name: str
    contract_version: str
    specification_fingerprint: str
    decision_gate_timing: str
    source_identities: Mapping[str, str]
    source_manifest_hashes: Mapping[str, str]
    evidence: tuple[RiskPolicyDecisionEvidence, ...]
    decisions: tuple[RiskPolicyGovernanceDecision, ...]
    limitations: tuple[str, ...]
    forward_protocol_v1: str
    identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_identities", MappingProxyType(dict(self.source_identities)))
        object.__setattr__(self, "source_manifest_hashes", MappingProxyType(dict(self.source_manifest_hashes)))


def _validate_provenance(source: RiskPolicyDecisionInput) -> None:
    expected = (
        (source.phase10a_result_identity, EXPECTED_PHASE10A_RESULT, "Phase 10A result"),
        (source.phase10a_specification_fingerprint, EXPECTED_PHASE10A_SPEC, "Phase 10A specification"),
        (source.phase10b_result_identity, EXPECTED_PHASE10B_RESULT, "Phase 10B result"),
        (source.phase10b_specification_fingerprint, EXPECTED_PHASE10B_SPEC, "Phase 10B specification"),
        (source.phase10c_result_identity, EXPECTED_PHASE10C_RESULT, "Phase 10C result"),
        (source.phase10c_specification_fingerprint, EXPECTED_PHASE10C_SPEC, "Phase 10C specification"),
    )
    for actual, frozen, label in expected:
        if actual != frozen:
            raise ValueError(f"{label} provenance mismatch")
    if dict(source.policy_fingerprints) != dict(EXPECTED_POLICY_FINGERPRINTS):
        raise ValueError("Phase 10B frozen policy fingerprint mismatch")
    if set(source.source_manifest_hashes) != {"phase10a", "phase10b", "phase10c"} or not all(source.source_manifest_hashes.values()):
        raise ValueError("complete Phase 10A/B/C manifest provenance is required")
    if {item.policy for item in source.policy_facts} != set(EXPECTED_POLICY_FINGERPRINTS) or len(source.policy_facts) != 2:
        raise ValueError("facts for exactly the two frozen policies are required")
    for item in source.policy_facts:
        if item.policy_fingerprint != EXPECTED_POLICY_FINGERPRINTS[item.policy]:
            raise ValueError(f"{item.policy} fact fingerprint mismatch")


def _item(policy: str, dimension: str, status: DecisionEvidenceStatus, relevant: bool, reason: str, **summary: Any) -> RiskPolicyDecisionEvidence:
    return RiskPolicyDecisionEvidence(policy, dimension, status, relevant, reason, summary)


def _policy_evidence(facts: PolicyDecisionFacts) -> tuple[RiskPolicyDecisionEvidence, ...]:
    policy = facts.policy
    control = policy == "NO_RISK_POLICY"
    evidence = [
        _item(policy, DIMENSIONS[0], DecisionEvidenceStatus.SUPPORTIVE, True, "EXACT_FROZEN_PROVENANCE_VERIFIED"),
        _item(
            policy, DIMENSIONS[1],
            DecisionEvidenceStatus.SUPPORTIVE if facts.structural_observation_count > 0 and facts.structural_failure_count == 0 else (DecisionEvidenceStatus.INSUFFICIENT if facts.structural_observation_count == 0 else DecisionEvidenceStatus.ADVERSE),
            True, "STRUCTURAL_CONTRACT_RECONCILES" if facts.structural_failure_count == 0 and facts.structural_observation_count > 0 else ("STRUCTURAL_EVIDENCE_MISSING" if facts.structural_observation_count == 0 else "STRUCTURAL_CONTRACT_VIOLATION"),
            observation_count=facts.structural_observation_count, failure_count=facts.structural_failure_count,
        ),
    ]
    if control:
        purpose = _item(policy, DIMENSIONS[2], DecisionEvidenceStatus.NOT_APPLICABLE, False, "CONTROL_HAS_NO_RISK_REDUCTION_REQUIREMENT")
    elif facts.applied_count == 0 or facts.defined_applied_risk_count == 0:
        purpose = _item(policy, DIMENSIONS[2], DecisionEvidenceStatus.INSUFFICIENT, True, "DECLARED_PURPOSE_EVIDENCE_UNAVAILABLE", applied_count=facts.applied_count, defined_applied_risk_count=facts.defined_applied_risk_count)
    else:
        purpose = _item(policy, DIMENSIONS[2], DecisionEvidenceStatus.SUPPORTIVE if facts.mechanical_failure_count == 0 else DecisionEvidenceStatus.ADVERSE, True, "DECLARED_MECHANICAL_PURPOSE_SATISFIED" if facts.mechanical_failure_count == 0 else "DECLARED_MECHANICAL_PURPOSE_CONTRADICTED", applied_count=facts.applied_count, defined_applied_risk_count=facts.defined_applied_risk_count, failure_count=facts.mechanical_failure_count)
    evidence.append(purpose)
    coverage_ok = facts.complete_summary_grid and facts.expected_summary_count > 0 and facts.evaluable_summary_count == facts.expected_summary_count and facts.temporal_blocks_with_evidence == facts.expected_temporal_blocks
    evidence.append(_item(policy, DIMENSIONS[3], DecisionEvidenceStatus.SUPPORTIVE if coverage_ok else DecisionEvidenceStatus.INSUFFICIENT, True, "COMPLETE_FROZEN_EVIDENCE_GRID" if coverage_ok else "REQUIRED_FROZEN_EVIDENCE_UNAVAILABLE", complete_summary_grid=facts.complete_summary_grid, evaluable_summary_count=facts.evaluable_summary_count, expected_summary_count=facts.expected_summary_count, temporal_blocks_with_evidence=facts.temporal_blocks_with_evidence, expected_temporal_blocks=facts.expected_temporal_blocks))
    if control:
        economic = _item(policy, DIMENSIONS[4], DecisionEvidenceStatus.CONTROL_REFERENCE, False, "CONTROL_ECONOMIC_REFERENCE")
    elif facts.economic_contrast_defined_count == 0:
        economic = _item(policy, DIMENSIONS[4], DecisionEvidenceStatus.INSUFFICIENT, False, "ECONOMIC_CONTRAST_UNAVAILABLE")
    else:
        nonzero = facts.economic_positive_delta_count + facts.economic_negative_delta_count
        economic = _item(policy, DIMENSIONS[4], DecisionEvidenceStatus.TRADE_OFF_PRESENT if nonzero else DecisionEvidenceStatus.NO_MATERIAL_TRADE_OFF_EVIDENCE, False, "DESCRIPTIVE_ECONOMIC_TRADE_OFF_PRESENT" if nonzero else "NO_NONZERO_MEAN_ECONOMIC_DELTA", defined_contrasts=facts.economic_contrast_defined_count, positive_deltas=facts.economic_positive_delta_count, zero_deltas=facts.economic_zero_delta_count, negative_deltas=facts.economic_negative_delta_count)
    evidence.append(economic)
    if facts.temporal_blocks_with_evidence == 0:
        temporal_status, temporal_reason = DecisionEvidenceStatus.INSUFFICIENT, "TEMPORAL_EVIDENCE_UNAVAILABLE"
    elif facts.temporal_mechanical_failure_count:
        temporal_status, temporal_reason = DecisionEvidenceStatus.ADVERSE, "TEMPORAL_MECHANICAL_FAILURE"
    else:
        temporal_status, temporal_reason = DecisionEvidenceStatus.SUPPORTIVE, "NO_TEMPORAL_MECHANICAL_CONTRADICTION"
    evidence.append(_item(policy, DIMENSIONS[5], temporal_status, not control, temporal_reason, blocks_with_evidence=facts.temporal_blocks_with_evidence, expected_blocks=facts.expected_temporal_blocks, mechanical_failure_count=facts.temporal_mechanical_failure_count))
    path_ok = facts.path_metric_status == "NOT_APPLICABLE" and facts.path_metric_reason == "OVERLAPPING_FORWARD_HORIZONS"
    evidence.append(_item(policy, DIMENSIONS[6], DecisionEvidenceStatus.NOT_APPLICABLE if path_ok else DecisionEvidenceStatus.INSUFFICIENT, False, "OVERLAPPING_FORWARD_HORIZONS" if path_ok else "PATH_LIMITATION_PROVENANCE_MISMATCH", path_metric_status=facts.path_metric_status, path_metric_reason=facts.path_metric_reason))
    return tuple(evidence)


def _decision(facts: PolicyDecisionFacts, evidence: tuple[RiskPolicyDecisionEvidence, ...]) -> RiskPolicyGovernanceDecision:
    if facts.policy == "NO_RISK_POLICY":
        decision, role, reason = RiskPolicyDecision.CONTROL_BASELINE, "CONTROL", "FROZEN_STRUCTURAL_CONTROL_NOT_AN_INTERVENTION"
    else:
        relevant = tuple(item for item in evidence if item.decision_relevant)
        if any(item.status is DecisionEvidenceStatus.ADVERSE for item in relevant):
            decision, reason = RiskPolicyDecision.REJECT_FOR_NOW, "MATERIAL_STRUCTURAL_OR_MECHANICAL_CONTRADICTION"
        elif any(item.status is DecisionEvidenceStatus.INSUFFICIENT for item in relevant):
            decision, reason = RiskPolicyDecision.INSUFFICIENT_EVIDENCE, "REQUIRED_DECISION_EVIDENCE_UNAVAILABLE"
        elif all(next(item for item in relevant if item.dimension == dimension).status is DecisionEvidenceStatus.SUPPORTIVE for dimension in (DIMENSIONS[0], DIMENSIONS[1], DIMENSIONS[2], DIMENSIONS[3], DIMENSIONS[5])):
            decision, reason = RiskPolicyDecision.ELIGIBLE_FOR_FUTURE_FORWARD_PROTOCOL, "DECLARED_PURPOSE_VALID_WITH_COMPLETE_FROZEN_EVIDENCE"
        else:
            decision, reason = RiskPolicyDecision.HOLD, "VALID_BUT_MIXED_EVIDENCE_DOES_NOT_JUSTIFY_ESCALATION"
        role = "INTERVENTION"
    return RiskPolicyGovernanceDecision(facts.policy, facts.policy_fingerprint, role, decision, reason, tuple(item.identity for item in evidence))


def evaluate_risk_policy_decision_gate(
    source: RiskPolicyDecisionInput,
    *,
    spec: RiskPolicyDecisionSpec = NEUTRAL_RISK_POLICY_DECISION_GATE_V1,
) -> RiskPolicyDecisionResult:
    _validate_provenance(source)
    evidence: list[RiskPolicyDecisionEvidence] = []
    decisions: list[RiskPolicyGovernanceDecision] = []
    for facts in source.policy_facts:
        items = _policy_evidence(facts)
        evidence.extend(items)
        decisions.append(_decision(facts, items))
    source_ids = MappingProxyType({
        "phase10a_result_identity": source.phase10a_result_identity,
        "phase10a_specification_fingerprint": source.phase10a_specification_fingerprint,
        "phase10b_result_identity": source.phase10b_result_identity,
        "phase10b_specification_fingerprint": source.phase10b_specification_fingerprint,
        "phase10c_result_identity": source.phase10c_result_identity,
        "phase10c_specification_fingerprint": source.phase10c_specification_fingerprint,
    })
    payload = {
        "contract": (CONTRACT, VERSION), "specification_fingerprint": spec.fingerprint,
        "decision_gate_timing": DECISION_GATE_TIMING, "source_identities": dict(source_ids),
        "source_manifest_hashes": dict(source.source_manifest_hashes),
        "evidence": tuple(item.identity for item in evidence),
        "decisions": tuple(item.identity for item in decisions), "limitations": LIMITATIONS,
        "forward_protocol_v1": FORWARD_PROTOCOL_V1,
    }
    return RiskPolicyDecisionResult(CONTRACT, VERSION, spec.fingerprint, DECISION_GATE_TIMING, source_ids, source.source_manifest_hashes, tuple(evidence), tuple(decisions), LIMITATIONS, FORWARD_PROTOCOL_V1, _hash(payload))
