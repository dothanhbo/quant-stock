from __future__ import annotations

"""Immutable contracts for neutral point-in-time panel factor evaluation."""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import math
from types import MappingProxyType
from typing import Any, Mapping

from quantlab.identity import canonical_identity_value, canonical_json


PANEL_FACTOR_EVALUATION_CONTRACT = "quantlab.point_in_time_panel_factor_evaluation"
PANEL_FACTOR_EVALUATION_VERSION = "v1"
AVERAGE_RANK_METHOD = "ascending_average_ranks_for_ties_v1"
RANK_IC_METHOD = "pearson_correlation_of_same_date_pairwise_finite_average_ranks_v1"
PERCENTILE_METHOD = "average_rank_minus_one_over_n_minus_one_v1"
AGGREGATION_METHOD = "equal_signal_date_weight_no_cross_date_pooling_v1"
ELIGIBILITY_RULE = "available_status_and_finite_factor_and_outcome_v1"
POPULATION_RESTRICTION = "complete_phase_5_1_point_in_time_observation_population_v1"
UNDEFINED_REASONS = (
    "fewer_than_minimum_pairwise_finite_observations",
    "constant_factor",
    "constant_outcome",
    "constant_factor_and_outcome",
    "correlation_denominator_zero",
    "empty_low_bucket",
    "empty_high_bucket",
    "overlapping_buckets",
)

BUILTIN_FACTOR_FIELDS = (
    "atr_percent_14",
    "rsi_14",
    "adx_14",
    "volume_ratio_20",
    "stock_return_20d_pct",
    "relative_strength_20d_pct_points",
    "ema20_distance_pct",
    "return_3d_pct",
)
SUPPORTED_OUTCOME_FIELDS = (
    "stock_forward_return_pct",
    "excess_forward_return_pct_points",
)
SUPPORTED_HORIZONS = (5, 10, 20)


class PanelFactorDirection(str, Enum):
    HIGHER_IS_BETTER = "HIGHER_IS_BETTER"
    LOWER_IS_BETTER = "LOWER_IS_BETTER"
    UNSPECIFIED = "UNSPECIFIED"


