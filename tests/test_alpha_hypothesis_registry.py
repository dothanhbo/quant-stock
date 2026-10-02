from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from quantlab.hypotheses import (
    ALPHA_HYPOTHESIS_REGISTRY_VERSION,
    AlphaHypothesis,
    AlphaHypothesisRegistry,
    AssessmentState,
    EvidenceOrigin,
    EvidenceReference,
    ExpectedSign,
    HypothesisStatus,
    LifecycleEvent,
    MinimumEconomicEffect,
    ProspectiveEvaluationWindow,
    ResearchBoundaryAssessments,
    load_alpha_hypothesis_registry,
    revise_hypothesis,
    transition_hypothesis,
)


CREATED = "2026-10-02T02:25:17Z"
REGISTERED = "2026-10-03T00:00:00Z"


def _retrospective(identity: str = "historical-result") -> EvidenceReference:
    return EvidenceReference(
        EvidenceOrigin.RETROSPECTIVE,
        "research_results/example/manifest.json",
        identity,
        "artifact-sha256",
        "2026-09-17",
    )


def _draft(**changes) -> AlphaHypothesis:
    values = {
        "hypothesis_id": "adx-forward-association-v1",
        "registry_schema_version": ALPHA_HYPOTHESIS_REGISTRY_VERSION,
        "created_at_utc": CREATED,
        "revision": 1,
        "research_question": "Does higher same-date ADX predict higher forward return?",
        "factor_policy_reference": "adx_14",
        "factor_policy_version": "quantlab-feature-v1",
        "expected_relationship": "Higher ADX is associated with higher forward return.",
        "expected_sign": ExpectedSign.POSITIVE,
        "universe_definition": "Point-in-time database coverage, 50 history / 5 staleness.",
        "universe_limitations": ("Database coverage is not historical VN100 membership.",),
        "feature_availability_timing": "Available only after the completed signal-session close.",
        "outcome_definition": "Stock and VNINDEX-relative forward returns evaluated separately.",
        "evaluation_horizons_sessions": (5, 10),
        "historical_discovery_cutoff": "2026-09-17",
        "registration_timestamp_utc": None,
        "evidence_references": (_retrospective(),),
        "evidence_family": "neutral_directional_factor_family_v1",
        "prospective_evaluation_window": ProspectiveEvaluationWindow("2026-10-02", "2027-10-02"),
        "minimum_sample_count": 100,
        "minimum_coverage_pct": 90.0,
        "minimum_economic_effect": MinimumEconomicEffect("mean_daily_rank_ic", "GTE", 0.01, "correlation"),
        "falsification_conditions": ("Mean rank IC is below the frozen minimum effect.",),
        "methodological_limitations": ("Forward outcomes overlap and require dependence-aware inference.",),
        "dependencies": (),
        "status": HypothesisStatus.DRAFT,
        "assessments": ResearchBoundaryAssessments(AssessmentState.PASS),
        "parent_revision_identity": None,
        "lifecycle_history": (LifecycleEvent(None, HypothesisStatus.DRAFT, CREATED, "Initial draft."),),
    }
    values.update(changes)
    return AlphaHypothesis(**values)


def _registered() -> AlphaHypothesis:
    return transition_hypothesis(
        _draft(), HypothesisStatus.REGISTERED,
        occurred_at_utc=REGISTERED, reason="Explicit prospective registration.",
    )


def test_schema_validation_and_frozen_contract() -> None:
    item = _draft()
    assert item.registry_schema_version == "v1"
    with pytest.raises(FrozenInstanceError):
        item.research_question = "mutated"  # type: ignore[misc]
    with pytest.raises(ValueError, match="schema version"):
        _draft(registry_schema_version="v2")


def test_fingerprint_and_registry_order_are_deterministic() -> None:
    first = _draft()
    second = _draft(
        hypothesis_id="volume-forward-association-v1",
        evidence_references=(_retrospective("volume-history"),),
    )
    assert _draft().specification_fingerprint == first.specification_fingerprint
    assert AlphaHypothesisRegistry((second, first)).registry_fingerprint == AlphaHypothesisRegistry((first, second)).registry_fingerprint


def test_registry_rejects_duplicate_hypothesis_revision() -> None:
    item = _draft()
    with pytest.raises(ValueError, match="duplicate hypothesis identity/revision"):
        AlphaHypothesisRegistry((item, item))


def test_missing_registered_declarations_fail_but_remain_explicit_in_draft() -> None:
    incomplete = _draft(
        expected_relationship=None,
        expected_sign=ExpectedSign.UNKNOWN,
        prospective_evaluation_window=None,
        minimum_sample_count=None,
        minimum_coverage_pct=None,
        minimum_economic_effect=None,
        falsification_conditions=(),
    )
    assert incomplete.status is HypothesisStatus.DRAFT
    with pytest.raises(ValueError, match="incomplete declarations"):
        transition_hypothesis(
            incomplete, HypothesisStatus.REGISTERED,
            occurred_at_utc=REGISTERED, reason="Should fail.",
        )


