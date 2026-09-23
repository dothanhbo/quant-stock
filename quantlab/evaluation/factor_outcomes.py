from __future__ import annotations

"""Pure same-date single-factor diagnostics over immutable future labels."""

from collections import defaultdict
from datetime import date
import math
from types import MappingProxyType
from typing import Any, Protocol

from quantlab.outcomes.contracts import ForwardOutcomeStatus

from .contracts import (
    CandidateFactorOutcomeEvaluationResult,
    DailyFactorOutcomeEvaluation,
    FactorHorizonEvaluationSummary,
    FactorOutcomeEvaluationSpec,
)


_OUTCOME_ATTRIBUTES = {
    "stock_forward_return_pct": "stock_forward_return_pct",
    "excess_forward_return_pct_points": "excess_forward_return_percentage_points",
}


class _CandidateLike(Protocol):
    candidate_key: str
    symbol: str
    signal_date: str


class _CandidateBatchLike(Protocol):
    candidates: tuple[_CandidateLike, ...]
    batch_identity: str


class _OutcomeLike(Protocol):
    candidate_key: str
    symbol: str
    signal_date: str
    horizon_sessions: int
    status: ForwardOutcomeStatus
    outcome_identity: str


class _OutcomeSetLike(Protocol):
    outcomes: tuple[_OutcomeLike, ...]
    source_candidate_batch_identity: str
    set_identity: str
    requested_horizons: tuple[int, ...]


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


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


def _average_ranks(values: tuple[float, ...]) -> tuple[float, ...]:
    ordered = sorted(range(len(values)), key=lambda index: (values[index], index))
    ranks = [0.0] * len(values)
    position = 0
    while position < len(ordered):
        end = position + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[position]]:
            end += 1
        average = ((position + 1) + end) / 2.0
        for offset in range(position, end):
            ranks[ordered[offset]] = average
        position = end
    return tuple(ranks)


def _pearson(first: tuple[float, ...], second: tuple[float, ...]) -> float:
    first_mean, second_mean = sum(first) / len(first), sum(second) / len(second)
    left = tuple(item - first_mean for item in first)
    right = tuple(item - second_mean for item in second)
    denominator = math.sqrt(sum(item * item for item in left) * sum(item * item for item in right))
    if denominator == 0.0 or not math.isfinite(denominator):
        raise ValueError("rank correlation denominator is zero")
    value = sum(a * b for a, b in zip(left, right, strict=True)) / denominator
    return max(-1.0, min(1.0, value))


