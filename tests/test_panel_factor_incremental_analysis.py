from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType, SimpleNamespace
from typing import Any, Mapping, Sequence

import pandas as pd
import pytest

from quantlab.evaluation import (
    NEUTRAL_PANEL_INCREMENTAL_FACTOR_ANALYSIS_V1,
    IncrementalFactorHypothesis,
    PanelFactorIncrementalAnalysisSpec,
    evaluate_panel_factor_incremental_analysis,
)
from quantlab.evaluation.panel_factor_temporal_stability import (
    NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2,
)
from quantlab.panels.research_dataset_contracts import (
    PERMITTED_USE,
    POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1,
    PointInTimeResearchDataset,
)


BLOCK_DATES = ("2018-08-07", "2021-01-01", "2023-01-01", "2025-01-01")
BLOCK_BOUNDARY_DATES = (
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
                row[benchmark_column] = (
                    benchmark[ordinal] if benchmark is not None else 0.0
                )
                row[excess_column] = (
                    excess[ordinal] if excess is not None else row[stock_column]
                )
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
        "session_audit": tuple(
            SimpleNamespace(session_date=value) for value in sorted(dates)
        ),
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


def _spec(
    hypothesis: IncrementalFactorHypothesis,
    *,
    horizons: tuple[int, ...] = (5,),
    outcomes: tuple[str, ...] = ("stock_forward_return_pct",),
) -> PanelFactorIncrementalAnalysisSpec:
    return PanelFactorIncrementalAnalysisSpec(
        name=f"test_{hypothesis.name}",
        version="1",
        hypotheses=(hypothesis,),
        horizons=horizons,
        outcome_fields=outcomes,
        blocks=NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2.blocks,
    )


def _daily(
    dataset: PointInTimeResearchDataset,
    hypothesis: IncrementalFactorHypothesis,
):
    spec = _spec(hypothesis)
    result = evaluate_panel_factor_incremental_analysis(dataset, spec)
    signal_date = dataset.session_audit[0].session_date
    return result, result.daily_for(
        signal_date, hypothesis.name, 5, "stock_forward_return_pct",
    )


def test_fixed_public_contract_and_hand_calculated_no_control_equivalence() -> None:
    expected = (
        ("adx_given_rsi", "adx_14", ("rsi_14",)),
        ("rsi_given_adx", "rsi_14", ("adx_14",)),
        ("volume_given_adx_rsi", "volume_ratio_20", ("adx_14", "rsi_14")),
        ("stock_return_20d_given_rsi", "stock_return_20d_pct", ("rsi_14",)),
        ("rsi_given_stock_return_20d", "rsi_14", ("stock_return_20d_pct",)),
        ("ema20_distance_given_rsi", "ema20_distance_pct", ("rsi_14",)),
        ("rsi_given_ema20_distance", "rsi_14", ("ema20_distance_pct",)),
        ("atr_given_adx_rsi", "atr_percent_14", ("adx_14", "rsi_14")),
    )
    assert tuple(
        (value.name, value.target_factor, value.control_factors)
        for value in NEUTRAL_PANEL_INCREMENTAL_FACTOR_ANALYSIS_V1.hypotheses
    ) == expected
    hypothesis = IncrementalFactorHypothesis("raw_equivalent", "adx_14", ())
    values = list(range(20))
    _, daily = _daily(_dataset(
        (BLOCK_DATES[0],),
        overrides={BLOCK_DATES[0]: {
            "adx_14": values,
            "stock_forward_return_5_pct": list(reversed(values)),
        }},
    ), hypothesis)
    assert daily.raw_rank_ic == pytest.approx(-1.0)
    assert daily.partial_rank_ic == pytest.approx(daily.raw_rank_ic)
    assert daily.control_design_rank == daily.expected_design_rank == 1


def test_control_removes_shared_rank_structure_but_not_independent_residual_signal() -> None:
    date_value = BLOCK_DATES[0]
    within = list(range(20)) * 2
    control = [0.0] * 20 + [1.0] * 20
    target = [float(value + (100 if index >= 20 else 0)) for index, value in enumerate(within)]
    uncorrelated = [(9 * value) % 20 for value in within]
    mostly_control = [
        float(value + (100 if index >= 20 else 0))
        for index, value in enumerate(uncorrelated)
    ]
    hypothesis = IncrementalFactorHypothesis("conditional", "adx_14", ("rsi_14",))
    _, removed = _daily(_dataset(
        (date_value,), row_counts={date_value: 40},
        overrides={date_value: {
            "adx_14": target,
            "rsi_14": control,
            "stock_forward_return_5_pct": mostly_control,
        }},
    ), hypothesis)
    assert removed.raw_rank_ic > 0.70
    assert abs(removed.partial_rank_ic or 0.0) < 0.10

    _, retained = _daily(_dataset(
        (date_value,), row_counts={date_value: 40},
        overrides={date_value: {
            "adx_14": target,
            "rsi_14": control,
            "stock_forward_return_5_pct": target,
        }},
    ), hypothesis)
    assert retained.partial_rank_ic == pytest.approx(1.0)


def test_two_control_residualization_is_deterministic_and_control_order_is_bound() -> None:
    date_value = BLOCK_DATES[0]
    target = list(range(20))
    first = [value % 2 for value in target]
    second = [(value // 2) % 2 for value in target]
    dataset = _dataset((date_value,), overrides={date_value: {
        "volume_ratio_20": target,
        "adx_14": first,
        "rsi_14": second,
        "stock_forward_return_5_pct": target,
    }})
    declared = IncrementalFactorHypothesis(
        "two_controls", "volume_ratio_20", ("adx_14", "rsi_14"),
    )
    reversed_controls = IncrementalFactorHypothesis(
        "two_controls", "volume_ratio_20", ("rsi_14", "adx_14"),
    )
    first_result, first_daily = _daily(dataset, declared)
    second_result, second_daily = _daily(dataset, reversed_controls)
    assert first_daily.partial_rank_ic == pytest.approx(1.0)
    assert second_daily.partial_rank_ic == pytest.approx(1.0)
    assert first_daily.control_design_rank == first_daily.expected_design_rank == 3
    assert first_result.specification_fingerprint != second_result.specification_fingerprint
    assert first_result.identity != second_result.identity


def test_average_rank_ties_exact_listwise_sample_and_twenty_row_boundary() -> None:
    date_value = BLOCK_DATES[0]
    count = 23
    target: list[Any] = [1.0, 1.0, *map(float, range(2, count))]
    control: list[Any] = [float(value % 3) for value in range(count)]
    outcome: list[Any] = list(target)
    target[20] = pd.NA
    control[21] = float("inf")
    statuses = ["AVAILABLE"] * count
    statuses[22] = "CENSORED_AFTER_DATA_END"
    hypothesis = IncrementalFactorHypothesis("listwise", "adx_14", ("rsi_14",))
    _, daily = _daily(_dataset(
        (date_value,), row_counts={date_value: count},
        overrides={date_value: {
            "adx_14": target,
            "rsi_14": control,
            "stock_forward_return_5_pct": outcome,
            "outcome_5__availability": statuses,
        }},
    ), hypothesis)
    assert daily.listwise_finite_count == 20
    assert daily.target_missing_or_nonfinite_count == 1
    assert daily.control_missing_or_nonfinite_counts == {"rsi_14": 1}
    assert daily.unavailable_outcome_status_count == 1
    assert daily.listwise_sample_evidence[0][4] == daily.listwise_sample_evidence[1][4]
    assert daily.undefined_reason is None

    below = _dataset(
        (date_value,), row_counts={date_value: 19},
        overrides={date_value: {
            "adx_14": list(range(19)),
            "rsi_14": [value % 2 for value in range(19)],
            "stock_forward_return_5_pct": list(range(19)),
        }},
    )
    assert _daily(below, hypothesis)[1].undefined_reason == (
        "fewer_than_minimum_listwise_finite_observations"
    )


@pytest.mark.parametrize(
    ("controls", "target", "control_values", "outcome", "reason"),
    (
        (("rsi_14",), [1.0] * 20, (list(range(20)),), list(range(20)), "constant_target_rank"),
        (("rsi_14",), list(range(20)), (list(range(20)),), [1.0] * 20, "constant_outcome_rank"),
        (("rsi_14",), list(range(20)), ([1.0] * 20,), list(range(20)), "constant_control:rsi_14"),
        (
            ("rsi_14", "stock_return_20d_pct"),
            [(9 * value) % 20 for value in range(20)],
            (list(range(20)), list(range(20))),
            [(9 * value) % 20 for value in range(20)],
            "rank_deficient_control_design",
        ),
        (
            ("rsi_14",), list(range(20)), (list(range(20)),),
            [(9 * value) % 20 for value in range(20)], "target_residual_variance_zero",
        ),
        (
            ("rsi_14",), [(9 * value) % 20 for value in range(20)],
            (list(range(20)),), list(range(20)), "outcome_residual_variance_zero",
        ),
    ),
)
def test_explicit_undefined_reasons(
    controls: tuple[str, ...],
    target: Sequence[Any],
    control_values: tuple[Sequence[Any], ...],
    outcome: Sequence[Any],
    reason: str,
) -> None:
    date_value = BLOCK_DATES[0]
    overrides: dict[str, Sequence[Any]] = {
        "adx_14": target,
        "stock_forward_return_5_pct": outcome,
    }
    overrides.update(dict(zip(controls, control_values, strict=True)))
    hypothesis = IncrementalFactorHypothesis("undefined", "adx_14", controls)
    daily = _daily(_dataset(
        (date_value,), overrides={date_value: overrides},
    ), hypothesis)[1]
    assert daily.partial_rank_ic is None
    assert daily.undefined_reason == reason


def test_builtin_dimensions_canonical_order_and_temporal_statistics() -> None:
    overrides: dict[str, dict[str, Sequence[Any]]] = {}
    for index, signal_date in enumerate(BLOCK_BOUNDARY_DATES):
        target = list(range(20))
        outcome = target if (index // 2) % 2 == 0 else list(reversed(target))
        overrides[signal_date] = {
            "adx_14": target,
            "rsi_14": [value % 2 for value in target],
            "stock_forward_return_5_pct": outcome,
        }
    result = evaluate_panel_factor_incremental_analysis(_dataset(
        BLOCK_BOUNDARY_DATES, overrides=overrides,
    ))
    assert len(result.summaries) == 48
    assert len(result.block_evaluations) == 192
    assert len(result.daily_evaluations) == len(BLOCK_BOUNDARY_DATES) * 48
    assert [
        (value.hypothesis_name, value.horizon_sessions, value.outcome_field)
        for value in result.summaries
    ] == [
        (hypothesis.name, horizon, outcome)
        for hypothesis in NEUTRAL_PANEL_INCREMENTAL_FACTOR_ANALYSIS_V1.hypotheses
        for horizon in (5, 10, 20)
        for outcome in ("stock_forward_return_pct", "excess_forward_return_pct_points")
    ]
    summary = result.summary_for("adx_given_rsi", 5, "stock_forward_return_pct")
    assert [
        value.total_signal_date_count
        for value in result.block_evaluations
        if value.hypothesis_name == "adx_given_rsi"
        and value.horizon_sessions == 5
        and value.outcome_field == "stock_forward_return_pct"
    ] == [2, 2, 2, 2]
    assert summary.chronological_partial_sign_flip_count == 3
    assert summary.largest_absolute_block_mean_partial_rank_ic_concentration == pytest.approx(0.25)
    assert summary.minimum_block_mean_partial_rank_ic == pytest.approx(-1.0)
    assert summary.maximum_block_mean_partial_rank_ic == pytest.approx(1.0)
    assert summary.all_blocks_positive_partial_rank_ic is False
    assert summary.all_blocks_negative_partial_rank_ic is False
    assert result.future_looking is True
    assert result.permitted_use == "offline_research_evaluation_only"
    assert result.production_signal_safe is False


def test_daily_statistics_receive_equal_date_weight_and_never_pool_symbols() -> None:
    dates = (BLOCK_DATES[0], "2018-08-08")
    hypothesis = IncrementalFactorHypothesis("equal_dates", "adx_14", ("rsi_14",))
    overrides = {
        dates[0]: {
            "adx_14": list(range(20)),
            "rsi_14": [value % 2 for value in range(20)],
            "stock_forward_return_5_pct": list(range(20)),
        },
        dates[1]: {
            "adx_14": list(range(40)),
            "rsi_14": [value % 2 for value in range(40)],
            "stock_forward_return_5_pct": list(reversed(range(40))),
        },
    }
    result = evaluate_panel_factor_incremental_analysis(_dataset(
        dates, row_counts={dates[0]: 20, dates[1]: 40}, overrides=overrides,
    ), _spec(hypothesis))
    values = tuple(value.partial_rank_ic for value in result.daily_evaluations)
    assert values == pytest.approx((1.0, -1.0))
    assert result.summaries[0].mean_daily_partial_rank_ic == pytest.approx(sum(values) / 2.0)
    assert result.summaries[0].average_listwise_finite_count == 30.0


def test_evaluation_frame_once_predictor_never_and_invalid_sources_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = _dataset((BLOCK_DATES[0],))
    original = PointInTimeResearchDataset.evaluation_frame
    calls: list[int] = []

    def evaluation_frame(self: PointInTimeResearchDataset) -> pd.DataFrame:
        calls.append(1)
        return original(self)

    monkeypatch.setattr(PointInTimeResearchDataset, "evaluation_frame", evaluation_frame)
    monkeypatch.setattr(
        PointInTimeResearchDataset,
        "predictor_frame",
        lambda _self: pytest.fail("predictor_frame must not be used"),
    )
    evaluate_panel_factor_incremental_analysis(dataset)
    assert calls == [1]

    with pytest.raises(ValueError, match="cannot control for its target"):
        IncrementalFactorHypothesis("bad", "adx_14", ("adx_14",))
    with pytest.raises(ValueError, match="unique"):
        IncrementalFactorHypothesis("bad", "adx_14", ("rsi_14", "rsi_14"))
    with pytest.raises(ValueError, match="unsupported target"):
        IncrementalFactorHypothesis("leak", "stock_forward_return_5_pct", ())

    corrupted = _dataset((BLOCK_DATES[0],))
    object.__setattr__(corrupted, "outcome_content_identity", "")
    with pytest.raises(ValueError, match="outcome_content_identity"):
        evaluate_panel_factor_incremental_analysis(corrupted)

    duplicate = _dataset((BLOCK_DATES[0],))
    frame = pd.concat([duplicate._frame, duplicate._frame.iloc[[0]]], ignore_index=True)
    object.__setattr__(duplicate, "_frame", frame)
    object.__setattr__(duplicate, "observation_row_count", len(frame))
    object.__setattr__(duplicate, "metadata", MappingProxyType({
        **dict(duplicate.metadata), "observation_row_count": len(frame),
    }))
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_panel_factor_incremental_analysis(duplicate)


def test_identity_order_invariance_content_sensitivity_and_defensive_ownership() -> None:
    date_value = BLOCK_DATES[0]
    baseline_dataset = _dataset((date_value,))
    original_frame = baseline_dataset._frame.copy(deep=True)
    baseline = evaluate_panel_factor_incremental_analysis(baseline_dataset)
    reordered = evaluate_panel_factor_incremental_analysis(_dataset(
        (date_value,), reverse_rows=True,
    ))
    assert baseline.identity == reordered.identity
    assert baseline.source_bounded_content_identity == reordered.source_bounded_content_identity

    changes = (
        {"adx_14": [*range(19), 100.0]},
        {"rsi_14": [*range(19), 100.0]},
        {"stock_forward_return_5_pct": [*range(19), 100.0]},
        {"adx_14": [*range(19), pd.NA]},
        {"outcome_5__availability": ["AVAILABLE"] * 19 + ["CENSORED_AFTER_DATA_END"]},
    )
    identities = {
        evaluate_panel_factor_incremental_analysis(_dataset(
            (date_value,), overrides={date_value: change},
        )).identity
        for change in changes
    }
    assert baseline.identity not in identities
    changed_provenance = evaluate_panel_factor_incremental_analysis(_dataset(
        (date_value,), identity_suffix="other",
    ))
    assert changed_provenance.identity != baseline.identity
    pd.testing.assert_frame_equal(baseline_dataset._frame, original_frame, check_exact=True)
    with pytest.raises(FrozenInstanceError):
        baseline.source_dataset_identity = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        baseline.limitations_metadata["partial_rank_ic_is_causal"] = True  # type: ignore[index]


def test_fresh_import_has_no_data_strategy_or_execution_side_effects(tmp_path: Path) -> None:
    script = """
import json, socket, sqlite3, sys
sqlite3.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('database access'))
socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('network access'))
from quantlab.evaluation import NEUTRAL_PANEL_INCREMENTAL_FACTOR_ANALYSIS_V1
assert NEUTRAL_PANEL_INCREMENTAL_FACTOR_ANALYSIS_V1.version == '1'
forbidden = (
    'core.database', 'quantlab.catalog.market_data_snapshot', 'quantlab.features.builtins',
    'quantlab.candidates', 'quantlab.alpha', 'strategy', 'backtesting', 'vnstock',
)
print(json.dumps(sorted(name for name in sys.modules if name.startswith(forbidden))))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(
            os.environ,
            MARKET_DATABASE_PATH=str(tmp_path / "must-not-exist.db"),
            PYTHONDONTWRITEBYTECODE="1",
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == []
    assert not (tmp_path / "must-not-exist.db").exists()
