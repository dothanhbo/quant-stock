from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from pathlib import Path

import pytest

from quantlab.hypotheses import (
    CONTROLLED_EXPERIMENT_VERSION,
    FALSIFICATION_PROTOCOL_VERSION,
    AssessmentState,
    ControlledComparison,
    ControlledExperiment,
    ControlledExperimentResult,
    ControlledExperimentStatus,
    DecisionCriterion,
    DependenceMethodology,
    EconomicInterpretation,
    EconomicValidityDeclarations,
    EvaluationDisposition,
    EvidenceOrigin,
    ExperimentEvidenceSource,
    ExperimentEvidenceValidity,
    ExperimentExecutionStatus,
    ExperimentTemporalIntegrity,
    ExperimentValidityCheck,
    FalsificationProtocol,
    FalsificationProtocolStatus,
    HypothesisStatus,
    MinimumEconomicEffect,
    MinimumEvidenceRequirements,
    ProspectiveEvaluationWindow,
    ResearchBoundaryAssessments,
    ValidityCheckKind,
    ValidityConsequence,
    freeze_controlled_experiment,
    freeze_falsification_protocol,
    load_alpha_hypothesis_registry,
    revise_controlled_experiment,
    transition_hypothesis,
)
from quantlab.identity import canonical_identity_value, canonical_json


REGISTERED = "2026-10-03T00:00:00Z"
PROTOCOL_FROZEN = "2026-10-03T01:00:00Z"
EXPERIMENT_FROZEN = "2026-10-03T02:00:00Z"


def _hash(value) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _registered_hypothesis():
    source = load_alpha_hypothesis_registry(Path("research/alpha_hypotheses/registry_v1.json"))
    draft = next(item for item in source.records if item.hypothesis_id == "neutral-adx-directional-association-v1")
    complete = replace(
        draft,
        evaluation_horizons_sessions=(10,),
        evidence_family="synthetic_prospective_adx_family_v1",
        prospective_evaluation_window=ProspectiveEvaluationWindow("2026-10-03", "2027-10-03"),
        minimum_sample_count=100,
        minimum_coverage_pct=90.0,
        minimum_economic_effect=MinimumEconomicEffect("mean_rank_ic", "GTE", 0.01, "correlation"),
        falsification_conditions=("Adjusted upper confidence boundary is at or below zero.",),
    )
    return transition_hypothesis(
        complete,
        HypothesisStatus.REGISTERED,
        occurred_at_utc=REGISTERED,
        reason="Synthetic registration for contract testing only.",
    )


def _requirements() -> MinimumEvidenceRequirements:
    return MinimumEvidenceRequirements(
        required_sample_size=100,
        minimum_coverage_pct=90.0,
        missing_data_treatment="Exclude unavailable observations and report missingness.",
        observation_eligibility="Use only mature, point-in-time eligible observations.",
        maturity_requirement="Complete ten-session outcomes are required.",
        continuity_gap_handling="Unresolved gaps prevent a conclusive disposition.",
        minimum_economic_effect=MinimumEconomicEffect("mean_rank_ic", "GTE", 0.01, "correlation"),
        support_criterion=DecisionCriterion("adjusted_lower_confidence_bound", "GT", 0.0, "correlation"),
        falsification_criterion=DecisionCriterion("adjusted_upper_confidence_bound", "LTE", 0.0, "correlation"),
    )


def _dependence() -> DependenceMethodology:
    return DependenceMethodology(
        overlapping_horizon_treatment="Ten-session outcomes use date-block dependence handling.",
        same_date_cross_section_treatment="Signal date is the cross-sectional cluster.",
        serial_dependence_treatment="Moving date blocks preserve serial dependence.",
        confidence_interval_method="Predeclared two-sided clustered interval.",
        block_or_cluster_resampling_unit="Signal-date blocks of ten market sessions.",
        multiple_testing_family="One primary outcome and horizon in this protocol revision.",
        multiplicity_adjustment="Single-family adjusted confidence boundary.",
        effective_information_method="Report date-cluster information separately from nominal observations.",
    )