def _validated_inputs(candidate_batch: object, outcome_set: object, spec: FactorOutcomeEvaluationSpec):
    if not isinstance(spec, FactorOutcomeEvaluationSpec):
        raise TypeError("spec must be FactorOutcomeEvaluationSpec")
    for value, names, label in (
        (candidate_batch, ("candidates", "batch_identity"), "candidate_batch"),
        (outcome_set, ("outcomes", "source_candidate_batch_identity", "set_identity", "requested_horizons"), "outcome_set"),
    ):
        missing = tuple(name for name in names if not hasattr(value, name))
        if missing:
            raise TypeError(f"{label} is missing required fields: {', '.join(missing)}")
    candidates = getattr(candidate_batch, "candidates")
    outcomes = getattr(outcome_set, "outcomes")
    if not isinstance(candidates, tuple) or not isinstance(outcomes, tuple):
        raise TypeError("candidate and outcome collections must be immutable tuples")
    if getattr(outcome_set, "source_candidate_batch_identity") != getattr(candidate_batch, "batch_identity"):
        raise ValueError("outcome set candidate-batch identity does not match supplied candidate batch")
    if tuple(sorted(getattr(outcome_set, "requested_horizons"))) != spec.horizons:
        raise ValueError("outcome-set horizons must exactly match evaluation-spec horizons")
    candidate_map: dict[str, _CandidateLike] = {}
    for candidate in candidates:
        missing = tuple(name for name in ("candidate_key", "symbol", "signal_date") if not hasattr(candidate, name))
        if missing:
            raise TypeError("candidate is missing required fields: " + ", ".join(missing))
        key = str(candidate.candidate_key)
        if not key or key in candidate_map:
            raise ValueError(f"duplicate or empty candidate key: {key}")
        try:
            date.fromisoformat(str(candidate.signal_date))
        except ValueError as exc:
            raise ValueError(f"candidate signal_date must be an ISO date: {key}") from exc
        absent_factors = tuple(factor for factor in spec.factors if not hasattr(candidate, factor))
        if absent_factors:
            raise TypeError(f"candidate {key} is missing factors: {', '.join(absent_factors)}")
        candidate_map[key] = candidate
    outcome_map: dict[tuple[str, int], _OutcomeLike] = {}
    for outcome in outcomes:
        missing = tuple(name for name in ("candidate_key", "symbol", "signal_date", "horizon_sessions", "status", "outcome_identity") if not hasattr(outcome, name))
        if missing:
            raise TypeError("outcome is missing required fields: " + ", ".join(missing))
        key = str(outcome.candidate_key)
        horizon = outcome.horizon_sessions
        pair = (key, horizon)
        if pair in outcome_map:
            raise ValueError(f"duplicate candidate/horizon outcome: {key}/{horizon}")
        if key not in candidate_map:
            raise ValueError(f"extra outcome candidate outside supplied batch: {key}")
        if horizon not in spec.horizons:
            raise ValueError(f"extra outcome horizon outside evaluation spec: {key}/{horizon}")
        candidate = candidate_map[key]
        if (outcome.symbol, outcome.signal_date) != (candidate.symbol, candidate.signal_date):
            raise ValueError(f"outcome candidate provenance mismatch: {key}/{horizon}")
        if not isinstance(outcome.status, ForwardOutcomeStatus):
            raise ValueError(f"invalid outcome status: {key}/{horizon}")
        for field in spec.outcome_fields:
            if not hasattr(outcome, _OUTCOME_ATTRIBUTES[field]):
                raise TypeError(f"outcome {key}/{horizon} is missing field: {field}")
        outcome_map[pair] = outcome
    expected = {(key, horizon) for key in candidate_map for horizon in spec.horizons}
    missing_pairs = sorted(expected.difference(outcome_map))
    if missing_pairs:
        key, horizon = missing_pairs[0]
        raise ValueError(f"missing candidate/horizon outcome: {key}/{horizon}")
    if set(outcome_map).difference(expected):
        raise ValueError("outcome set contains unexpected candidate/horizon records")
    return candidate_map, outcome_map


