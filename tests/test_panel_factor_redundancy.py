from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

import pandas as pd
import pytest

from quantlab.evaluation import (
    NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1,
    evaluate_panel_factor_redundancy,
)
from quantlab.evaluation.panel_factor_contracts import BUILTIN_FACTOR_FIELDS
import quantlab.evaluation.panel_factor_redundancy as redundancy
from quantlab.panels.research_dataset_contracts import (
    POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1,
    PointInTimeResearchDataset,
)


BLOCK_DATES = (
    "2018-08-07",
    "2021-01-01",
    "2023-01-01",
    "2025-01-01",
)
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
    feature_count = len(spec.feature_columns)
    for signal_date in dates:
        count = counts[signal_date]
        values_by_factor = supplied.get(signal_date, {})
        for ordinal in range(count):
            symbol = f"S{ordinal:03d}"
            row: dict[str, Any] = {
                "session_date": signal_date,
                "symbol": symbol,
                "benchmark_symbol": "VNINDEX",
                "membership_set_hash": f"membership-{signal_date}",
                "membership_count": count,
                "member_ordinal": ordinal,
                "market_row_available": True,
                "availability": "AVAILABLE",
                "source_market_time": signal_date,
                "source_row_ordinal": ordinal,
                "requested_feature_count": feature_count,
                "available_feature_count": feature_count,
                "complete_feature_row": True,
            }
            for factor in spec.feature_columns:
                factor_values = values_by_factor.get(factor)
                row[factor] = (
                    factor_values[ordinal] if factor_values is not None else float(ordinal + 1)
                )
                row[f"{factor}__availability"] = "AVAILABLE"
            rows.append(row)
    if reverse_rows:
        rows.reverse()
    frame = pd.DataFrame(rows, columns=spec.predictor_columns)
    dataset = object.__new__(PointInTimeResearchDataset)
    values = {
        "spec": spec,
        "observation_index_identity": f"observation-index-{identity_suffix}",
        "observation_content_identity": f"observation-content-{identity_suffix}",
        "feature_panel_identity": f"feature-panel-{identity_suffix}",
        "feature_content_identity": f"feature-content-{identity_suffix}",
        "content_identity": f"dataset-content-{identity_suffix}",
        "identity": f"dataset-{identity_suffix}",
        "session_audit": tuple(
            SimpleNamespace(session_date=value) for value in sorted(dates)
        ),
        "_frame": frame,
    }
    for name, value in values.items():
        object.__setattr__(dataset, name, value)
    return dataset


def _pair(result: Any, date_value: str, first: str, second: str) -> Any:
    return result.daily_for(date_value, first, second)


def test_hand_calculated_positive_negative_and_tied_rank_spearman() -> None:
    date_value = BLOCK_DATES[0]
    increasing = list(range(20))
    decreasing = list(reversed(increasing))
    tied = [0, 0, *range(2, 20)]
    result = evaluate_panel_factor_redundancy(_dataset(
        (date_value,),
        overrides={date_value: {
            "atr_percent_14": increasing,
            "rsi_14": increasing,
            "adx_14": decreasing,
            "volume_ratio_20": tied,
            "stock_return_20d_pct": increasing,
        }},
    ))

    assert _pair(result, date_value, "atr_percent_14", "rsi_14").spearman_correlation == 1.0
    assert _pair(result, date_value, "atr_percent_14", "adx_14").spearman_correlation == -1.0
    tied_record = _pair(
        result, date_value, "volume_ratio_20", "stock_return_20d_pct",
    )
    expected_average_rank_correlation = (1329.0 / 1330.0) ** 0.5
    assert tied_record.spearman_correlation == pytest.approx(expected_average_rank_correlation)
    assert tied_record.absolute_spearman_correlation == pytest.approx(
        expected_average_rank_correlation,
    )


def test_pairwise_finite_exclusion_and_twenty_observation_boundary() -> None:
    date_value = BLOCK_DATES[0]
    first = list(range(21))
    second: list[Any] = list(range(21))
    second[-1] = float("inf")
    result = evaluate_panel_factor_redundancy(_dataset(
        (date_value,),
        row_counts={date_value: 21},
        overrides={date_value: {"atr_percent_14": first, "rsi_14": second}},
    ))
    record = _pair(result, date_value, "atr_percent_14", "rsi_14")
    assert record.pairwise_finite_count == 20
    assert record.spearman_correlation == 1.0

    second[-2] = pd.NA
    below_boundary = evaluate_panel_factor_redundancy(_dataset(
        (date_value,),
        row_counts={date_value: 21},
        overrides={date_value: {"atr_percent_14": first, "rsi_14": second}},
    ))
    record = _pair(below_boundary, date_value, "atr_percent_14", "rsi_14")
    assert record.pairwise_finite_count == 19
    assert record.spearman_correlation is None
    assert record.absolute_spearman_correlation is None
    assert record.undefined_reason == "fewer_than_minimum_pairwise_finite_observations"


