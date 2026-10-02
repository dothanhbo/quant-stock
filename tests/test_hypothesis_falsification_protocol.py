from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from quantlab.hypotheses import (
    ALPHA_HYPOTHESIS_REGISTRY_VERSION,
    FALSIFICATION_PROTOCOL_VERSION,
    AlphaHypothesis,
    AssessmentState,
    DecisionCriterion,
    DependenceMethodology,
    EconomicValidityDeclarations,
    EvaluationDisposition,
    EvidenceOrigin,
    EvidenceReference,
    ExpectedSign,
    FalsificationProtocol,
    FalsificationProtocolStatus,
    HypothesisStatus,
    LifecycleEvent,
    MinimumEconomicEffect,
    MinimumEvidenceRequirements,
    ProspectiveEvaluationWindow,
    ProspectiveEvidencePreconditions,
    ResearchBoundaryAssessments,
    classify_prospective_evidence,
    freeze_falsification_protocol,
    load_alpha_hypothesis_registry,
    revise_falsification_protocol,
    transition_hypothesis,
    validate_disposition_transition,
)


CREATED = "2026-10-02T03:00:00Z"
REGISTERED = "2026-10-03T00:00:00Z"
FROZEN = "2026-10-03T01:00:00Z"


def _registered_hypothesis() -> AlphaHypothesis:
    draft = AlphaHypothesis(
        hypothesis_id="synthetic-adx-v1",
        registry_schema_version=ALPHA_HYPOTHESIS_REGISTRY_VERSION,
        created_at_utc=CREATED,
        revision=1,
        research_question="Does same-date ADX associate with future return?",
        factor_policy_reference="adx_14",
        factor_policy_version="quantlab-feature-v1",
        expected_relationship="Higher ADX is associated with higher forward return.",
        expected_sign=ExpectedSign.POSITIVE,
        universe_definition="Point-in-time database coverage 50/5.",
        universe_limitations=("Database coverage is not historical VN100 membership.",),
        feature_availability_timing="Available after completed signal-session close.",
        outcome_definition="Stock and VNINDEX-relative returns remain separate.",
        evaluation_horizons_sessions=(10,),
        historical_discovery_cutoff="2026-09-17",
        registration_timestamp_utc=None,
        evidence_references=(EvidenceReference(
            EvidenceOrigin.RETROSPECTIVE,
            "research_results/example/manifest.json",
            "historical-result",
            "historical-fingerprint",
            "2026-09-17",
        ),),
        evidence_family="synthetic_directional_family_v1",
        prospective_evaluation_window=ProspectiveEvaluationWindow("2026-10-03", "2027-10-03"),
        minimum_sample_count=100,
        minimum_coverage_pct=90.0,
        minimum_economic_effect=MinimumEconomicEffect("mean_rank_ic", "GTE", 0.01, "correlation"),
        falsification_conditions=("Mean rank IC is at or below zero under the frozen inference method.",),
        methodological_limitations=(
            "Forward outcomes overlap and require dependence-aware inference.",
            "Historical corporate-action provenance is unresolved.",
        ),
        dependencies=(),
        status=HypothesisStatus.DRAFT,
        assessments=ResearchBoundaryAssessments(AssessmentState.PASS),
        parent_revision_identity=None,
        lifecycle_history=(LifecycleEvent(None, HypothesisStatus.DRAFT, CREATED, "Synthetic draft."),),
    )
    return transition_hypothesis(
        draft,
        HypothesisStatus.REGISTERED,
        occurred_at_utc=REGISTERED,
        reason="Synthetic prospective registration.",
    )


def _requirements() -> MinimumEvidenceRequirements:
    return MinimumEvidenceRequirements(
        required_sample_size=100,
        minimum_coverage_pct=90.0,
        missing_data_treatment="Exclude unavailable observations and report missingness.",
        observation_eligibility="Use only mature, point-in-time eligible observations.",
        maturity_requirement="The complete ten-session outcome must be observed.",
        continuity_gap_handling="Any unresolved continuity gap blocks a conclusive disposition.",
        minimum_economic_effect=MinimumEconomicEffect("mean_rank_ic", "GTE", 0.01, "correlation"),
        support_criterion=DecisionCriterion("adjusted_lower_confidence_bound", "GT", 0.0, "correlation"),
        falsification_criterion=DecisionCriterion("adjusted_upper_confidence_bound", "LTE", 0.0, "correlation"),
    )


def _dependence() -> DependenceMethodology:
    return DependenceMethodology(
        overlapping_horizon_treatment="Ten-session outcomes use date-block dependence handling.",
        same_date_cross_section_treatment="Signal date is the cross-sectional cluster.",
        serial_dependence_treatment="Moving date blocks preserve serial dependence.",
        confidence_interval_method="Predeclared two-sided cluster-bootstrap interval.",
        block_or_cluster_resampling_unit="Signal-date blocks of ten market sessions.",
        multiple_testing_family="One registered primary outcome and horizon in this protocol revision.",
        multiplicity_adjustment="Single-family adjusted confidence boundary declared before evidence.",
        effective_information_method="Report date-cluster information separately from nominal observations.",
    )