def _daily(
    *,
    signal_date: str,
    candidates: tuple[_CandidateLike, ...],
    outcomes: dict[tuple[str, int], _OutcomeLike],
    factor: str,
    horizon: int,
    outcome_field: str,
    spec: FactorOutcomeEvaluationSpec,
) -> DailyFactorOutcomeEvaluation:
    ordered = tuple(sorted(candidates, key=lambda item: (item.symbol, item.candidate_key)))
    evidence: list[tuple[Any, ...]] = []
    pairs: list[tuple[str, float, float]] = []
    available_count = 0
    attribute = _OUTCOME_ATTRIBUTES[outcome_field]
    for candidate in ordered:
        outcome = outcomes[(candidate.candidate_key, horizon)]
        factor_value = _finite(getattr(candidate, factor))
        outcome_value = _finite(getattr(outcome, attribute)) if outcome.status is ForwardOutcomeStatus.AVAILABLE else None
        if outcome.status is ForwardOutcomeStatus.AVAILABLE:
            available_count += 1
        evidence.append((candidate.candidate_key, factor_value, outcome_value, outcome.status.value, outcome.outcome_identity))
        if factor_value is not None and outcome_value is not None:
            pairs.append((candidate.candidate_key, factor_value, outcome_value))
    factor_values = tuple(item[1] for item in pairs)
    outcome_values = tuple(item[2] for item in pairs)
    unique_count = len(set(factor_values))
    constant_factor = bool(factor_values) and unique_count == 1
    minimum = spec.minimum_cross_section_size
    if len(pairs) < minimum:
        rank_ic = None
        ic_reason = "unavailable_outcome_status" if available_count == 0 and ordered else "fewer_than_minimum_pairwise_finite_observations"
    elif constant_factor:
        rank_ic, ic_reason = None, "constant_factor"
    elif len(set(outcome_values)) == 1:
        rank_ic, ic_reason = None, "constant_outcome"
    else:
        rank_ic = _pearson(_average_ranks(factor_values), _average_ranks(outcome_values))
        ic_reason = None
    low: tuple[float, ...] = ()
    high: tuple[float, ...] = ()
    if len(pairs) < minimum:
        bucket_reason = "unavailable_outcome_status" if available_count == 0 and ordered else "fewer_than_minimum_pairwise_finite_observations"
    else:
        ranks = _average_ranks(factor_values)
        percentiles = tuple((rank - 1.0) / (len(ranks) - 1.0) for rank in ranks)
        low_indexes = {index for index, value in enumerate(percentiles) if value <= spec.low_bucket_max_percentile}
        high_indexes = {index for index, value in enumerate(percentiles) if value >= spec.high_bucket_min_percentile}
        if low_indexes.intersection(high_indexes):
            bucket_reason = "overlapping_buckets"
        elif not low_indexes:
            bucket_reason = "empty_low_bucket"
        elif not high_indexes:
            bucket_reason = "empty_high_bucket"
        else:
            bucket_reason = None
            low = tuple(outcome_values[index] for index in sorted(low_indexes))
            high = tuple(outcome_values[index] for index in sorted(high_indexes))
    low_mean, low_median = _mean(low), _median(low)
    high_mean, high_median = _mean(high), _median(high)
    return DailyFactorOutcomeEvaluation(
        signal_date=signal_date,
        factor=factor,
        horizon_sessions=horizon,
        outcome_field=outcome_field,
        total_same_date_candidates=len(ordered),
        available_labeled_candidates=available_count,
        pairwise_finite_count=len(pairs),
        factor_unique_count=unique_count,
        factor_constant=constant_factor,
        rank_ic=rank_ic,
        ic_undefined_reason=ic_reason,
        low_bucket_count=len(low),
        high_bucket_count=len(high),
        low_bucket_mean_outcome=low_mean,
        low_bucket_median_outcome=low_median,
        high_bucket_mean_outcome=high_mean,
        high_bucket_median_outcome=high_median,
        high_minus_low_mean_spread=None if low_mean is None or high_mean is None else high_mean - low_mean,
        high_minus_low_median_spread=None if low_median is None or high_median is None else high_median - low_median,
        bucket_undefined_reason=bucket_reason,
        _joined_evidence=tuple(evidence),
    )


def _summary(items: tuple[DailyFactorOutcomeEvaluation, ...]) -> FactorHorizonEvaluationSummary:
    first = items[0]
    ic_values = tuple(item.rank_ic for item in items if item.rank_ic is not None)
    bucket_items = tuple(item for item in items if item.bucket_undefined_reason is None)
    mean_spreads = tuple(item.high_minus_low_mean_spread for item in bucket_items if item.high_minus_low_mean_spread is not None)
    median_spreads = tuple(item.high_minus_low_median_spread for item in bucket_items if item.high_minus_low_median_spread is not None)
    total = len(items)
    return FactorHorizonEvaluationSummary(
        factor=first.factor,
        horizon_sessions=first.horizon_sessions,
        outcome_field=first.outcome_field,
        total_signal_dates=total,
        dates_with_available_labels=sum(item.available_labeled_candidates > 0 for item in items),
        ic_defined_date_count=len(ic_values),
        ic_coverage_pct=0.0 if total == 0 else len(ic_values) / total * 100.0,
        mean_daily_rank_ic=_mean(ic_values),
        median_daily_rank_ic=_median(ic_values),
        population_std_daily_ic=_population_std(ic_values),
        positive_ic_date_count=sum(item > 0 for item in ic_values),
        zero_ic_date_count=sum(item == 0 for item in ic_values),
        negative_ic_date_count=sum(item < 0 for item in ic_values),
        positive_ic_rate=None if not ic_values else sum(item > 0 for item in ic_values) / len(ic_values),
        bucket_defined_date_count=len(bucket_items),
        bucket_coverage_pct=0.0 if total == 0 else len(bucket_items) / total * 100.0,
        mean_daily_high_minus_low_mean_spread=_mean(mean_spreads),
        median_daily_high_minus_low_mean_spread=_median(mean_spreads),
        mean_daily_high_minus_low_median_spread=_mean(median_spreads),
        median_daily_high_minus_low_median_spread=_median(median_spreads),
        positive_spread_date_count=sum(item > 0 for item in mean_spreads),
        zero_spread_date_count=sum(item == 0 for item in mean_spreads),
        negative_spread_date_count=sum(item < 0 for item in mean_spreads),
        positive_spread_rate=None if not mean_spreads else sum(item > 0 for item in mean_spreads) / len(mean_spreads),
        average_low_bucket_size=_mean(tuple(float(item.low_bucket_count) for item in bucket_items)),
        average_high_bucket_size=_mean(tuple(float(item.high_bucket_count) for item in bucket_items)),
        included_daily_identities=tuple(item.identity for item in items),
    )


