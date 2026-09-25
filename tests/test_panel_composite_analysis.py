from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import json
import math
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType, SimpleNamespace
from typing import Any, Mapping, Sequence

import pandas as pd
import pytest

from quantlab.evaluation import (
    CompositeFactorWeight,
    NEUTRAL_ADX_RSI_COMPOSITE_COMPARISON_V1,
    NeutralCompositePolicy,
    evaluate_panel_composites,
)
from quantlab.evaluation import panel_composite_analysis as composite
from quantlab.panels.research_dataset_contracts import (
    PERMITTED_USE,
    POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1,
    PointInTimeResearchDataset,
)


BLOCK_BOUNDARIES = (
    "2018-08-07", "2020-12-31",
    "2021-01-01", "2022-12-31",
    "2023-01-01", "2024-12-31",
    "2025-01-01", "2026-09-17",
)


def _dataset(
    dates: Sequence[str],
    *,
    row_counts: Mapping[str, int] | None = None,
    overrides: Mapping[str, Mapping[str, Sequence[Any]]] | None = None,
    identity_suffix: str = "base",
    reverse_rows: bool = False,
) -> PointInTimeResearchDataset:
    spec = POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1
    counts = row_counts or {value: 20 for value in dates}
    supplied = overrides or {}
    rows: list[dict[str, Any]] = []
    for signal_date in dates:
        count = counts[signal_date]
        values_by_field = supplied.get(signal_date, {})
        for ordinal in range(count):
            row = {column: None for column in spec.output_columns}
            row.update({
                "session_date": signal_date,
                "symbol": f"S{ordinal:03d}",
                "benchmark_symbol": "VNINDEX",
                "membership_set_hash": f"membership-{signal_date}",
                "membership_count": count,
                "member_ordinal": ordinal,
                "market_row_available": True,
                "availability": "AVAILABLE",
                "source_market_time": signal_date,
                "source_row_ordinal": ordinal,
                "requested_feature_count": len(spec.feature_columns),
                "available_feature_count": len(spec.feature_columns),
                "complete_feature_row": True,
                "requested_horizon_count": 3,
                "available_horizon_count": 3,
                "fully_labeled_outcome_row": True,
            })
            for factor_index, factor in enumerate(spec.feature_columns):
                values = values_by_field.get(factor)
                row[factor] = (
                    values[ordinal]
                    if values is not None
                    else float((ordinal + 1) * (factor_index + 1))
                )
                row[f"{factor}__availability"] = "AVAILABLE"
            for horizon in spec.outcome_panel_spec.horizons:
                row[f"target_session_{horizon}"] = signal_date
                stock_column = f"stock_forward_return_{horizon}_pct"
                benchmark_column = f"benchmark_forward_return_{horizon}_pct"
                excess_column = f"excess_forward_return_{horizon}_pct_points"
                status_column = f"outcome_{horizon}__availability"
                stock = values_by_field.get(stock_column)
                benchmark = values_by_field.get(benchmark_column)
                excess = values_by_field.get(excess_column)
                status = values_by_field.get(status_column)
                row[stock_column] = stock[ordinal] if stock is not None else float(ordinal + 1)
                row[benchmark_column] = benchmark[ordinal] if benchmark is not None else 0.0
                row[excess_column] = excess[ordinal] if excess is not None else row[stock_column]
                row[status_column] = status[ordinal] if status is not None else "AVAILABLE"
            rows.append(row)
    if reverse_rows:
        rows.reverse()
    frame = pd.DataFrame(rows, columns=spec.output_columns)
    dataset = object.__new__(PointInTimeResearchDataset)
    values = {
        "spec": spec,
        "observation_index_identity": f"observation-index-{identity_suffix}",
        "observation_content_identity": f"observation-content-{identity_suffix}",
        "feature_panel_identity": f"feature-panel-{identity_suffix}",
        "feature_content_identity": f"feature-content-{identity_suffix}",
        "outcome_panel_identity": f"outcome-panel-{identity_suffix}",
        "outcome_content_identity": f"outcome-content-{identity_suffix}",
        "content_identity": f"dataset-content-{identity_suffix}",
        "identity": f"dataset-{identity_suffix}",
        "session_audit": tuple(SimpleNamespace(session_date=value) for value in sorted(dates)),
        "observation_row_count": len(frame),
        "metadata": MappingProxyType({
            "future_looking": True,
            "production_signal_safe": False,
            "permitted_use": PERMITTED_USE,
            "observation_row_count": len(frame),
        }),
        "_frame": frame,
    }
    for name, value in values.items():
        object.__setattr__(dataset, name, value)
    return dataset


