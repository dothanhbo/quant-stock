from __future__ import annotations

from dataclasses import FrozenInstanceError
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
    NEUTRAL_ADX_RSI_SELECTION_DIAGNOSTICS_V1,
    evaluate_panel_policy_selection_diagnostics,
)
from quantlab.panels.research_dataset_contracts import (
    PERMITTED_USE,
    POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1,
    PointInTimeResearchDataset,
)


BOUNDARIES = (
    "2018-08-07", "2020-12-31", "2021-01-01", "2022-12-31",
    "2023-01-01", "2024-12-31", "2025-01-01", "2026-09-17",
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
    counts = row_counts or {value: 25 for value in dates}
    supplied = overrides or {}
    rows: list[dict[str, Any]] = []
    for signal_date in dates:
        count = counts[signal_date]
        values = supplied.get(signal_date, {})
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
                "requested_horizon_count": len(spec.outcome_panel_spec.horizons),
                "available_horizon_count": len(spec.outcome_panel_spec.horizons),
                "fully_labeled_outcome_row": True,
            })
            for factor_index, factor in enumerate(spec.feature_columns):
                factor_values = values.get(factor)
                row[factor] = (
                    factor_values[ordinal]
                    if factor_values is not None
                    else float((ordinal + 1) * (factor_index + 1))
                )
                row[f"{factor}__availability"] = "AVAILABLE"
            for horizon in spec.outcome_panel_spec.horizons:
                row[f"target_session_{horizon}"] = signal_date
                row[f"stock_forward_return_{horizon}_pct"] = float(ordinal)
                row[f"benchmark_forward_return_{horizon}_pct"] = 0.0
                row[f"excess_forward_return_{horizon}_pct_points"] = float(ordinal)
                row[f"outcome_{horizon}__availability"] = "AVAILABLE"
            rows.append(row)
    if reverse_rows:
        rows.reverse()
    frame = pd.DataFrame(rows, columns=spec.output_columns)
    dataset = object.__new__(PointInTimeResearchDataset)
    attributes = {
        "spec": spec,
        "observation_index_identity": f"observation-index-{identity_suffix}",
        "observation_content_identity": f"observation-content-{identity_suffix}",
        "feature_panel_identity": f"feature-panel-{identity_suffix}",
        "feature_content_identity": f"feature-content-{identity_suffix}",
        "outcome_panel_identity": f"outcome-panel-{identity_suffix}",
        "outcome_content_identity": f"outcome-content-{identity_suffix}",
        "snapshot_id": f"snapshot-{identity_suffix}",
        "universe_membership_identity": f"universe-{identity_suffix}",
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
    for name, value in attributes.items():
        object.__setattr__(dataset, name, value)
    return dataset


def test_frozen_policy_score_arithmetic_common_population_and_single_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert tuple(policy.name for policy in NEUTRAL_ADX_RSI_SELECTION_DIAGNOSTICS_V1.policies) == (
        "ADX_ONLY", "ADX_RSI_EQUAL_WEIGHT",
    )
    assert NEUTRAL_ADX_RSI_SELECTION_DIAGNOSTICS_V1.selection_budgets == (5, 10, 20)
    signal_date = BOUNDARIES[0]
    dataset = _dataset(
        (signal_date,),
        row_counts={signal_date: 5},
        overrides={signal_date: {
            "adx_14": [1, 2, 3, 4, 5],
            "rsi_14": [5, 1, 4, 2, 3],
        }},
    )
    calls = {"predictor": 0, "evaluation": 0}
    original = PointInTimeResearchDataset.predictor_frame

    def predictor(self):
        calls["predictor"] += 1
        return original(self)

    def forbidden(_self):
        calls["evaluation"] += 1
        raise AssertionError("evaluation_frame must not be called")

    monkeypatch.setattr(PointInTimeResearchDataset, "predictor_frame", predictor)
    monkeypatch.setattr(PointInTimeResearchDataset, "evaluation_frame", forbidden)
    result = evaluate_panel_policy_selection_diagnostics(dataset)

    adx = result.selection_for(signal_date, "ADX_ONLY", 5)
    equal = result.selection_for(signal_date, "ADX_RSI_EQUAL_WEIGHT", 5)
    assert calls == {"predictor": 1, "evaluation": 0}
    assert adx.complete_eligible_factor_evidence == equal.complete_eligible_factor_evidence
    assert adx.complete_policy_score_ordering == (
        ("S004", 1.0), ("S003", 0.75), ("S002", 0.5), ("S001", 0.25), ("S000", 0.0),
    )
    assert tuple(item[0] for item in equal.complete_policy_score_ordering) == (
        "S004", "S002", "S000", "S003", "S001",
    )
    assert tuple(item[1] for item in equal.complete_policy_score_ordering) == pytest.approx(
        (.75, .625, .5, .5, .125),
    )


def test_top_budgets_insufficient_population_and_boundary_tie_split() -> None:
    signal_date = BOUNDARIES[0]
    result = evaluate_panel_policy_selection_diagnostics(_dataset((signal_date,)))
    assert tuple(
        result.selection_for(signal_date, "ADX_ONLY", budget).actual_selected_count
        for budget in (5, 10, 20)
    ) == (5, 10, 20)

    tied = evaluate_panel_policy_selection_diagnostics(_dataset(
        (signal_date,),
        row_counts={signal_date: 6},
        overrides={signal_date: {"adx_14": [1.0] * 6, "rsi_14": list(range(6))}},
    )).selection_for(signal_date, "ADX_ONLY", 5)
    assert tied.ordered_selected_symbols == ("S000", "S001", "S002", "S003", "S004")
    assert tied.boundary_tie_count == 6
    assert tied.selected_from_boundary_tie_count == 5
    assert tied.boundary_split_equal_score_group is True
    assert tied.insufficient_cross_section is False

    short = evaluate_panel_policy_selection_diagnostics(_dataset(
        (signal_date,), row_counts={signal_date: 3},
    )).selection_for(signal_date, "ADX_ONLY", 20)
    assert short.actual_selected_count == 3
    assert short.requested_selection_count == 20
    assert short.insufficient_cross_section is True


def test_first_date_and_hand_calculated_membership_turnover() -> None:
    dates = ("2018-08-07", "2018-08-08")
    result = evaluate_panel_policy_selection_diagnostics(_dataset(
        dates,
        row_counts={value: 6 for value in dates},
        overrides={
            dates[0]: {"adx_14": [1, 2, 3, 4, 5, 6]},
            dates[1]: {"adx_14": [7, 2, 3, 4, 5, 6]},
        },
    ))
    first = result.selection_for(dates[0], "ADX_ONLY", 5)
    second = result.selection_for(dates[1], "ADX_ONLY", 5)
    assert first.one_way_turnover is None and first.symmetric_turnover is None
    assert first.one_way_turnover_undefined_reason == "first_available_date"
    assert second.entries == ("S000",)
    assert second.exits == ("S001",)
    assert second.retained_symbols == ("S005", "S004", "S003", "S002")
    assert second.overlap_with_previous_count == 4
    assert second.one_way_turnover == pytest.approx(0.2)
    assert second.symmetric_turnover == pytest.approx(0.2)


def test_policy_overlap_jaccard_ordinal_and_set_versus_order_equality() -> None:
    day = BOUNDARIES[0]
    result = evaluate_panel_policy_selection_diagnostics(_dataset(
        (day,),
        row_counts={day: 6},
        overrides={day: {
            "adx_14": [1, 2, 3, 4, 5, 6],
            "rsi_14": [6, 5, 4, 3, 2, 1],
        }},
    ))
    comparison = result.comparison_for(day, 5)
    assert comparison.intersection_count == 4
    assert comparison.union_count == 6
    assert comparison.overlap_coefficient == pytest.approx(0.8)
    assert comparison.jaccard_similarity == pytest.approx(4 / 6)
    assert comparison.symbols_unique_to_reference == ("S005",)
    assert comparison.symbols_unique_to_challenger == ("S000",)
    assert comparison.mean_absolute_ordinal_displacement == pytest.approx(2.0)
    assert comparison.maximum_absolute_ordinal_displacement == 3
    assert comparison.exact_selected_set_equality is False
    assert comparison.exact_ordered_list_equality is False

    same_set = evaluate_panel_policy_selection_diagnostics(_dataset(
        (day,),
        row_counts={day: 5},
        overrides={day: {
            "adx_14": [1, 2, 3, 4, 5],
            "rsi_14": [5, 4, 3, 2, 1],
        }},
    )).comparison_for(day, 5)
    assert same_set.exact_selected_set_equality is True
    assert same_set.exact_ordered_list_equality is False


def test_phase_55_boundaries_dimensions_and_equal_date_aggregation() -> None:
    result = evaluate_panel_policy_selection_diagnostics(_dataset(
        BOUNDARIES,
        row_counts={value: 6 for value in BOUNDARIES},
    ))
    assert len(result.daily_selections) == len(BOUNDARIES) * 2 * 3
    assert len(result.daily_policy_comparisons) == len(BOUNDARIES) * 3
    assert len(result.turnover_summaries) == 2 * 3 * 5
    assert len(result.overlap_summaries) == 3 * 5
    block_counts = {
        item.scope_name: item.dates_evaluated
        for item in result.turnover_summaries
        if item.policy_name == "ADX_ONLY" and item.requested_selection_count == 5
    }
    assert block_counts == {
        "whole_period": 8,
        "early_2018_2020": 2,
        "middle_2021_2022": 2,
        "middle_2023_2024": 2,
        "recent_2025_2026": 2,
    }
    whole = next(
        item for item in result.turnover_summaries
        if item.policy_name == "ADX_ONLY"
        and item.requested_selection_count == 5
        and item.scope_name == "whole_period"
    )
    assert whole.mean_actual_selection_size == 5.0
    assert whole.dates_evaluated == 8


def test_daily_equal_weight_and_population_standard_deviation() -> None:
    dates = ("2018-08-07", "2018-08-08", "2018-08-09")
    result = evaluate_panel_policy_selection_diagnostics(_dataset(
        dates,
        row_counts={value: 6 for value in dates},
        overrides={
            dates[0]: {"adx_14": [1, 2, 3, 4, 5, 6]},
            dates[1]: {"adx_14": [7, 2, 3, 4, 5, 6]},
            dates[2]: {"adx_14": [7, 8, 3, 4, 5, 6]},
        },
    ))
    summary = next(
        item for item in result.turnover_summaries
        if item.policy_name == "ADX_ONLY"
        and item.requested_selection_count == 5
        and item.scope_name == "whole_period"
    )
    assert summary.defined_one_way_turnover_date_count == 2
    assert summary.mean_one_way_turnover == pytest.approx(0.2)
    assert summary.median_one_way_turnover == pytest.approx(0.2)
    assert summary.population_std_one_way_turnover == pytest.approx(0.0)
    assert summary.total_entry_count == 2


def test_malformed_provenance_duplicates_and_forbidden_columns_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    day = BOUNDARIES[0]
    malformed = _dataset((day,))
    object.__setattr__(malformed, "feature_content_identity", "")
    with pytest.raises(ValueError, match="feature_content_identity"):
        evaluate_panel_policy_selection_diagnostics(malformed)

    duplicate = _dataset((day,))
    duplicate._frame.loc[1, "symbol"] = duplicate._frame.loc[0, "symbol"]
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_panel_policy_selection_diagnostics(duplicate)

    source = _dataset((day,))
    original = PointInTimeResearchDataset.predictor_frame

    def leaking(self):
        frame = original(self)
        frame["stock_forward_return_5_pct"] = 99.0
        return frame

    monkeypatch.setattr(PointInTimeResearchDataset, "predictor_frame", leaking)
    with pytest.raises(ValueError, match="future-looking forbidden"):
        evaluate_panel_policy_selection_diagnostics(source)


def test_identity_is_presentation_invariant_sensitive_and_result_is_immutable() -> None:
    dates = ("2018-08-07", "2018-08-08")
    base = evaluate_panel_policy_selection_diagnostics(_dataset(dates))
    reversed_result = evaluate_panel_policy_selection_diagnostics(_dataset(dates, reverse_rows=True))
    assert base.identity == reversed_result.identity
    changed = evaluate_panel_policy_selection_diagnostics(_dataset(
        dates,
        overrides={dates[0]: {"rsi_14": [1000.0, *range(1, 25)]}},
    ))
    assert changed.source_bounded_content_identity != base.source_bounded_content_identity
    assert changed.identity != base.identity
    with pytest.raises(FrozenInstanceError):
        base.specification_fingerprint = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        base.limitations_metadata["outcome_free_descriptive_selection_diagnostics_only"] = False  # type: ignore[index]


def test_empty_common_population_keeps_dates_and_undefined_numeric_aggregates() -> None:
    day = BOUNDARIES[0]
    result = evaluate_panel_policy_selection_diagnostics(_dataset(
        (day,),
        row_counts={day: 3},
        overrides={day: {"rsi_14": [math.nan, math.inf, pd.NA]}},
    ))
    daily = result.selection_for(day, "ADX_ONLY", 5)
    comparison = result.comparison_for(day, 5)
    assert daily.eligible_cross_section_count == 0
    assert daily.ordered_selected_symbols == ()
    assert daily.boundary_score is None
    assert comparison.overlap_coefficient is None
    assert comparison.jaccard_similarity is None
    summary = next(
        item for item in result.overlap_summaries
        if item.requested_selection_count == 5 and item.scope_name == "whole_period"
    )
    assert summary.mean_overlap_coefficient is None
    assert summary.mean_jaccard_similarity is None


def test_fresh_process_import_is_outcome_free_and_side_effect_isolated() -> None:
    root = Path(__file__).resolve().parents[1]
    script = """
import json, sys
before = set(sys.modules)
from quantlab.evaluation import PanelSelectionPolicy
loaded = set(sys.modules) - before
forbidden = sorted(name for name in loaded if name in {
    'pandas',
    'quantlab.panels.research_dataset_contracts',
    'quantlab.panels.outcome_panel',
    'quantlab.outcomes.forward_returns',
    'backtesting.engine',
    'strategy.scanner',
})
print(json.dumps({'forbidden': forbidden, 'type': PanelSelectionPolicy.__name__}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout.strip())
    assert payload == {"forbidden": [], "type": "PanelSelectionPolicy"}