def evaluate_candidate_factor_outcomes(
    candidate_batch: _CandidateBatchLike,
    outcome_set: _OutcomeSetLike,
    spec: FactorOutcomeEvaluationSpec,
) -> CandidateFactorOutcomeEvaluationResult:
    """Evaluate each factor independently by date; never load or mutate data."""
    candidates, outcomes = _validated_inputs(candidate_batch, outcome_set, spec)
    by_date: dict[str, list[_CandidateLike]] = defaultdict(list)
    for candidate in candidates.values():
        by_date[candidate.signal_date].append(candidate)
    daily = tuple(
        _daily(
            signal_date=signal_date,
            candidates=tuple(by_date[signal_date]),
            outcomes=outcomes,
            factor=factor,
            horizon=horizon,
            outcome_field=outcome_field,
            spec=spec,
        )
        for signal_date in sorted(by_date)
        for factor in spec.factors
        for horizon in spec.horizons
        for outcome_field in spec.outcome_fields
    )
    lookup = {
        (item.signal_date, item.factor, item.horizon_sessions, item.outcome_field): item
        for item in daily
    }
    summaries: list[FactorHorizonEvaluationSummary] = []
    for factor in spec.factors:
        for horizon in spec.horizons:
            for outcome_field in spec.outcome_fields:
                items = tuple(
                    lookup[(signal_date, factor, horizon, outcome_field)]
                    for signal_date in sorted(by_date)
                )
                if items:
                    summaries.append(_summary(items))
                else:
                    summaries.append(FactorHorizonEvaluationSummary(
                        factor=factor, horizon_sessions=horizon, outcome_field=outcome_field,
                        total_signal_dates=0, dates_with_available_labels=0,
                        ic_defined_date_count=0, ic_coverage_pct=0.0,
                        mean_daily_rank_ic=None, median_daily_rank_ic=None,
                        population_std_daily_ic=None, positive_ic_date_count=0,
                        zero_ic_date_count=0, negative_ic_date_count=0,
                        positive_ic_rate=None, bucket_defined_date_count=0,
                        bucket_coverage_pct=0.0,
                        mean_daily_high_minus_low_mean_spread=None,
                        median_daily_high_minus_low_mean_spread=None,
                        mean_daily_high_minus_low_median_spread=None,
                        median_daily_high_minus_low_median_spread=None,
                        positive_spread_date_count=0, zero_spread_date_count=0,
                        negative_spread_date_count=0, positive_spread_rate=None,
                        average_low_bucket_size=None, average_high_bucket_size=None,
                        included_daily_identities=(),
                    ))
    status_counts = MappingProxyType({
        horizon: MappingProxyType({
            status: sum(item.horizon_sessions == horizon and item.status is status for item in outcomes.values())
            for status in ForwardOutcomeStatus
        })
        for horizon in spec.horizons
    })
    return CandidateFactorOutcomeEvaluationResult(
        source_candidate_batch_identity=candidate_batch.batch_identity,
        source_outcome_set_identity=outcome_set.set_identity,
        evaluation_spec_fingerprint=spec.fingerprint,
        daily_evaluations=daily,
        summaries=tuple(summaries),
        status_counts_by_horizon=status_counts,
    )