def _daily(result, policy: str = "ADX_ONLY", date_value: str = BLOCK_BOUNDARIES[0]):
    return result.daily_for(date_value, policy, 5, "stock_forward_return_pct")


def test_frozen_policies_contrasts_and_rank_percentile_math_with_ties() -> None:
    spec = NEUTRAL_ADX_RSI_COMPOSITE_COMPARISON_V1
    assert tuple(policy.name for policy in spec.policies) == (
        "ADX_ONLY", "RSI_ONLY", "ADX_RSI_EQUAL_WEIGHT",
        "ADX_RSI_VOLUME_EQUAL_WEIGHT",
    )
    assert tuple(
        (item.name, item.variant_policy, item.reference_policy) for item in spec.contrasts
    ) == (
        ("ADX_RSI_vs_ADX", "ADX_RSI_EQUAL_WEIGHT", "ADX_ONLY"),
        ("ADX_RSI_vs_RSI", "ADX_RSI_EQUAL_WEIGHT", "RSI_ONLY"),
        ("VOLUME_ADDON_vs_ADX_RSI", "ADX_RSI_VOLUME_EQUAL_WEIGHT", "ADX_RSI_EQUAL_WEIGHT"),
    )
    ranks = composite._average_ranks((1.0, 1.0, 3.0, 4.0))
    assert ranks == (1.5, 1.5, 3.0, 4.0)
    assert composite._percentiles(ranks) == pytest.approx((1 / 6, 1 / 6, 2 / 3, 1.0))
    factors = {
        "adx_14": (0.0, 0.5, 1.0),
        "rsi_14": (1.0, 0.5, 0.0),
        "volume_ratio_20": (0.25, 0.5, 0.75),
    }
    assert composite._composite_scores(spec.policies[0], factors) == (0.0, 0.5, 1.0)
    assert composite._composite_scores(spec.policies[2], factors) == (0.5, 0.5, 0.5)
    assert composite._composite_scores(spec.policies[3], factors) == pytest.approx(
        (5 / 12, 0.5, 7 / 12)
    )


def test_common_sample_is_identical_and_minimum_twenty_is_exact() -> None:
    day = BLOCK_BOUNDARIES[0]
    twenty_one = list(range(21))
    volume = [float(value) for value in twenty_one]
    volume[-1] = math.nan
    result = evaluate_panel_composites(_dataset(
        (day,), row_counts={day: 21}, overrides={day: {"volume_ratio_20": volume}},
    ))
    records = tuple(result.daily_for(day, policy.name, 5, "stock_forward_return_pct") for policy in result_spec().policies)
    assert {item.shared_listwise_finite_count for item in records} == {20}
    assert len({item.shared_sample_evidence_sha256 for item in records}) == 1
    assert all(item.rank_ic is not None for item in records)

    short = evaluate_panel_composites(_dataset((day,), row_counts={day: 19}))
    assert _daily(short).rank_ic is None
    assert _daily(short).ic_undefined_reason == "fewer_than_minimum_shared_observations"
    assert _daily(short).spread_undefined_reason == "fewer_than_minimum_shared_observations"


def result_spec():
    return NEUTRAL_ADX_RSI_COMPOSITE_COMPARISON_V1


