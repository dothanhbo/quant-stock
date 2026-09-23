from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, replace
from datetime import date, timedelta
import os
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType

import pytest

from quantlab.evaluation import (
    FROZEN_Q70_VOLUME_RSI_TEMPORAL_STABILITY_V1,
    CandidateFactorTemporalStabilityResult,
    FactorTemporalStabilitySpec,
    TemporalBlock,
    evaluate_candidate_factor_temporal_stability,
)
from quantlab.evaluation.temporal_stability_contracts import RAW_EXCESS_NONINDEPENDENCE


STOCK = "stock_forward_return_pct"
EXCESS = "excess_forward_return_pct_points"


@dataclass(frozen=True)
class _Daily:
    signal_date: str
    factor: str
    horizon_sessions: int
    outcome_field: str
    available_labeled_candidates: int
    rank_ic: float | None
    bucket_undefined_reason: str | None
    high_minus_low_mean_spread: float | None
    high_minus_low_median_spread: float | None
    low_bucket_count: int
    high_bucket_count: int
    identity: str


@dataclass(frozen=True)
class _Evaluation:
    daily_evaluations: tuple[_Daily, ...]
    source_candidate_batch_identity: str = "candidate-batch"
    source_outcome_set_identity: str = "outcome-set"
    evaluation_spec_fingerprint: str = "phase-4.5-spec"
    result_identity: str = "phase-4.5-result"


def _days(start: str, count: int) -> tuple[str, ...]:
    first = date.fromisoformat(start)
    return tuple((first + timedelta(days=offset)).isoformat() for offset in range(count))


def _spec(
    *,
    blocks: tuple[TemporalBlock, ...] | None = None,
    factors: tuple[str, ...] = ("volume_ratio",),
    horizons: tuple[int, ...] = (5,),
    outcomes: tuple[str, ...] = (STOCK,),
    minimum: int = 2,
    name: str = "test-temporal-stability",
) -> FactorTemporalStabilitySpec:
    selected = blocks or (
        TemporalBlock("b1", "2020-01-01", "2020-01-02"),
        TemporalBlock("b2", "2020-01-03", "2020-01-04"),
        TemporalBlock("b3", "2020-01-05", "2020-01-06"),
        TemporalBlock("b4", "2020-01-07", "2020-01-08"),
    )
    return FactorTemporalStabilitySpec(
        name=name,
        version="1",
        factors=factors,
        horizons=horizons,
        outcome_fields=outcomes,
        blocks=selected,
        overall_start_date=selected[0].start_date,
        overall_end_date=selected[-1].end_date,
        minimum_defined_dates_per_eligible_block=minimum,
    )


def _grid(
    spec: FactorTemporalStabilitySpec,
    dates: tuple[str, ...],
    metric=None,
) -> _Evaluation:
    rows = []
    for date_text in dates:
        for factor in spec.factors:
            for horizon in spec.horizons:
                for outcome in spec.outcome_fields:
                    values = (
                        metric(date_text, factor, horizon, outcome)
                        if metric is not None else {}
                    )
                    rank_ic = values.get("rank_ic", .1)
                    spread = values.get("spread", 1.0)
                    median_spread = values.get("median_spread", spread)
                    bucket_reason = values.get(
                        "bucket_reason", None if spread is not None else "undefined",
                    )
                    rows.append(_Daily(
                        signal_date=date_text,
                        factor=factor,
                        horizon_sessions=horizon,
                        outcome_field=outcome,
                        available_labeled_candidates=values.get("available", 5),
                        rank_ic=rank_ic,
                        bucket_undefined_reason=bucket_reason,
                        high_minus_low_mean_spread=spread,
                        high_minus_low_median_spread=median_spread,
                        low_bucket_count=values.get("low", 2),
                        high_bucket_count=values.get("high", 2),
                        identity=values.get(
                            "identity", f"{date_text}:{factor}:{horizon}:{outcome}",
                        ),
                    ))
    return _Evaluation(tuple(rows))


