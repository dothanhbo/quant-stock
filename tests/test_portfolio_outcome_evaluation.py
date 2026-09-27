from __future__ import annotations

import csv
from dataclasses import FrozenInstanceError
from hashlib import sha256
import json
from pathlib import Path

import pytest

from quantlab.portfolio import (
    COST_GRID_BPS,
    DailyPortfolioOutcome,
    FrozenForwardOutcome,
    FrozenOutcomePortfolio,
    FrozenOutcomePosition,
    PortfolioOutcomeAvailability,
    PortfolioOutcomeInput,
    TemporalEvidence,
    evaluate_portfolio_outcomes,
    load_portfolio_outcome_input,
)
from research import run_quantlab_portfolio_outcome_evaluation as runner


HORIZONS = (5, 10, 20)


def _returns(value: float | None) -> dict[int, float | None]:
    return {horizon: value for horizon in HORIZONS}


def _outcome(
    session: str,
    symbol: str,
    *,
    stock: float = 2.0,
    benchmark: float = 1.0,
    status: str = "AVAILABLE",
) -> FrozenForwardOutcome:
    available = status == "AVAILABLE"
    return FrozenForwardOutcome(
        session,
        symbol,
        "VNINDEX",
        {horizon: "2099-12-31" for horizon in HORIZONS},
        _returns(stock if available else None),
        _returns(benchmark if available else None),
        _returns(stock - benchmark if available else None),
        {horizon: status for horizon in HORIZONS},
    )


def _portfolio(
    session: str,
    budget: int,
    positions: tuple[tuple[str, float], ...],
    *,
    turnover: float | None = 0.5,
) -> FrozenOutcomePortfolio:
    frozen = tuple(
        FrozenOutcomePosition(symbol, rank, weight, f"position-{session}-{budget}-{symbol}")
        for rank, (symbol, weight) in enumerate(positions, 1)
    )
    gross = sum(weight for _, weight in positions)
    return FrozenOutcomePortfolio(
        session,
        budget,
        "EQUAL_WEIGHT",
        f"selection-{session}-{budget}",
        len(positions),
        len(positions),
        gross,
        1.0 - gross,
        turnover,
        f"portfolio-{session}-{budget}",
        frozen,
    )


def _source(
    portfolios: tuple[FrozenOutcomePortfolio, ...],
    outcomes: tuple[FrozenForwardOutcome, ...],
) -> PortfolioOutcomeInput:
    return PortfolioOutcomeInput(
        {"phase6": "phase6-id", "phase65": "phase65-id"},
        tuple(sorted(portfolios, key=lambda item: (item.session_date, item.requested_budget))),
        tuple(sorted(outcomes, key=lambda item: (item.session_date, item.symbol))),
    )


def _daily(result, budget: int, horizon: int = 5) -> DailyPortfolioOutcome:
    return next(
        item for item in result.daily_outcomes
        if item.requested_budget == budget and item.horizon_sessions == horizon
    )


def test_weighted_aggregation_benchmark_excess_and_causal_target() -> None:
    session = "2018-08-07"
    result = evaluate_portfolio_outcomes(_source(
        (_portfolio(session, 5, (("AAA", 0.25), ("BBB", 0.75))),),
        (_outcome(session, "AAA", stock=10.0), _outcome(session, "BBB", stock=2.0)),
    ))
    item = _daily(result, 5)
    assert item.portfolio_stock_return_pct == pytest.approx(4.0)
    assert item.portfolio_benchmark_return_pct == pytest.approx(1.0)
    assert item.portfolio_excess_return_pct_points == pytest.approx(3.0)
    assert item.target_session == "2099-12-31"
    with pytest.raises(ValueError, match="after formation"):
        FrozenForwardOutcome(
            session, "AAA", "VNINDEX", {h: session for h in HORIZONS},
            _returns(1.0), _returns(0.0), _returns(1.0),
            {h: "AVAILABLE" for h in HORIZONS},
        )


