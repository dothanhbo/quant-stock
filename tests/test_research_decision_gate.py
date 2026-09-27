from __future__ import annotations

import csv
import json
from pathlib import Path
from types import MappingProxyType

import pytest

from quantlab.evaluation import (
    EvidenceStatus,
    ResearchDecision,
    ResearchDecisionEvidenceInput,
    evaluate_research_decision_gate,
    load_phase59b_decision_evidence,
)
from quantlab.evaluation.research_decision_gate import (
    FACTOR_CANDIDATES,
    HORIZONS,
    OUTCOMES,
    POLICY_CANDIDATES,
    SOURCE_ARTIFACTS,
)
from research.run_quantlab_research_decision_gate import run_research_decision_gate


def _grid(candidate_field: str, candidate: str, **values: object) -> list[dict[str, str]]:
    rows = []
    for horizon in HORIZONS:
        for outcome in OUTCOMES:
            row = {
                candidate_field: candidate,
                "horizon_sessions": str(horizon),
                "outcome_field": outcome,
                "identity": f"{candidate}-{horizon}-{outcome}-{candidate_field}",
            }
            row.update({key: str(value).lower() if isinstance(value, bool) else str(value) for key, value in values.items()})
            rows.append(row)
    return rows


def _source(*, reverse: bool = False) -> ResearchDecisionEvidenceInput:
    tables: dict[str, list[dict[str, str]]] = {name: [] for name in SOURCE_ARTIFACTS}
    for factor in FACTOR_CANDIDATES:
        tables["factor_coverage.csv"].extend(_grid("factor", factor, descriptive_review_eligible=True))
        tables["factor_summary.csv"].extend(_grid(
            "factor", factor, mean_daily_rank_ic=0.01,
            mean_daily_high_minus_low_mean_spread=0.1,
        ))
        temporal_support = factor not in {"rsi_14", "volume_ratio_20", "stock_return_20d_pct", "return_3d_pct"}
        tables["temporal_stability_summary.csv"].extend(_grid(
            "factor", factor,
            descriptive_temporal_support=temporal_support,
            coverage_sufficient_for_temporal_review=True,
            all_blocks_negative_ic=False,
            all_blocks_negative_spread=False,
        ))
    for index, first in enumerate(FACTOR_CANDIDATES):
        for second in FACTOR_CANDIDATES[index + 1:]:
            tables["factor_redundancy_summary.csv"].append({
                "first_factor": first, "second_factor": second,
                "identity": f"redundancy-{first}-{second}",
            })
    incremental = {
        "adx_14": ("adx_given_rsi", 0.02, True),
        "atr_percent_14": ("atr_given_adx_rsi", -0.02, False),
        "rsi_14": ("rsi_given_adx", 0.01, False),
        "volume_ratio_20": ("volume_given_adx_rsi", 0.01, False),
        "stock_return_20d_pct": ("stock_return_20d_given_rsi", 0.01, False),
        "ema20_distance_pct": ("ema20_distance_given_rsi", 0.01, False),
    }
    for factor, (hypothesis, mean, blocks_positive) in incremental.items():
        rows = _grid(
            "target_factor", factor, hypothesis_name=hypothesis,
            mean_daily_partial_rank_ic=mean,
            all_blocks_positive_partial_rank_ic=blocks_positive,
        )
        tables["factor_incremental_summary.csv"].extend(rows)
    for policy in POLICY_CANDIDATES:
        rows = _grid(
            "policy_name", policy, policy_fingerprint=f"fingerprint-{policy}",
            mean_daily_rank_ic=0.02, mean_daily_mean_spread=0.2,
            all_blocks_positive_ic=policy == "ADX_ONLY",
            all_blocks_positive_spread=True,
            all_blocks_negative_ic=False, all_blocks_negative_spread=False,
        )
        tables["composite_policy_summary.csv"].extend(rows)
    contrasts = (
        ("ADX_RSI_vs_ADX", "ADX_RSI_EQUAL_WEIGHT", "ADX_ONLY", -0.01, 0.01),
        ("ADX_RSI_vs_RSI", "ADX_RSI_EQUAL_WEIGHT", "RSI_ONLY", 0.01, 0.01),
        ("VOLUME_ADDON_vs_ADX_RSI", "ADX_RSI_VOLUME_EQUAL_WEIGHT", "ADX_RSI_EQUAL_WEIGHT", 0.01, -0.01),
    )
    for name, variant, reference, ic_delta, spread_delta in contrasts:
        for row in _grid("contrast_name", name, variant_policy=variant, reference_policy=reference, mean_daily_ic_delta=ic_delta, mean_daily_spread_delta=spread_delta):
            tables["composite_contrast_summary.csv"].append(row)
    for policy in ("ADX_ONLY", "ADX_RSI_EQUAL_WEIGHT"):
        for budget in (5, 10, 20):
            tables["policy_selection_turnover_summary.csv"].append({
                "policy_name": policy, "selection_budget": str(budget),
                "scope_name": "whole_period", "mean_one_way_turnover": "999.0",
                "identity": f"turnover-{policy}-{budget}",
            })
    for budget in (5, 10, 20):
        tables["policy_selection_overlap_summary.csv"].append({
            "selection_budget": str(budget), "scope_name": "whole_period",
            "mean_overlap_coefficient": "0.0", "identity": f"overlap-{budget}",
        })
    frozen = {
        name: tuple(reversed(rows)) if reverse else tuple(rows)
        for name, rows in tables.items()
    }
    return ResearchDecisionEvidenceInput(
        manifest_identity="manifest-identity",
        runner_contract="quantlab.neutral_panel_factor_evaluation_runner",
        runner_version="v6",
        start_date="2018-08-07",
        end_date="2026-09-17",
        source_result_identities=MappingProxyType({"evaluation": "evaluation-identity"}),
        tables=MappingProxyType(frozen),
    )


