import pytest

from research.run_nested_score_wfo import (
    build_entry_model,
    research_gates,
    select_best_score,
    validate_scores,
)


def test_validate_scores_deduplicates_and_sorts() -> None:
    assert validate_scores([90, 75, 85, 75]) == [75, 85, 90]
    with pytest.raises(ValueError):
        validate_scores([101])


def test_nested_entry_changes_only_score_gate() -> None:
    model = build_entry_model(85)

    assert model.min_hybrid_score == 85
    assert model.require_hybrid_score is True
    assert model.donchian_model.regime_threshold_fields == {
        "min_volume_ratio"
    }


def test_selection_prefers_eligible_train_sharpe() -> None:
    rows = [
        {
            "score": 75,
            "eligible": True,
            "train_sharpe": 0.5,
            "train_return_pct": 10.0,
            "train_drawdown_pct": -10.0,
        },
        {
            "score": 85,
            "eligible": True,
            "train_sharpe": 0.8,
            "train_return_pct": 5.0,
            "train_drawdown_pct": -8.0,
        },
        {
            "score": 90,
            "eligible": False,
            "train_sharpe": 2.0,
            "train_return_pct": 20.0,
            "train_drawdown_pct": -5.0,
        },
    ]

    selected, fallback = select_best_score(rows)

    assert selected["score"] == 85
    assert fallback is False


def test_nested_research_gate() -> None:
    passing = {
        "profitable_folds": 6,
        "median_test_return_pct": 0.1,
        "recent_3_folds_return_pct": 0.0,
        "worst_test_drawdown_pct": -14.0,
        "chained_max_drawdown_pct": -14.0,
    }
    failing = {**passing, "median_test_return_pct": -0.1}

    assert research_gates(passing)["research_gate_passed"] is True
    assert research_gates(failing)["research_gate_passed"] is False
