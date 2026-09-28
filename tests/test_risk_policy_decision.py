from __future__ import annotations

from dataclasses import replace
import inspect
import json
from pathlib import Path

import pytest

from quantlab.portfolio import (
    DECISION_GATE_TIMING,
    FORWARD_PROTOCOL_V1,
    DecisionEvidenceStatus,
    PolicyDecisionFacts,
    RiskPolicyDecision,
    RiskPolicyDecisionInput,
    evaluate_risk_policy_decision_gate,
)
from quantlab.portfolio.risk_policy_decision import (
    EXPECTED_PHASE10A_RESULT,
    EXPECTED_PHASE10A_SPEC,
    EXPECTED_PHASE10B_RESULT,
    EXPECTED_PHASE10B_SPEC,
    EXPECTED_PHASE10C_RESULT,
    EXPECTED_PHASE10C_SPEC,
    EXPECTED_POLICY_FINGERPRINTS,
)
from research import run_quantlab_risk_policy_decision as runner


def _facts(policy: str, **changes) -> PolicyDecisionFacts:
    values = {
        "policy": policy,
        "policy_fingerprint": EXPECTED_POLICY_FINGERPRINTS[policy],
        "structural_observation_count": 100,
        "structural_failure_count": 0,
        "applied_count": 50 if policy == "VOLATILITY_SCALING" else 0,
        "defined_applied_risk_count": 50 if policy == "VOLATILITY_SCALING" else 0,
        "mechanical_failure_count": 0,
        "complete_summary_grid": True,
        "evaluable_summary_count": 9,
        "expected_summary_count": 9,
        "temporal_blocks_with_evidence": 4,
        "expected_temporal_blocks": 4,
        "temporal_mechanical_failure_count": 0,
        "economic_contrast_defined_count": 18 if policy == "VOLATILITY_SCALING" else 0,
        "economic_positive_delta_count": 3 if policy == "VOLATILITY_SCALING" else 0,
        "economic_zero_delta_count": 0,
        "economic_negative_delta_count": 15 if policy == "VOLATILITY_SCALING" else 0,
        "path_metric_status": "NOT_APPLICABLE",
        "path_metric_reason": "OVERLAPPING_FORWARD_HORIZONS",
    }
    values.update(changes)
    return PolicyDecisionFacts(**values)


def _source(*, scaled: PolicyDecisionFacts | None = None, reverse: bool = False) -> RiskPolicyDecisionInput:
    facts = (_facts("NO_RISK_POLICY"), scaled or _facts("VOLATILITY_SCALING"))
    if reverse: facts = tuple(reversed(facts))
    return RiskPolicyDecisionInput(
        EXPECTED_PHASE10A_RESULT, EXPECTED_PHASE10A_SPEC,
        EXPECTED_PHASE10B_RESULT, EXPECTED_PHASE10B_SPEC,
        EXPECTED_PHASE10C_RESULT, EXPECTED_PHASE10C_SPEC,
        {"phase10a": "a", "phase10b": "b", "phase10c": "c"},
        EXPECTED_POLICY_FINGERPRINTS, facts,
    )


def _decision(result, policy: str):
    return next(item for item in result.decisions if item.policy == policy)


def _evidence(result, policy: str, dimension: str):
    return next(item for item in result.evidence if item.policy == policy and item.dimension == dimension)


def test_exact_upstream_provenance_and_policy_fingerprints_are_required() -> None:
    for field in ("phase10a_result_identity", "phase10b_specification_fingerprint", "phase10c_result_identity", "phase10c_specification_fingerprint"):
        with pytest.raises(ValueError, match="provenance mismatch"):
            evaluate_risk_policy_decision_gate(replace(_source(), **{field: "changed"}))
    with pytest.raises(ValueError, match="policy fingerprint"):
        evaluate_risk_policy_decision_gate(replace(_source(), policy_fingerprints={**EXPECTED_POLICY_FINGERPRINTS, "VOLATILITY_SCALING": "changed"}))


def test_structural_or_mechanical_violation_is_adverse_and_rejected() -> None:
    for changes in ({"structural_failure_count": 1}, {"mechanical_failure_count": 1}, {"temporal_mechanical_failure_count": 1}):
        result = evaluate_risk_policy_decision_gate(_source(scaled=_facts("VOLATILITY_SCALING", **changes)))
        assert _decision(result, "VOLATILITY_SCALING").decision is RiskPolicyDecision.REJECT_FOR_NOW
        assert any(item.status is DecisionEvidenceStatus.ADVERSE for item in result.evidence if item.policy == "VOLATILITY_SCALING")


def test_missing_required_evidence_is_insufficient() -> None:
    scaled = _facts("VOLATILITY_SCALING", complete_summary_grid=False, evaluable_summary_count=8)
    result = evaluate_risk_policy_decision_gate(_source(scaled=scaled))
    assert _decision(result, "VOLATILITY_SCALING").decision is RiskPolicyDecision.INSUFFICIENT_EVIDENCE


def test_economic_underperformance_alone_does_not_imply_rejection() -> None:
    scaled = _facts("VOLATILITY_SCALING", economic_positive_delta_count=0, economic_negative_delta_count=18)
    result = evaluate_risk_policy_decision_gate(_source(scaled=scaled))
    assert _evidence(result, "VOLATILITY_SCALING", "ECONOMIC_TRADE_OFF").status is DecisionEvidenceStatus.TRADE_OFF_PRESENT
    assert _decision(result, "VOLATILITY_SCALING").decision is RiskPolicyDecision.ELIGIBLE_FOR_FUTURE_FORWARD_PROTOCOL