def _nonempty(value: Any, *, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{name} must be a non-empty string")
    return text


def _optional_finite(value: float | None, *, name: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite or None")
    return number


def _counts(values: Mapping[str, int], *, name: str) -> Mapping[str, int]:
    normalized = {str(key): int(value) for key, value in sorted(values.items())}
    if any(not key or value < 0 for key, value in normalized.items()):
        raise ValueError(f"{name} must contain non-negative counts with non-empty keys")
    return MappingProxyType(normalized)


def _frozen_identity_value(value: Any) -> Any:
    normalized = canonical_identity_value(value)
    if isinstance(normalized, dict):
        return MappingProxyType({
            key: _frozen_identity_value(item) for key, item in sorted(normalized.items())
        })
    if isinstance(normalized, list):
        return tuple(_frozen_identity_value(item) for item in normalized)
    return normalized


@dataclass(frozen=True, slots=True)
class PanelFactorEvaluationSpec:
    name: str
    version: str
    factors: tuple[str, ...]
    factor_directions: tuple[PanelFactorDirection, ...] | Mapping[str, PanelFactorDirection]
    horizons: tuple[int, ...]
    outcome_fields: tuple[str, ...]
    minimum_cross_section_size: int = 5
    low_bucket_max_percentile: float = 0.30
    high_bucket_min_percentile: float = 0.70
    rank_method: str = AVERAGE_RANK_METHOD
    rank_ic_method: str = RANK_IC_METHOD
    percentile_method: str = PERCENTILE_METHOD
    aggregation_method: str = AGGREGATION_METHOD
    eligibility_rule: str = ELIGIBILITY_RULE
    population_restriction: str = POPULATION_RESTRICTION
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        name = _nonempty(self.name, name="evaluation spec name")
        version = _nonempty(self.version, name="evaluation spec version")
        factors = tuple(str(item).strip() for item in self.factors)
        raw_directions = self.factor_directions
        if isinstance(raw_directions, Mapping):
            if set(raw_directions) != set(factors):
                raise ValueError("factor direction mapping must contain exactly the configured factors")
            directions = tuple(raw_directions[factor] for factor in factors)
        else:
            directions = tuple(raw_directions)
        horizons = tuple(self.horizons)
        outcomes = tuple(str(item).strip() for item in self.outcome_fields)
        if not factors or any(not item for item in factors) or len(set(factors)) != len(factors):
            raise ValueError("evaluation factors must be non-empty and unique")
        unsupported = tuple(item for item in factors if item not in BUILTIN_FACTOR_FIELDS)
        if unsupported:
            raise ValueError("forbidden or unknown evaluation factor: " + ", ".join(unsupported))
        if len(directions) != len(factors) or any(
            not isinstance(item, PanelFactorDirection) for item in directions
        ):
            raise ValueError("factor_directions must align with factors and use PanelFactorDirection")
        if (
            not horizons
            or len(set(horizons)) != len(horizons)
            or any(isinstance(item, bool) or not isinstance(item, int) for item in horizons)
            or any(item not in SUPPORTED_HORIZONS for item in horizons)
        ):
            raise ValueError("unsupported evaluation horizon")
        if not outcomes or len(set(outcomes)) != len(outcomes):
            raise ValueError("outcome fields must be non-empty and unique")
        unsupported_outcomes = tuple(item for item in outcomes if item not in SUPPORTED_OUTCOME_FIELDS)
        if unsupported_outcomes:
            raise ValueError("unsupported outcome field: " + ", ".join(unsupported_outcomes))
        if (
            isinstance(self.minimum_cross_section_size, bool)
            or not isinstance(self.minimum_cross_section_size, int)
            or self.minimum_cross_section_size <= 0
        ):
            raise ValueError("minimum_cross_section_size must be a positive integer")
        low, high = float(self.low_bucket_max_percentile), float(self.high_bucket_min_percentile)
        if not math.isfinite(low) or not math.isfinite(high) or not 0.0 <= low <= 1.0 or not 0.0 <= high <= 1.0:
            raise ValueError("bucket cutoffs must be finite and within [0, 1]")
        if low >= high:
            raise ValueError("bucket cutoffs overlap or leave no ordered low/high boundary")
        expected_methods = (
            AVERAGE_RANK_METHOD,
            RANK_IC_METHOD,
            PERCENTILE_METHOD,
            AGGREGATION_METHOD,
            ELIGIBILITY_RULE,
            POPULATION_RESTRICTION,
        )
        actual_methods = (
            self.rank_method,
            self.rank_ic_method,
            self.percentile_method,
            self.aggregation_method,
            self.eligibility_rule,
            self.population_restriction,
        )
        if actual_methods != expected_methods:
            raise ValueError("unsupported panel factor evaluation method contract")
        payload = {
            "contract": {
                "name": PANEL_FACTOR_EVALUATION_CONTRACT,
                "version": PANEL_FACTOR_EVALUATION_VERSION,
            },
            "name": name,
            "version": version,
            "factors": [
                {"field": factor, "direction": direction.value}
                for factor, direction in zip(factors, directions, strict=True)
            ],
            "horizons": list(horizons),
            "outcome_fields": list(outcomes),
            "minimum_cross_section_size": self.minimum_cross_section_size,
            "ranking_and_tie_method": self.rank_method,
            "rank_ic_method": self.rank_ic_method,
            "bucket_percentile_formula": self.percentile_method,
            "low_bucket_max_percentile": low,
            "high_bucket_min_percentile": high,
            "aggregation_method": self.aggregation_method,
            "eligibility_rule": self.eligibility_rule,
            "undefined_reason_contract": list(UNDEFINED_REASONS),
            "population_restriction": self.population_restriction,
        }
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "factors", factors)
        object.__setattr__(self, "factor_directions", directions)
        object.__setattr__(self, "horizons", horizons)
        object.__setattr__(self, "outcome_fields", outcomes)
        object.__setattr__(self, "low_bucket_max_percentile", low)
        object.__setattr__(self, "high_bucket_min_percentile", high)
        object.__setattr__(self, "fingerprint", sha256(canonical_json(payload)).hexdigest())

    def direction_for(self, factor: str) -> PanelFactorDirection:
        try:
            return self.factor_directions[self.factors.index(factor)]
        except ValueError as exc:
            raise KeyError(f"unknown factor: {factor}") from exc


NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1 = PanelFactorEvaluationSpec(
    name="NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1",
    version="1",
    factors=BUILTIN_FACTOR_FIELDS,
    factor_directions=(PanelFactorDirection.UNSPECIFIED,) * len(BUILTIN_FACTOR_FIELDS),
    horizons=SUPPORTED_HORIZONS,
    outcome_fields=SUPPORTED_OUTCOME_FIELDS,
)


@dataclass(frozen=True, slots=True)
class PanelDailyFactorEvaluation:
    source_dataset_identity: str
    source_dataset_content_identity: str
    specification_fingerprint: str
    factor: str
    factor_direction: PanelFactorDirection
    horizon_sessions: int
    outcome_field: str
    outcome_column: str
    signal_date: str
    total_observation_count: int
    factor_available_count: int
    outcome_available_count: int
    factor_usable_count: int
    outcome_usable_count: int
    pairwise_finite_eligible_count: int
    excluded_for_factor_count: int
    excluded_for_outcome_count: int
    excluded_for_both_count: int
    factor_status_counts: Mapping[str, int]
    outcome_status_counts: Mapping[str, int]
    factor_nonfinite_available_count: int
    outcome_nonfinite_available_count: int
    factor_unique_count: int
    outcome_unique_count: int
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
    observation_status_evidence: tuple[tuple[Any, ...], ...] = field(repr=False)
    eligible_evidence: tuple[tuple[Any, ...], ...] = field(repr=False)
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "source_dataset_identity", "source_dataset_content_identity",
            "specification_fingerprint", "factor", "outcome_field", "outcome_column",
            "signal_date",
        ):
            object.__setattr__(self, name, _nonempty(getattr(self, name), name=name))
        if not isinstance(self.factor_direction, PanelFactorDirection):
            raise TypeError("factor_direction must be PanelFactorDirection")
        count_names = (
            "total_observation_count", "factor_available_count", "outcome_available_count",
            "factor_usable_count", "outcome_usable_count", "pairwise_finite_eligible_count",
            "excluded_for_factor_count", "excluded_for_outcome_count", "excluded_for_both_count",
            "factor_nonfinite_available_count", "outcome_nonfinite_available_count",
            "factor_unique_count", "outcome_unique_count", "low_bucket_count", "high_bucket_count",
        )
        if any(isinstance(getattr(self, name), bool) or int(getattr(self, name)) < 0 for name in count_names):
            raise ValueError("daily evaluation counts must be non-negative integers")
        if (
            self.pairwise_finite_eligible_count
            + self.excluded_for_factor_count
            + self.excluded_for_outcome_count
            + self.excluded_for_both_count
            != self.total_observation_count
        ):
            raise ValueError("daily eligibility categories do not reconcile")
        factor_status = _counts(self.factor_status_counts, name="factor_status_counts")
        outcome_status = _counts(self.outcome_status_counts, name="outcome_status_counts")
        if sum(factor_status.values()) != self.total_observation_count:
            raise ValueError("factor status counts do not reconcile")
        if sum(outcome_status.values()) != self.total_observation_count:
            raise ValueError("outcome status counts do not reconcile")
        if factor_status.get("AVAILABLE", 0) != self.factor_available_count:
            raise ValueError("factor available count does not reconcile")
        if outcome_status.get("AVAILABLE", 0) != self.outcome_available_count:
            raise ValueError("outcome available count does not reconcile")
        if len(self.eligible_evidence) != self.pairwise_finite_eligible_count:
            raise ValueError("eligible evidence does not reconcile")
        if len(self.observation_status_evidence) != self.total_observation_count:
            raise ValueError("observation status evidence does not reconcile")
        optional_names = (
            "rank_ic", "low_bucket_mean_outcome", "low_bucket_median_outcome",
            "high_bucket_mean_outcome", "high_bucket_median_outcome",
            "high_minus_low_mean_spread", "high_minus_low_median_spread",
        )
        optional = {
            name: _optional_finite(getattr(self, name), name=name) for name in optional_names
        }
        if self.ic_undefined_reason is not None and self.ic_undefined_reason not in UNDEFINED_REASONS:
            raise ValueError("unsupported IC undefined reason")
        if self.bucket_undefined_reason is not None and self.bucket_undefined_reason not in UNDEFINED_REASONS:
            raise ValueError("unsupported bucket undefined reason")
        if (self.rank_ic is None) == (self.ic_undefined_reason is None):
            raise ValueError("rank IC and its undefined reason are inconsistent")
        if (self.high_minus_low_mean_spread is None) == (self.bucket_undefined_reason is None):
            raise ValueError("bucket spread and its undefined reason are inconsistent")
        status_evidence = tuple(tuple(item) for item in self.observation_status_evidence)
        evidence = tuple(tuple(item) for item in self.eligible_evidence)
        content = {
            "source_dataset_identity": self.source_dataset_identity,
            "source_dataset_content_identity": self.source_dataset_content_identity,
            "specification_fingerprint": self.specification_fingerprint,
            "factor": self.factor,
            "factor_direction": self.factor_direction.value,
            "horizon_sessions": self.horizon_sessions,
            "outcome_field": self.outcome_field,
            "outcome_column": self.outcome_column,
            "signal_date": self.signal_date,
            "eligibility_counts": {name: int(getattr(self, name)) for name in count_names[:13]},
            "factor_nonfinite_available_count": self.factor_nonfinite_available_count,
            "outcome_nonfinite_available_count": self.outcome_nonfinite_available_count,
            "factor_status_counts": factor_status,
            "outcome_status_counts": outcome_status,
            "factor_unique_count": self.factor_unique_count,
            "outcome_unique_count": self.outcome_unique_count,
            "rank_ic": optional["rank_ic"],
            "ic_undefined_reason": self.ic_undefined_reason,
            "bucket_counts": {"low": self.low_bucket_count, "high": self.high_bucket_count},
            "bucket_statistics": {name: optional[name] for name in optional_names[1:]},
            "bucket_undefined_reason": self.bucket_undefined_reason,
            "ordered_observation_status_evidence": status_evidence,
            "ordered_eligible_symbol_value_rank_evidence": evidence,
        }
        for name, value in optional.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "factor_status_counts", factor_status)
        object.__setattr__(self, "outcome_status_counts", outcome_status)
        object.__setattr__(self, "observation_status_evidence", status_evidence)
        object.__setattr__(self, "eligible_evidence", evidence)
        object.__setattr__(self, "identity", sha256(canonical_json(canonical_identity_value(content))).hexdigest())


