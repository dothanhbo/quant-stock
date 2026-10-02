from __future__ import annotations

import csv
from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from quantctl.decision_gate import (
    DecisionArtifactState,
    DecisionCandidateType,
    inspect_decision_gate_catalog,
    resolve_decision_context,
)


def _csv(path: Path, rows: tuple[dict[str, object], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _artifacts(root: Path) -> None:
    results = root / "research_results"
    gate = results / "quantlab_research_decision_gate_2018-08-07_2026-09-17"
    _json(gate / "research_decision_manifest.json", {
        "completed": True,
        "decision_result_identity": "factor-gate-result",
        "input_date_range": {"start_date": "2018-08-07", "end_date": "2026-09-17"},
        "input_canonical_root": str(results / "neutral-source"),
        "limitations": ["historical evidence is not independent prospective confirmation"],
    })
    _csv(gate / "research_decision_summary.csv", (
        {
            "candidate_type": "factor", "candidate": "adx_14", "decision": "ADVANCE",
            "reason_codes": "ALL_FROZEN_SIGNAL_DIRECTIONS_POSITIVE",
            "candidate_identity": "adx-candidate", "identity": "adx-decision",
        },
        {
            "candidate_type": "policy", "candidate": "ADX_ONLY", "decision": "HOLD",
            "reason_codes": "FROZEN_POLICY_CONTRASTS_MIXED",
            "candidate_identity": "policy-candidate", "identity": "policy-decision",
        },
    ))
    _csv(gate / "research_decision_evidence.csv", (
        {
            "candidate_type": "factor", "candidate": "adx_14", "dimension": "coverage",
            "status": "SUPPORTIVE", "defined": "true", "decision_relevant": "true",
            "source_artifact": "factor_coverage.csv", "source_identity": "coverage-source",
            "observation_summary": "{}", "reason_code": "FROZEN_COVERAGE_ELIGIBLE",
            "rationale": "Frozen coverage is complete.", "identity": "adx-coverage",
        },
        {
            "candidate_type": "factor", "candidate": "adx_14", "dimension": "redundancy",
            "status": "NEUTRAL", "defined": "true", "decision_relevant": "false",
            "source_artifact": "factor_redundancy_summary.csv", "source_identity": "redundancy-source",
            "observation_summary": "{}", "reason_code": "REDUNDANCY_DESCRIPTIVE_NO_FROZEN_CUTOFF",
            "rationale": "No frozen redundancy cutoff exists.", "identity": "adx-redundancy",
        },
        {
            "candidate_type": "policy", "candidate": "ADX_ONLY", "dimension": "paired_contrasts",
            "status": "MIXED", "defined": "true", "decision_relevant": "true",
            "source_artifact": "composite_contrast_summary.csv", "source_identity": "contrast-source",
            "observation_summary": "{}", "reason_code": "FROZEN_POLICY_CONTRASTS_MIXED",
            "rationale": "Persisted contrasts are mixed.", "identity": "policy-contrast",
        },
    ))

    portfolio = results / "quantlab_portfolio_research_synthesis_2018-08-07_2026-09-17"
    _json(portfolio / "portfolio_research_synthesis_manifest.json", {
        "completed": True,
        "result_identity": "portfolio-result",
        "selection_policy": "ADX_ONLY",
        "temporal_partition": [{"start_date": "2018-08-07", "end_date": "2026-09-17"}],
        "limitations": ["overlapping outcomes are not an executable return path"],
    })
    _csv(portfolio / "portfolio_research_decisions.csv", ({
        "requested_budget": "5", "horizon_sessions": "5",
        "decision": "ADVANCE_TO_FORWARD_VALIDATION",
        "reason_code": "COHERENT_DEFINED_EVIDENCE_WITH_NO_ADVERSE_DIMENSION",
        "identity": "portfolio-decision",
    },))
    _csv(portfolio / "portfolio_research_evidence.csv", (
        {
            "requested_budget": "5", "horizon_sessions": "5", "dimension": "OUTCOME_COVERAGE",
            "status": "SUPPORTIVE", "reason_code": "OUTCOMES_DEFINED_IN_EVERY_FROZEN_BLOCK",
            "identity": "portfolio-coverage",
        },
        {
            "requested_budget": "5", "horizon_sessions": "5", "dimension": "PATH_LIMITATION",
            "status": "NOT_APPLICABLE", "reason_code": "OVERLAPPING_FORWARD_HORIZONS",
            "identity": "portfolio-path",
        },
    ))

    risk = results / "quantlab_risk_policy_decision_2018-08-07_2026-09-17"
    _json(risk / "risk_policy_decision_manifest.json", {
        "completed": True,
        "result_identity": "risk-result",
        "limitations": ["no production authorization is granted"],
    })
    _csv(risk / "risk_policy_decision_summary.csv", ({
        "policy": "VOLATILITY_SCALING", "policy_fingerprint": "risk-fingerprint",
        "decision": "ELIGIBLE_FOR_FUTURE_FORWARD_PROTOCOL",
        "reason_code": "DECLARED_PURPOSE_VALID_WITH_COMPLETE_FROZEN_EVIDENCE",
        "identity": "risk-decision",
    },))
    _csv(risk / "risk_policy_decision_evidence.csv", ({
        "policy": "VOLATILITY_SCALING", "dimension": "ECONOMIC_TRADE_OFF",
        "status": "TRADE_OFF_PRESENT", "decision_relevant": "False",
        "reason_code": "DESCRIPTIVE_ECONOMIC_TRADE_OFF_PRESENT",
        "observation_summary": "{}", "identity": "risk-tradeoff",
    },))


def test_catalog_reproduces_canonical_decisions_dimensions_and_provenance(tmp_path: Path) -> None:
    _artifacts(tmp_path)
    catalog = inspect_decision_gate_catalog(root=tmp_path, environ={})

    assert len(catalog.candidates) == 4
    adx = catalog.candidate("factor:adx_14")
    assert adx is not None
    assert adx.disposition == "ADVANCE"
    assert adx.decision_reason == "ALL_FROZEN_SIGNAL_DIRECTIONS_POSITIVE"
    assert adx.candidate_identity == "adx-candidate"
    assert adx.artifact_identity == "factor-gate-result"
    assert [(item.dimension, item.status, item.reason_code) for item in adx.dimensions] == [
        ("coverage", "SUPPORTIVE", "FROZEN_COVERAGE_ELIGIBLE"),
        ("redundancy", "NEUTRAL", "REDUNDANCY_DESCRIPTIVE_NO_FROZEN_CUTOFF"),
    ]
    assert adx.contract_bound_next_step is None
    assert "redundancy" in adx.mixed_or_uncertain_dimensions
    assert all(item.state is DecisionArtifactState.AVAILABLE for item in catalog.artifacts)


def test_only_explicit_contract_bound_advancement_is_projected(tmp_path: Path) -> None:
    _artifacts(tmp_path)
    catalog = inspect_decision_gate_catalog(root=tmp_path, environ={})

    assert catalog.candidate("factor:adx_14").contract_bound_next_step is None  # type: ignore[union-attr]
    assert catalog.candidate("policy:ADX_ONLY").contract_bound_next_step is None  # type: ignore[union-attr]
    assert catalog.candidate("portfolio:5:5").contract_bound_next_step == "ADVANCE_TO_FORWARD_VALIDATION"  # type: ignore[union-attr]
    assert catalog.candidate("risk:VOLATILITY_SCALING").contract_bound_next_step == "ELIGIBLE_FOR_FUTURE_FORWARD_PROTOCOL"  # type: ignore[union-attr]
    assert not any(hasattr(item, "score") or hasattr(item, "winner") for item in catalog.candidates)


def test_research_decision_and_operational_readiness_remain_separate(tmp_path: Path) -> None:
    _artifacts(tmp_path)
    catalog = inspect_decision_gate_catalog(root=tmp_path, environ={})
    candidate = catalog.candidate("portfolio:5:5")

    assert candidate is not None and candidate.disposition == "ADVANCE_TO_FORWARD_VALIDATION"
    assert catalog.readiness.available is False
    assert catalog.readiness.readiness == "UNAVAILABLE"
    assert candidate.blockers == ()
    assert all("Repository identity" not in item.reason_code for item in candidate.dimensions)
    assert catalog.production_replacement.note


def test_context_resolution_preserves_identity_and_never_substitutes(tmp_path: Path) -> None:
    _artifacts(tmp_path)
    catalog = inspect_decision_gate_catalog(root=tmp_path, environ={})

    factor = resolve_decision_context(catalog, {"candidate_type": "factor", "factor": "adx_14"})
    portfolio = resolve_decision_context(
        catalog,
        {"candidate_type": "portfolio_configuration", "budget": 5, "horizon_sessions": 5},
    )
    unsupported = resolve_decision_context(
        catalog,
        {"candidate_type": "factor", "factor": "not_persisted"},
    )

    assert factor.candidate is catalog.candidate("factor:adx_14")
    assert portfolio.candidate is catalog.candidate("portfolio:5:5")
    assert unsupported.candidate is None
    assert "no substitute" in unsupported.notice


def test_missing_and_incompatible_artifacts_fail_closed(tmp_path: Path) -> None:
    missing = inspect_decision_gate_catalog(root=tmp_path, environ={})
    assert missing.candidates == ()
    assert all(item.state is DecisionArtifactState.UNAVAILABLE for item in missing.artifacts)

    _artifacts(tmp_path)
    summary = (
        tmp_path / "research_results" / "quantlab_research_decision_gate_2018-08-07_2026-09-17"
        / "research_decision_summary.csv"
    )
    _csv(summary, ({"wrong": "schema"},))
    incompatible = inspect_decision_gate_catalog(root=tmp_path, environ={})
    factor_status = next(item for item in incompatible.artifacts if item.family == "Factor / selection policy")
    assert factor_status.state is DecisionArtifactState.INCOMPATIBLE
    assert incompatible.candidates_for(DecisionCandidateType.FACTOR) == ()
    assert incompatible.candidates_for(DecisionCandidateType.PORTFOLIO_CONFIGURATION)


def test_adapter_is_immutable_read_only_and_imports_no_operational_surface(tmp_path: Path) -> None:
    _artifacts(tmp_path)
    before = {
        path: path.read_bytes()
        for path in (tmp_path / "research_results").rglob("*")
        if path.is_file()
    }
    catalog = inspect_decision_gate_catalog(root=tmp_path, environ={})
    after = {path: path.read_bytes() for path in before}

    assert before == after
    assert not (tmp_path / "data").exists()
    with pytest.raises(FrozenInstanceError):
        catalog.current_research_stage = "changed"  # type: ignore[misc]
    source = (Path(__file__).resolve().parents[1] / "quantctl" / "decision_gate.py").read_text(encoding="utf-8")
    forbidden = (
        "sqlite3.connect", ".initialize(", ".append(record", "capture_", "run_backtest(",
        "run_forward_validation", "requests.", "vnstock", "strategy.scanner",
    )
    assert all(item not in source for item in forbidden)