def _decision(result, candidate: str):
    return next(item for item in result.decisions if item.candidate == candidate)


def test_gate_is_deterministic_ordered_and_presentation_invariant() -> None:
    first = evaluate_research_decision_gate(_source())
    second = evaluate_research_decision_gate(_source(reverse=True))
    assert first.identity == second.identity
    assert first.decisions == second.decisions
    assert tuple((item.candidate_type, item.candidate) for item in first.decisions) == (
        tuple(("factor", factor) for factor in FACTOR_CANDIDATES)
        + tuple(("policy", policy) for policy in POLICY_CANDIDATES)
    )


def test_missing_incremental_evidence_is_insufficient_not_adverse() -> None:
    result = evaluate_research_decision_gate(_source())
    decision = _decision(result, "relative_strength_20d_pct_points")
    evidence = next(item for item in result.evidence if item.candidate == decision.candidate and item.dimension == "incremental_contribution")
    assert evidence.status is EvidenceStatus.INSUFFICIENT
    assert decision.decision is ResearchDecision.INSUFFICIENT_EVIDENCE
    assert not decision.adverse_dimensions


def test_frozen_coverage_failure_remains_insufficient() -> None:
    source = _source()
    tables = {name: list(rows) for name, rows in source.tables.items()}
    tables["factor_coverage.csv"][0] = dict(tables["factor_coverage.csv"][0], descriptive_review_eligible="false")
    changed = ResearchDecisionEvidenceInput(
        source.manifest_identity, source.runner_contract, source.runner_version,
        source.start_date, source.end_date, source.source_result_identities,
        {name: tuple(rows) for name, rows in tables.items()},
    )
    assert _decision(evaluate_research_decision_gate(changed), "atr_percent_14").insufficient_dimensions == ("coverage",)


def test_supportive_evidence_advances_research_without_production_claim() -> None:
    result = evaluate_research_decision_gate(_source())
    adx = _decision(result, "adx_14")
    assert adx.decision is ResearchDecision.ADVANCE
    assert "portfolio" in adx.rationale
    assert any("not production readiness" in item for item in result.limitations)
    assert not hasattr(adx, "score")


def test_mixed_and_structurally_adverse_evidence_have_distinct_decisions() -> None:
    result = evaluate_research_decision_gate(_source())
    assert _decision(result, "rsi_14").decision is ResearchDecision.HOLD
    atr = _decision(result, "atr_percent_14")
    assert atr.decision is ResearchDecision.REJECT_FOR_NOW
    assert "incremental_contribution" in atr.adverse_dimensions
    assert _decision(result, "RSI_ONLY").decision is ResearchDecision.REJECT_FOR_NOW


