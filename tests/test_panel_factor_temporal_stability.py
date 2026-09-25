from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType

import pytest

from quantlab.evaluation import (
    NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V1,
    NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1,
    PanelDailyFactorEvaluation,
    PanelFactorHorizonSummary,
    PanelFactorTemporalStabilitySpec,
    PanelTemporalBlock,
    PointInTimePanelFactorEvaluationResult,
    evaluate_panel_factor_temporal_stability,
)


_PANEL_SPEC = NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1
_TEMPORAL_SPEC = NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V1


def _daily(
    factor: str,
    horizon: int,
    outcome: str,
    signal_date: str,
    *,
    rank_ic: float | None,
    spread: float | None,
) -> PanelDailyFactorEvaluation:
    eligible = 5
    return PanelDailyFactorEvaluation(
        source_dataset_identity="dataset-id",
        source_dataset_content_identity="dataset-content",
        specification_fingerprint=_PANEL_SPEC.fingerprint,
        factor=factor,
        factor_direction=_PANEL_SPEC.direction_for(factor),
        horizon_sessions=horizon,
        outcome_field=outcome,
        outcome_column=(
            f"stock_forward_return_{horizon}_pct"
            if outcome == "stock_forward_return_pct"
            else f"excess_forward_return_{horizon}_pct_points"
        ),
        signal_date=signal_date,
        total_observation_count=5,
        factor_available_count=5,
        outcome_available_count=5,
        factor_usable_count=5,
        outcome_usable_count=5,
        pairwise_finite_eligible_count=eligible,
        excluded_for_factor_count=0,
        excluded_for_outcome_count=0,
        excluded_for_both_count=0,
        factor_status_counts=MappingProxyType({"AVAILABLE": 5}),
        outcome_status_counts=MappingProxyType({"AVAILABLE": 5}),
        factor_nonfinite_available_count=0,
        outcome_nonfinite_available_count=0,
        factor_unique_count=5,
        outcome_unique_count=5,
        rank_ic=rank_ic,
        ic_undefined_reason=None if rank_ic is not None else "correlation_denominator_zero",
        low_bucket_count=1 if spread is not None else 0,
        high_bucket_count=1 if spread is not None else 0,
        low_bucket_mean_outcome=0.0 if spread is not None else None,
        low_bucket_median_outcome=0.0 if spread is not None else None,
        high_bucket_mean_outcome=spread,
        high_bucket_median_outcome=spread,
        high_minus_low_mean_spread=spread,
        high_minus_low_median_spread=spread,
        bucket_undefined_reason=(
            None if spread is not None else "fewer_than_minimum_pairwise_finite_observations"
        ),
        observation_status_evidence=tuple(
            (f"S{index}", "AVAILABLE", "AVAILABLE", float(index), float(index), True, True)
            for index in range(eligible)
        ),
        eligible_evidence=tuple(
            (f"S{index}", float(index), float(index), float(index + 1),
             float(index + 1), index / 4.0, "MIDDLE")
            for index in range(eligible)
        ),
    )


def _summary(
    factor: str,
    horizon: int,
    outcome: str,
    items: tuple[PanelDailyFactorEvaluation, ...],
) -> PanelFactorHorizonSummary:
    return PanelFactorHorizonSummary(
        factor=factor,
        factor_direction=_PANEL_SPEC.direction_for(factor),
        horizon_sessions=horizon,
        outcome_field=outcome,
        total_signal_dates=len(items),
        dates_with_any_pairwise_finite_observations=len(items),
        ic_defined_date_count=sum(item.rank_ic is not None for item in items),
        ic_coverage_pct=0.0,
        mean_daily_rank_ic=None,
        median_daily_rank_ic=None,
        population_std_daily_ic=None,
        positive_ic_date_count=0,
        zero_ic_date_count=0,
        negative_ic_date_count=0,
        positive_ic_rate=None,
        bucket_defined_date_count=sum(item.high_minus_low_mean_spread is not None for item in items),
        bucket_coverage_pct=0.0,
        mean_daily_high_minus_low_mean_spread=None,
        median_daily_high_minus_low_mean_spread=None,
        mean_daily_high_minus_low_median_spread=None,
        median_daily_high_minus_low_median_spread=None,
        positive_mean_spread_date_count=0,
        zero_mean_spread_date_count=0,
        negative_mean_spread_date_count=0,
        positive_mean_spread_rate=None,
        average_low_bucket_size=None,
        average_high_bucket_size=None,
        total_eligible_observations=sum(item.pairwise_finite_eligible_count for item in items),
        average_eligible_cross_section_size=None,
        factor_availability_coverage_pct=None,
        outcome_availability_coverage_pct=None,
        warnings=("synthetic_source",),
        included_daily_identities=tuple(item.identity for item in items),
    )