def _economic(hypothesis: AlphaHypothesis) -> EconomicValidityDeclarations:
    return EconomicValidityDeclarations(
        gross_association_definition="Daily cross-sectional rank association before friction.",
        benchmark_relative_definition="VNINDEX-relative outcome is reported separately from stock return.",
        transaction_friction_treatment="Sensitivity estimates are assumptions, not measured execution cost.",
        executable_net_performance_treatment="Not established by an association evaluation.",
        inherited_limitations=(*hypothesis.methodological_limitations,),
    )


def _draft_protocol(hypothesis: AlphaHypothesis, **changes) -> FalsificationProtocol:
    values = {
        "protocol_id": "synthetic-adx-falsification-v1",
        "protocol_version": FALSIFICATION_PROTOCOL_VERSION,
        "created_at_utc": REGISTERED,
        "revision": 1,
        "hypothesis_id": hypothesis.hypothesis_id,
        "hypothesis_revision": hypothesis.revision,
        "parent_hypothesis_specification_fingerprint": hypothesis.specification_fingerprint,
        "expected_sign": hypothesis.expected_sign,
        "testable_null": "The predeclared dependence-adjusted association is not positive.",
        "evaluation_universe": hypothesis.universe_definition,
        "universe_limitations": hypothesis.universe_limitations,
        "feature_formation_and_availability_timing": hypothesis.feature_availability_timing,
        "prospective_evaluation_window": hypothesis.prospective_evaluation_window,
        "primary_outcome": "VNINDEX-relative ten-session forward return.",
        "primary_benchmark": "VNINDEX close-to-close price return without dividends.",
        "primary_horizon_sessions": 10,
        "evidence_family": hypothesis.evidence_family,
        "minimum_evidence": _requirements(),
        "dependence_methodology": _dependence(),
        "economic_validity": _economic(hypothesis),
        "status": FalsificationProtocolStatus.DRAFT,
        "frozen_at_utc": None,
        "parent_revision_identity": None,
    }
    values.update(changes)
    return FalsificationProtocol(**values)


def _frozen_protocol() -> tuple[AlphaHypothesis, FalsificationProtocol]:
    hypothesis = _registered_hypothesis()
    return hypothesis, freeze_falsification_protocol(_draft_protocol(hypothesis), hypothesis, frozen_at_utc=FROZEN)


def _evidence(protocol: FalsificationProtocol, **changes) -> ProspectiveEvidencePreconditions:
    values = {
        "evidence_origin": EvidenceOrigin.PROSPECTIVE,
        "protocol_identity": protocol.identity,
        "hypothesis_id": protocol.hypothesis_id,
        "hypothesis_revision": protocol.hypothesis_revision,
        "parent_hypothesis_specification_fingerprint": protocol.parent_hypothesis_specification_fingerprint,
        "evidence_family": protocol.evidence_family,
        "contract_valid": True,
        "evidence_usable": True,
        "evaluation_window_complete": True,
        "mature_eligible_sample_size": 120,
        "coverage_pct": 95.0,
        "continuity_requirement_met": True,
        "dependence_method_applied": True,
        "multiplicity_adjustment_applied": True,
        "statistical_support_criterion_met": False,
        "economic_effect_criterion_met": False,
        "falsification_criterion_met": False,
    }
    values.update(changes)
    return ProspectiveEvidencePreconditions(**values)


def test_protocol_binding_identity_and_immutability() -> None:
    hypothesis, protocol = _frozen_protocol()
    duplicate = freeze_falsification_protocol(_draft_protocol(hypothesis), hypothesis, frozen_at_utc=FROZEN)
    assert duplicate.specification_fingerprint == protocol.specification_fingerprint
    assert duplicate.identity == protocol.identity
    with pytest.raises(FrozenInstanceError):
        protocol.primary_horizon_sessions = 20  # type: ignore[misc]
    with pytest.raises(ValueError, match="binding mismatch"):
        freeze_falsification_protocol(
            replace(_draft_protocol(hypothesis), parent_hypothesis_specification_fingerprint="wrong"),
            hypothesis,
            frozen_at_utc=FROZEN,
        )


def test_missing_mandatory_declarations_are_explicit_and_prevent_freeze() -> None:
    hypothesis = _registered_hypothesis()
    incomplete = _draft_protocol(
        hypothesis,
        minimum_evidence=replace(_requirements(), required_sample_size=None),
        dependence_methodology=replace(_dependence(), multiplicity_adjustment=None),
    )
    assert "minimum_evidence.required_sample_size" in incomplete.unresolved_declarations()
    assert "dependence_methodology.multiplicity_adjustment" in incomplete.unresolved_declarations()
    with pytest.raises(ValueError, match="unresolved declarations"):
        freeze_falsification_protocol(incomplete, hypothesis, frozen_at_utc=FROZEN)