def test_builtin_exact_blocks_inclusive_endpoints_and_canonical_order() -> None:
    spec = FROZEN_Q70_VOLUME_RSI_TEMPORAL_STABILITY_V1
    assert spec.factors == ("volume_ratio", "rsi14")
    assert spec.horizons == (5, 10, 20)
    assert spec.outcome_fields == (STOCK, EXCESS)
    assert tuple((item.name, item.start_date, item.end_date) for item in spec.blocks) == (
        ("early_2018_2020", "2018-08-07", "2020-12-31"),
        ("middle_2021_2022", "2021-01-01", "2022-12-31"),
        ("middle_2023_2024", "2023-01-01", "2024-12-31"),
        ("recent_2025_2026", "2025-01-01", "2026-09-17"),
    )
    endpoints = tuple(value for block in spec.blocks for value in (block.start_date, block.end_date))
    result = evaluate_candidate_factor_temporal_stability(_grid(spec, endpoints), spec)
    assert tuple((item.factor, item.horizon_sessions, item.outcome_field) for item in result.summaries) == tuple(
        (factor, horizon, outcome)
        for factor in spec.factors
        for horizon in spec.horizons
        for outcome in spec.outcome_fields
    )
    assert tuple(item.total_signal_date_rows for item in result.summaries[0].block_results) == (2, 2, 2, 2)


def test_spec_rejects_overlaps_gaps_inverted_ranges_and_duplicate_names() -> None:
    with pytest.raises(ValueError, match="start_date must not be after"):
        TemporalBlock("bad", "2020-01-02", "2020-01-01")
    overlap = (
        TemporalBlock("a", "2020-01-01", "2020-01-03"),
        TemporalBlock("b", "2020-01-03", "2020-01-04"),
    )
    with pytest.raises(ValueError, match="must not overlap"):
        _spec(blocks=overlap)
    gap = (
        TemporalBlock("a", "2020-01-01", "2020-01-02"),
        TemporalBlock("b", "2020-01-04", "2020-01-05"),
    )
    with pytest.raises(ValueError, match="calendar gaps"):
        _spec(blocks=gap)
    duplicate = (
        TemporalBlock("same", "2020-01-01", "2020-01-02"),
        TemporalBlock("same", "2020-01-03", "2020-01-04"),
    )
    with pytest.raises(ValueError, match="names must be unique"):
        _spec(blocks=duplicate)


def test_equal_date_aggregation_undefined_exclusion_std_median_and_date_counts() -> None:
    spec = _spec()
    dates = tuple(value for block in spec.blocks for value in (block.start_date, block.end_date))
    values = {
        dates[0]: {"rank_ic": .2, "spread": 1.0, "median_spread": 2.0, "low": 1, "high": 3},
        dates[1]: {"rank_ic": .4, "spread": 3.0, "median_spread": 4.0, "low": 3, "high": 5},
        dates[2]: {"rank_ic": None, "spread": None, "bucket_reason": "undefined", "available": 0},
        dates[3]: {"rank_ic": -.2, "spread": -2.0, "median_spread": -1.0},
    }
    result = evaluate_candidate_factor_temporal_stability(
        _grid(spec, dates, lambda day, *_: values.get(day, {"rank_ic": 0.0, "spread": 0.0})),
        spec,
    )
    first, second = result.summaries[0].block_results[:2]
    assert first.mean_daily_rank_ic == pytest.approx(.3)
    assert first.median_daily_rank_ic == pytest.approx(.3)
    assert first.population_std_daily_rank_ic == pytest.approx(.1)
    assert first.mean_daily_high_minus_low_mean_spread == pytest.approx(2.0)
    assert first.median_daily_high_minus_low_median_spread == pytest.approx(3.0)
    assert (first.average_low_bucket_size, first.average_high_bucket_size) == (2.0, 4.0)
    assert (first.positive_ic_date_count, first.zero_ic_date_count, first.negative_ic_date_count) == (2, 0, 0)
    assert second.total_signal_date_rows == 2
    assert second.dates_with_available_labels == 1
    assert second.ic_defined_date_count == second.bucket_defined_date_count == 1
    assert second.mean_daily_rank_ic == -.2


