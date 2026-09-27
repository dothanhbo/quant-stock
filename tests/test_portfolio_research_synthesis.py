from __future__ import annotations

import csv
from dataclasses import FrozenInstanceError
from hashlib import sha256
import json
from pathlib import Path

import pytest

from quantlab.portfolio import (
    EvidenceStatus,
    FrozenCostEvidence,
    FrozenOutcomeBlockEvidence,
    FrozenOutcomeSummaryEvidence,
    FrozenStructuralEvidence,
    PortfolioResearchDecision,
    PortfolioResearchSynthesisInput,
    RobustnessFlag,
    evaluate_portfolio_research_synthesis,
    load_portfolio_research_synthesis_input,
)
from quantlab.portfolio.construction import BLOCKS, BUDGETS
from quantlab.portfolio.outcome_evaluation import COST_GRID_BPS, HORIZONS
from research import run_quantlab_portfolio_research_synthesis as runner


def _source(
    *,
    provenance: bool = True,
    total_dates: int = 100,
    evaluable_dates: int = 100,
    mean_excess: float | None = 1.0,
    median_excess: float | None = 0.5,
    positive_rate: float | None = 0.6,
    block_means: tuple[float | None, ...] = (1.0, 1.0, 1.0, 1.0),
    block_labels: tuple[str, ...] = ("SAME_DIRECTION",) * 4,
    maximum_cost_net: float | None = 0.8,
    effective_n: float | None = 3.0,
    max_weight: float | None = 1 / 3,
    turnover: float | None = 0.1,
) -> PortfolioResearchSynthesisInput:
    structures = tuple(
        FrozenStructuralEvidence(
            budget, 100, 0, 0, 1.0, effective_n, max_weight, 1 / 3,
            99 if turnover is not None else 0, turnover, "STRUCTURALLY_VALID", f"structure-{budget}",
        ) for budget in BUDGETS
    )
    outcomes = tuple(
        FrozenOutcomeSummaryEvidence(
            budget, horizon, total_dates, evaluable_dates, mean_excess, median_excess,
            positive_rate, "NOT_APPLICABLE", "OVERLAPPING_FORWARD_HORIZONS",
            f"outcome-{budget}-{horizon}",
        ) for budget in BUDGETS for horizon in HORIZONS
    )
    blocks = tuple(
        FrozenOutcomeBlockEvidence(
            budget, horizon, block[0], evaluable_dates // 4,
            block_means[index], block_labels[index], f"block-{budget}-{horizon}-{block[0]}",
        )
        for budget in BUDGETS for horizon in HORIZONS
        for index, block in enumerate(BLOCKS)
    )
    costs = tuple(
        FrozenCostEvidence(
            budget, horizon, bps, evaluable_dates, max(evaluable_dates - 1, 0),
            mean_excess, 0.0 if bps == 0 else 0.2,
            mean_excess if bps == 0 else maximum_cost_net,
            "HYPOTHETICAL_COST_SENSITIVITY", f"cost-{budget}-{horizon}-{bps}",
        )
        for budget in BUDGETS for horizon in HORIZONS for bps in COST_GRID_BPS
    )
    return PortfolioResearchSynthesisInput(
        {"phase6": "phase6", "phase7": "phase7"}, provenance,
        structures, outcomes, blocks, costs,
    )


def _scenario(result, budget: int = 5, horizon: int = 5):
    return next(item for item in result.decisions if item.requested_budget == budget and item.horizon_sessions == horizon)


def _dimension(result, name: str, budget: int = 5, horizon: int = 5):
    return next(
        item for item in result.evidence
        if item.requested_budget == budget and item.horizon_sessions == horizon and item.dimension == name
    )


def test_supportive_contract_advances_multiple_scenarios_and_path_is_not_applicable() -> None:
    result = evaluate_portfolio_research_synthesis(_source())
    assert len(result.decisions) == 9 and len(result.evidence) == 90
    assert all(item.decision is PortfolioResearchDecision.ADVANCE_TO_FORWARD_VALIDATION for item in result.decisions)
    assert _dimension(result, "PROVENANCE_COMPLETENESS").status is EvidenceStatus.SUPPORTIVE
    assert _dimension(result, "OUTCOME_COVERAGE").status is EvidenceStatus.SUPPORTIVE
    path = _dimension(result, "PATH_LIMITATION")
    assert path.status is EvidenceStatus.NOT_APPLICABLE
    assert path.reason_code == "OVERLAPPING_FORWARD_HORIZONS"