@pytest.mark.parametrize(
    ("first", "second", "reason"),
    (
        ([1.0] * 20, list(range(20)), "constant_first_factor"),
        (list(range(20)), [1.0] * 20, "constant_second_factor"),
        ([1.0] * 20, [2.0] * 20, "constant_both_factors"),
    ),
)
def test_constant_inputs_are_explicitly_undefined(
    first: Sequence[float], second: Sequence[float], reason: str,
) -> None:
    date_value = BLOCK_DATES[0]
    result = evaluate_panel_factor_redundancy(_dataset(
        (date_value,),
        overrides={date_value: {"atr_percent_14": first, "rsi_14": second}},
    ))
    record = _pair(result, date_value, "atr_percent_14", "rsi_14")
    assert record.spearman_correlation is None
    assert record.undefined_reason == reason
    assert result.summary_for(
        "atr_percent_14", "rsi_14",
    ).largest_absolute_block_mean_concentration is None


def test_zero_denominator_has_its_own_undefined_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(redundancy, "_pearson", lambda _first, _second: None)
    date_value = BLOCK_DATES[0]
    result = evaluate_panel_factor_redundancy(_dataset((date_value,)))
    record = _pair(result, date_value, "atr_percent_14", "rsi_14")
    assert record.spearman_correlation is None
    assert record.undefined_reason == "correlation_denominator_zero"


def test_builtin_pair_block_dimensions_and_canonical_order() -> None:
    result = evaluate_panel_factor_redundancy(_dataset(BLOCK_DATES))
    expected_pairs = tuple(
        (BUILTIN_FACTOR_FIELDS[left], BUILTIN_FACTOR_FIELDS[right])
        for left in range(len(BUILTIN_FACTOR_FIELDS))
        for right in range(left + 1, len(BUILTIN_FACTOR_FIELDS))
    )
    assert NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1.pairs == expected_pairs
    assert len(expected_pairs) == 28
    assert len(result.summaries) == 28
    assert len(result.block_correlations) == 112
    assert tuple(
        (value.first_factor, value.second_factor) for value in result.summaries
    ) == expected_pairs
    assert tuple(value.block_name for value in result.block_correlations[:4]) == (
        "early_2018_2020", "middle_2021_2022", "middle_2023_2024", "recent_2025_2026",
    )


def test_daily_values_receive_equal_weight_and_absolute_mean_is_distinct() -> None:
    dates = ("2018-08-07", "2018-08-08")
    result = evaluate_panel_factor_redundancy(_dataset(
        dates,
        row_counts={dates[0]: 20, dates[1]: 40},
        overrides={
            dates[0]: {
                "atr_percent_14": list(range(20)),
                "rsi_14": list(range(20)),
            },
            dates[1]: {
                "atr_percent_14": list(range(40)),
                "rsi_14": list(reversed(range(40))),
            },
        },
    ))
    summary = result.summary_for("atr_percent_14", "rsi_14")
    assert summary.mean_daily_correlation == 0.0
    assert summary.mean_daily_absolute_correlation == 1.0
    assert abs(summary.mean_daily_correlation) != summary.mean_daily_absolute_correlation
    assert summary.average_pairwise_finite_count == 30.0