def _source(
    dates: tuple[str, ...],
    *,
    ic_by_date: dict[str, float | None] | None = None,
    spread_by_date: dict[str, float | None] | None = None,
) -> PointInTimePanelFactorEvaluationResult:
    ic_by_date = ic_by_date or {}
    spread_by_date = spread_by_date or {}
    daily = tuple(
        _daily(
            factor, horizon, outcome, signal_date,
            rank_ic=ic_by_date.get(signal_date, 0.1),
            spread=spread_by_date.get(signal_date, 1.0),
        )
        for factor in _PANEL_SPEC.factors
        for horizon in _PANEL_SPEC.horizons
        for outcome in _PANEL_SPEC.outcome_fields
        for signal_date in dates
    )
    summaries = tuple(
        _summary(
            factor,
            horizon,
            outcome,
            tuple(
                item for item in daily
                if (item.factor, item.horizon_sessions, item.outcome_field)
                == (factor, horizon, outcome)
            ),
        )
        for factor in _PANEL_SPEC.factors
        for horizon in _PANEL_SPEC.horizons
        for outcome in _PANEL_SPEC.outcome_fields
    )
    return PointInTimePanelFactorEvaluationResult(
        source_dataset_identity="dataset-id",
        source_dataset_content_identity="dataset-content",
        specification_fingerprint=_PANEL_SPEC.fingerprint,
        daily_evaluations=daily,
        summaries=summaries,
        selection_bias_metadata=MappingProxyType({"descriptive_only": True}),
    )


def _threshold(value: int = 1) -> PanelFactorTemporalStabilitySpec:
    return replace(_TEMPORAL_SPEC, minimum_defined_dates_per_block=value)


def _target(result):
    return result.summary_for("atr_percent_14", 5, "stock_forward_return_pct")


def test_exact_four_block_boundary_assignment() -> None:
    dates = (
        "2018-08-07", "2020-12-31", "2021-01-01", "2022-12-31",
        "2023-01-01", "2024-12-31", "2025-01-01", "2026-09-17",
    )
    result = evaluate_panel_factor_temporal_stability(_source(dates), _threshold())
    blocks = tuple(
        result.block_for("atr_percent_14", 5, "stock_forward_return_pct", block.name)
        for block in _TEMPORAL_SPEC.blocks
    )
    assert [item.total_source_signal_dates for item in blocks] == [2, 2, 2, 2]
    assert [len(item.included_daily_identities) for item in blocks] == [2, 2, 2, 2]