def test_adverse_direction_rejects_every_scenario_so_zero_may_advance() -> None:
    result = evaluate_portfolio_research_synthesis(_source(
        mean_excess=-1.0, median_excess=-0.5, positive_rate=0.4,
        block_means=(-1.0, -1.0, -1.0, -1.0), maximum_cost_net=-1.2,
    ))
    assert all(item.decision is PortfolioResearchDecision.REJECT_FOR_NOW for item in result.decisions)
    assert _dimension(result, "GROSS_EXCESS_DIRECTION").status is EvidenceStatus.ADVERSE
    assert _dimension(result, "POSITIVE_EXCESS_FREQUENCY").status is EvidenceStatus.ADVERSE


def test_missing_evidence_produces_insufficient_decision() -> None:
    result = evaluate_portfolio_research_synthesis(_source(
        provenance=False, evaluable_dates=0, mean_excess=None, median_excess=None,
        positive_rate=None, block_means=(None, None, None, None),
        block_labels=("UNDEFINED",) * 4, maximum_cost_net=None,
    ))
    assert _scenario(result).decision is PortfolioResearchDecision.INSUFFICIENT_EVIDENCE
    assert _dimension(result, "PROVENANCE_COMPLETENESS").status is EvidenceStatus.INSUFFICIENT
    assert _dimension(result, "OUTCOME_COVERAGE").status is EvidenceStatus.INSUFFICIENT


def test_recent_weakening_and_temporal_mixture_hold_without_selecting() -> None:
    weakening = evaluate_portfolio_research_synthesis(_source(block_means=(1.0, 1.1, 1.2, 0.5)))
    assert _scenario(weakening).decision is PortfolioResearchDecision.HOLD
    assert RobustnessFlag.RECENT_WEAKENING.value in _scenario(weakening).flags
    mixed = evaluate_portfolio_research_synthesis(_source(
        block_means=(1.0, -0.2, 1.0, 1.0),
        block_labels=("SAME_DIRECTION", "MIXED", "SAME_DIRECTION", "SAME_DIRECTION"),
    ))
    assert _scenario(mixed).decision is PortfolioResearchDecision.HOLD
    assert RobustnessFlag.TEMPORALLY_MIXED.value in _scenario(mixed).flags


def test_predeclared_low_coverage_cost_concentration_and_burden_flags() -> None:
    result = evaluate_portfolio_research_synthesis(_source(
        evaluable_dates=40, maximum_cost_net=-0.1,
        effective_n=1.5, max_weight=0.6, turnover=0.5,
    ))
    values = set(_scenario(result).flags)
    assert {
        RobustnessFlag.LOW_COVERAGE.value,
        RobustnessFlag.COST_SENSITIVE.value,
        RobustnessFlag.STRUCTURALLY_CONCENTRATED.value,
        RobustnessFlag.HIGH_IMPLEMENTATION_BURDEN.value,
        RobustnessFlag.PATH_EVIDENCE_UNAVAILABLE.value,
    } <= values
    assert _scenario(result).decision is PortfolioResearchDecision.REJECT_FOR_NOW


def test_result_has_no_score_ranking_winner_or_path_performance_fields() -> None:
    result = evaluate_portfolio_research_synthesis(_source())
    for forbidden in ("score", "rank", "winner", "best_budget", "cagr", "sharpe", "drawdown"):
        assert not hasattr(result, forbidden)
        assert not hasattr(result.decisions[0], forbidden)
    assert all(RobustnessFlag.PATH_EVIDENCE_UNAVAILABLE.value in item.flags for item in result.decisions)