def _frozen_protocol(hypothesis) -> FalsificationProtocol:
    protocol = FalsificationProtocol(
        protocol_id="synthetic-adx-falsification-v1",
        protocol_version=FALSIFICATION_PROTOCOL_VERSION,
        created_at_utc=REGISTERED,
        revision=1,
        hypothesis_id=hypothesis.hypothesis_id,
        hypothesis_revision=hypothesis.revision,
        parent_hypothesis_specification_fingerprint=hypothesis.specification_fingerprint,
        expected_sign=hypothesis.expected_sign,
        testable_null="The dependence-adjusted prospective association is not positive.",
        evaluation_universe=hypothesis.universe_definition,
        universe_limitations=hypothesis.universe_limitations,
        feature_formation_and_availability_timing=hypothesis.feature_availability_timing,
        prospective_evaluation_window=hypothesis.prospective_evaluation_window,
        primary_outcome="VNINDEX-relative ten-session forward return.",
        primary_benchmark="VNINDEX price return without dividends.",
        primary_horizon_sessions=10,
        evidence_family=hypothesis.evidence_family,
        minimum_evidence=_requirements(),
        dependence_methodology=_dependence(),
        economic_validity=EconomicValidityDeclarations(
            gross_association_definition="Daily cross-sectional rank association before friction.",
            benchmark_relative_definition="VNINDEX-relative outcome remains separate from stock return.",
            transaction_friction_treatment="Sensitivity assumptions are not measured costs.",
            executable_net_performance_treatment="Not established by this association experiment.",
            inherited_limitations=hypothesis.methodological_limitations,
        ),
        status=FalsificationProtocolStatus.DRAFT,
        frozen_at_utc=None,
        parent_revision_identity=None,
    )
    return freeze_falsification_protocol(protocol, hypothesis, frozen_at_utc=PROTOCOL_FROZEN)


def _comparison(hypothesis, protocol) -> ControlledComparison:
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


def _validity_checks() -> tuple[ExperimentValidityCheck, ...]:
    insufficient = {
        ValidityCheckKind.INSUFFICIENT_MATURITY,
        ValidityCheckKind.COVERAGE_FAILURE,
        ValidityCheckKind.CONTINUITY_GAP,
    }
    limitations = {
        ValidityCheckKind.CORPORATE_ACTION_PROVENANCE,
        ValidityCheckKind.HISTORICAL_UNIVERSE_LIMITATION,
    }
    return tuple(
        ExperimentValidityCheck(
            kind,
            f"Apply the frozen {kind.value.lower()} check before disposition.",
            ValidityConsequence.INSUFFICIENT_EVIDENCE if kind in insufficient else (
                ValidityConsequence.LIMITATION_DISCLOSURE if kind in limitations else ValidityConsequence.INVALID_EVIDENCE
            ),
        )
        for kind in ValidityCheckKind
    )


def _draft_experiment(hypothesis, protocol, **changes) -> ControlledExperiment:
    limitations = tuple(sorted(
        set(hypothesis.universe_limitations)
        | set(hypothesis.methodological_limitations)
        | set(protocol.economic_validity.inherited_limitations)
    ))
    values = {
        "experiment_id": "synthetic-adx-controlled-experiment-v1",
        "schema_version": CONTROLLED_EXPERIMENT_VERSION,
        "created_at_utc": "2026-10-03T01:30:00Z",
        "revision": 1,
        "hypothesis_id": hypothesis.hypothesis_id,
        "hypothesis_revision": hypothesis.revision,
        "hypothesis_specification_fingerprint": hypothesis.specification_fingerprint,
        "falsification_protocol_id": protocol.protocol_id,
        "falsification_protocol_revision": protocol.revision,
        "falsification_protocol_specification_fingerprint": protocol.specification_fingerprint,
        "falsification_protocol_identity": protocol.identity,
        "evidence_sources": (ExperimentEvidenceSource(
            "prospective-observation-ledger",
            "synthetic.prospective_evidence",
            "v1",
            "source-ledger-identity",
            "source-ledger-fingerprint",
            EvidenceOrigin.PROSPECTIVE,
            "2026-10-03",
        ),),
        "expected_evidence_identity_rule": "Each immutable observation identity binds source, session, subject and payload.",
        "temporal_integrity": ExperimentTemporalIntegrity(
            historical_discovery_cutoff=hypothesis.historical_discovery_cutoff,
            registration_timestamp_utc=hypothesis.registration_timestamp_utc,
            protocol_freeze_timestamp_utc=protocol.frozen_at_utc,
            prospective_evaluation_start_session="2026-10-04",
            evaluation_end_session="2027-10-03",
            stopping_rule=None,
            outcome_maturity_rule=protocol.minimum_evidence.maturity_requirement,
            observation_eligibility=protocol.minimum_evidence.observation_eligibility,
            retrospective_exclusion_rule="Reject observations at or before the frozen eligible-after boundary.",
        ),
        "controlled_comparison": _comparison(hypothesis, protocol),
        "validity_checks": _validity_checks(),
        "inherited_limitations": limitations,
        "status": ControlledExperimentStatus.DRAFT,
        "frozen_at_utc": None,
        "parent_revision_identity": None,
    }
    values.update(changes)
    return ControlledExperiment(**values)