def test_hand_calculated_block_statistics_sign_counts_and_coverage() -> None:
    dates = ("2019-01-02", "2019-01-03", "2019-01-04")
    result = evaluate_panel_factor_temporal_stability(
        _source(
            dates,
            ic_by_date=dict(zip(dates, (-1.0, 0.0, 1.0), strict=True)),
            spread_by_date=dict(zip(dates, (1.0, 2.0, 6.0), strict=True)),
        ),
        _threshold(),
    )
    block = result.block_for(
        "atr_percent_14", 5, "stock_forward_return_pct", "early_2018_2020",
    )
    assert block.mean_daily_rank_ic == pytest.approx(0.0)
    assert block.median_daily_rank_ic == pytest.approx(0.0)
    assert block.population_std_daily_ic == pytest.approx((2.0 / 3.0) ** 0.5)
    assert (block.minimum_daily_ic, block.maximum_daily_ic) == (-1.0, 1.0)
    assert (
        block.positive_ic_date_count,
        block.zero_ic_date_count,
        block.negative_ic_date_count,
    ) == (1, 1, 1)
    assert block.ic_coverage_pct == 100.0
    assert block.mean_daily_mean_spread == pytest.approx(3.0)
    assert block.population_std_daily_mean_spread == pytest.approx((14.0 / 3.0) ** 0.5)


def test_undefined_daily_values_are_excluded_not_zero_filled() -> None:
    dates = ("2019-01-02", "2019-01-03", "2019-01-04")
    result = evaluate_panel_factor_temporal_stability(
        _source(
            dates,
            ic_by_date={dates[0]: 1.0, dates[1]: None, dates[2]: None},
            spread_by_date={dates[0]: None, dates[1]: None, dates[2]: None},
        ),
        _threshold(),
    )
    block = result.block_for(
        "atr_percent_14", 5, "stock_forward_return_pct", "early_2018_2020",
    )
    assert block.ic_defined_date_count == 1
    assert block.mean_daily_rank_ic == 1.0
    assert block.spread_defined_date_count == 0
    assert block.mean_daily_mean_spread is None
    assert block.spread_undefined_reason == "no_defined_daily_spread"
    assert "daily_spread_undefined_for_entire_block" in block.warnings


def test_summary_uses_equal_block_weight_not_pooled_daily_values() -> None:
    dates = ("2019-01-02", "2019-01-03", "2021-01-04")
    values = {dates[0]: 1.0, dates[1]: 1.0, dates[2]: -1.0}
    result = evaluate_panel_factor_temporal_stability(
        _source(dates, ic_by_date=values, spread_by_date=values), _threshold(),
    )
    summary = _target(result)
    assert summary.mean_ic_across_block_means == pytest.approx(0.0)
    assert summary.mean_ic_across_block_means != pytest.approx(sum(values.values()) / 3.0)


def test_directional_flags_all_positive_and_zero_aware_sign_flips() -> None:
    dates = ("2019-01-02", "2021-01-04", "2023-01-03", "2025-01-03")
    positive_then_negative = dict(zip(dates, (1.0, 1.0, 1.0, -1.0), strict=True))
    supported = _target(evaluate_panel_factor_temporal_stability(
        _source(dates, ic_by_date=positive_then_negative, spread_by_date=positive_then_negative),
        _threshold(),
    ))
    assert supported.coverage_sufficient_for_temporal_review
    assert supported.directionally_consistent_ic
    assert supported.directionally_consistent_spread
    assert supported.descriptive_temporal_support
    assert not supported.all_blocks_positive_ic
    assert supported.ic_sign_flip_count == 1
    zero_path = dict(zip(dates, (1.0, 1.0, 0.0, -1.0), strict=True))
    zero_summary = _target(evaluate_panel_factor_temporal_stability(
        _source(dates, ic_by_date=zero_path, spread_by_date=zero_path), _threshold(),
    ))
    assert zero_summary.ic_sign_flip_count == 2
    assert zero_summary.spread_sign_flip_count == 2


def test_concentration_and_zero_denominator_behavior() -> None:
    dates = ("2019-01-02", "2021-01-04", "2023-01-03", "2025-01-03")
    values = dict(zip(dates, (1.0, -3.0, 0.0, 0.0), strict=True))
    summary = _target(evaluate_panel_factor_temporal_stability(
        _source(dates, ic_by_date=values, spread_by_date=values), _threshold(),
    ))
    assert summary.largest_absolute_mean_ic_block_concentration == pytest.approx(0.75)
    zeros = {signal_date: 0.0 for signal_date in dates}
    zero_summary = _target(evaluate_panel_factor_temporal_stability(
        _source(dates, ic_by_date=zeros, spread_by_date=zeros), _threshold(),
    ))
    assert zero_summary.largest_absolute_mean_ic_block_concentration is None
    assert zero_summary.largest_absolute_mean_spread_block_concentration is None