def test_registered_specification_is_immutable_across_lifecycle_transitions() -> None:
    registered = _registered()
    evaluating = transition_hypothesis(
        registered, HypothesisStatus.EVALUATING,
        occurred_at_utc="2026-10-04T00:00:00Z", reason="Evaluation began.",
    )
    assert evaluating.specification_fingerprint == registered.specification_fingerprint
    assert evaluating.identity != registered.identity


def test_revision_is_explicit_and_content_addressed() -> None:
    registered = _registered()
    revised = revise_hypothesis(
        registered,
        created_at_utc="2026-10-05T00:00:00Z",
        reason="New horizon requires a new revision.",
        changes={"evaluation_horizons_sessions": (5, 10, 20)},
    )
    assert revised.revision == 2
    assert revised.status is HypothesisStatus.DRAFT
    assert revised.parent_revision_identity == registered.identity
    assert revised.registration_timestamp_utc is None
    assert revised.specification_fingerprint != registered.specification_fingerprint
    registry = AlphaHypothesisRegistry((registered, revised))
    assert len(registry.records) == 2


def test_invalid_lifecycle_transitions_and_historical_promotion_are_rejected() -> None:
    with pytest.raises(ValueError, match="DRAFT -> SUPPORTED"):
        transition_hypothesis(
            _draft(), HypothesisStatus.SUPPORTED,
            occurred_at_utc=REGISTERED, reason="Historical evidence cannot promote.",
        )
    with pytest.raises(ValueError, match="REGISTERED -> FALSIFIED"):
        transition_hypothesis(
            _registered(), HypothesisStatus.FALSIFIED,
            occurred_at_utc="2026-10-04T00:00:00Z", reason="Evaluation was never started.",
        )


def test_retrospective_and_prospective_evidence_cannot_be_relabelled() -> None:
    with pytest.raises(ValueError, match="retrospective evidence"):
        EvidenceReference(
            EvidenceOrigin.RETROSPECTIVE, "artifact.csv", "identity", None,
            "2026-09-17", "2026-10-03T00:00:00Z",
        )
    premature = EvidenceReference(
        EvidenceOrigin.PROSPECTIVE, "future.csv", "future", None,
        "2026-10-04", "2026-10-02T00:00:00Z",
    )
    with pytest.raises(ValueError, match="cannot predate registration"):
        _draft(
            status=HypothesisStatus.REGISTERED,
            registration_timestamp_utc=REGISTERED,
            evidence_references=(_retrospective(), premature),
            lifecycle_history=(
                LifecycleEvent(None, HypothesisStatus.DRAFT, CREATED, "Draft."),
                LifecycleEvent(HypothesisStatus.DRAFT, HypothesisStatus.REGISTERED, REGISTERED, "Registered."),
            ),
        )


def test_supported_does_not_imply_alpha_validation_or_production_readiness() -> None:
    evaluating = transition_hypothesis(
        _registered(), HypothesisStatus.EVALUATING,
        occurred_at_utc="2026-10-04T00:00:00Z", reason="Evaluation began.",
    )
    supported = transition_hypothesis(
        evaluating, HypothesisStatus.SUPPORTED,
        occurred_at_utc="2027-10-03T00:00:00Z", reason="Frozen registry criteria met.",
    )
    assert supported.assessments.alpha_validated is AssessmentState.NOT_ASSESSED
    assert supported.assessments.production_ready is AssessmentState.NOT_ASSESSED


def test_initial_inventory_loads_as_draft_retrospective_candidates() -> None:
    path = Path("research/alpha_hypotheses/registry_v1.json")
    registry = load_alpha_hypothesis_registry(path)
    assert [item.hypothesis_id for item in registry.records] == [
        "frozen-q70-historical-policy-v1",
        "neutral-adx-directional-association-v1",
        "neutral-adx-only-portfolio-v1",
    ]
    assert all(item.status is HypothesisStatus.DRAFT for item in registry.records)
    assert all(item.registration_timestamp_utc is None for item in registry.records)
    assert all(item.prospective_evaluation_window is None for item in registry.records)
    assert all({ref.origin for ref in item.evidence_references} == {EvidenceOrigin.RETROSPECTIVE} for item in registry.records)
    assert all(item.assessments.alpha_validated is AssessmentState.NOT_ASSESSED for item in registry.records)
    assert all(item.assessments.production_ready is AssessmentState.NOT_ASSESSED for item in registry.records)


def test_persisted_fingerprints_detect_manifest_tampering(tmp_path: Path) -> None:
    source = Path("research/alpha_hypotheses/registry_v1.json")
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["records"][0]["research_question"] = "tampered"
    changed = tmp_path / "registry.json"
    changed.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        load_alpha_hypothesis_registry(changed)
