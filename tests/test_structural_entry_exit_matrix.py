from types import SimpleNamespace

import pytest

from research.run_structural_entry_exit_matrix import (
    BreakEvenOverlayExitModel,
    build_cases,
    build_entry_model,
    research_gates,
)


class _BaseExitStub:
    def calculate_levels(self, entry_price, entry_row, config):
        return 95.0, 110.0

    def update_levels(self, **kwargs):
        return kwargs["current_stop"], kwargs["current_target"]


def test_structural_matrix_has_unique_four_cases() -> None:
    cases = build_cases()

    assert len(cases) == 4
    assert len({case.case_id for case in cases}) == 4
    assert {case.min_hybrid_score for case in cases} == {60, 85}
    assert {case.protective_exit for case in cases} == {False, True}


def test_score_85_is_a_real_hybrid_gate() -> None:
    model = build_entry_model(85)

    assert model.min_hybrid_score == 85
    assert model.require_hybrid_score is True
    assert model.donchian_model.regime_threshold_fields == {
        "min_volume_ratio"
    }


def test_break_even_overlay_preserves_levels_and_updates_next_stop() -> None:
    model = BreakEvenOverlayExitModel(
        base_model=_BaseExitStub(),
        trigger_pct=3.0,
    )
    assert model.calculate_levels(100.0, {}, SimpleNamespace()) == (
        95.0,
        110.0,
    )

    before = model.update_levels(
        entry_price=100.0,
        current_row={},
        current_stop=95.0,
        current_target=110.0,
        highest_price=102.9,
        config=SimpleNamespace(),
    )
    after = model.update_levels(
        entry_price=100.0,
        current_row={},
        current_stop=95.0,
        current_target=110.0,
        highest_price=103.0,
        config=SimpleNamespace(),
    )

    assert before == pytest.approx((95.0, 110.0))
    assert after == pytest.approx((100.0, 110.0))


def test_research_gate_requires_all_structural_conditions() -> None:
    passing = {
        "profitable_folds": 6,
        "median_test_return_pct": 0.0,
        "return_excluding_first_fold_pct": 0.1,
        "recent_3_folds_return_pct": 0.0,
        "worst_test_drawdown_pct": -14.9,
        "chained_max_drawdown_pct": -14.9,
    }
    failing = {**passing, "profitable_folds": 5}

    assert research_gates(passing)["research_gate_passed"] is True
    assert research_gates(failing)["research_gate_passed"] is False
