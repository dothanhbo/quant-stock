from __future__ import annotations

"""Pure contemporaneous diagnostics over accepted immutable candidates."""

from collections import defaultdict
from hashlib import sha256
from itertools import combinations
import math
from types import MappingProxyType
from typing import Any, Iterable

from quantlab.candidates import FrozenQ70CandidateBatch, FrozenQ70CandidateRecord
from quantlab.features.contracts import canonical_json

from .contracts import (
    CandidateFactorDiagnosticsResult,
    CandidateFactorSet,
    FactorAggregateDiagnostics,
    FactorAssociationDiagnostics,
    FactorDateDiagnostics,
    FactorDateStabilityDiagnostics,
    FactorDescriptiveDiagnostics,
)


FROZEN_Q70_CANDIDATE_DIAGNOSTICS_V1 = CandidateFactorSet(
    name="FROZEN_Q70_CANDIDATE_DIAGNOSTICS_V1",
    version="1",
    fields=(
        "quality_score",
        "score",
        "relative_strength_20d",
        "adx",
        "atr_percent",
        "rsi14",
        "volume_ratio",
        "breadth_ema50_pct",
        "breadth_ema50_change_10d",
    ),
)


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _quantile(values: tuple[float, ...], quantile: float) -> float | None:
    """NumPy-compatible linear interpolation at index ``(n - 1) * q``."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


def _population_std(values: tuple[float, ...], mean: float | None = None) -> float | None:
    if not values:
        return None
    center = sum(values) / len(values) if mean is None else mean
    return math.sqrt(sum((value - center) ** 2 for value in values) / len(values))


def _descriptive(values: Iterable[Any], *, total_count: int) -> FactorDescriptiveDiagnostics:
    finite = tuple(value for item in values if (value := _finite(item)) is not None)
    finite_count = len(finite)
    mean = None if not finite else sum(finite) / finite_count
    p25, median, p75 = (_quantile(finite, quantile) for quantile in (.25, .5, .75))
    unique_count = len(set(finite))
    tie_count = finite_count - unique_count
    return FactorDescriptiveDiagnostics(
        total_candidate_count=total_count,
        finite_count=finite_count,
        missing_nonfinite_count=total_count - finite_count,
        finite_coverage_pct=0.0 if total_count == 0 else finite_count / total_count * 100.0,
        minimum=None if not finite else min(finite),
        maximum=None if not finite else max(finite),
        mean=mean,
        population_std=_population_std(finite, mean),
        median=median,
        percentile_25=p25,
        percentile_75=p75,
        interquartile_range=None if p25 is None or p75 is None else p75 - p25,
        unique_finite_count=unique_count,
        tie_count=tie_count,
        tie_rate=None if finite_count == 0 else tie_count / finite_count,
        constant=finite_count > 0 and unique_count == 1,
    )


def _average_ranks(values: tuple[float, ...]) -> tuple[float, ...]:
    ordered = sorted(range(len(values)), key=lambda index: (values[index], index))
    ranks = [0.0] * len(values)
    position = 0
    while position < len(ordered):
        end = position + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[position]]:
            end += 1
        average_rank = ((position + 1) + end) / 2.0
        for offset in range(position, end):
            ranks[ordered[offset]] = average_rank
        position = end
    return tuple(ranks)


def _correlation(first: tuple[float, ...], second: tuple[float, ...]) -> tuple[float | None, str | None]:
    if len(first) < 2:
        return None, "fewer_than_two_pairwise_finite_observations"
    constant = []
    if len(set(first)) == 1:
        constant.append("first")
    if len(set(second)) == 1:
        constant.append("second")
    if constant:
        return None, "constant_factor:" + ",".join(constant)
    first_mean, second_mean = sum(first) / len(first), sum(second) / len(second)
    first_centered = tuple(value - first_mean for value in first)
    second_centered = tuple(value - second_mean for value in second)
    denominator = math.sqrt(sum(value * value for value in first_centered) * sum(value * value for value in second_centered))
    if denominator == 0.0 or not math.isfinite(denominator):
        return None, "correlation_denominator_zero"
    value = sum(left * right for left, right in zip(first_centered, second_centered, strict=True)) / denominator
    return max(-1.0, min(1.0, value)), None


def _association(candidates: tuple[FrozenQ70CandidateRecord, ...], first: str, second: str) -> FactorAssociationDiagnostics:
    pairs = tuple(
        (left, right)
        for candidate in candidates
        if (left := _finite(getattr(candidate, first))) is not None
        and (right := _finite(getattr(candidate, second))) is not None
    )
    first_values = tuple(pair[0] for pair in pairs)
    second_values = tuple(pair[1] for pair in pairs)
    pearson, reason = _correlation(first_values, second_values)
    if reason is None:
        spearman, spearman_reason = _correlation(_average_ranks(first_values), _average_ranks(second_values))
        reason = spearman_reason
    else:
        spearman = None
    return FactorAssociationDiagnostics(first, second, len(pairs), pearson, spearman, reason)


def _associations(candidates: tuple[FrozenQ70CandidateRecord, ...], factor_set: CandidateFactorSet) -> tuple[FactorAssociationDiagnostics, ...]:
    return tuple(_association(candidates, first, second) for first, second in combinations(factor_set.fields, 2))


def _source_content_identity(candidates: tuple[FrozenQ70CandidateRecord, ...]) -> str:
    content = [candidate.canonical_content() for candidate in sorted(candidates, key=lambda item: (item.symbol, item.candidate_key))]
    return sha256(canonical_json(content)).hexdigest()


def _date_diagnostics(candidates: tuple[FrozenQ70CandidateRecord, ...], signal_date: str, factor_set: CandidateFactorSet) -> FactorDateDiagnostics:
    ordered = tuple(sorted(candidates, key=lambda item: (item.symbol, item.candidate_key)))
    return FactorDateDiagnostics(
        signal_date=signal_date,
        candidate_count=len(ordered),
        factor_set_fingerprint=factor_set.fingerprint,
        source_candidate_content_identity=_source_content_identity(ordered),
        per_factor=MappingProxyType({
            factor: _descriptive((getattr(candidate, factor) for candidate in ordered), total_count=len(ordered))
            for factor in factor_set.fields
        }),
        pairwise_associations=_associations(ordered, factor_set),
    )


def _mean(values: tuple[float, ...]) -> float | None:
    return None if not values else sum(values) / len(values)


def _stability(per_date: tuple[FactorDateDiagnostics, ...], factor: str) -> FactorDateStabilityDiagnostics:
    diagnostics = tuple(item.per_factor[factor] for item in per_date)
    medians = tuple(item.median for item in diagnostics if item.median is not None)
    iqrs = tuple(item.interquartile_range for item in diagnostics if item.interquartile_range is not None)
    coverages = tuple(item.finite_coverage_pct for item in diagnostics)
    median_mean = _mean(medians)
    return FactorDateStabilityDiagnostics(
        dates_with_finite_observations=sum(item.finite_count > 0 for item in diagnostics),
        constant_date_count=sum(item.constant for item in diagnostics),
        mean_date_finite_coverage_pct=_mean(coverages),
        mean_date_median=median_mean,
        population_std_date_median=_population_std(medians, median_mean),
        mean_date_iqr=_mean(iqrs),
    )


def diagnose_candidate_factors(
    candidate_batch: FrozenQ70CandidateBatch,
    factor_set: CandidateFactorSet = FROZEN_Q70_CANDIDATE_DIAGNOSTICS_V1,
) -> CandidateFactorDiagnosticsResult:
    """Describe contemporaneous candidate fields without outcomes or ranking."""
    if not isinstance(candidate_batch, FrozenQ70CandidateBatch):
        raise TypeError("candidate_batch must be FrozenQ70CandidateBatch")
    if not isinstance(factor_set, CandidateFactorSet):
        raise TypeError("factor_set must be CandidateFactorSet")
    grouped: dict[str, list[FrozenQ70CandidateRecord]] = defaultdict(list)
    seen: set[str] = set()
    for candidate in candidate_batch.candidates:
        if candidate.candidate_key in seen:
            raise ValueError(f"duplicate candidate key: {candidate.candidate_key}")
        seen.add(candidate.candidate_key)
        grouped[candidate.signal_date].append(candidate)
    per_date = tuple(
        _date_diagnostics(tuple(grouped[signal_date]), signal_date, factor_set)
        for signal_date in sorted(grouped)
    )
    pooled = tuple(sorted(candidate_batch.candidates, key=lambda item: (item.signal_date, item.symbol, item.candidate_key)))
    aggregate = FactorAggregateDiagnostics(
        date_count=len(per_date),
        total_candidate_observations=len(pooled),
        per_factor=MappingProxyType({
            factor: _descriptive((getattr(candidate, factor) for candidate in pooled), total_count=len(pooled))
            for factor in factor_set.fields
        }),
        pairwise_associations=_associations(pooled, factor_set),
        date_level_stability=MappingProxyType({factor: _stability(per_date, factor) for factor in factor_set.fields}),
    )
    return CandidateFactorDiagnosticsResult(
        source_candidate_batch_identity=candidate_batch.batch_identity,
        factor_set_fingerprint=factor_set.fingerprint,
        per_date=per_date,
        aggregate=aggregate,
    )