def _frozen_experiment():
    hypothesis = _registered_hypothesis()
    protocol = _frozen_protocol(hypothesis)
    experiment = freeze_controlled_experiment(
        _draft_experiment(hypothesis, protocol),
        hypothesis,
        protocol,
        frozen_at_utc=EXPERIMENT_FROZEN,
    )
    return hypothesis, protocol, experiment


def test_freeze_binds_hypothesis_protocol_sources_and_is_deterministic() -> None:
    hypothesis, protocol, experiment = _frozen_experiment()
    duplicate = freeze_controlled_experiment(
        _draft_experiment(hypothesis, protocol), hypothesis, protocol, frozen_at_utc=EXPERIMENT_FROZEN,
    )
    assert duplicate.specification_fingerprint == experiment.specification_fingerprint
    assert duplicate.identity == experiment.identity
    assert experiment.evidence_sources[0].evidence_origin is EvidenceOrigin.PROSPECTIVE
    with pytest.raises(FrozenInstanceError):
        experiment.experiment_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"historical_discovery_cutoff": "2026-10-04"}, "historical discovery cutoff"),
        ({"evaluation_end_session": "2026-10-03"}, "evaluation end"),
        ({"registration_timestamp_utc": "2026-10-03T02:00:00Z"}, "protocol freeze"),
    ],
)
def test_temporal_ordering_is_rejected(changes, message) -> None:
    hypothesis = _registered_hypothesis()
    protocol = _frozen_protocol(hypothesis)
    values = {
        "historical_discovery_cutoff": "2026-09-17",
        "registration_timestamp_utc": REGISTERED,
        "protocol_freeze_timestamp_utc": PROTOCOL_FROZEN,
        "prospective_evaluation_start_session": "2026-10-04",
        "evaluation_end_session": "2027-10-03",
        "stopping_rule": None,
        "outcome_maturity_rule": "mature",
        "observation_eligibility": "eligible",
        "retrospective_exclusion_rule": "exclude",
    }
    values.update(changes)
    with pytest.raises(ValueError, match=message):
        _draft_experiment(hypothesis, protocol, temporal_integrity=ExperimentTemporalIntegrity(**values))


def test_retrospective_evidence_reuse_cannot_freeze() -> None:
    hypothesis = _registered_hypothesis()
    protocol = _frozen_protocol(hypothesis)
    source = replace(
        _draft_experiment(hypothesis, protocol).evidence_sources[0],
        evidence_origin=EvidenceOrigin.RETROSPECTIVE,
    )
    draft = _draft_experiment(hypothesis, protocol, evidence_sources=(source,))
    with pytest.raises(ValueError, match="prospective_evidence_sources"):
        freeze_controlled_experiment(draft, hypothesis, protocol, frozen_at_utc=EXPERIMENT_FROZEN)


def test_missing_declarations_and_validity_checks_prevent_freeze() -> None:
    hypothesis = _registered_hypothesis()
    protocol = _frozen_protocol(hypothesis)
    draft = _draft_experiment(
        hypothesis,
        protocol,
        expected_evidence_identity_rule=None,
        validity_checks=_validity_checks()[:-1],
    )
    assert "expected_evidence_identity_rule" in draft.unresolved_declarations()
    assert "complete_validity_check_set" in draft.unresolved_declarations()
    with pytest.raises(ValueError, match="unresolved declarations"):
        freeze_controlled_experiment(draft, hypothesis, protocol, frozen_at_utc=EXPERIMENT_FROZEN)


def test_identity_and_post_freeze_mismatches_fail_visibly() -> None:
    hypothesis = _registered_hypothesis()
    protocol = _frozen_protocol(hypothesis)
    draft = _draft_experiment(hypothesis, protocol, falsification_protocol_identity="tampered")
    with pytest.raises(ValueError, match="falsification_protocol_identity"):
        freeze_controlled_experiment(draft, hypothesis, protocol, frozen_at_utc=EXPERIMENT_FROZEN)


def test_conflicting_support_and_falsification_declarations_are_rejected() -> None:
    criterion = DecisionCriterion("mean_effect", "GTE", 0.01, "return")
    with pytest.raises(ValueError, match="declarations conflict"):
        ControlledComparison(
            "primary", "null", "benchmark", "outcome", 10, "universe", "eligible", "exclude",
            "dependence", MinimumEconomicEffect("mean_effect", "GTE", 0.01, "return"), criterion, criterion,
        )