@dataclass(frozen=True, slots=True)
class PanelFactorHorizonSummary:
    factor: str
    factor_direction: PanelFactorDirection
    horizon_sessions: int
    outcome_field: str
    total_signal_dates: int
    dates_with_any_pairwise_finite_observations: int
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
    positive_mean_spread_date_count: int
    zero_mean_spread_date_count: int
    negative_mean_spread_date_count: int
    positive_mean_spread_rate: float | None
    average_low_bucket_size: float | None
    average_high_bucket_size: float | None
    total_eligible_observations: int
    average_eligible_cross_section_size: float | None
    factor_availability_coverage_pct: float | None
    outcome_availability_coverage_pct: float | None
    warnings: tuple[str, ...]
    included_daily_identities: tuple[str, ...]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.factor_direction, PanelFactorDirection):
            raise TypeError("factor_direction must be PanelFactorDirection")
        identities = tuple(_nonempty(item, name="daily identity") for item in self.included_daily_identities)
        warnings = tuple(_nonempty(item, name="summary warning") for item in self.warnings)
        if len(set(identities)) != len(identities):
            raise ValueError("summary includes duplicate daily identities")
        optional_names = (
            "mean_daily_rank_ic", "median_daily_rank_ic", "population_std_daily_ic",
            "positive_ic_rate", "mean_daily_high_minus_low_mean_spread",
            "median_daily_high_minus_low_mean_spread",
            "mean_daily_high_minus_low_median_spread",
            "median_daily_high_minus_low_median_spread", "positive_mean_spread_rate",
            "average_low_bucket_size", "average_high_bucket_size",
            "average_eligible_cross_section_size", "factor_availability_coverage_pct",
            "outcome_availability_coverage_pct",
        )
        optional = {
            name: _optional_finite(getattr(self, name), name=name) for name in optional_names
        }
        content = {
            name: canonical_identity_value(getattr(self, name))
            for name in self.__dataclass_fields__
            if name != "identity" and name not in optional_names
        }
        content.update(optional)
        content["warnings"] = list(warnings)
        content["included_daily_identities"] = list(identities)
        for name, value in optional.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(self, "included_daily_identities", identities)
        object.__setattr__(self, "identity", sha256(canonical_json(canonical_identity_value(content))).hexdigest())