def test_ordering_identity_and_immutability_are_deterministic_and_sensitive() -> None:
    first = evaluate_portfolio_research_synthesis(_source())
    second = evaluate_portfolio_research_synthesis(_source())
    assert first == second and first.identity == second.identity
    assert tuple((item.requested_budget, item.horizon_sessions) for item in first.decisions) == tuple(
        (budget, horizon) for budget in BUDGETS for horizon in HORIZONS
    )
    changed = evaluate_portfolio_research_synthesis(_source(positive_rate=0.5))
    assert changed.identity != first.identity
    with pytest.raises(FrozenInstanceError):
        first.identity = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        first.source_identities["x"] = "y"  # type: ignore[index]


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def _write_artifact_fixture(phase6: Path, phase7: Path) -> None:
    phase6.mkdir(); phase7.mkdir()
    source = _source()
    structural_rows = [{
        "candidate_source": "ADX_ONLY", "requested_budget": item.requested_budget,
        "weighting_policy": "EQUAL_WEIGHT", "scope_name": "whole_period",
        "scope_start_date": "2018-08-07", "scope_end_date": "2026-09-17",
        "evaluated_dates": item.evaluated_dates, "underfilled_date_count": item.underfilled_date_count,
        "empty_date_count": item.empty_date_count, "mean_selected_count": 3,
        "median_selected_count": 3, "mean_fill_ratio": item.mean_fill_ratio,
        "median_fill_ratio": item.mean_fill_ratio, "mean_effective_n": item.mean_effective_n,
        "median_effective_n": item.mean_effective_n,
        "mean_max_single_name_weight": item.mean_max_single_name_weight,
        "median_max_single_name_weight": item.mean_max_single_name_weight,
        "mean_herfindahl": item.mean_herfindahl, "median_herfindahl": item.mean_herfindahl,
        "defined_weight_turnover_dates": item.defined_weight_turnover_dates,
        "mean_one_way_weight_turnover": item.mean_one_way_weight_turnover,
        "median_one_way_weight_turnover": item.mean_one_way_weight_turnover,
        "total_additions": 0, "total_removals": 0, "mean_weight_stability": 0.9,
        "structural_state": item.structural_state, "daily_identity_count": 100,
        "daily_identities_sha256": "daily", "identity": item.source_identity,
    } for item in source.structures]
    structural_path = phase6 / "portfolio_construction_summary.csv"
    _write_csv(structural_path, structural_rows)
    manifest6 = {
        "completed": True, "budgets": [5, 10, 20], "candidate_source": "ADX_ONLY",
        "weighting_policies": ["EQUAL_WEIGHT"], "result_identity": "phase6-result",
        "artifacts": {structural_path.name: len(structural_rows)},
    }
    manifest6_path = phase6 / "portfolio_construction_manifest.json"
    manifest6_path.write_text(json.dumps(manifest6), encoding="utf-8")

    summary_rows = [{
        "requested_budget": item.requested_budget, "horizon_sessions": item.horizon_sessions,
        "total_dates": item.total_dates, "evaluable_dates": item.evaluable_dates,
        "empty_dates": 0, "underfilled_dates": 0, "unavailable_constituent_dates": 0,
        "unavailable_benchmark_dates": 0, "censored_dates": 0,
        "mean_stock_return_pct": 2.0, "median_stock_return_pct": 1.0,
        "stock_return_std_pct": 1.0, "minimum_stock_return_pct": -1.0,
        "maximum_stock_return_pct": 3.0, "positive_stock_return_rate": 0.6,
        "mean_benchmark_return_pct": 1.0, "median_benchmark_return_pct": 0.5,
        "mean_excess_return_pct_points": item.mean_excess_return_pct_points,
        "median_excess_return_pct_points": item.median_excess_return_pct_points,
        "excess_return_std_pct_points": 1.0, "minimum_excess_return_pct_points": -1.0,
        "maximum_excess_return_pct_points": 2.0, "positive_excess_rate": item.positive_excess_rate,
        "evidence_state": "OUTCOME_EVIDENCE_DEFINED", "path_metric_status": item.path_metric_status,
        "path_metric_reason": item.path_metric_reason, "identity": item.source_identity,
    } for item in source.outcomes]
    block_rows = [{
        "requested_budget": item.requested_budget, "horizon_sessions": item.horizon_sessions,
        "block_name": item.block_name, "block_start_date": "2018-08-07",
        "block_end_date": "2026-09-17", "total_dates": 25,
        "evaluable_dates": item.evaluable_dates, "mean_stock_return_pct": 2.0,
        "median_stock_return_pct": 1.0, "mean_benchmark_return_pct": 1.0,
        "mean_excess_return_pct_points": item.mean_excess_return_pct_points,
        "median_excess_return_pct_points": item.mean_excess_return_pct_points,
        "positive_stock_return_rate": 0.6, "positive_excess_rate": 0.6,
        "temporal_evidence": item.temporal_evidence, "identity": item.source_identity,
    } for item in source.blocks]
    cost_rows = [{
        "requested_budget": item.requested_budget, "horizon_sessions": item.horizon_sessions,
        "cost_rate_bps": item.cost_rate_bps, "evaluable_date_count": item.evaluable_date_count,
        "turnover_defined_date_count": item.turnover_defined_date_count,
        "mean_gross_stock_return_pct": 2.0,
        "mean_estimated_turnover_cost_pct_points": item.mean_estimated_turnover_cost_pct_points,
        "mean_net_stock_return_pct": 1.8,
        "mean_gross_excess_return_pct_points": item.mean_gross_excess_return_pct_points,
        "mean_net_excess_return_pct_points": item.mean_net_excess_return_pct_points,
        "label": item.label, "identity": item.source_identity,
    } for item in source.costs]
    artifacts = {}
    for name, rows in (
        ("portfolio_outcome_summary.csv", summary_rows),
        ("portfolio_outcome_by_block.csv", block_rows),
        ("portfolio_cost_sensitivity.csv", cost_rows),
    ):
        path = phase7 / name; _write_csv(path, rows)
        artifacts[name] = {"rows": len(rows), "sha256": sha256(path.read_bytes()).hexdigest()}
    manifest7 = {
        "completed": True, "budgets": [5, 10, 20], "horizons": [5, 10, 20],
        "selection_policy": "ADX_ONLY", "weighting_policy": "EQUAL_WEIGHT",
        "path_metric_applicability": "NOT_APPLICABLE",
        "path_metric_reason": "OVERLAPPING_FORWARD_HORIZONS",
        "cost_sensitivity_grid_bps": [0, 10, 25, 50], "result_identity": "phase7-result",
        "source_identities": {
            "phase6_manifest_sha256": sha256(manifest6_path.read_bytes()).hexdigest(),
            "phase6_result_identity": "phase6-result",
        },
        "artifacts": artifacts,
    }
    (phase7 / "portfolio_outcome_manifest.json").write_text(json.dumps(manifest7), encoding="utf-8")