@pytest.mark.parametrize(
    ("joined", "status", "expected"),
    (
        (False, "AVAILABLE", PortfolioOutcomeAvailability.UNAVAILABLE_CONSTITUENT_OUTCOME),
        (True, "MISSING_BENCHMARK_TARGET_CLOSE", PortfolioOutcomeAvailability.UNAVAILABLE_BENCHMARK_OUTCOME),
        (True, "CENSORED_AFTER_DATA_END", PortfolioOutcomeAvailability.CENSORED_TARGET),
    ),
)
def test_missing_outcomes_are_explicit_and_never_renormalized(joined, status, expected) -> None:
    session = "2018-08-07"
    outcomes = [_outcome(session, "AAA")]
    if joined:
        outcomes.append(_outcome(session, "BBB", status=status))
    item = _daily(evaluate_portfolio_outcomes(_source(
        (_portfolio(session, 5, (("AAA", 0.5), ("BBB", 0.5))),), tuple(outcomes),
    )), 5)
    assert item.availability is expected
    assert item.portfolio_stock_return_pct is None
    assert item.portfolio_benchmark_return_pct is None
    assert item.portfolio_excess_return_pct_points is None


def test_empty_and_underfilled_portfolios_remain_distinct() -> None:
    session = "2018-08-07"
    result = evaluate_portfolio_outcomes(_source(
        (
            _portfolio(session, 5, (), turnover=None),
            _portfolio(session, 10, (("AAA", 1.0),)),
        ),
        (_outcome(session, "AAA"),),
    ))
    empty, underfilled = _daily(result, 5), _daily(result, 10)
    assert empty.availability is PortfolioOutcomeAvailability.EMPTY_PORTFOLIO
    assert empty.selected_count == 0 and empty.underfilled
    assert empty.portfolio_stock_return_pct is None
    assert underfilled.availability is PortfolioOutcomeAvailability.FULLY_EVALUABLE
    assert underfilled.selected_count == 1 and underfilled.underfilled


def test_deterministic_identity_immutability_and_no_ranking_contract() -> None:
    session = "2018-08-07"
    source = _source(
        (_portfolio(session, 5, (("AAA", 1.0),)),),
        (_outcome(session, "AAA"),),
    )
    first, second = evaluate_portfolio_outcomes(source), evaluate_portfolio_outcomes(source)
    assert first == second and first.identity == second.identity
    assert tuple((item.session_date, item.requested_budget, item.horizon_sessions) for item in first.daily_outcomes) == (
        (session, 5, 5), (session, 5, 10), (session, 5, 20),
    )
    with pytest.raises(TypeError):
        first.source_identities["changed"] = "x"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        first.identity = "changed"  # type: ignore[misc]
    assert not hasattr(first, "winner")
    assert not hasattr(first.summaries[0], "rank")


def test_summaries_reuse_four_blocks_and_prohibit_path_metrics() -> None:
    dates = ("2018-08-07", "2021-01-04", "2023-01-03", "2025-01-02")
    portfolios = tuple(_portfolio(date, 5, (("AAA", 1.0),)) for date in dates)
    outcomes = tuple(_outcome(date, "AAA", stock=2.0, benchmark=1.0) for date in dates)
    result = evaluate_portfolio_outcomes(_source(portfolios, outcomes))
    assert len(result.summaries) == 9
    assert len(result.block_summaries) == 36
    selected = tuple(item for item in result.block_summaries if item.requested_budget == 5 and item.horizon_sessions == 5)
    assert tuple(item.block_name for item in selected) == (
        "early_2018_2020", "middle_2021_2022", "middle_2023_2024", "recent_2025_2026",
    )
    assert all(item.temporal_evidence is TemporalEvidence.SAME_DIRECTION for item in selected)
    summary = next(item for item in result.summaries if item.requested_budget == 5 and item.horizon_sessions == 5)
    assert summary.path_metric_status == "NOT_APPLICABLE"
    assert summary.path_metric_reason == "OVERLAPPING_FORWARD_HORIZONS"
    assert not hasattr(summary, "cagr") and not hasattr(summary, "sharpe")


