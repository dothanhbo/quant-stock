from __future__ import annotations

import pytest

from research.run_regime_threshold_ablation import (
    build_cases,
    build_entry_model,
)
from strategy.donchian_breakout_entry import (
    DonchianBreakoutEntryModel,
    REGIME_THRESHOLD_FIELDS,
)


def test_regime_threshold_ablation_has_eight_cases() -> None:
    cases = build_cases()

    assert len(cases) == 8
    assert len({case.case_id for case in cases}) == 8
    assert cases[0].threshold_fields == frozenset()
    assert cases[-1].threshold_fields == REGIME_THRESHOLD_FIELDS
    assert {
        next(iter(case.threshold_fields))
        for case in cases
        if len(case.threshold_fields) == 1
    } == REGIME_THRESHOLD_FIELDS

    combo = next(
        case
        for case in cases
        if case.case_id == "combo__min_volume_ratio__min_adx"
    )
    assert combo.threshold_fields == {
        "min_volume_ratio",
        "min_adx",
    }


def test_single_threshold_case_enables_only_selected_field() -> None:
    case = next(
        case
        for case in build_cases()
        if case.case_id == "only__min_relative_strength"
    )
    entry_model = build_entry_model(case)
    donchian = entry_model.donchian_model

    assert donchian.regime_threshold_fields == {
        "min_relative_strength"
    }
    assert donchian.uses_regime_threshold("min_relative_strength")
    assert not donchian.uses_regime_threshold("min_adx")


def test_current_default_still_enables_all_regime_thresholds() -> None:
    model = DonchianBreakoutEntryModel(
        use_regime_thresholds=True
    )

    assert model.regime_threshold_fields == REGIME_THRESHOLD_FIELDS


def test_invalid_regime_threshold_field_is_rejected() -> None:
    with pytest.raises(ValueError, match="không hợp lệ"):
        DonchianBreakoutEntryModel(
            use_regime_thresholds=True,
            regime_threshold_fields={"not_a_threshold"},
        )


def test_partial_fields_require_regime_thresholds_enabled() -> None:
    with pytest.raises(ValueError, match="yêu cầu"):
        DonchianBreakoutEntryModel(
            use_regime_thresholds=False,
            regime_threshold_fields={"min_adx"},
        )