def test_hand_calculated_rank_ic_and_thirty_seventy_buckets() -> None:
    day = BLOCK_BOUNDARIES[0]
    ascending = list(range(20))
    result = evaluate_panel_composites(_dataset(
        (day,), overrides={day: {
            "adx_14": ascending,
            "rsi_14": ascending,
            "volume_ratio_20": ascending,
            "stock_forward_return_5_pct": ascending,
        }},
    ))
    daily = _daily(result)
    assert daily.rank_ic == pytest.approx(1.0)
    assert daily.low_bucket_count == 6
    assert daily.high_bucket_count == 6
    assert daily.low_bucket_mean_outcome == pytest.approx(2.5)
    assert daily.high_bucket_mean_outcome == pytest.approx(16.5)
    assert daily.high_minus_low_mean_spread == pytest.approx(14.0)

    tied = composite._percentiles(composite._average_ranks((0.0,) * 8 + tuple(range(8, 20))))
    assert len(set(tied[:8])) == 1
    assert not ({index for index, value in enumerate(tied) if value <= .30} &
                {index for index, value in enumerate(tied) if value >= .70})


@pytest.mark.parametrize(
    ("overrides", "expected_ic", "expected_spread"),
    (
        ({"adx_14": [1.0] * 20, "rsi_14": [1.0] * 20, "volume_ratio_20": [1.0] * 20},
         "constant_composite_score", "empty_low_bucket"),
        ({"stock_forward_return_5_pct": [1.0] * 20}, "constant_outcome", None),
        ({"outcome_5__availability": ["CENSORED_AFTER_DATA_END"] * 20},
         "unavailable_outcome_status", "unavailable_outcome_status"),
    ),
)
def test_explicit_undefined_reasons(
    overrides: Mapping[str, Sequence[Any]], expected_ic: str, expected_spread: str | None,
) -> None:
    day = BLOCK_BOUNDARIES[0]
    daily = _daily(evaluate_panel_composites(_dataset((day,), overrides={day: overrides})))
    assert daily.ic_undefined_reason == expected_ic
    if expected_spread is not None:
        assert daily.spread_undefined_reason == expected_spread


def test_exact_dimensions_temporal_boundaries_sign_flips_and_concentration() -> None:
    directions = (1, 1, -1, -1, -1, -1, 1, 1)
    overrides = {
        signal_date: {
            "stock_forward_return_5_pct": (
                list(range(20)) if direction > 0 else list(reversed(range(20)))
            )
        }
        for signal_date, direction in zip(BLOCK_BOUNDARIES, directions, strict=True)
    }
    result = evaluate_panel_composites(_dataset(BLOCK_BOUNDARIES, overrides=overrides))
    assert len(result.daily_evaluations) == len(BLOCK_BOUNDARIES) * 24
    assert len(result.summaries) == 24
    assert len(result.block_evaluations) == 96
    assert len(result.contrast_summaries) == 18
    assert len(result.contrast_block_evaluations) == 72
    assert result.future_looking is True
    assert result.permitted_use == "offline_research_evaluation_only"
    assert result.production_signal_safe is False
    summary = next(
        item for item in result.summaries
        if item.policy_name == "ADX_ONLY" and item.horizon_sessions == 5
        and item.outcome_field == "stock_forward_return_pct"
    )
    assert summary.chronological_block_mean_ic_sign_flip_count == 2
    assert summary.minimum_block_mean_ic == pytest.approx(-1.0)
    assert summary.maximum_block_mean_ic == pytest.approx(1.0)
    assert summary.range_block_mean_ic == pytest.approx(2.0)
    assert summary.largest_absolute_block_mean_ic_concentration == pytest.approx(.25)
    assert len(summary.ordered_block_identities) == 4
    assert tuple(item.block_name for item in result.block_evaluations[:4]) == tuple(
        block.name for block in result_spec().blocks
    )