def test_freeze_requires_registered_hypothesis_and_revision_is_explicit() -> None:
    registered, frozen = _frozen_protocol()
    with pytest.raises(ValueError, match="only a draft protocol"):
        freeze_falsification_protocol(frozen, registered, frozen_at_utc=FROZEN)
    revised = revise_falsification_protocol(
        frozen,
        created_at_utc="2026-10-04T00:00:00Z",
        changes={"testable_null": "A newly declared null for a future revision."},
    )
    assert revised.revision == 2
    assert revised.status is FalsificationProtocolStatus.DRAFT
    assert revised.parent_revision_identity == frozen.identity
    assert revised.identity != frozen.identity


def test_insufficient_and_inconclusive_are_distinct() -> None:
    _, protocol = _frozen_protocol()
    insufficient = classify_prospective_evidence(
        protocol,
        _evidence(protocol, mature_eligible_sample_size=99, statistical_support_criterion_met=True),
    )
    inconclusive = classify_prospective_evidence(protocol, _evidence(protocol))
    assert insufficient.disposition is EvaluationDisposition.INSUFFICIENT_EVIDENCE
    assert inconclusive.disposition is EvaluationDisposition.INCONCLUSIVE


def test_invalid_evidence_precedes_an_observed_poor_result() -> None:
    _, protocol = _frozen_protocol()
    decision = classify_prospective_evidence(
        protocol,
        _evidence(protocol, contract_valid=False, falsification_criterion_met=True),
    )
    assert decision.disposition is EvaluationDisposition.INVALID_EVIDENCE
    assert "BROKEN_EVIDENCE_CONTRACT" in decision.reason_codes


def test_predeclared_falsification_and_support_boundaries_are_non_binary() -> None:
    _, protocol = _frozen_protocol()
    falsified = classify_prospective_evidence(
        protocol, _evidence(protocol, falsification_criterion_met=True),
    )
    supported = classify_prospective_evidence(
        protocol,
        _evidence(
            protocol,
            statistical_support_criterion_met=True,
            economic_effect_criterion_met=True,
            falsification_criterion_met=False,
        ),
    )
    assert falsified.disposition is EvaluationDisposition.FALSIFIED
    assert supported.disposition is EvaluationDisposition.SUPPORTED
    assert supported.assessments.alpha_validated is AssessmentState.NOT_ASSESSED
    assert supported.assessments.production_ready is AssessmentState.NOT_ASSESSED
    assert supported.capital_authorized is False


def test_retrospective_evidence_cannot_substitute_for_prospective_evidence() -> None:
    _, protocol = _frozen_protocol()
    decision = classify_prospective_evidence(
        protocol,
        _evidence(protocol, evidence_origin=EvidenceOrigin.RETROSPECTIVE),
    )
    assert decision.disposition is EvaluationDisposition.INVALID_EVIDENCE
    assert "NON_PROSPECTIVE_EVIDENCE" in decision.reason_codes


def test_terminal_disposition_transitions_are_rejected() -> None:
    with pytest.raises(ValueError, match="terminal disposition"):
        validate_disposition_transition(EvaluationDisposition.SUPPORTED, EvaluationDisposition.FALSIFIED)
    validate_disposition_transition(EvaluationDisposition.INSUFFICIENT_EVIDENCE, EvaluationDisposition.INCONCLUSIVE)


def test_initial_registry_candidates_remain_draft_and_protocol_compatible() -> None:
    registry = load_alpha_hypothesis_registry(Path("research/alpha_hypotheses/registry_v1.json"))
    for hypothesis in registry.records:
        protocol = FalsificationProtocol(
            protocol_id=f"{hypothesis.hypothesis_id}-protocol-v1",
            protocol_version=FALSIFICATION_PROTOCOL_VERSION,
            created_at_utc=CREATED,
            revision=1,
            hypothesis_id=hypothesis.hypothesis_id,
            hypothesis_revision=hypothesis.revision,
            parent_hypothesis_specification_fingerprint=hypothesis.specification_fingerprint,
            expected_sign=hypothesis.expected_sign,
            testable_null=None,
            evaluation_universe=hypothesis.universe_definition,
            universe_limitations=hypothesis.universe_limitations,
            feature_formation_and_availability_timing=hypothesis.feature_availability_timing,
            prospective_evaluation_window=None,
            primary_outcome=None,
            primary_benchmark=None,
            primary_horizon_sessions=None,
            evidence_family=None,
            minimum_evidence=MinimumEvidenceRequirements(None, None, None, None, None, None, None, None, None),
            dependence_methodology=DependenceMethodology(None, None, None, None, None, None, None, None),
            economic_validity=EconomicValidityDeclarations(None, None, None, None, hypothesis.methodological_limitations),
            status=FalsificationProtocolStatus.DRAFT,
            frozen_at_utc=None,
            parent_revision_identity=None,
        )
        assert protocol.unresolved_declarations()
        assert hypothesis.status is HypothesisStatus.DRAFT


def test_protocol_contract_is_pure_and_does_not_import_evaluation_or_forward_runtime() -> None:
    import sys

    forbidden = {
        "quantlab.forward.daily",
        "quantlab.forward.ledger",
        "quantlab.evaluation.panel_factor_analysis",
        "quantlab.portfolio.simulator",
    }
    assert forbidden.isdisjoint(sys.modules)