def test_revision_is_explicit_and_never_mutates_frozen_specification() -> None:
    _, _, frozen = _frozen_experiment()
    revised = revise_controlled_experiment(
        frozen,
        created_at_utc="2026-10-04T00:00:00Z",
        changes={"expected_evidence_identity_rule": "A newly declared rule for a future revision."},
    )
    assert revised.status is ControlledExperimentStatus.DRAFT
    assert revised.revision == 2
    assert revised.parent_revision_identity == frozen.identity
    assert revised.identity != frozen.identity


def test_result_contract_separates_completion_validity_statistics_and_economics() -> None:
    _, _, experiment = _frozen_experiment()
    result = ControlledExperimentResult(
        experiment.identity,
        experiment.specification_fingerprint,
        ExperimentExecutionStatus.COMPLETED,
        ExperimentEvidenceValidity.VALID,
        EvaluationDisposition.SUPPORTED,
        EconomicInterpretation.BENCHMARK_RELATIVE_ASSOCIATION,
        "Valid benchmark-relative association; no executable-net claim.",
        ResearchBoundaryAssessments(AssessmentState.PASS),
        ("evidence-2", "evidence-1"),
        (),
    )
    assert result.execution_status is ExperimentExecutionStatus.COMPLETED
    assert result.evidence_validity is ExperimentEvidenceValidity.VALID
    assert result.assessments.alpha_validated is AssessmentState.NOT_ASSESSED
    assert result.assessments.production_ready is AssessmentState.NOT_ASSESSED


def test_invalid_result_state_combinations_are_rejected() -> None:
    _, _, experiment = _frozen_experiment()
    with pytest.raises(ValueError, match="incomplete experiment"):
        ControlledExperimentResult(
            experiment.identity, experiment.specification_fingerprint,
            ExperimentExecutionStatus.RUNNING, ExperimentEvidenceValidity.NOT_ASSESSED,
            EvaluationDisposition.SUPPORTED, EconomicInterpretation.NOT_ASSESSED, None,
            ResearchBoundaryAssessments(), (), (),
        )
    with pytest.raises(ValueError, match="validity and statistical disposition conflict"):
        ControlledExperimentResult(
            experiment.identity, experiment.specification_fingerprint,
            ExperimentExecutionStatus.COMPLETED, ExperimentEvidenceValidity.INVALID,
            EvaluationDisposition.FALSIFIED, EconomicInterpretation.NOT_ASSESSED, None,
            ResearchBoundaryAssessments(), ("bad-evidence",), ("fingerprint mismatch",),
        )
    failed = ControlledExperimentResult(
        experiment.identity, experiment.specification_fingerprint,
        ExperimentExecutionStatus.FAILED, ExperimentEvidenceValidity.INVALID,
        None, EconomicInterpretation.NOT_ASSESSED, None,
        ResearchBoundaryAssessments(), (), ("execution failed before statistical disposition",),
    )
    assert failed.statistical_disposition is None
    with pytest.raises(ValueError, match="at least one evidence identity"):
        ControlledExperimentResult(
            experiment.identity, experiment.specification_fingerprint,
            ExperimentExecutionStatus.COMPLETED, ExperimentEvidenceValidity.VALID,
            EvaluationDisposition.SUPPORTED, EconomicInterpretation.NOT_ASSESSED, None,
            ResearchBoundaryAssessments(), (), (),
        )
    with pytest.raises(ValueError, match="economic interpretation requires valid evidence"):
        ControlledExperimentResult(
            experiment.identity, experiment.specification_fingerprint,
            ExperimentExecutionStatus.COMPLETED, ExperimentEvidenceValidity.INVALID,
            EvaluationDisposition.INVALID_EVIDENCE, EconomicInterpretation.EXECUTABLE_NET_PERFORMANCE,
            "This claim must be rejected because its evidence is invalid.",
            ResearchBoundaryAssessments(), ("invalid-evidence",), ("contract mismatch",),
        )


def test_initial_candidates_remain_draft_and_no_experiment_is_created_for_them() -> None:
    registry = load_alpha_hypothesis_registry(Path("research/alpha_hypotheses/registry_v1.json"))
    assert len(registry.records) == 3
    assert all(item.status is HypothesisStatus.DRAFT for item in registry.records)
    assert all(item.registration_timestamp_utc is None for item in registry.records)