def test_mechanical_risk_reduction_alone_does_not_imply_eligibility_or_superiority() -> None:
    scaled = _facts("VOLATILITY_SCALING", complete_summary_grid=False, evaluable_summary_count=0, temporal_blocks_with_evidence=0)
    result = evaluate_risk_policy_decision_gate(_source(scaled=scaled))
    assert _evidence(result, "VOLATILITY_SCALING", "DECLARED_RISK_PURPOSE_EFFECT").status is DecisionEvidenceStatus.SUPPORTIVE
    assert _decision(result, "VOLATILITY_SCALING").decision is RiskPolicyDecision.INSUFFICIENT_EVIDENCE
    assert not hasattr(result, "winner") and not hasattr(result, "ranking")


def test_baseline_is_a_control_not_an_intervention_or_rejected_policy() -> None:
    result = evaluate_risk_policy_decision_gate(_source())
    baseline = _decision(result, "NO_RISK_POLICY")
    assert baseline.role == "CONTROL"
    assert baseline.decision is RiskPolicyDecision.CONTROL_BASELINE
    assert _evidence(result, baseline.policy, "DECLARED_RISK_PURPOSE_EFFECT").status is DecisionEvidenceStatus.NOT_APPLICABLE


def test_overlapping_path_limitation_is_preserved_and_not_treated_as_adverse() -> None:
    result = evaluate_risk_policy_decision_gate(_source())
    path = _evidence(result, "VOLATILITY_SCALING", "PATH_EVIDENCE")
    assert path.status is DecisionEvidenceStatus.NOT_APPLICABLE
    assert path.observation_summary["path_metric_reason"] == "OVERLAPPING_FORWARD_HORIZONS"
    assert not path.decision_relevant


def test_decision_precedence_is_deterministic() -> None:
    adverse_and_missing = _facts("VOLATILITY_SCALING", structural_failure_count=1, complete_summary_grid=False)
    assert _decision(evaluate_risk_policy_decision_gate(_source(scaled=adverse_and_missing)), "VOLATILITY_SCALING").decision is RiskPolicyDecision.REJECT_FOR_NOW
    assert evaluate_risk_policy_decision_gate(_source()) == evaluate_risk_policy_decision_gate(_source(reverse=True))


def test_result_identity_is_deterministic_and_binds_observable_evidence() -> None:
    first = evaluate_risk_policy_decision_gate(_source())
    assert first.identity == evaluate_risk_policy_decision_gate(_source(reverse=True)).identity
    changed = evaluate_risk_policy_decision_gate(_source(scaled=_facts("VOLATILITY_SCALING", economic_positive_delta_count=4, economic_negative_delta_count=14)))
    assert changed.identity != first.identity


def test_contract_contains_no_outcome_tuned_threshold_scoring_or_ranking() -> None:
    source = inspect.getsource(__import__("quantlab.portfolio.risk_policy_decision", fromlist=["x"]))
    for forbidden in ("mean excess delta >", "return sacrifice", "application rate >", "weighted_score"):
        assert forbidden not in source.lower()
    result = evaluate_risk_policy_decision_gate(_source())
    assert not hasattr(result, "score") and not hasattr(result, "winner")


def test_future_eligibility_is_only_for_a_new_protocol_and_v1_is_identity_bound() -> None:
    result = evaluate_risk_policy_decision_gate(_source())
    assert _decision(result, "VOLATILITY_SCALING").decision is RiskPolicyDecision.ELIGIBLE_FOR_FUTURE_FORWARD_PROTOCOL
    assert result.forward_protocol_v1 == FORWARD_PROTOCOL_V1 == "QV-FWD-V1-8c60839fa29fc04e"
    assert any("separate future protocol" in item for item in result.limitations)


def test_post_evidence_formalization_disclosure_is_persisted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "_load_decision_input", lambda *args: _source())
    output = tmp_path / "phase10d"
    result = runner.run_risk_policy_decision(phase10a_root=tmp_path/"a", phase10b_root=tmp_path/"b", phase10c_root=tmp_path/"c", output_root=output)
    manifest = json.loads((output / "risk_policy_decision_manifest.json").read_text(encoding="utf-8"))
    assert result.decision_gate_timing == manifest["decision_gate_timing"] == DECISION_GATE_TIMING
    assert manifest["no_post_hoc_numerical_thresholds"] and manifest["no_ranking"]
    assert manifest["forward_v1_protocol"] == FORWARD_PROTOCOL_V1
    assert set(path.name for path in output.iterdir()) == set(runner.FILES)
    assert len(list(__import__("csv").DictReader((output / runner.FILES[0]).open(encoding="utf-8")))) == 2
    assert len(list(__import__("csv").DictReader((output / runner.FILES[1]).open(encoding="utf-8")))) == 14


def test_runner_fails_closed_on_changed_phase10c_result_before_csv_loading(tmp_path: Path) -> None:
    root = tmp_path / "phase10c"; root.mkdir()
    manifest = {"completed": True, "result_identity": "changed", "specification_fingerprint": EXPECTED_PHASE10C_SPEC, "artifacts": {}}
    (root / "portfolio_risk_policy_outcome_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="provenance mismatch"):
        runner._manifest(root, "portfolio_risk_policy_outcome_manifest.json", EXPECTED_PHASE10C_RESULT, EXPECTED_PHASE10C_SPEC)


def test_contracts_are_immutable_and_runtime_has_no_market_or_forward_dependency() -> None:
    result = evaluate_risk_policy_decision_gate(_source())
    with pytest.raises(TypeError): result.source_identities["x"] = "y"  # type: ignore[index]
    signature = inspect.signature(evaluate_risk_policy_decision_gate)
    assert "snapshot" not in signature.parameters and "database" not in signature.parameters
    runner_source = inspect.getsource(runner)
    assert "market.db" not in runner_source and "forward_validation.db" not in runner_source
