from __future__ import annotations

"""Immutable descriptive contracts for candidate-factor forward outcomes."""

from dataclasses import dataclass, field
from hashlib import sha256
import math
from types import MappingProxyType
from typing import Any, Mapping

from quantlab.identity import canonical_identity_value, canonical_json
from quantlab.outcomes.contracts import ForwardOutcomeStatus


EVALUATION_CONTRACT_NAME = "quantlab.candidate_factor_outcomes"
EVALUATION_CONTRACT_VERSION = "v1"
AVERAGE_RANK_METHOD = "ascending_average_ranks_for_ties_v1"
RANK_IC_METHOD = "pearson_correlation_of_pairwise_finite_average_ranks_v1"
PERCENTILE_METHOD = "average_rank_minus_one_over_n_minus_one_v1"
BUCKET_SPREAD_METHOD = "high_minus_low_v1"
DESCRIPTIVE_WARNING = "descriptive_only_overlapping_forward_horizons_no_significance_claim"

SUPPORTED_FACTORS = frozenset({
    "quality_score",
    "score",
    "relative_strength_20d",
    "adx",
    "atr_percent",
    "rsi14",
    "volume_ratio",
    "breadth_ema50_pct",
    "breadth_ema50_change_10d",
})
SUPPORTED_OUTCOME_FIELDS = frozenset({
    "stock_forward_return_pct",
    "excess_forward_return_pct_points",
})


def _optional_finite(value: float | None, *, name: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite or None")
    return number


@dataclass(frozen=True, slots=True)
class FactorOutcomeEvaluationSpec:
    name: str
    version: str
    factors: tuple[str, ...]
    horizons: tuple[int, ...]
    outcome_fields: tuple[str, ...]
    minimum_cross_section_size: int
    low_bucket_max_percentile: float
    high_bucket_min_percentile: float
    rank_method: str = AVERAGE_RANK_METHOD
    rank_ic_method: str = RANK_IC_METHOD
    percentile_method: str = PERCENTILE_METHOD
    bucket_spread_method: str = BUCKET_SPREAD_METHOD
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        name, version = str(self.name).strip(), str(self.version).strip()
        factors = tuple(str(item).strip() for item in self.factors)
        outcomes = tuple(str(item).strip() for item in self.outcome_fields)
        raw_horizons = tuple(self.horizons)
        if not name or not version:
            raise ValueError("evaluation spec name and version are required")
        if not factors or len(set(factors)) != len(factors):
            raise ValueError("evaluation factors must be non-empty and unique")
        unsupported_factors = tuple(item for item in factors if item not in SUPPORTED_FACTORS)
        if unsupported_factors:
            raise ValueError("unsupported evaluation factor: " + ", ".join(unsupported_factors))
        if not outcomes or len(set(outcomes)) != len(outcomes):
            raise ValueError("evaluation outcome fields must be non-empty and unique")
        unsupported_outcomes = tuple(item for item in outcomes if item not in SUPPORTED_OUTCOME_FIELDS)
        if unsupported_outcomes:
            raise ValueError("unsupported outcome field: " + ", ".join(unsupported_outcomes))
        if not raw_horizons or len(set(raw_horizons)) != len(raw_horizons) or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in raw_horizons):
            raise ValueError("evaluation horizons must be unique positive integers")
        if isinstance(self.minimum_cross_section_size, bool) or not isinstance(self.minimum_cross_section_size, int) or self.minimum_cross_section_size < 2:
            raise ValueError("minimum_cross_section_size must be an integer of at least two")
        low, high = float(self.low_bucket_max_percentile), float(self.high_bucket_min_percentile)
        if not math.isfinite(low) or not 0.0 <= low <= 1.0:
            raise ValueError("low bucket cutoff must be within [0, 1]")
        if not math.isfinite(high) or not 0.0 <= high <= 1.0:
            raise ValueError("high bucket cutoff must be within [0, 1]")
        expected_methods = (AVERAGE_RANK_METHOD, RANK_IC_METHOD, PERCENTILE_METHOD, BUCKET_SPREAD_METHOD)
        if (self.rank_method, self.rank_ic_method, self.percentile_method, self.bucket_spread_method) != expected_methods:
            raise ValueError("unsupported factor-outcome evaluation method version")
        horizons = tuple(sorted(raw_horizons))
        payload = {
            "name": name,
            "version": version,
            "factors": list(factors),
            "horizons": list(horizons),
            "outcome_fields": list(outcomes),
            "minimum_cross_section_size": self.minimum_cross_section_size,
            "low_bucket_max_percentile": low,
            "high_bucket_min_percentile": high,
            "rank_method": self.rank_method,
            "rank_ic_method": self.rank_ic_method,
            "percentile_method": self.percentile_method,
            "bucket_spread_method": self.bucket_spread_method,
        }
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "factors", factors)
        object.__setattr__(self, "horizons", horizons)
        object.__setattr__(self, "outcome_fields", outcomes)
        object.__setattr__(self, "low_bucket_max_percentile", low)
        object.__setattr__(self, "high_bucket_min_percentile", high)
        object.__setattr__(self, "fingerprint", sha256(canonical_json(payload)).hexdigest())


FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1 = FactorOutcomeEvaluationSpec(
    name="FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1",
    version="1",
    factors=(
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
    horizons=(5, 10, 20),
    outcome_fields=("stock_forward_return_pct", "excess_forward_return_pct_points"),
    minimum_cross_section_size=5,
    low_bucket_max_percentile=.30,
    high_bucket_min_percentile=.70,
)


@dataclass(frozen=True, slots=True)
class DailyFactorOutcomeEvaluation:
    signal_date: str
    factor: str
    horizon_sessions: int
    outcome_field: str
    total_same_date_candidates: int
    available_labeled_candidates: int
    pairwise_finite_count: int
    factor_unique_count: int
    factor_constant: bool
    rank_ic: float | None
    ic_undefined_reason: str | None
    low_bucket_count: int
    high_bucket_count: int
    low_bucket_mean_outcome: float | None
    low_bucket_median_outcome: float | None
    high_bucket_mean_outcome: float | None
    high_bucket_median_outcome: float | None
    high_minus_low_mean_spread: float | None
    high_minus_low_median_spread: float | None
    bucket_undefined_reason: str | None
    _joined_evidence: tuple[tuple[Any, ...], ...] = field(repr=False, compare=False)
    joined_input_identity: str = field(init=False)
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        optional_names = (
            "rank_ic", "low_bucket_mean_outcome", "low_bucket_median_outcome",
            "high_bucket_mean_outcome", "high_bucket_median_outcome",
            "high_minus_low_mean_spread", "high_minus_low_median_spread",
        )
        numeric = {name: _optional_finite(getattr(self, name), name=name) for name in optional_names}
        evidence = tuple(tuple(item) for item in self._joined_evidence)
        joined_identity = sha256(canonical_json(canonical_identity_value(evidence))).hexdigest()
        content = {
            "signal_date": self.signal_date,
            "factor": self.factor,
            "horizon_sessions": self.horizon_sessions,
            "outcome_field": self.outcome_field,
            "total_same_date_candidates": self.total_same_date_candidates,
            "available_labeled_candidates": self.available_labeled_candidates,
            "pairwise_finite_count": self.pairwise_finite_count,
            "factor_unique_count": self.factor_unique_count,
            "factor_constant": self.factor_constant,
            **numeric,
            "ic_undefined_reason": self.ic_undefined_reason,
            "low_bucket_count": self.low_bucket_count,
            "high_bucket_count": self.high_bucket_count,
            "bucket_undefined_reason": self.bucket_undefined_reason,
            "joined_input_identity": joined_identity,
        }
        for name, value in numeric.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "_joined_evidence", evidence)
        object.__setattr__(self, "joined_input_identity", joined_identity)
        object.__setattr__(self, "identity", sha256(canonical_json(canonical_identity_value(content))).hexdigest())


