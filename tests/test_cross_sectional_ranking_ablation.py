from research.run_cross_sectional_ranking_ablation import (
    build_cases,
    build_entry_model,
    research_gates,
)


def test_ranking_ablation_changes_only_three_ranking_methods() -> None:
    cases = build_cases()

    assert len(cases) == 3
    assert {case.ranking_method for case in cases} == {
        "signal_score",
        "relative_strength",
        "cross_sectional_leadership",
    }


def test_ranking_ablation_entry_is_fixed_volume_only() -> None:
    model = build_entry_model(60)

    assert model.min_hybrid_score == 60
    assert model.require_hybrid_score is True
    assert model.donchian_model.regime_threshold_fields == {
        "min_volume_ratio"
    }


def test_ranking_gate_rejects_bull_period_dependency() -> None:
    passing = {
        "profitable_folds": 6,
        "median_test_return_pct": 0.1,
        "return_excluding_first_fold_pct": 0.1,
        "return_excluding_first_two_folds_pct": 0.1,
        "recent_3_folds_return_pct": 0.1,
        "chained_max_drawdown_pct": -14.0,
    }
    failing = {
        **passing,
        "return_excluding_first_two_folds_pct": -0.1,
    }

    assert research_gates(passing)["research_gate_passed"] is True
    assert research_gates(failing)["research_gate_passed"] is False