def test_summary_extrema_ranges_largest_absolute_and_concentration() -> None:
    spec = _spec(minimum=1)
    dates = tuple(block.start_date for block in spec.blocks)
    ic = {dates[0]: .1, dates[1]: -.4, dates[2]: 0.0, dates[3]: .2}
    spread = {dates[0]: 1.0, dates[1]: -4.0, dates[2]: 0.0, dates[3]: 2.0}
    summary = evaluate_candidate_factor_temporal_stability(
        _grid(spec, dates, lambda day, *_: {"rank_ic": ic[day], "spread": spread[day]}),
        spec,
    ).summaries[0]
    assert (summary.minimum_block_mean_ic, summary.maximum_block_mean_ic) == (-.4, .2)
    assert summary.range_block_mean_ic == pytest.approx(.6)
    assert (summary.minimum_block_mean_spread, summary.maximum_block_mean_spread, summary.range_block_mean_spread) == (-4.0, 2.0, 6.0)
    assert summary.largest_absolute_mean_ic_block_name == "b2"
    assert summary.largest_absolute_mean_spread_block_name == "b2"
    assert summary.largest_absolute_mean_ic_block_identity == summary.block_results[1].identity
    assert summary.absolute_mean_ic_concentration == pytest.approx(.4 / .7)
    assert summary.absolute_mean_spread_concentration == pytest.approx(4 / 7)
    assert (summary.positive_mean_ic_block_count, summary.zero_mean_ic_block_count, summary.negative_mean_ic_block_count) == (2, 1, 1)


def test_concentration_all_zero_and_all_undefined_remain_none() -> None:
    spec = _spec(minimum=1)
    dates = tuple(block.start_date for block in spec.blocks)
    zeros = evaluate_candidate_factor_temporal_stability(
        _grid(spec, dates, lambda *_: {"rank_ic": 0.0, "spread": 0.0}), spec,
    ).summaries[0]
    assert zeros.absolute_mean_ic_concentration is None
    assert zeros.absolute_mean_spread_concentration is None
    undefined = evaluate_candidate_factor_temporal_stability(
        _grid(spec, dates, lambda *_: {
            "rank_ic": None, "spread": None, "median_spread": None,
            "bucket_reason": "undefined",
        }),
        spec,
    ).summaries[0]
    assert undefined.minimum_block_mean_ic is None
    assert undefined.absolute_mean_ic_concentration is None
    assert undefined.largest_absolute_mean_ic_block_identity is None


def _twenty_day_spec() -> FactorTemporalStabilitySpec:
    blocks = []
    current = date(2020, 1, 1)
    for index in range(4):
        end = current + timedelta(days=19)
        blocks.append(TemporalBlock(f"b{index + 1}", current.isoformat(), end.isoformat()))
        current = end + timedelta(days=1)
    return _spec(blocks=tuple(blocks), minimum=20)


def test_twenty_date_gate_and_descriptive_flags_exact_boundaries() -> None:
    spec = _twenty_day_spec()
    dates = tuple(day for block in spec.blocks for day in _days(block.start_date, 20))
    result = evaluate_candidate_factor_temporal_stability(
        _grid(spec, dates, lambda day, *_: {"rank_ic": .1, "spread": 1.0}), spec,
    )
    summary = result.summaries[0]
    assert summary.blocks_with_at_least_minimum_ic_dates == 4
    assert summary.blocks_with_at_least_minimum_spread_dates == 4
    assert summary.coverage_sufficient_for_stability_review is True
    assert summary.directionally_consistent_ic is True
    assert summary.directionally_consistent_spread is True
    assert summary.descriptive_temporal_support is True

    last_day_first_three = {block.end_date for block in spec.blocks[:3]}
    below = evaluate_candidate_factor_temporal_stability(
        _grid(spec, dates, lambda day, *_: (
            {"rank_ic": None, "spread": None, "bucket_reason": "undefined"}
            if day in last_day_first_three else {"rank_ic": .1, "spread": 1.0}
        )),
        spec,
    ).summaries[0]
    assert below.blocks_with_at_least_minimum_ic_dates == 1
    assert below.coverage_sufficient_for_stability_review is False
    assert below.descriptive_temporal_support is False


