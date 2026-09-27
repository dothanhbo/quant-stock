from __future__ import annotations

import csv
from hashlib import sha256
import json
from pathlib import Path

import pytest

from quantlab.identity import canonical_identity_value, canonical_json
from quantlab.portfolio import (
    FrozenSelectionObservation,
    PortfolioConstructionInput,
    PortfolioPosition,
    PortfolioStructuralState,
    WeightingPolicy,
    construct_portfolios,
    load_phase6_construction_input,
)
from research.run_quantlab_portfolio_construction import run_portfolio_construction


def _identity(value) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _source(
    dates: tuple[str, ...] = ("2018-08-07", "2018-08-08", "2018-08-09"),
    symbols: dict[tuple[str, int], tuple[str, ...]] | None = None,
) -> PortfolioConstructionInput:
    selections = []
    symbols = symbols or {}
    for session in dates:
        for budget in (5, 10, 20):
            chosen = symbols.get((session, budget), tuple(chr(65 + index) for index in range(min(budget, 3))))
            selections.append(FrozenSelectionObservation(
                session, "ADX_ONLY", "policy-id", budget, max(len(chosen), 3), chosen,
                f"selection-{session}-{budget}",
            ))
    return PortfolioConstructionInput(
        "p59-manifest", "p59-selection", "p511-manifest", "p511-result",
        "adx_14", "adx-id", "ADX_ONLY", "policy-id",
        "2018-08-07", "2026-09-17", tuple(selections),
    )


def _daily(result, session: str, budget: int):
    return next(item for item in result.daily_portfolios if item.session_date == session and item.requested_budget == budget)


def test_equal_weight_normalization_ordering_and_frozen_budgets() -> None:
    result = construct_portfolios(_source())
    assert tuple((item.session_date, item.requested_budget) for item in result.daily_portfolios) == tuple(
        (session, budget) for session in ("2018-08-07", "2018-08-08", "2018-08-09") for budget in (5, 10, 20)
    )
    item = _daily(result, "2018-08-07", 5)
    assert tuple(position.symbol for position in item.positions) == ("A", "B", "C")
    assert tuple(position.selection_rank for position in item.positions) == (1, 2, 3)
    assert sum(position.weight for position in item.positions) == pytest.approx(1.0)
    assert all(position.weight == pytest.approx(1 / 3) for position in item.positions)


def test_underfill_and_empty_portfolio_are_explicit() -> None:
    mapping = {
        ("2018-08-07", 5): ("A", "B"),
        ("2018-08-08", 5): (),
    }
    result = construct_portfolios(_source(symbols=mapping))
    underfilled = _daily(result, "2018-08-07", 5)
    assert (underfilled.selected_count, underfilled.unfilled_slots, underfilled.fill_ratio) == (2, 3, 0.4)
    assert underfilled.gross_weight == pytest.approx(1.0)
    assert underfilled.cash_weight == pytest.approx(0.0)
    empty = _daily(result, "2018-08-08", 5)
    assert empty.positions == ()
    assert empty.gross_weight == 0.0 and empty.cash_weight == 1.0
    assert empty.effective_number_of_positions is None


def test_effective_n_and_herfindahl_are_hand_calculated() -> None:
    item = _daily(construct_portfolios(_source()), "2018-08-07", 5)
    assert item.herfindahl_concentration == pytest.approx(1 / 3)
    assert item.effective_number_of_positions == pytest.approx(3.0)
    assert item.max_single_name_weight == pytest.approx(1 / 3)


def test_first_date_turnover_is_undefined_and_changes_are_empty() -> None:
    item = _daily(construct_portfolios(_source()), "2018-08-07", 5)
    assert item.one_way_weight_turnover is None
    assert item.weight_stability is None
    assert item.additions == item.removals == item.retained_positions == ()


def test_identical_portfolio_has_zero_turnover_and_full_retention() -> None:
    item = _daily(construct_portfolios(_source()), "2018-08-08", 5)
    assert item.one_way_weight_turnover == pytest.approx(0.0)
    assert item.weight_stability == pytest.approx(1.0)
    assert item.additions == item.removals == ()
    assert item.retained_positions == ("A", "B", "C")


def test_complete_replacement_has_unit_turnover() -> None:
    mapping = {
        ("2018-08-07", 5): ("A", "B"),
        ("2018-08-08", 5): ("C", "D"),
    }
    item = _daily(construct_portfolios(_source(symbols=mapping)), "2018-08-08", 5)
    assert item.one_way_weight_turnover == pytest.approx(1.0)
    assert item.weight_stability == pytest.approx(0.0)
    assert item.additions == ("C", "D")
    assert item.removals == ("A", "B")


def test_cash_transition_is_included_in_weight_turnover() -> None:
    mapping = {
        ("2018-08-07", 5): (),
        ("2018-08-08", 5): ("A", "B"),
    }
    item = _daily(construct_portfolios(_source(symbols=mapping)), "2018-08-08", 5)
    assert item.one_way_weight_turnover == pytest.approx(1.0)