def test_whole_period_uses_equal_date_weight_and_contrasts_are_exactly_paired() -> None:
    dates = ("2018-08-07", "2018-08-08")
    counts = {dates[0]: 20, dates[1]: 40}
    overrides = {
        dates[0]: {"stock_forward_return_5_pct": list(range(20))},
        dates[1]: {"stock_forward_return_5_pct": list(reversed(range(40)))},
    }
    result = evaluate_panel_composites(_dataset(dates, row_counts=counts, overrides=overrides))
    summary = next(
        item for item in result.summaries
        if item.policy_name == "ADX_ONLY" and item.horizon_sessions == 5
        and item.outcome_field == "stock_forward_return_pct"
    )
    assert summary.mean_daily_rank_ic == pytest.approx(0.0)
    contrast = next(
        item for item in result.contrast_summaries
        if item.contrast_name == "ADX_RSI_vs_ADX" and item.horizon_sessions == 5
        and item.outcome_field == "stock_forward_return_pct"
    )
    variant = tuple(
        item for item in result.daily_evaluations
        if item.policy_name == "ADX_RSI_EQUAL_WEIGHT" and item.horizon_sessions == 5
        and item.outcome_field == "stock_forward_return_pct" and item.rank_ic is not None
    )
    reference = tuple(
        item for item in result.daily_evaluations
        if item.policy_name == "ADX_ONLY" and item.horizon_sessions == 5
        and item.outcome_field == "stock_forward_return_pct" and item.rank_ic is not None
    )
    deltas = tuple(left.rank_ic - right.rank_ic for left, right in zip(variant, reference, strict=True))
    assert contrast.paired_ic_date_count == len(deltas)
    assert contrast.mean_daily_ic_delta == pytest.approx(sum(deltas) / len(deltas))


def test_evaluation_frame_called_once_predictor_never_and_malformed_sources_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = _dataset((BLOCK_BOUNDARIES[0],))
    calls = {"evaluation": 0}
    original = PointInTimeResearchDataset.evaluation_frame

    def evaluation_frame(self):
        calls["evaluation"] += 1
        return original(self)

    def predictor_frame(self):
        raise AssertionError("predictor_frame must not be called")

    monkeypatch.setattr(PointInTimeResearchDataset, "evaluation_frame", evaluation_frame)
    monkeypatch.setattr(PointInTimeResearchDataset, "predictor_frame", predictor_frame)
    evaluate_panel_composites(dataset)
    assert calls == {"evaluation": 1}

    broken = _dataset((BLOCK_BOUNDARIES[0],), identity_suffix="broken")
    object.__setattr__(broken, "identity", "")
    with pytest.raises(ValueError, match="non-empty"):
        evaluate_panel_composites(broken)

    duplicate = _dataset((BLOCK_BOUNDARIES[0],), identity_suffix="duplicate")
    duplicated_frame = pd.concat(
        [duplicate._frame, duplicate._frame.iloc[[0]]], ignore_index=True,
    )
    object.__setattr__(duplicate, "_frame", duplicated_frame)
    object.__setattr__(duplicate, "observation_row_count", len(duplicated_frame))
    object.__setattr__(duplicate, "metadata", MappingProxyType({
        **dict(duplicate.metadata), "observation_row_count": len(duplicated_frame),
    }))
    with pytest.raises(ValueError, match="duplicate observation keys"):
        evaluate_panel_composites(duplicate)

    with pytest.raises(ValueError, match="outside the composite interval"):
        evaluate_panel_composites(_dataset(("2018-08-06",), identity_suffix="outside"))