def test_paired_budget_contrasts_use_only_same_date_evaluable_rows() -> None:
    session = "2018-08-07"
    source = _source(
        (
            _portfolio(session, 5, (("AAA", 1.0),)),
            _portfolio(session, 10, (("BBB", 1.0),)),
        ),
        (_outcome(session, "AAA", stock=2.0), _outcome(session, "BBB", stock=5.0)),
    )
    result = evaluate_portfolio_outcomes(source)
    item = next(
        row for row in result.contrasts
        if row.scope_name == "whole_period" and row.horizon_sessions == 5
        and row.lower_budget == 5 and row.higher_budget == 10
    )
    assert item.paired_date_count == 1
    assert item.mean_stock_delta_pct_points == pytest.approx(3.0)
    assert item.mean_excess_delta_pct_points == pytest.approx(3.0)
    assert item.positive_stock_delta_rate == 1.0


def test_turnover_cost_grid_and_zero_cost_identity() -> None:
    session = "2018-08-07"
    result = evaluate_portfolio_outcomes(_source(
        (_portfolio(session, 5, (("AAA", 1.0),), turnover=0.5),),
        (_outcome(session, "AAA", stock=4.0, benchmark=1.0),),
    ))
    rows = tuple(item for item in result.cost_sensitivity if item.requested_budget == 5 and item.horizon_sessions == 5)
    assert tuple(item.cost_rate_bps for item in rows) == COST_GRID_BPS
    zero, ten = rows[:2]
    assert zero.mean_estimated_turnover_cost_pct_points == 0.0
    assert zero.mean_net_stock_return_pct == zero.mean_gross_stock_return_pct == 4.0
    assert zero.mean_net_excess_return_pct_points == zero.mean_gross_excess_return_pct_points == 3.0
    assert ten.mean_estimated_turnover_cost_pct_points == pytest.approx(0.05)
    assert ten.mean_net_stock_return_pct == pytest.approx(3.95)
    assert ten.label == "HYPOTHETICAL_COST_SENSITIVITY"


def test_evaluation_does_not_mutate_frozen_membership_or_weights() -> None:
    session = "2018-08-07"
    source = _source(
        (_portfolio(session, 5, (("AAA", 0.4), ("BBB", 0.6))),),
        (_outcome(session, "AAA"), _outcome(session, "BBB")),
    )
    before = tuple((item.symbol, item.weight) for item in source.portfolios[0].positions)
    evaluate_portfolio_outcomes(source)
    after = tuple((item.symbol, item.weight) for item in source.portfolios[0].positions)
    assert after == before


