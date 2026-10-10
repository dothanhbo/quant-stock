from __future__ import annotations

"""Pure same-date factor diagnostics over a point-in-time research dataset."""

from collections import Counter
from datetime import date
import math
from statistics import median
from types import MappingProxyType
from typing import Any

import pandas as pd

from quantlab.panels.feature_contracts import FeatureValueAvailability
from quantlab.panels.outcome_contracts import PanelForwardOutcomeAvailability
from quantlab.panels.research_dataset_contracts import PERMITTED_USE

from .panel_factor_contracts import (
    NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1,
    PanelDailyFactorEvaluation,
    PanelFactorEvaluationSpec,
    PanelFactorHorizonSummary,
    PointInTimePanelFactorEvaluationResult,
)


_OUTCOME_COLUMN_TEMPLATES = {
    "stock_forward_return_pct": "stock_forward_return_{horizon}_pct",
    "excess_forward_return_pct_points": "excess_forward_return_{horizon}_pct_points",
}


def _mean(values: tuple[float, ...]) -> float | None:
    return None if not values else sum(values) / len(values)


def _median(values: tuple[float, ...]) -> float | None:
    return None if not values else float(median(values))


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


def _pearson(first: tuple[float, ...], second: tuple[float, ...]) -> float | None:
    first_mean, second_mean = sum(first) / len(first), sum(second) / len(second)
    left = tuple(item - first_mean for item in first)
    right = tuple(item - second_mean for item in second)
    denominator = math.sqrt(
        sum(item * item for item in left) * sum(item * item for item in right)
    )
    if denominator == 0.0 or not math.isfinite(denominator):
        return None
    value = sum(a * b for a, b in zip(left, right, strict=True)) / denominator
    return max(-1.0, min(1.0, value))


def _numeric(value: Any, *, column: str) -> float | None:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{column} contains a boolean rather than a numeric value")
    if isinstance(value, str):
        raise ValueError(f"{column} contains a non-numeric value")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{column} contains a non-numeric value") from exc
    return number if math.isfinite(number) else None