def test_invalid_weights_factors_policy_order_and_contrasts_fail() -> None:
    with pytest.raises(ValueError, match="strictly positive"):
        CompositeFactorWeight("adx_14", 0.0)
    with pytest.raises(ValueError, match="unsupported"):
        CompositeFactorWeight("ema20_distance_pct", 1.0)
    with pytest.raises(ValueError, match="unsupported"):
        CompositeFactorWeight("target_session_5", 1.0)
    with pytest.raises(ValueError, match="sum to one"):
        NeutralCompositePolicy("BAD", (CompositeFactorWeight("adx_14", .9),))
    with pytest.raises(ValueError, match="four frozen policies"):
        replace(
            result_spec(),
            policies=tuple(reversed(result_spec().policies)),
        )
    with pytest.raises(ValueError, match="three frozen contrasts"):
        replace(
            result_spec(),
            contrasts=tuple(reversed(result_spec().contrasts)),
        )
    with pytest.raises(ValueError, match="bucket boundaries"):
        replace(result_spec(), low_bucket_max_percentile=.70, high_bucket_min_percentile=.30)


def test_identity_is_order_invariant_sensitive_and_result_is_defensively_immutable() -> None:
    day = BLOCK_BOUNDARIES[0]
    base = _dataset((day,))
    original_frame = base._frame.copy(deep=True)
    base_result = evaluate_panel_composites(base)
    reordered = evaluate_panel_composites(_dataset((day,), reverse_rows=True))
    assert reordered.identity == base_result.identity

    changed_factor = evaluate_panel_composites(_dataset(
        (day,), overrides={day: {"adx_14": list(reversed(range(20)))}},
    ))
    changed_outcome = evaluate_panel_composites(_dataset(
        (day,), overrides={day: {"stock_forward_return_5_pct": list(reversed(range(20)))}},
    ))
    changed_status = evaluate_panel_composites(_dataset(
        (day,), overrides={day: {"outcome_5__availability": ["AVAILABLE"] * 19 + ["CENSORED_AFTER_DATA_END"]}},
    ))
    changed_source = evaluate_panel_composites(_dataset((day,), identity_suffix="different"))
    assert len({
        base_result.identity, changed_factor.identity, changed_outcome.identity,
        changed_status.identity, changed_source.identity,
    }) == 5
    assert NeutralCompositePolicy(
        "P", (CompositeFactorWeight("adx_14", .6), CompositeFactorWeight("rsi_14", .4)),
    ).fingerprint != NeutralCompositePolicy(
        "P", (CompositeFactorWeight("adx_14", .4), CompositeFactorWeight("rsi_14", .6)),
    ).fingerprint
    pd.testing.assert_frame_equal(base._frame, original_frame)
    with pytest.raises(FrozenInstanceError):
        base_result.production_signal_safe = True  # type: ignore[misc]
    with pytest.raises(TypeError):
        base_result.limitations_metadata["new"] = True  # type: ignore[index]
    with pytest.raises(TypeError):
        _daily(base_result).factor_missing_or_nonfinite_counts["adx_14"] = 9  # type: ignore[index]


def test_fresh_import_is_lazy_and_has_no_database_strategy_or_execution_dependencies() -> None:
    project_root = Path(__file__).resolve().parents[1]
    script = """
import json, sys
import quantlab.evaluation
before = set(sys.modules)
names = (
    'CompositeFactorWeight', 'NeutralCompositePolicy', 'PanelCompositeAnalysisSpec',
    'DailyCompositeEvaluation', 'CompositeBlockEvaluation',
    'CompositeEvaluationSummary', 'PanelCompositeAnalysisResult',
    'NEUTRAL_ADX_RSI_COMPOSITE_COMPARISON_V1', 'evaluate_panel_composites',
)
for name in names:
    getattr(quantlab.evaluation, name)
forbidden_prefixes = (
    'core.database', 'quantlab.market_data', 'quantlab.features',
    'quantlab.candidates', 'quantlab.alpha', 'strategy', 'backtesting',
    'execution', 'vnstock',
)
loaded = sorted(
    module for module in set(sys.modules).difference(before)
    if module == 'sqlite3' or module.startswith(forbidden_prefixes)
)
print(json.dumps({'loaded': loaded}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=project_root,
        text=True, capture_output=True, check=True,
    )
    assert json.loads(completed.stdout) == {"loaded": []}
