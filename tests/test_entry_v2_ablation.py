from research.run_entry_v2_ablation import (
    build_cases,
    build_entry_model,
    research_gates,
)


def test_entry_v2_ablation_has_baseline_and_one_candidate() -> None:
    cases = build_cases()

    assert len(cases) == 2
    assert {case.model_name for case in cases} == {
        "breakout_v1",
        "pullback_retest_v2",
    }
    assert build_entry_model(cases[0]).min_hybrid_score == 60
    assert build_entry_model(cases[1]).name == "trend_pullback_retest_v2"


def test_entry_v2_gate_requires_enough_trades_and_stability() -> None:
    passing = {
        "total_test_trades": 60,
        "profitable_folds": 6,
        "median_test_return_pct": 0.1,
        "return_excluding_first_two_folds_pct": 0.1,
        "recent_3_folds_return_pct": 0.1,
        "chained_max_drawdown_pct": -14.0,
    }
    failing = {**passing, "total_test_trades": 59}

    assert research_gates(
        passing,
        minimum_test_trades=60,
    )["research_gate_passed"] is True
    assert research_gates(
        failing,
        minimum_test_trades=60,
    )["research_gate_passed"] is False