def test_one_block_concentration_fails_direction_while_three_positive_blocks_pass() -> None:
    spec = _twenty_day_spec()
    dates = tuple(day for block in spec.blocks for day in _days(block.start_date, 20))
    block_by_date = {
        day: index
        for index, block in enumerate(spec.blocks)
        for day in _days(block.start_date, 20)
    }
    concentrated = evaluate_candidate_factor_temporal_stability(
        _grid(spec, dates, lambda day, *_: {
            "rank_ic": 1.0 if block_by_date[day] == 0 else -.1,
            "spread": 10.0 if block_by_date[day] == 0 else -1.0,
        }),
        spec,
    ).summaries[0]
    assert concentrated.maximum_block_mean_ic > 0
    assert concentrated.coverage_sufficient_for_stability_review is True
    assert concentrated.directionally_consistent_ic is False
    assert concentrated.directionally_consistent_spread is False
    assert concentrated.descriptive_temporal_support is False

    three_positive = evaluate_candidate_factor_temporal_stability(
        _grid(spec, dates, lambda day, *_: {
            "rank_ic": .1 if block_by_date[day] < 3 else -.1,
            "spread": 1.0 if block_by_date[day] < 3 else -1.0,
        }),
        spec,
    ).summaries[0]
    assert three_positive.directionally_consistent_ic is True
    assert three_positive.directionally_consistent_spread is True
    assert three_positive.descriptive_temporal_support is True


def test_raw_excess_nonindependence_and_machine_precision_spreads_are_retained() -> None:
    spec = _spec(outcomes=(STOCK, EXCESS), minimum=1)
    dates = tuple(block.start_date for block in spec.blocks)
    epsilon = 2.220446049250313e-16
    result = evaluate_candidate_factor_temporal_stability(
        _grid(spec, dates, lambda _day, _factor, _horizon, outcome: {
            "rank_ic": .25,
            "spread": 1.0 if outcome == STOCK else 1.0 + epsilon,
        }),
        spec,
    )
    raw = result.summary_for("volume_ratio", 5, STOCK)
    excess = result.summary_for("volume_ratio", 5, EXCESS)
    assert tuple(item.mean_daily_rank_ic for item in raw.block_results) == tuple(
        item.mean_daily_rank_ic for item in excess.block_results
    )
    assert raw.block_results[0].mean_daily_high_minus_low_mean_spread != excess.block_results[0].mean_daily_high_minus_low_mean_spread
    assert result.provenance_metadata["raw_vs_excess_rank_ic_relationship"] == RAW_EXCESS_NONINDEPENDENCE


def test_order_invariance_and_identity_sensitivity() -> None:
    spec = _spec(minimum=1)
    dates = tuple(block.start_date for block in spec.blocks)
    source = _grid(spec, dates)
    first = evaluate_candidate_factor_temporal_stability(source, spec)
    reversed_input = evaluate_candidate_factor_temporal_stability(
        replace(source, daily_evaluations=tuple(reversed(source.daily_evaluations))), spec,
    )
    assert first.result_identity == reversed_input.result_identity

    changed_daily_identity = replace(source.daily_evaluations[0], identity="changed-daily")
    changed_identity = evaluate_candidate_factor_temporal_stability(
        replace(source, daily_evaluations=(changed_daily_identity,) + source.daily_evaluations[1:]), spec,
    )
    assert changed_identity.result_identity != first.result_identity
    changed_metric = replace(source.daily_evaluations[0], rank_ic=.9)
    assert evaluate_candidate_factor_temporal_stability(
        replace(source, daily_evaluations=(changed_metric,) + source.daily_evaluations[1:]), spec,
    ).result_identity != first.result_identity
    assert evaluate_candidate_factor_temporal_stability(
        replace(source, result_identity="changed-source-evaluation"), spec,
    ).result_identity != first.result_identity

    shifted_blocks = (
        TemporalBlock("b1", "2020-01-01", "2020-01-01"),
        TemporalBlock("b2", "2020-01-02", "2020-01-04"),
        *spec.blocks[2:],
    )
    shifted_spec = _spec(blocks=shifted_blocks, minimum=1, name="shifted")
    assert shifted_spec.fingerprint != spec.fingerprint
    assert evaluate_candidate_factor_temporal_stability(source, shifted_spec).result_identity != first.result_identity

    rsi_spec = _spec(factors=("rsi14",), minimum=1, name="rsi-factor")
    rsi_source = replace(source, daily_evaluations=tuple(
        replace(item, factor="rsi14", identity=item.identity + ":rsi")
        for item in source.daily_evaluations
    ))
    assert evaluate_candidate_factor_temporal_stability(rsi_source, rsi_spec).result_identity != first.result_identity

    horizon_spec = _spec(horizons=(10,), minimum=1, name="ten-session")
    horizon_source = replace(source, daily_evaluations=tuple(
        replace(item, horizon_sessions=10, identity=item.identity + ":10")
        for item in source.daily_evaluations
    ))
    assert evaluate_candidate_factor_temporal_stability(horizon_source, horizon_spec).result_identity != first.result_identity

    excess_spec = _spec(outcomes=(EXCESS,), minimum=1, name="excess-outcome")
    excess_source = replace(source, daily_evaluations=tuple(
        replace(item, outcome_field=EXCESS, identity=item.identity + ":excess")
        for item in source.daily_evaluations
    ))
    assert evaluate_candidate_factor_temporal_stability(excess_source, excess_spec).result_identity != first.result_identity


def test_duplicate_missing_extra_keys_and_internal_dates_fail_clearly() -> None:
    spec = _spec(factors=("volume_ratio", "rsi14"), minimum=1)
    dates = tuple(block.start_date for block in spec.blocks)
    source = _grid(spec, dates)
    duplicate_identity = replace(source.daily_evaluations[1], identity=source.daily_evaluations[0].identity)
    with pytest.raises(ValueError, match="duplicate or empty daily identity"):
        evaluate_candidate_factor_temporal_stability(
            replace(source, daily_evaluations=(source.daily_evaluations[0], duplicate_identity) + source.daily_evaluations[2:]), spec,
        )
    duplicate_key = replace(source.daily_evaluations[0], identity="different-identity")
    with pytest.raises(ValueError, match="duplicate factor/horizon/outcome/date key"):
        evaluate_candidate_factor_temporal_stability(
            replace(source, daily_evaluations=source.daily_evaluations + (duplicate_key,)), spec,
        )
    with pytest.raises(ValueError, match="missing requested"):
        evaluate_candidate_factor_temporal_stability(
            replace(source, daily_evaluations=source.daily_evaluations[1:]), spec,
        )
    extra = replace(source.daily_evaluations[0], horizon_sessions=99, identity="extra-horizon")
    with pytest.raises(ValueError, match="extra requested-factor"):
        evaluate_candidate_factor_temporal_stability(
            replace(source, daily_evaluations=source.daily_evaluations + (extra,)), spec,
        )
    outside = replace(source.daily_evaluations[0], signal_date="2019-12-31", identity="outside")
    allowed = evaluate_candidate_factor_temporal_stability(
        replace(source, daily_evaluations=source.daily_evaluations + (outside,)), spec,
    )
    assert allowed.result_identity == evaluate_candidate_factor_temporal_stability(source, spec).result_identity


def test_outputs_are_frozen_and_provenance_mapping_is_read_only() -> None:
    spec = _spec(minimum=1)
    dates = tuple(block.start_date for block in spec.blocks)
    result = evaluate_candidate_factor_temporal_stability(_grid(spec, dates), spec)
    assert isinstance(result, CandidateFactorTemporalStabilityResult)
    with pytest.raises(FrozenInstanceError):
        result.source_evaluation_identity = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        result.provenance_metadata["factor_search_performed"] = True  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        result.summaries[0].block_results[0].block_name = "changed"  # type: ignore[misc]


def test_fresh_process_import_is_dependency_isolated(tmp_path: Path) -> None:
    missing = tmp_path / "must-not-exist.db"
    script = (
        "import sys; import quantlab.evaluation.temporal_stability; "
        "forbidden={"
        "'sqlite3','quantlab.catalog.market_data_snapshot','quantlab.features.registry',"
        "'quantlab.features.builtins','quantlab.adapters.frozen_q70_candidate_records',"
        "'quantlab.adapters.frozen_q70_candidate_decisions','quantlab.alpha.frozen_q70',"
        "'quantlab.outcomes.forward_returns','backtesting.trade',"
        "'backtesting.portfolio_simulator','backtesting.walk_forward',"
        "'backtesting.walk_forward_optimizer','core.universe','vnstock'}; "
        "loaded=forbidden.intersection(sys.modules); "
        "assert not loaded, f'forbidden temporal imports: {sorted(loaded)}'"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, MARKET_DATABASE_PATH=str(missing), PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert not missing.exists()
