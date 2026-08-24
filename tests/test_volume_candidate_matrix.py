from research.run_volume_candidate_matrix import (
    build_cases,
    build_entry_model,
    research_gates,
)


def test_volume_candidate_matrix_has_four_unique_cases() -> None:
    cases = build_cases()

    assert len(cases) == 4
    assert len({case.case_id for case in cases}) == 4
    assert {case.exit for case in cases} == {"current", "frozen"}
    assert {case.sizing for case in cases} == {"atr_risk", "fixed20"}


def test_volume_candidate_uses_only_dynamic_volume_threshold() -> None:
    entry_model = build_entry_model()

    assert entry_model.donchian_model.regime_threshold_fields == {
        "min_volume_ratio"
    }
    assert entry_model.require_hybrid_score is True


def test_research_gates_require_every_condition() -> None:
    passing = {
        "median_test_return_pct": -0.4,
        "return_excluding_first_fold_pct": 0.1,
        "recent_3_folds_return_pct": 0.0,
        "worst_test_drawdown_pct": -14.9,
        "chained_max_drawdown_pct": -14.9,
    }
    failing = {**passing, "median_test_return_pct": -0.6}

    assert research_gates(passing)["research_gate_passed"] is True
    assert research_gates(failing)["research_gate_passed"] is False