def test_invalid_position_weights_and_selection_membership_are_rejected() -> None:
    with pytest.raises(ValueError, match="weight"):
        PortfolioPosition("A", 1, float("nan"), "selection")
    with pytest.raises(ValueError, match="unique"):
        FrozenSelectionObservation("2018-08-07", "ADX_ONLY", "policy", 5, 5, ("A", "A"), "selection")


def test_result_is_outcome_free_and_structural_states_are_not_rankings() -> None:
    result = construct_portfolios(_source())
    assert all(item.structural_state is PortfolioStructuralState.STRUCTURALLY_VALID for item in result.summaries if item.evaluated_dates)
    assert not hasattr(result.daily_portfolios[0], "future_return")
    assert not hasattr(result.summaries[0], "score")
    assert any("no future outcome" in item for item in result.limitations)


def _write_artifact_fixture(root59: Path, root511: Path) -> None:
    root59.mkdir(); root511.mkdir()
    rows = []
    for session in ("2018-08-07", "2018-08-08"):
        for budget in (5, 10, 20):
            symbols = ["A", "B"]
            rows.append({
                "session_date": session, "policy_name": "ADX_ONLY",
                "selection_budget": budget, "eligible_cross_section_count": 2,
                "actual_selected_count": 2, "selected_symbols_json": json.dumps(symbols),
                "identity": f"selection-{session}-{budget}",
            })
    with (root59 / "policy_selection_by_date.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)
    manifest59 = {
        "runner_version": "v6", "completed": True,
        "requested_bounds": {"start_date": "2018-08-07", "end_date": "2026-09-17"},
        "policy_selection_diagnostics": {
            "result_identity": "selection-result",
            "policies": [{"name": "ADX_ONLY", "fingerprint": "policy-id"}],
        },
    }
    (root59 / "experiment_manifest.json").write_text(json.dumps(manifest59), encoding="utf-8")
    manifest511 = {
        "completed": True, "input_canonical_manifest_identity": _identity(manifest59),
        "decision_result_identity": "decision-result",
    }
    (root511 / "research_decision_manifest.json").write_text(json.dumps(manifest511), encoding="utf-8")
    with (root511 / "research_decision_summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("candidate_type", "candidate", "decision", "candidate_identity"), lineterminator="\n")
        writer.writeheader(); writer.writerow({"candidate_type": "factor", "candidate": "adx_14", "decision": "ADVANCE", "candidate_identity": "adx-id"})


def test_artifact_adapter_binds_provenance_and_rejects_future_fields(tmp_path: Path) -> None:
    root59, root511 = tmp_path / "p59", tmp_path / "p511"
    _write_artifact_fixture(root59, root511)
    source = load_phase6_construction_input(root59, root511)
    assert source.advanced_factor_identity == "adx-id"
    assert source.selection_policy_identity == "policy-id"
    assert len(source.selections) == 6
    text = (root59 / "policy_selection_by_date.csv").read_text(encoding="utf-8")
    (root59 / "policy_selection_by_date.csv").write_text(text.replace("identity\n", "future_return,identity\n").replace("selection-", ",selection-"), encoding="utf-8")
    with pytest.raises(ValueError, match="forbidden"):
        load_phase6_construction_input(root59, root511)


def test_runner_artifacts_are_deterministic_and_upstream_is_unchanged(tmp_path: Path) -> None:
    root59, root511 = tmp_path / "p59", tmp_path / "p511"
    _write_artifact_fixture(root59, root511)
    before59 = {item.name: item.read_bytes() for item in root59.iterdir()}
    before511 = {item.name: item.read_bytes() for item in root511.iterdir()}
    first, second = tmp_path / "first", tmp_path / "second"
    run_portfolio_construction(phase59_root=root59, phase511_root=root511, output_root=first)
    run_portfolio_construction(phase59_root=root59, phase511_root=root511, output_root=second)
    expected = {
        "portfolio_construction_by_date.csv", "portfolio_construction_manifest.json",
        "portfolio_construction_report.md", "portfolio_construction_summary.csv",
        "portfolio_positions.csv", "portfolio_structural_contrasts.csv",
    }
    assert {item.name for item in first.iterdir()} == expected
    assert {item.name: item.read_bytes() for item in first.iterdir()} == {item.name: item.read_bytes() for item in second.iterdir()}
    assert {item.name: item.read_bytes() for item in root59.iterdir()} == before59
    assert {item.name: item.read_bytes() for item in root511.iterdir()} == before511
    manifest = json.loads((first / "portfolio_construction_manifest.json").read_text())
    assert manifest["budgets"] == [5, 10, 20]
    assert manifest["weighting_policies"] == ["EQUAL_WEIGHT"]
    assert manifest["no_future_return_pnl_backtest_evidence_used_for_construction_or_scenario_selection"] is True