@dataclass(frozen=True, slots=True)
class FactorHorizonEvaluationSummary:
    factor: str
    horizon_sessions: int
    outcome_field: str
    total_signal_dates: int
    dates_with_available_labels: int
    ic_defined_date_count: int
    ic_coverage_pct: float
    mean_daily_rank_ic: float | None
    median_daily_rank_ic: float | None
    population_std_daily_ic: float | None
    positive_ic_date_count: int
    zero_ic_date_count: int
    negative_ic_date_count: int
    positive_ic_rate: float | None
    bucket_defined_date_count: int
    bucket_coverage_pct: float
    mean_daily_high_minus_low_mean_spread: float | None
    median_daily_high_minus_low_mean_spread: float | None
    mean_daily_high_minus_low_median_spread: float | None
    median_daily_high_minus_low_median_spread: float | None
    positive_spread_date_count: int
    zero_spread_date_count: int
    negative_spread_date_count: int
    positive_spread_rate: float | None
    average_low_bucket_size: float | None
    average_high_bucket_size: float | None
    included_daily_identities: tuple[str, ...]
    warning: str = DESCRIPTIVE_WARNING
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        if self.warning != DESCRIPTIVE_WARNING:
            raise ValueError("factor-outcome summaries require the descriptive-only warning")
        optional_names = (
            "mean_daily_rank_ic", "median_daily_rank_ic", "population_std_daily_ic",
            "positive_ic_rate", "mean_daily_high_minus_low_mean_spread",
            "median_daily_high_minus_low_mean_spread",
            "mean_daily_high_minus_low_median_spread",
            "median_daily_high_minus_low_median_spread", "positive_spread_rate",
            "average_low_bucket_size", "average_high_bucket_size",
        )
        numeric = {name: _optional_finite(getattr(self, name), name=name) for name in optional_names}
        identities = tuple(self.included_daily_identities)
        content = {
            "factor": self.factor,
            "horizon_sessions": self.horizon_sessions,
            "outcome_field": self.outcome_field,
            "total_signal_dates": self.total_signal_dates,
            "dates_with_available_labels": self.dates_with_available_labels,
            "ic_defined_date_count": self.ic_defined_date_count,
            "ic_coverage_pct": float(self.ic_coverage_pct),
            **numeric,
            "positive_ic_date_count": self.positive_ic_date_count,
            "zero_ic_date_count": self.zero_ic_date_count,
            "negative_ic_date_count": self.negative_ic_date_count,
            "bucket_defined_date_count": self.bucket_defined_date_count,
            "bucket_coverage_pct": float(self.bucket_coverage_pct),
            "positive_spread_date_count": self.positive_spread_date_count,
            "zero_spread_date_count": self.zero_spread_date_count,
            "negative_spread_date_count": self.negative_spread_date_count,
            "included_daily_identities": list(identities),
            "warning": self.warning,
        }
        for name, value in numeric.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "ic_coverage_pct", float(self.ic_coverage_pct))
        object.__setattr__(self, "bucket_coverage_pct", float(self.bucket_coverage_pct))
        object.__setattr__(self, "included_daily_identities", identities)
        object.__setattr__(self, "identity", sha256(canonical_json(canonical_identity_value(content))).hexdigest())


@dataclass(frozen=True, slots=True)
class CandidateFactorOutcomeEvaluationResult:
    source_candidate_batch_identity: str
    source_outcome_set_identity: str
    evaluation_spec_fingerprint: str
    daily_evaluations: tuple[DailyFactorOutcomeEvaluation, ...]
    summaries: tuple[FactorHorizonEvaluationSummary, ...]
    status_counts_by_horizon: Mapping[int, Mapping[ForwardOutcomeStatus, int]]
    contract_name: str = EVALUATION_CONTRACT_NAME
    contract_version: str = EVALUATION_CONTRACT_VERSION
    result_identity: str = field(init=False)
    _summaries_by_key: Mapping[tuple[str, int, str], FactorHorizonEvaluationSummary] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (self.contract_name, self.contract_version) != (EVALUATION_CONTRACT_NAME, EVALUATION_CONTRACT_VERSION):
            raise ValueError("unsupported candidate-factor-outcome evaluation contract")
        daily, summaries = tuple(self.daily_evaluations), tuple(self.summaries)
        summary_map = {(item.factor, item.horizon_sessions, item.outcome_field): item for item in summaries}
        if len(summary_map) != len(summaries):
            raise ValueError("duplicate factor/horizon/outcome summary")
        statuses = MappingProxyType({
            int(horizon): MappingProxyType({status: int(count) for status, count in counts.items()})
            for horizon, counts in sorted(self.status_counts_by_horizon.items())
        })
        payload = {
            "contract": {"name": self.contract_name, "version": self.contract_version},
            "source_candidate_batch_identity": self.source_candidate_batch_identity,
            "source_outcome_set_identity": self.source_outcome_set_identity,
            "evaluation_spec_fingerprint": self.evaluation_spec_fingerprint,
            "daily_identities": [item.identity for item in daily],
            "summary_identities": [item.identity for item in summaries],
            "status_counts_by_horizon": {
                str(horizon): {status.value: counts.get(status, 0) for status in ForwardOutcomeStatus}
                for horizon, counts in statuses.items()
            },
        }
        object.__setattr__(self, "daily_evaluations", daily)
        object.__setattr__(self, "summaries", summaries)
        object.__setattr__(self, "status_counts_by_horizon", statuses)
        object.__setattr__(self, "_summaries_by_key", MappingProxyType(summary_map))
        object.__setattr__(self, "result_identity", sha256(canonical_json(payload)).hexdigest())

    def summary_for(self, factor: str, horizon_sessions: int, outcome_field: str) -> FactorHorizonEvaluationSummary:
        try:
            return self._summaries_by_key[(factor, horizon_sessions, outcome_field)]
        except KeyError as exc:
            raise KeyError(f"unknown factor/horizon/outcome summary: {factor}/{horizon_sessions}/{outcome_field}") from exc