def _write_fixture(phase6: Path, phase65: Path) -> None:
    phase6.mkdir(); phase65.mkdir()
    daily = []
    positions = []
    for budget in (5, 10, 20):
        identity = f"portfolio-{budget}"
        daily.append({
            "session_date": "2018-08-07", "candidate_source": "ADX_ONLY",
            "requested_budget": budget, "weighting_policy": "EQUAL_WEIGHT",
            "selection_identity": f"selection-{budget}", "eligible_cross_section_count": 1,
            "selected_count": 1, "fill_ratio": 1 / budget, "unfilled_slots": budget - 1,
            "gross_weight": 1.0, "cash_weight": 0.0, "max_single_name_weight": 1.0,
            "herfindahl_concentration": 1.0, "effective_number_of_positions": 1.0,
            "additions_json": "[]", "removals_json": "[]", "retained_positions_json": "[]",
            "one_way_weight_turnover": "", "weight_stability": "", "identity": identity,
        })
        positions.append({
            "session_date": "2018-08-07", "candidate_source": "ADX_ONLY",
            "requested_budget": budget, "weighting_policy": "EQUAL_WEIGHT", "symbol": "AAA",
            "selection_rank": 1, "weight": 1.0, "selection_identity": f"selection-{budget}",
            "position_identity": f"position-{budget}", "portfolio_identity": identity,
        })
    for name, rows in (("portfolio_construction_by_date.csv", daily), ("portfolio_positions.csv", positions)):
        with (phase6 / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), lineterminator="\n")
            writer.writeheader(); writer.writerows(rows)
    manifest6 = {
        "completed": True, "budgets": [5, 10, 20], "candidate_source": "ADX_ONLY",
        "weighting_policies": ["EQUAL_WEIGHT"], "result_identity": "phase6-result",
        "artifacts": {"portfolio_construction_by_date.csv": 3, "portfolio_positions.csv": 3},
    }
    (phase6 / "portfolio_construction_manifest.json").write_text(json.dumps(manifest6), encoding="utf-8")
    outcome = {
        "session_date": "2018-08-07", "symbol": "AAA", "benchmark_symbol": "VNINDEX",
    }
    for horizon in HORIZONS:
        outcome.update({
            f"target_session_{horizon}": "2099-12-31",
            f"stock_forward_return_{horizon}_pct": 2.0,
            f"benchmark_forward_return_{horizon}_pct": 1.0,
            f"excess_forward_return_{horizon}_pct_points": 1.0,
            f"outcome_{horizon}__availability": "AVAILABLE",
        })
    outcome_path = phase65 / "point_in_time_forward_outcomes.csv"
    with outcome_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(outcome), lineterminator="\n")
        writer.writeheader(); writer.writerow(outcome)
    artifact_hash = sha256(outcome_path.read_bytes()).hexdigest()
    manifest65 = {
        "completed": True, "horizons": [5, 10, 20], "row_count": 1,
        "artifact": {"filename": outcome_path.name, "sha256": artifact_hash},
        "observation_index": {"identity": "observation", "content_identity": "observation-content"},
        "outcome_panel": {"identity": "outcomes", "content_identity": "outcomes-content"},
    }
    (phase65 / "point_in_time_forward_outcomes_manifest.json").write_text(json.dumps(manifest65), encoding="utf-8")


def test_adapter_provenance_determinism_and_artifact_hash_verification(tmp_path: Path) -> None:
    phase6, phase65 = tmp_path / "phase6", tmp_path / "phase65"
    _write_fixture(phase6, phase65)
    first, second = load_portfolio_outcome_input(phase6, phase65), load_portfolio_outcome_input(phase6, phase65)
    assert first == second
    assert len(first.portfolios) == 3 and len(first.outcomes) == 1
    assert first.source_identities["phase65_outcome_row_count"] == "1"
    path = phase65 / "point_in_time_forward_outcomes.csv"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        load_portfolio_outcome_input(phase6, phase65)


def test_runner_writes_seven_deterministic_neutral_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    phase6, phase65 = tmp_path / "phase6", tmp_path / "phase65"
    _write_fixture(phase6, phase65)
    monkeypatch.setattr(runner, "_validate_provenance", lambda source: None)
    first, second = tmp_path / "first", tmp_path / "second"
    one = runner.run_portfolio_outcome_evaluation(phase6_root=phase6, phase65_root=phase65, output_root=first)
    runner.run_portfolio_outcome_evaluation(phase6_root=phase6, phase65_root=phase65, output_root=second)
    assert {item.name for item in first.iterdir()} == set(runner.FILES)
    assert {item.name: item.read_bytes() for item in first.iterdir()} == {
        item.name: item.read_bytes() for item in second.iterdir()
    }
    manifest = json.loads((first / "portfolio_outcome_manifest.json").read_text(encoding="utf-8"))
    assert manifest["path_metric_applicability"] == "NOT_APPLICABLE"
    assert manifest["path_metric_reason"] == "OVERLAPPING_FORWARD_HORIZONS"
    assert manifest["assertions"]["ranking_or_winner"] is False
    assert one["result"].identity == manifest["result_identity"]