def test_builtin_dimensions_order_and_no_adx_special_case() -> None:
    dates = ("2019-01-02", "2021-01-04", "2023-01-03", "2025-01-03")
    result = evaluate_panel_factor_temporal_stability(_source(dates), _threshold())
    assert len(result.block_results) == 192
    assert len(result.summaries) == 48
    assert [item.factor for item in result.block_results[:24:4]] == [
        "atr_percent_14", "atr_percent_14", "atr_percent_14",
        "atr_percent_14", "atr_percent_14", "atr_percent_14",
    ]
    adx = result.summary_for("adx_14", 5, "stock_forward_return_pct")
    atr = result.summary_for("atr_percent_14", 5, "stock_forward_return_pct")
    assert adx.mean_ic_across_block_means == atr.mean_ic_across_block_means
    assert result.limitations_metadata["factor_selection_or_weighting_authority"] is False


@pytest.mark.parametrize("corruption", ("duplicate", "missing", "extra", "out_of_range"))
def test_duplicate_missing_extra_and_out_of_range_source_records_fail(corruption: str) -> None:
    source = _source(("2019-01-02",))
    changed = copy.copy(source)
    daily = list(source.daily_evaluations)
    if corruption == "duplicate":
        daily.append(daily[0])
        message = "duplicate"
    elif corruption == "missing":
        daily.pop(0)
        message = "missing"
    elif corruption == "extra":
        daily.append(replace(daily[0], factor="unknown_factor"))
        message = "outside"
    else:
        daily.append(replace(daily[0], signal_date="2027-01-01"))
        message = "outside"
    object.__setattr__(changed, "daily_evaluations", tuple(daily))
    with pytest.raises(ValueError, match=message):
        evaluate_panel_factor_temporal_stability(changed, _threshold())


def test_order_normalization_identity_sensitivity_immutability_and_pure_import() -> None:
    dates = ("2019-01-02", "2021-01-04", "2023-01-03", "2025-01-03")
    source = _source(dates)
    first = evaluate_panel_factor_temporal_stability(source, _threshold())
    changed = _source(dates, ic_by_date={dates[0]: 0.9})
    assert evaluate_panel_factor_temporal_stability(changed, _threshold()).identity != first.identity
    reordered = copy.copy(source)
    reversed_daily = tuple(reversed(source.daily_evaluations))
    summaries = tuple(
        replace(
            item,
            included_daily_identities=tuple(
                daily.identity for daily in reversed_daily
                if (daily.factor, daily.horizon_sessions, daily.outcome_field)
                == (item.factor, item.horizon_sessions, item.outcome_field)
            ),
        )
        for item in source.summaries
    )
    object.__setattr__(reordered, "daily_evaluations", reversed_daily)
    object.__setattr__(reordered, "summaries", summaries)
    normalized = evaluate_panel_factor_temporal_stability(reordered, _threshold())
    assert [item.identity for item in normalized.block_results] == [
        item.identity for item in first.block_results
    ]
    assert normalized.identity == first.identity
    with pytest.raises(FrozenInstanceError):
        first.identity = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        first.limitations_metadata["descriptive_only"] = False  # type: ignore[index]
    script = """
import json
import sys
from quantlab.evaluation import evaluate_panel_factor_temporal_stability
forbidden = {'core.database', 'backtesting.engine', 'quantlab.candidates.frozen_q70',
             'strategy.paper_v2_scanner', 'vnstock'}
print(json.dumps(sorted(forbidden.intersection(sys.modules))))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == []
