from __future__ import annotations

"""Pure temporal aggregation of immutable Phase 4.5 daily evaluations."""

from collections import defaultdict
from datetime import date
import math
from types import MappingProxyType
from typing import Any, Protocol

from .temporal_stability_contracts import (
    DESCRIPTIVE_TEMPORAL_WARNING,
    RAW_EXCESS_NONINDEPENDENCE,
    CandidateFactorTemporalStabilityResult,
    FactorBlockStabilityResult,
    FactorHorizonTemporalStabilitySummary,
    FactorTemporalStabilitySpec,
    TemporalBlock,
)


class _DailyLike(Protocol):
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


class _EvaluationLike(Protocol):
    source_candidate_batch_identity: str
    source_outcome_set_identity: str
    evaluation_spec_fingerprint: str
    daily_evaluations: tuple[_DailyLike, ...]
    result_identity: str


def _finite_or_none(value: Any, *, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite or None")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite or None") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite or None")
    return number


def _mean(values: tuple[float, ...]) -> float | None:
    return None if not values else sum(values) / len(values)


def _median(values: tuple[float, ...]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2.0


def _population_std(values: tuple[float, ...]) -> float | None:
    if not values:
        return None
    center = sum(values) / len(values)
    return math.sqrt(sum((item - center) ** 2 for item in values) / len(values))


def _block_for_date(date_text: str, blocks: tuple[TemporalBlock, ...]) -> TemporalBlock | None:
    return next(
        (block for block in blocks if block.start_date <= date_text <= block.end_date),
        None,
    )


def _validated_input(
    evaluation_result: object,
    spec: FactorTemporalStabilitySpec,
) -> tuple[dict[tuple[str, str, int, str], _DailyLike], tuple[str, ...]]:
    if not isinstance(spec, FactorTemporalStabilitySpec):
        raise TypeError("spec must be FactorTemporalStabilitySpec")
    required = (
        "source_candidate_batch_identity", "source_outcome_set_identity",
        "evaluation_spec_fingerprint", "daily_evaluations", "result_identity",
    )
    missing = tuple(name for name in required if not hasattr(evaluation_result, name))
    if missing:
        raise TypeError("evaluation_result is missing required fields: " + ", ".join(missing))
    for name in required[:-1] + ("result_identity",):
        if name == "daily_evaluations":
            continue
        value = getattr(evaluation_result, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"evaluation_result.{name} must be a non-empty string")
    daily_values = getattr(evaluation_result, "daily_evaluations")
    if not isinstance(daily_values, tuple):
        raise TypeError("evaluation_result.daily_evaluations must be an immutable tuple")

    seen_identities: set[str] = set()
    seen_keys: set[tuple[str, str, int, str]] = set()
    selected: dict[tuple[str, str, int, str], _DailyLike] = {}
    signal_dates: set[str] = set()
    daily_required = (
        "signal_date", "factor", "horizon_sessions", "outcome_field",
        "available_labeled_candidates", "rank_ic", "bucket_undefined_reason",
        "high_minus_low_mean_spread", "high_minus_low_median_spread",
        "low_bucket_count", "high_bucket_count", "identity",
    )
    for item in daily_values:
        absent = tuple(name for name in daily_required if not hasattr(item, name))
        if absent:
            raise TypeError("daily evaluation is missing required fields: " + ", ".join(absent))
        identity = str(item.identity)
        if not identity or identity in seen_identities:
            raise ValueError(f"duplicate or empty daily identity: {identity}")
        seen_identities.add(identity)
        try:
            signal_date = date.fromisoformat(str(item.signal_date)).isoformat()
        except (TypeError, ValueError) as exc:
            raise ValueError(f"daily signal_date must be an ISO date: {item.signal_date}") from exc
        key = (signal_date, str(item.factor), item.horizon_sessions, str(item.outcome_field))
        if key in seen_keys:
            raise ValueError(f"duplicate factor/horizon/outcome/date key: {key}")
        seen_keys.add(key)
        inside = spec.overall_start_date <= signal_date <= spec.overall_end_date
        block = _block_for_date(signal_date, spec.blocks)
        if inside and block is None:
            raise ValueError(f"daily date is inside the overall boundary but outside every block: {signal_date}")
        if not inside:
            continue
        signal_dates.add(signal_date)
        if item.factor not in spec.factors:
            continue
        if item.horizon_sessions not in spec.horizons or item.outcome_field not in spec.outcome_fields:
            raise ValueError(f"extra requested-factor daily key outside specification: {key}")
        _finite_or_none(item.rank_ic, name=f"rank_ic:{key}")
        _finite_or_none(item.high_minus_low_mean_spread, name=f"mean_spread:{key}")
        _finite_or_none(item.high_minus_low_median_spread, name=f"median_spread:{key}")
        selected[key] = item

    expected = {
        (signal_date, factor, horizon, outcome_field)
        for signal_date in signal_dates
        for factor in spec.factors
        for horizon in spec.horizons
        for outcome_field in spec.outcome_fields
    }
    missing_keys = sorted(expected.difference(selected))
    if missing_keys:
        raise ValueError(f"missing requested factor/horizon/outcome/date key: {missing_keys[0]}")
    if not signal_dates:
        raise ValueError("evaluation result contains no signal dates inside the registered overall boundary")
    return selected, tuple(sorted(signal_dates))


def _block_result(
    *,
    source_identity: str,
    spec: FactorTemporalStabilitySpec,
    factor: str,
    horizon: int,
    outcome_field: str,
    block: TemporalBlock,
    items: tuple[_DailyLike, ...],
) -> FactorBlockStabilityResult:
    ic_values = tuple(float(item.rank_ic) for item in items if item.rank_ic is not None)
    bucket_items = tuple(
        item for item in items
        if item.bucket_undefined_reason is None
        and item.high_minus_low_mean_spread is not None
    )
    mean_spreads = tuple(float(item.high_minus_low_mean_spread) for item in bucket_items)
    median_spreads = tuple(
        float(item.high_minus_low_median_spread)
        for item in bucket_items
        if item.high_minus_low_median_spread is not None
    )
    minimum = spec.minimum_defined_dates_per_eligible_block
    warnings = [DESCRIPTIVE_TEMPORAL_WARNING]
    if len(ic_values) < minimum:
        warnings.append("fewer_than_minimum_defined_ic_dates_for_eligible_block")
    if len(mean_spreads) < minimum:
        warnings.append("fewer_than_minimum_defined_spread_dates_for_eligible_block")
    if not items:
        undefined_reason = "no_signal_dates_in_block"
    elif not ic_values and not mean_spreads:
        undefined_reason = "no_defined_ic_or_bucket_spread_dates"
    elif not ic_values:
        undefined_reason = "no_defined_ic_dates"
    elif not mean_spreads:
        undefined_reason = "no_defined_bucket_spread_dates"
    else:
        undefined_reason = None
    return FactorBlockStabilityResult(
        source_evaluation_identity=source_identity,
        specification_fingerprint=spec.fingerprint,
        factor=factor,
        horizon_sessions=horizon,
        outcome_field=outcome_field,
        block_name=block.name,
        block_start_date=block.start_date,
        block_end_date=block.end_date,
        total_signal_date_rows=len(items),
        dates_with_available_labels=sum(item.available_labeled_candidates > 0 for item in items),
        ic_defined_date_count=len(ic_values),
        bucket_defined_date_count=len(mean_spreads),
        mean_daily_rank_ic=_mean(ic_values),
        median_daily_rank_ic=_median(ic_values),
        population_std_daily_rank_ic=_population_std(ic_values),
        positive_ic_date_count=sum(value > 0 for value in ic_values),
        zero_ic_date_count=sum(value == 0 for value in ic_values),
        negative_ic_date_count=sum(value < 0 for value in ic_values),
        positive_ic_rate=None if not ic_values else sum(value > 0 for value in ic_values) / len(ic_values),
        mean_daily_high_minus_low_mean_spread=_mean(mean_spreads),
        median_daily_high_minus_low_mean_spread=_median(mean_spreads),
        mean_daily_high_minus_low_median_spread=_mean(median_spreads),
        median_daily_high_minus_low_median_spread=_median(median_spreads),
        positive_mean_spread_date_count=sum(value > 0 for value in mean_spreads),
        zero_mean_spread_date_count=sum(value == 0 for value in mean_spreads),
        negative_mean_spread_date_count=sum(value < 0 for value in mean_spreads),
        positive_mean_spread_rate=(
            None if not mean_spreads else sum(value > 0 for value in mean_spreads) / len(mean_spreads)
        ),
        average_low_bucket_size=_mean(tuple(float(item.low_bucket_count) for item in bucket_items)),
        average_high_bucket_size=_mean(tuple(float(item.high_bucket_count) for item in bucket_items)),
        undefined_reason=undefined_reason,
        warnings=tuple(warnings),
        _included_daily_identities=tuple(item.identity for item in items),
    )


def _largest_absolute(
    blocks: tuple[FactorBlockStabilityResult, ...],
    attribute: str,
) -> FactorBlockStabilityResult | None:
    available = tuple(item for item in blocks if getattr(item, attribute) is not None)
    return None if not available else max(available, key=lambda item: abs(getattr(item, attribute)))


def _concentration(values: tuple[float, ...]) -> float | None:
    if not values:
        return None
    denominator = sum(abs(value) for value in values)
    return None if denominator == 0.0 else max(abs(value) for value in values) / denominator


def _summary(
    *,
    source_identity: str,
    spec: FactorTemporalStabilitySpec,
    factor: str,
    horizon: int,
    outcome_field: str,
    blocks: tuple[FactorBlockStabilityResult, ...],
) -> FactorHorizonTemporalStabilitySummary:
    ic_blocks = tuple(item for item in blocks if item.mean_daily_rank_ic is not None)
    spread_blocks = tuple(
        item for item in blocks if item.mean_daily_high_minus_low_mean_spread is not None
    )
    ic_values = tuple(float(item.mean_daily_rank_ic) for item in ic_blocks)
    spread_values = tuple(float(item.mean_daily_high_minus_low_mean_spread) for item in spread_blocks)
    minimum = spec.minimum_defined_dates_per_eligible_block
    eligible_ic = tuple(item for item in blocks if item.ic_defined_date_count >= minimum)
    eligible_spread = tuple(item for item in blocks if item.bucket_defined_date_count >= minimum)
    coverage = len(eligible_ic) >= 3 and len(eligible_spread) >= 3
    directional_ic = coverage and sum(
        item.mean_daily_rank_ic is not None and item.mean_daily_rank_ic > 0
        for item in eligible_ic
    ) >= 3
    directional_spread = coverage and sum(
        item.mean_daily_high_minus_low_mean_spread is not None
        and item.mean_daily_high_minus_low_mean_spread > 0
        for item in eligible_spread
    ) >= 3
    largest_ic = _largest_absolute(blocks, "mean_daily_rank_ic")
    largest_spread = _largest_absolute(blocks, "mean_daily_high_minus_low_mean_spread")
    warnings = [DESCRIPTIVE_TEMPORAL_WARNING, RAW_EXCESS_NONINDEPENDENCE]
    if len(eligible_ic) < 3:
        warnings.append("insufficient_ic_temporal_coverage")
    if len(eligible_spread) < 3:
        warnings.append("insufficient_spread_temporal_coverage")
    if ic_values and sum(abs(value) for value in ic_values) == 0.0:
        warnings.append("absolute_mean_ic_concentration_undefined_zero_sum")
    if spread_values and sum(abs(value) for value in spread_values) == 0.0:
        warnings.append("absolute_mean_spread_concentration_undefined_zero_sum")
    return FactorHorizonTemporalStabilitySummary(
        source_evaluation_identity=source_identity,
        specification_fingerprint=spec.fingerprint,
        factor=factor,
        horizon_sessions=horizon,
        outcome_field=outcome_field,
        block_results=blocks,
        blocks_with_defined_ic=len(ic_blocks),
        blocks_with_defined_spread=len(spread_blocks),
        blocks_with_at_least_minimum_ic_dates=len(eligible_ic),
        blocks_with_at_least_minimum_spread_dates=len(eligible_spread),
        positive_mean_ic_block_count=sum(value > 0 for value in ic_values),
        zero_mean_ic_block_count=sum(value == 0 for value in ic_values),
        negative_mean_ic_block_count=sum(value < 0 for value in ic_values),
        positive_mean_spread_block_count=sum(value > 0 for value in spread_values),
        zero_mean_spread_block_count=sum(value == 0 for value in spread_values),
        negative_mean_spread_block_count=sum(value < 0 for value in spread_values),
        minimum_block_mean_ic=None if not ic_values else min(ic_values),
        maximum_block_mean_ic=None if not ic_values else max(ic_values),
        range_block_mean_ic=None if not ic_values else max(ic_values) - min(ic_values),
        minimum_block_mean_spread=None if not spread_values else min(spread_values),
        maximum_block_mean_spread=None if not spread_values else max(spread_values),
        range_block_mean_spread=(
            None if not spread_values else max(spread_values) - min(spread_values)
        ),
        largest_absolute_mean_ic_block_name=None if largest_ic is None else largest_ic.block_name,
        largest_absolute_mean_ic_block_identity=None if largest_ic is None else largest_ic.identity,
        largest_absolute_mean_spread_block_name=(
            None if largest_spread is None else largest_spread.block_name
        ),
        largest_absolute_mean_spread_block_identity=(
            None if largest_spread is None else largest_spread.identity
        ),
        absolute_mean_ic_concentration=_concentration(ic_values),
        absolute_mean_spread_concentration=_concentration(spread_values),
        coverage_sufficient_for_stability_review=coverage,
        directionally_consistent_ic=directional_ic,
        directionally_consistent_spread=directional_spread,
        descriptive_temporal_support=directional_ic and directional_spread,
        warnings=tuple(warnings),
    )


def evaluate_candidate_factor_temporal_stability(
    evaluation_result: _EvaluationLike,
    spec: FactorTemporalStabilitySpec,
) -> CandidateFactorTemporalStabilityResult:
    """Aggregate existing daily Phase 4.5 metrics into fixed calendar blocks."""
    selected, _signal_dates = _validated_input(evaluation_result, spec)
    grouped: dict[tuple[str, int, str, str], list[_DailyLike]] = defaultdict(list)
    for (signal_date, factor, horizon, outcome_field), item in selected.items():
        block = _block_for_date(signal_date, spec.blocks)
        if block is None:  # guarded by validation; retained as a hard causal boundary
            raise ValueError(f"registered signal date has no temporal block: {signal_date}")
        grouped[(factor, horizon, outcome_field, block.name)].append(item)

    summaries: list[FactorHorizonTemporalStabilitySummary] = []
    for factor in spec.factors:
        for horizon in spec.horizons:
            for outcome_field in spec.outcome_fields:
                block_results = tuple(
                    _block_result(
                        source_identity=evaluation_result.result_identity,
                        spec=spec,
                        factor=factor,
                        horizon=horizon,
                        outcome_field=outcome_field,
                        block=block,
                        items=tuple(sorted(
                            grouped.get((factor, horizon, outcome_field, block.name), ()),
                            key=lambda item: (item.signal_date, item.identity),
                        )),
                    )
                    for block in spec.blocks
                )
                summaries.append(_summary(
                    source_identity=evaluation_result.result_identity,
                    spec=spec,
                    factor=factor,
                    horizon=horizon,
                    outcome_field=outcome_field,
                    blocks=block_results,
                ))

    metadata = MappingProxyType({
        "raw_vs_excess_rank_ic_relationship": RAW_EXCESS_NONINDEPENDENCE,
        "raw_vs_excess_spread_handling": (
            "retain_exact_stored_calculations_and_allow_machine_precision_differences"
        ),
        "aggregation_scope": "existing_phase_4_5_daily_records_only",
        "factor_search_performed": False,
        "weight_optimization_performed": False,
        "statistical_significance_claimed": False,
        "overall_start_date": spec.overall_start_date,
        "overall_end_date": spec.overall_end_date,
    })
    return CandidateFactorTemporalStabilityResult(
        source_evaluation_identity=evaluation_result.result_identity,
        source_candidate_batch_identity=evaluation_result.source_candidate_batch_identity,
        source_outcome_set_identity=evaluation_result.source_outcome_set_identity,
        source_evaluation_spec_fingerprint=evaluation_result.evaluation_spec_fingerprint,
        temporal_stability_spec_fingerprint=spec.fingerprint,
        summaries=tuple(summaries),
        provenance_metadata=metadata,
    )