def test_adapter_verifies_provenance_and_rejects_artifact_hash_drift(tmp_path: Path) -> None:
    phase6, phase7 = tmp_path / "phase6", tmp_path / "phase7"
    _write_artifact_fixture(phase6, phase7)
    first = load_portfolio_research_synthesis_input(phase6, phase7)
    second = load_portfolio_research_synthesis_input(phase6, phase7)
    assert first == second and first.provenance_verified
    assert len(first.structures) == 3 and len(first.outcomes) == 9
    path = phase7 / "portfolio_outcome_summary.csv"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        load_portfolio_research_synthesis_input(phase6, phase7)


def test_runner_writes_five_compact_deterministic_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    phase6, phase7 = tmp_path / "phase6", tmp_path / "phase7"
    _write_artifact_fixture(phase6, phase7)
    monkeypatch.setattr(runner, "_validate_canonical_provenance", lambda source: None)
    first, second = tmp_path / "first", tmp_path / "second"
    one = runner.run_portfolio_research_synthesis(phase6_root=phase6, phase7_root=phase7, output_root=first)
    runner.run_portfolio_research_synthesis(phase6_root=phase6, phase7_root=phase7, output_root=second)
    assert {item.name for item in first.iterdir()} == set(runner.FILES)
    assert {item.name: item.read_bytes() for item in first.iterdir()} == {
        item.name: item.read_bytes() for item in second.iterdir()
    }
    manifest = json.loads((first / "portfolio_research_synthesis_manifest.json").read_text(encoding="utf-8"))
    assert manifest["assertions"]["no_ranking"] is True
    assert manifest["assertions"]["no_winner"] is True
    assert manifest["path_metric_applicability"] == "NOT_APPLICABLE"
    assert one["result"].identity == manifest["result_identity"]