def test_temporal_boundaries_sign_flips_and_concentration() -> None:
    overrides: dict[str, dict[str, Sequence[Any]]] = {}
    for index, date_value in enumerate(BLOCK_BOUNDARY_DATES):
        values = list(range(20))
        overrides[date_value] = {
            "atr_percent_14": values,
            "rsi_14": values if (index // 2) % 2 == 0 else list(reversed(values)),
        }
    result = evaluate_panel_factor_redundancy(_dataset(
        BLOCK_BOUNDARY_DATES, overrides=overrides,
    ))
    summary = result.summary_for("atr_percent_14", "rsi_14")
    blocks = tuple(
        result.block_for("atr_percent_14", "rsi_14", block.name)
        for block in NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1.blocks
    )
    assert tuple(value.total_signal_date_count for value in blocks) == (2, 2, 2, 2)
    assert tuple(value.mean_daily_correlation for value in blocks) == (1.0, -1.0, 1.0, -1.0)
    assert tuple(value.median_daily_correlation for value in blocks) == (1.0, -1.0, 1.0, -1.0)
    assert tuple(value.population_std_daily_correlation for value in blocks) == (
        0.0, 0.0, 0.0, 0.0,
    )
    assert tuple(value.correlation_coverage_pct for value in blocks) == (
        100.0, 100.0, 100.0, 100.0,
    )
    assert tuple(value.positive_correlation_date_count for value in blocks) == (2, 0, 2, 0)
    assert tuple(value.negative_correlation_date_count for value in blocks) == (0, 2, 0, 2)
    assert summary.chronological_sign_flip_count == 3
    assert summary.largest_absolute_block_mean_concentration == 0.25
    assert summary.minimum_block_mean_correlation == -1.0
    assert summary.maximum_block_mean_correlation == 1.0
    assert summary.range_block_mean_correlation == 2.0
    assert summary.all_blocks_positive is False
    assert summary.all_blocks_negative is False
    assert redundancy._sign_flips((1.0, 0.0, -1.0, None, 1.0)) == 3
    assert redundancy._concentration((0.0, 0.0)) is None


def test_predictor_only_boundary_and_corrupted_sources_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = _dataset((BLOCK_DATES[0],))
    monkeypatch.setattr(
        PointInTimeResearchDataset,
        "evaluation_frame",
        lambda _self: pytest.fail("future-looking evaluation_frame was accessed"),
    )
    evaluate_panel_factor_redundancy(dataset)

    original = PointInTimeResearchDataset.predictor_frame
    forbidden = dataset.spec.forbidden_predictor_columns[0]
    monkeypatch.setattr(
        PointInTimeResearchDataset,
        "predictor_frame",
        lambda self: original(self).assign(**{forbidden: 1.0}),
    )
    with pytest.raises(ValueError, match="future-looking forbidden"):
        evaluate_panel_factor_redundancy(dataset)
    monkeypatch.setattr(PointInTimeResearchDataset, "predictor_frame", original)

    duplicate = _dataset((BLOCK_DATES[0],))
    object.__setattr__(
        duplicate, "_frame", pd.concat([duplicate._frame, duplicate._frame.iloc[[0]]]),
    )
    with pytest.raises(ValueError, match="duplicate date/symbol keys"):
        evaluate_panel_factor_redundancy(duplicate)

    malformed = _dataset(
        (BLOCK_DATES[0],),
        overrides={BLOCK_DATES[0]: {"atr_percent_14": ["bad", *range(19)]}},
    )
    with pytest.raises(TypeError, match="numeric nullable contract"):
        evaluate_panel_factor_redundancy(malformed)

    missing_identity = _dataset((BLOCK_DATES[0],))
    object.__setattr__(missing_identity, "feature_content_identity", "")
    with pytest.raises(ValueError, match="dataset feature_content_identity"):
        evaluate_panel_factor_redundancy(missing_identity)

    out_of_range = _dataset(("2018-08-06",))
    with pytest.raises(ValueError, match="outside the redundancy interval"):
        evaluate_panel_factor_redundancy(out_of_range)


def test_identity_is_order_invariant_and_sensitive_to_content_and_provenance() -> None:
    date_value = BLOCK_DATES[0]
    baseline = evaluate_panel_factor_redundancy(_dataset((date_value,)))
    reordered = evaluate_panel_factor_redundancy(_dataset(
        (date_value,), reverse_rows=True,
    ))
    changed_value = evaluate_panel_factor_redundancy(_dataset(
        (date_value,),
        overrides={date_value: {"atr_percent_14": [*range(19), 100.0]}},
    ))
    changed_missingness = evaluate_panel_factor_redundancy(_dataset(
        (date_value,),
        overrides={date_value: {"atr_percent_14": [*range(19), pd.NA]}},
    ))
    changed_nonfinite = evaluate_panel_factor_redundancy(_dataset(
        (date_value,),
        overrides={date_value: {"atr_percent_14": [*range(19), float("nan")]}},
    ))
    changed_provenance = evaluate_panel_factor_redundancy(_dataset(
        (date_value,), identity_suffix="other",
    ))

    assert baseline.identity == reordered.identity
    assert baseline.source_bounded_content_identity == reordered.source_bounded_content_identity
    assert changed_value.identity != baseline.identity
    assert changed_missingness.identity != baseline.identity
    assert changed_nonfinite.identity != baseline.identity
    assert changed_nonfinite.identity != changed_missingness.identity
    assert changed_provenance.identity != baseline.identity


def test_immutable_result_ownership_and_fresh_import_isolation(tmp_path: Path) -> None:
    dataset = _dataset((BLOCK_DATES[0],))
    result = evaluate_panel_factor_redundancy(dataset)
    original_identity = result.identity
    dataset._frame.loc[:, "atr_percent_14"] = 999.0
    assert result.identity == original_identity
    with pytest.raises(FrozenInstanceError):
        result.source_dataset_identity = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        result.limitations_metadata["future_outcomes_used"] = True  # type: ignore[index]

    script = """
import json, socket, sqlite3, sys
sqlite3.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('database access'))
socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('network access'))
import quantlab.evaluation.panel_factor_redundancy
forbidden = (
    'core.database', 'quantlab.market_data', 'quantlab.outcomes',
    'quantlab.candidates', 'strategy', 'backtesting',
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
