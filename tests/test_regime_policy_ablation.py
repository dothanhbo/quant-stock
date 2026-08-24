from research.run_regime_policy_ablation import (
    build_cases,
    build_entry_model,
    build_policy,
    research_gates,
)


def test_regime_ablation_has_three_controlled_cases() -> None:
    cases = build_cases()

    assert len(cases) == 3
    assert len({case.case_id for case in cases}) == 3
    assert build_policy(cases[0]) is None
    assert build_policy(cases[1]).resolve("SIDEWAY").max_positions == 5
    assert build_policy(cases[2]).resolve("SIDEWAY").max_positions == 3
    assert build_policy(cases[2]).resolve("BEAR").allow_new_positions is False


def test_regime_ablation_keeps_entry_volume_only() -> None:
    model = build_entry_model(60)

    assert model.min_hybrid_score == 60
    assert model.require_hybrid_score is True
    assert model.donchian_model.regime_threshold_fields == {
        "min_volume_ratio"
    }


def test_regime_gate_rejects_bull_period_dependency() -> None:
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