def _date_text(value: Any) -> str:
    text = str(value).strip()
    try:
        parsed = date.fromisoformat(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid session_date: {value!r}") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"session_date must use canonical YYYY-MM-DD form: {value!r}")
    return text


def _validated_dataset(dataset: object, spec: PanelFactorEvaluationSpec) -> tuple[pd.DataFrame, Any]:
    if not isinstance(spec, PanelFactorEvaluationSpec):
        raise TypeError("spec must be PanelFactorEvaluationSpec")
    required = (
        "identity", "content_identity", "metadata", "spec", "observation_row_count",
        "evaluation_frame",
    )
    missing = tuple(name for name in required if not hasattr(dataset, name))
    if missing:
        raise TypeError(
            "dataset is not structurally compatible with PointInTimeResearchDataset: "
            + ", ".join(missing)
        )
    identity = getattr(dataset, "identity")
    content_identity = getattr(dataset, "content_identity")
    if not isinstance(identity, str) or not identity.strip():
        raise ValueError("dataset identity must be non-empty")
    if not isinstance(content_identity, str) or not content_identity.strip():
        raise ValueError("dataset content identity must be non-empty")
    metadata = getattr(dataset, "metadata")
    if not hasattr(metadata, "get"):
        raise TypeError("dataset metadata must be a mapping")
    if (
        metadata.get("future_looking") is not True
        or metadata.get("production_signal_safe") is not False
        or metadata.get("permitted_use") != PERMITTED_USE
    ):
        raise ValueError("dataset must retain its future-looking offline-research boundary")
    dataset_spec = getattr(dataset, "spec")
    for name in (
        "output_columns", "observation_columns", "feature_columns",
        "feature_availability_columns", "feature_diagnostic_columns", "outcome_columns",
        "outcome_availability_columns", "outcome_diagnostic_columns", "forbidden_predictor_columns",
        "outcome_panel_spec",
    ):
        if not hasattr(dataset_spec, name):
            raise TypeError(f"dataset specification is missing {name}")
    forbidden_groups = (
        set(dataset_spec.observation_columns)
        | set(dataset_spec.feature_availability_columns)
        | set(dataset_spec.feature_diagnostic_columns)
        | set(dataset_spec.outcome_columns)
        | set(dataset_spec.outcome_availability_columns)
        | set(dataset_spec.outcome_diagnostic_columns)
        | set(dataset_spec.forbidden_predictor_columns)
    )
    invalid = tuple(
        factor for factor in spec.factors
        if factor not in set(dataset_spec.feature_columns) or factor in forbidden_groups
    )
    if invalid:
        raise ValueError("evaluation factors violate the declared dataset schema: " + ", ".join(invalid))
    if any(horizon not in tuple(dataset_spec.outcome_panel_spec.horizons) for horizon in spec.horizons):
        raise ValueError("evaluation horizon is absent from the dataset outcome specification")
    frame = dataset.evaluation_frame()
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("dataset.evaluation_frame() must return a DataFrame")
    frame = frame.copy(deep=True)
    if tuple(frame.columns) != tuple(dataset_spec.output_columns):
        raise ValueError("source frame schema is inconsistent with the declared dataset schema")
    if len(frame) != getattr(dataset, "observation_row_count"):
        raise ValueError("dataset population count does not match its evaluation frame")
    if metadata.get("observation_row_count") != len(frame):
        raise ValueError("dataset metadata population count does not reconcile")
    keys: list[tuple[str, str]] = []
    for row in frame.loc[:, ["session_date", "symbol"]].itertuples(index=False):
        session_date = _date_text(row.session_date)
        symbol = str(row.symbol).strip().upper()
        if not symbol:
            raise ValueError("dataset contains an empty symbol")
        keys.append((session_date, symbol))
    if len(set(keys)) != len(keys):
        raise ValueError("dataset contains duplicate (session_date, symbol) keys")
    frame.loc[:, "session_date"] = tuple(item[0] for item in keys)
    frame.loc[:, "symbol"] = tuple(item[1] for item in keys)

    feature_statuses = {item.value for item in FeatureValueAvailability}
    outcome_statuses = {item.value for item in PanelForwardOutcomeAvailability}
    required_columns: set[str] = set()
    for factor in spec.factors:
        required_columns.update((factor, f"{factor}__availability"))
    for horizon in spec.horizons:
        required_columns.add(f"outcome_{horizon}__availability")
        required_columns.update(
            template.format(horizon=horizon) for template in _OUTCOME_COLUMN_TEMPLATES.values()
        )
    absent = sorted(required_columns.difference(frame.columns))
    if absent:
        raise ValueError("dataset is missing required evaluation columns: " + ", ".join(absent))
    for factor in spec.factors:
        statuses = set(frame[f"{factor}__availability"].astype(str))
        unsupported = sorted(statuses.difference(feature_statuses))
        if unsupported:
            raise ValueError(f"invalid feature availability label for {factor}: {unsupported}")
        for value in frame[factor]:
            _numeric(value, column=factor)
    for horizon in spec.horizons:
        status_column = f"outcome_{horizon}__availability"
        statuses = set(frame[status_column].astype(str))
        unsupported = sorted(statuses.difference(outcome_statuses))
        if unsupported:
            raise ValueError(f"invalid outcome availability label for horizon {horizon}: {unsupported}")
        for template in _OUTCOME_COLUMN_TEMPLATES.values():
            column = template.format(horizon=horizon)
            for value in frame[column]:
                _numeric(value, column=column)
    return frame, dataset_spec


def _daily(
    rows: pd.DataFrame,
    *,
    source_identity: str,
    source_content_identity: str,
    spec: PanelFactorEvaluationSpec,
    factor: str,
    horizon: int,
    outcome_field: str,
    signal_date: str,
) -> PanelDailyFactorEvaluation:
    factor_status_column = f"{factor}__availability"
    outcome_status_column = f"outcome_{horizon}__availability"
    outcome_column = _OUTCOME_COLUMN_TEMPLATES[outcome_field].format(horizon=horizon)
    ordered = rows.sort_values("symbol", kind="mergesort")
    factor_status_counts = Counter({item.value: 0 for item in FeatureValueAvailability})
    outcome_status_counts = Counter({item.value: 0 for item in PanelForwardOutcomeAvailability})
    eligible: list[tuple[str, float, float]] = []
    status_evidence: list[tuple[Any, ...]] = []
    excluded_factor = excluded_outcome = excluded_both = 0
    factor_available = outcome_available = 0
    factor_usable = outcome_usable = 0
    factor_nonfinite = outcome_nonfinite = 0
    for row in ordered.itertuples(index=False):
        values = row._asdict()
        factor_status = str(values[factor_status_column])
        outcome_status = str(values[outcome_status_column])
        factor_status_counts[factor_status] += 1
        outcome_status_counts[outcome_status] += 1
        factor_value = _numeric(values[factor], column=factor)
        outcome_value = _numeric(values[outcome_column], column=outcome_column)
        factor_is_available = factor_status == FeatureValueAvailability.AVAILABLE.value
        outcome_is_available = outcome_status == PanelForwardOutcomeAvailability.AVAILABLE.value
        factor_available += factor_is_available
        outcome_available += outcome_is_available
        if factor_is_available and factor_value is None:
            factor_nonfinite += 1
        if outcome_is_available and outcome_value is None:
            outcome_nonfinite += 1
        factor_ok = factor_is_available and factor_value is not None
        outcome_ok = outcome_is_available and outcome_value is not None
        factor_usable += factor_ok
        outcome_usable += outcome_ok
        status_evidence.append((
            str(values["symbol"]), factor_status, outcome_status,
            factor_value, outcome_value, factor_ok, outcome_ok,
        ))
        if factor_ok and outcome_ok:
            eligible.append((str(values["symbol"]), factor_value, outcome_value))
        elif not factor_ok and outcome_ok:
            excluded_factor += 1
        elif factor_ok and not outcome_ok:
            excluded_outcome += 1
        else:
            excluded_both += 1

    factor_values = tuple(item[1] for item in eligible)
    outcome_values = tuple(item[2] for item in eligible)
    factor_ranks = _average_ranks(factor_values)
    outcome_ranks = _average_ranks(outcome_values)
    factor_unique = len(set(factor_values))
    outcome_unique = len(set(outcome_values))
    minimum = spec.minimum_cross_section_size
    if len(eligible) < minimum:
        rank_ic, ic_reason = None, "fewer_than_minimum_pairwise_finite_observations"
    elif factor_unique == 1 and outcome_unique == 1:
        rank_ic, ic_reason = None, "constant_factor_and_outcome"
    elif factor_unique == 1:
        rank_ic, ic_reason = None, "constant_factor"
    elif outcome_unique == 1:
        rank_ic, ic_reason = None, "constant_outcome"
    else:
        rank_ic = _pearson(factor_ranks, outcome_ranks)
        ic_reason = None if rank_ic is not None else "correlation_denominator_zero"

    percentiles = (
        tuple((rank - 1.0) / (len(eligible) - 1.0) for rank in factor_ranks)
        if len(eligible) > 1 else tuple(0.0 for _ in eligible)
    )
    low_indexes: tuple[int, ...] = ()
    high_indexes: tuple[int, ...] = ()
    if len(eligible) < minimum:
        bucket_reason = "fewer_than_minimum_pairwise_finite_observations"
    else:
        low_indexes = tuple(
            index for index, value in enumerate(percentiles)
            if value <= spec.low_bucket_max_percentile
        )
        high_indexes = tuple(
            index for index, value in enumerate(percentiles)
            if value >= spec.high_bucket_min_percentile
        )
        if set(low_indexes).intersection(high_indexes):
            bucket_reason = "overlapping_buckets"
        elif not low_indexes:
            bucket_reason = "empty_low_bucket"
        elif not high_indexes:
            bucket_reason = "empty_high_bucket"
        else:
            bucket_reason = None
    low = tuple(outcome_values[index] for index in low_indexes) if bucket_reason is None else ()
    high = tuple(outcome_values[index] for index in high_indexes) if bucket_reason is None else ()
    low_mean, high_mean = _mean(low), _mean(high)
    low_median, high_median = _median(low), _median(high)
    low_set, high_set = set(low_indexes), set(high_indexes)
    evidence = tuple(
        (
            symbol,
            factor_value,
            outcome_value,
            factor_ranks[index],
            outcome_ranks[index],
            percentiles[index],
            "LOW" if index in low_set else "HIGH" if index in high_set else "MIDDLE",
        )
        for index, (symbol, factor_value, outcome_value) in enumerate(eligible)
    )
    return PanelDailyFactorEvaluation(
        source_dataset_identity=source_identity,
        source_dataset_content_identity=source_content_identity,
        specification_fingerprint=spec.fingerprint,
        factor=factor,
        factor_direction=spec.direction_for(factor),
        horizon_sessions=horizon,
        outcome_field=outcome_field,
        outcome_column=outcome_column,
        signal_date=signal_date,
        total_observation_count=len(ordered),
        factor_available_count=factor_available,
        outcome_available_count=outcome_available,
        factor_usable_count=factor_usable,
        outcome_usable_count=outcome_usable,
        pairwise_finite_eligible_count=len(eligible),
        excluded_for_factor_count=excluded_factor,
        excluded_for_outcome_count=excluded_outcome,
        excluded_for_both_count=excluded_both,
        factor_status_counts=MappingProxyType(dict(factor_status_counts)),
        outcome_status_counts=MappingProxyType(dict(outcome_status_counts)),
        factor_nonfinite_available_count=factor_nonfinite,
        outcome_nonfinite_available_count=outcome_nonfinite,
        factor_unique_count=factor_unique,
        outcome_unique_count=outcome_unique,
        rank_ic=rank_ic,
        ic_undefined_reason=ic_reason,
        low_bucket_count=len(low),
        high_bucket_count=len(high),
        low_bucket_mean_outcome=low_mean,
        low_bucket_median_outcome=low_median,
        high_bucket_mean_outcome=high_mean,
        high_bucket_median_outcome=high_median,
        high_minus_low_mean_spread=(
            None if low_mean is None or high_mean is None else high_mean - low_mean
        ),
        high_minus_low_median_spread=(
            None if low_median is None or high_median is None else high_median - low_median
        ),
        bucket_undefined_reason=bucket_reason,
        observation_status_evidence=tuple(status_evidence),
        eligible_evidence=evidence,
    )


def _summary(items: tuple[PanelDailyFactorEvaluation, ...]) -> PanelFactorHorizonSummary:
    if not items:
        raise ValueError("summary requires daily records")
    first = items[0]
    ic_values = tuple(item.rank_ic for item in items if item.rank_ic is not None)
    bucket_items = tuple(item for item in items if item.bucket_undefined_reason is None)
    mean_spreads = tuple(
        item.high_minus_low_mean_spread for item in bucket_items
        if item.high_minus_low_mean_spread is not None
    )
    median_spreads = tuple(
        item.high_minus_low_median_spread for item in bucket_items
        if item.high_minus_low_median_spread is not None
    )
    total_dates = len(items)
    total_observations = sum(item.total_observation_count for item in items)
    total_eligible = sum(item.pairwise_finite_eligible_count for item in items)
    warnings: list[str] = [
        "descriptive_only_does_not_establish_tradability_or_causality",
        "overlapping_forward_horizons_no_significance_claim",
    ]
    if not ic_values:
        warnings.append("rank_ic_undefined_for_all_signal_dates")
    elif len(ic_values) / total_dates < 0.5:
        warnings.append("sparse_rank_ic_coverage_below_50_pct")
    if not bucket_items:
        warnings.append("bucket_spread_undefined_for_all_signal_dates")
    elif len(bucket_items) / total_dates < 0.5:
        warnings.append("sparse_bucket_coverage_below_50_pct")
    factor_available = sum(item.factor_available_count for item in items)
    outcome_available = sum(item.outcome_available_count for item in items)
    factor_coverage = None if total_observations == 0 else factor_available / total_observations * 100.0
    outcome_coverage = None if total_observations == 0 else outcome_available / total_observations * 100.0
    if factor_coverage is not None and factor_coverage < 50.0:
        warnings.append("sparse_factor_availability_below_50_pct")
    if outcome_coverage is not None and outcome_coverage < 50.0:
        warnings.append("sparse_outcome_availability_below_50_pct")
    return PanelFactorHorizonSummary(
        factor=first.factor,
        factor_direction=first.factor_direction,
        horizon_sessions=first.horizon_sessions,
        outcome_field=first.outcome_field,
        total_signal_dates=total_dates,
        dates_with_any_pairwise_finite_observations=sum(
            item.pairwise_finite_eligible_count > 0 for item in items
        ),
        ic_defined_date_count=len(ic_values),
        ic_coverage_pct=len(ic_values) / total_dates * 100.0,
        mean_daily_rank_ic=_mean(ic_values),
        median_daily_rank_ic=_median(ic_values),
        population_std_daily_ic=_population_std(ic_values),
        positive_ic_date_count=sum(item > 0 for item in ic_values),
        zero_ic_date_count=sum(item == 0 for item in ic_values),
        negative_ic_date_count=sum(item < 0 for item in ic_values),
        positive_ic_rate=None if not ic_values else sum(item > 0 for item in ic_values) / len(ic_values),
        bucket_defined_date_count=len(bucket_items),
        bucket_coverage_pct=len(bucket_items) / total_dates * 100.0,
        mean_daily_high_minus_low_mean_spread=_mean(mean_spreads),
        median_daily_high_minus_low_mean_spread=_median(mean_spreads),
        mean_daily_high_minus_low_median_spread=_mean(median_spreads),
        median_daily_high_minus_low_median_spread=_median(median_spreads),
        positive_mean_spread_date_count=sum(item > 0 for item in mean_spreads),
        zero_mean_spread_date_count=sum(item == 0 for item in mean_spreads),
        negative_mean_spread_date_count=sum(item < 0 for item in mean_spreads),
        positive_mean_spread_rate=(
            None if not mean_spreads else sum(item > 0 for item in mean_spreads) / len(mean_spreads)
        ),
        average_low_bucket_size=_mean(tuple(float(item.low_bucket_count) for item in bucket_items)),
        average_high_bucket_size=_mean(tuple(float(item.high_bucket_count) for item in bucket_items)),
        total_eligible_observations=total_eligible,
        average_eligible_cross_section_size=total_eligible / total_dates,
        factor_availability_coverage_pct=factor_coverage,
        outcome_availability_coverage_pct=outcome_coverage,
        warnings=tuple(warnings),
        included_daily_identities=tuple(item.identity for item in items),
    )


def _empty_summary(
    spec: PanelFactorEvaluationSpec, factor: str, horizon: int, outcome_field: str,
) -> PanelFactorHorizonSummary:
    return PanelFactorHorizonSummary(
        factor=factor,
        factor_direction=spec.direction_for(factor),
        horizon_sessions=horizon,
        outcome_field=outcome_field,
        total_signal_dates=0,
        dates_with_any_pairwise_finite_observations=0,
        ic_defined_date_count=0,
        ic_coverage_pct=0.0,
        mean_daily_rank_ic=None,
        median_daily_rank_ic=None,
        population_std_daily_ic=None,
        positive_ic_date_count=0,
        zero_ic_date_count=0,
        negative_ic_date_count=0,
        positive_ic_rate=None,
        bucket_defined_date_count=0,
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
        total_eligible_observations=0,
        average_eligible_cross_section_size=None,
        factor_availability_coverage_pct=None,
        outcome_availability_coverage_pct=None,
        warnings=(
            "descriptive_only_does_not_establish_tradability_or_causality",
            "empty_point_in_time_observation_population",
            "rank_ic_undefined_for_all_signal_dates",
            "bucket_spread_undefined_for_all_signal_dates",
        ),
        included_daily_identities=(),
    )


def evaluate_point_in_time_panel_factors(
    dataset: object,
    spec: PanelFactorEvaluationSpec = NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1,
) -> PointInTimePanelFactorEvaluationResult:
    """Evaluate neutral factor relationships independently within each signal date."""
    frame, _dataset_spec = _validated_dataset(dataset, spec)
    signal_dates = tuple(sorted(set(frame["session_date"].astype(str))))
    by_date = {
        signal_date: frame.loc[frame["session_date"].eq(signal_date)].copy(deep=True)
        for signal_date in signal_dates
    }
    daily = tuple(
        _daily(
            by_date[signal_date],
            source_identity=dataset.identity,
            source_content_identity=dataset.content_identity,
            spec=spec,
            factor=factor,
            horizon=horizon,
            outcome_field=outcome_field,
            signal_date=signal_date,
        )
        for factor in spec.factors
        for horizon in spec.horizons
        for outcome_field in spec.outcome_fields
        for signal_date in signal_dates
    )
    daily_by_key = {
        (item.factor, item.horizon_sessions, item.outcome_field, item.signal_date): item
        for item in daily
    }
    summaries = tuple(
        (
            _summary(tuple(
                daily_by_key[(factor, horizon, outcome_field, signal_date)]
                for signal_date in signal_dates
            ))
            if signal_dates else _empty_summary(spec, factor, horizon, outcome_field)
        )
        for factor in spec.factors
        for horizon in spec.horizons
        for outcome_field in spec.outcome_fields
    )
    universe_mode = str(dataset.metadata.get("universe_mode", "supplied_point_in_time_universe_contract"))
    source_claims_historical_vn100 = bool(
        dataset.metadata.get("point_in_time_historical_vn100_membership", False)
    )
    universe_interpretation = (
        "source contract explicitly identifies point-in-time historical VN100 membership"
        if source_claims_historical_vn100
        else (
            "database coverage, not historical VN100"
            if universe_mode == "database_coverage"
            else "supplied point-in-time universe contract; no historical VN100 claim"
        )
    )
    metadata = MappingProxyType({
        "population": "complete Phase 5.1 point-in-time observation population",
        "candidate_or_strategy_acceptance_filter_applied": False,
        "universe_contract": universe_mode,
        "universe_interpretation": universe_interpretation,
        "historical_vn100_claim_from_source_contract": source_claims_historical_vn100,
        "outcomes_future_looking": True,
        "permitted_use": "offline research evaluation only",
        "daily_cross_sections_equal_weighted": True,
        "descriptive_only": True,
        "establishes_tradability_or_causality": False,
        "stock_and_excess_daily_rank_ic_note": (
            "same-date benchmark subtraction can make daily rank IC algebraically identical; "
            "bucket spreads may differ only through floating-point arithmetic"
        ),
    })
    return PointInTimePanelFactorEvaluationResult(
        source_dataset_identity=dataset.identity,
        source_dataset_content_identity=dataset.content_identity,
        specification_fingerprint=spec.fingerprint,
        daily_evaluations=daily,
        summaries=summaries,
        selection_bias_metadata=metadata,
    )
