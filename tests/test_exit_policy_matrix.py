from types import SimpleNamespace

from research.run_exit_policy_matrix import (
    DelayedTrailingATRExitModel,
    build_cases,
    build_exit_model,
)


def _row(atr: float):
    return {"ATR14": atr}


def test_exit_matrix_contains_eight_unique_frozen_cases():
    cases = build_cases()
    assert len(cases) == 8
    assert len({case.case_id for case in cases}) == 8
    assert sum(case.is_current_production for case in cases) == 1
    assert {case.target_atr for case in cases if case.exit_family == "fixed"} == {
        4.0,
        5.0,
        6.0,
    }


def test_all_exit_cases_build():
    assert all(build_exit_model(case) is not None for case in build_cases())


def test_delayed_trailing_uses_initial_r_trigger():
    model = DelayedTrailingATRExitModel(
        activation_r=1.0,
        stop_atr_multiplier=2.0,
        target_atr_multiplier=5.0,
        trailing_atr_multiplier=2.5,
    )
    unchanged = model.update_levels(
        entry_price=100.0,
        current_row=_row(5.0),
        current_stop=90.0,
        current_target=125.0,
        highest_price=109.99,
        config=SimpleNamespace(),
    )
    activated = model.update_levels(
        entry_price=100.0,
        current_row=_row(5.0),
        current_stop=90.0,
        current_target=125.0,
        highest_price=110.0,
        config=SimpleNamespace(),
    )
    assert unchanged == (90.0, 125.0)
    assert activated == (97.5, 125.0)


def test_two_r_activation_waits_for_twenty_point_gain():
    model = DelayedTrailingATRExitModel(
        activation_r=2.0,
        stop_atr_multiplier=2.0,
        target_atr_multiplier=5.0,
        trailing_atr_multiplier=2.5,
    )
    assert model.update_levels(
        entry_price=100.0,
        current_row=_row(5.0),
        current_stop=90.0,
        current_target=125.0,
        highest_price=119.99,
        config=SimpleNamespace(),
    ) == (90.0, 125.0)