@dataclass(frozen=True, slots=True)
class PointInTimePanelFactorEvaluationResult:
    source_dataset_identity: str
    source_dataset_content_identity: str
    specification_fingerprint: str
    daily_evaluations: tuple[PanelDailyFactorEvaluation, ...]
    summaries: tuple[PanelFactorHorizonSummary, ...]
    selection_bias_metadata: Mapping[str, Any]
    contract_name: str = PANEL_FACTOR_EVALUATION_CONTRACT
    contract_version: str = PANEL_FACTOR_EVALUATION_VERSION
    identity: str = field(init=False)
    _daily_by_key: Mapping[tuple[str, int, str, str], PanelDailyFactorEvaluation] = field(init=False, repr=False, compare=False)
    _summary_by_key: Mapping[tuple[str, int, str], PanelFactorHorizonSummary] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (self.contract_name, self.contract_version) != (
            PANEL_FACTOR_EVALUATION_CONTRACT, PANEL_FACTOR_EVALUATION_VERSION,
        ):
            raise ValueError("unsupported panel factor evaluation result contract")
        source_identity = _nonempty(self.source_dataset_identity, name="source dataset identity")
        source_content = _nonempty(
            self.source_dataset_content_identity, name="source dataset content identity",
        )
        fingerprint = _nonempty(self.specification_fingerprint, name="specification fingerprint")
        daily, summaries = tuple(self.daily_evaluations), tuple(self.summaries)
        daily_map = {
            (item.factor, item.horizon_sessions, item.outcome_field, item.signal_date): item
            for item in daily
        }
        summary_map = {
            (item.factor, item.horizon_sessions, item.outcome_field): item for item in summaries
        }
        if len(daily_map) != len(daily):
            raise ValueError("duplicate daily factor evaluation key")
        if len(summary_map) != len(summaries):
            raise ValueError("duplicate factor/horizon/outcome summary key")
        if any(
            item.source_dataset_identity != source_identity
            or item.source_dataset_content_identity != source_content
            or item.specification_fingerprint != fingerprint
            for item in daily
        ):
            raise ValueError("daily evaluation provenance does not match its result")
        for key, summary in summary_map.items():
            expected = tuple(
                item.identity for item in daily
                if (item.factor, item.horizon_sessions, item.outcome_field) == key
            )
            if summary.included_daily_identities != expected:
                raise ValueError("summary daily identities do not match ordered result records")
        metadata = MappingProxyType({
            str(key): _frozen_identity_value(value)
            for key, value in sorted(self.selection_bias_metadata.items())
        })
        payload = {
            "contract": {"name": self.contract_name, "version": self.contract_version},
            "source_dataset_identity": source_identity,
            "source_dataset_content_identity": source_content,
            "specification_fingerprint": fingerprint,
            "daily_identities": [item.identity for item in daily],
            "summary_identities": [item.identity for item in summaries],
            "selection_bias_metadata": metadata,
        }
        object.__setattr__(self, "source_dataset_identity", source_identity)
        object.__setattr__(self, "source_dataset_content_identity", source_content)
        object.__setattr__(self, "specification_fingerprint", fingerprint)
        object.__setattr__(self, "daily_evaluations", daily)
        object.__setattr__(self, "summaries", summaries)
        object.__setattr__(self, "selection_bias_metadata", metadata)
        object.__setattr__(self, "_daily_by_key", MappingProxyType(daily_map))
        object.__setattr__(self, "_summary_by_key", MappingProxyType(summary_map))
        object.__setattr__(self, "identity", sha256(canonical_json(canonical_identity_value(payload))).hexdigest())

    def daily_for(
        self, factor: str, horizon_sessions: int, outcome_field: str, signal_date: str,
    ) -> PanelDailyFactorEvaluation:
        try:
            return self._daily_by_key[(factor, horizon_sessions, outcome_field, signal_date)]
        except KeyError as exc:
            raise KeyError(
                f"unknown daily factor evaluation: {factor}/{horizon_sessions}/{outcome_field}/{signal_date}"
            ) from exc

    def summary_for(
        self, factor: str, horizon_sessions: int, outcome_field: str,
    ) -> PanelFactorHorizonSummary:
        try:
            return self._summary_by_key[(factor, horizon_sessions, outcome_field)]
        except KeyError as exc:
            raise KeyError(
                f"unknown factor/horizon/outcome summary: {factor}/{horizon_sessions}/{outcome_field}"
            ) from exc