def test_descriptive_turnover_overlap_and_budgets_cannot_reject_policy() -> None:
    result = evaluate_research_decision_gate(_source())
    adx = _decision(result, "ADX_ONLY")
    assert adx.decision is ResearchDecision.HOLD
    evidence = {item.dimension: item for item in result.evidence if item.candidate == "ADX_ONLY"}
    assert evidence["selection_turnover"].status is EvidenceStatus.NEUTRAL
    assert evidence["selection_overlap"].status is EvidenceStatus.NEUTRAL
    assert evidence["budget_sensitivity"].observation_summary["observed_budgets"] == (5, 10, 20)
    assert not evidence["selection_turnover"].decision_relevant


def _write_canonical_fixture(root: Path, source: ResearchDecisionEvidenceInput) -> None:
    root.mkdir()
    artifacts: dict[str, int] = {}
    for name, rows in source.tables.items():
        columns = tuple(dict.fromkeys(key for row in rows for key in row))
        with (root / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        artifacts[name] = len(rows)
    manifest = {
        "runner_contract": source.runner_contract,
        "runner_version": source.runner_version,
        "completed": True,
        "requested_bounds": {"start_date": source.start_date, "end_date": source.end_date},
        "artifacts": artifacts,
        "evaluation": {"result_identity": "evaluation"},
        "temporal_stability": {"temporal_result_identity": "temporal"},
        "factor_redundancy": {"result_identity": "redundancy"},
        "factor_incremental_analysis": {"result_identity": "incremental"},
        "composite_comparison": {"result_identity": "composite"},
        "policy_selection_diagnostics": {"result_identity": "selection"},
        "research_dataset": {"identity": "dataset", "content_identity": "content"},
    }
    (root / "experiment_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_artifact_adapter_and_in_memory_engine_agree(tmp_path: Path) -> None:
    source = _source()
    root = tmp_path / "canonical"
    _write_canonical_fixture(root, source)
    loaded = load_phase59b_decision_evidence(root)
    expected = evaluate_research_decision_gate(loaded)
    repeated = evaluate_research_decision_gate(load_phase59b_decision_evidence(root))
    assert expected == repeated
    assert expected.identity == repeated.identity


def test_runner_emits_compact_provenance_complete_artifacts_without_scores(tmp_path: Path) -> None:
    source_root = tmp_path / "canonical"
    output_root = tmp_path / "decision"
    _write_canonical_fixture(source_root, _source())
    before = {item.name: item.read_bytes() for item in source_root.iterdir()}
    result = run_research_decision_gate(input_root=source_root, output_root=output_root)
    assert sorted(item.name for item in output_root.iterdir()) == [
        "research_decision_evidence.csv", "research_decision_manifest.json",
        "research_decision_report.md", "research_decision_summary.csv",
    ]
    manifest = json.loads((output_root / "research_decision_manifest.json").read_text())
    assert manifest["completed"] is True
    assert manifest["input_runner_version"] == "v6"
    assert manifest["no_portfolio_backtest_pnl_evidence_used"] is True
    assert manifest["no_numeric_overall_score_or_candidate_ranking"] is True
    assert manifest["candidate_count"] == 12
    assert manifest["evidence_record_count"] == 64
    assert {item.name: item.read_bytes() for item in source_root.iterdir()} == before
    summary_header = (output_root / "research_decision_summary.csv").read_text().splitlines()[0]
    assert "score" not in summary_header.lower()
    assert len(result.decisions) == 12


def test_runner_protects_existing_output_and_canonical_input(tmp_path: Path) -> None:
    source_root = tmp_path / "canonical"
    output_root = tmp_path / "decision"
    _write_canonical_fixture(source_root, _source())
    output_root.mkdir()
    marker = output_root / "marker.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError, match="already exists"):
        run_research_decision_gate(input_root=source_root, output_root=output_root)
    assert marker.read_text(encoding="utf-8") == "keep"
    with pytest.raises(ValueError, match="must not overwrite"):
        run_research_decision_gate(input_root=source_root, output_root=source_root)


def test_result_contract_is_immutable_and_rejects_incomplete_grids() -> None:
    result = evaluate_research_decision_gate(_source())
    with pytest.raises(TypeError):
        result.source_result_identities["new"] = "value"  # type: ignore[index]
    source = _source()
    tables = dict(source.tables)
    tables["factor_summary.csv"] = tables["factor_summary.csv"][:-1]
    broken = ResearchDecisionEvidenceInput(
        source.manifest_identity, source.runner_contract, source.runner_version,
        source.start_date, source.end_date, source.source_result_identities, tables,
    )
    with pytest.raises(ValueError, match="incomplete or duplicate evidence grid"):
        evaluate_research_decision_gate(broken)
