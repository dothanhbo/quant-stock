from __future__ import annotations

"""Immutable contracts for pre-registered descriptive temporal stability."""

from dataclasses import dataclass, field
from datetime import date, timedelta
from hashlib import sha256
import math
from types import MappingProxyType
from typing import Any, Mapping

from quantlab.identity import canonical_identity_value, canonical_json


TEMPORAL_STABILITY_CONTRACT = "quantlab.candidate_factor_temporal_stability"
TEMPORAL_STABILITY_CONTRACT_VERSION = "v1"
EQUAL_DATE_WEIGHT_METHOD = "equal_weight_per_defined_signal_date_v1"
MEDIAN_METHOD = "sorted_midpoint_median_v1"
POPULATION_STD_METHOD = "population_standard_deviation_ddof_0_v1"
CONCENTRATION_METHOD = "largest_absolute_block_mean_over_sum_absolute_block_means_v1"
BLOCK_ASSIGNMENT_METHOD = "inclusive_non_overlapping_calendar_blocks_v1"
DESCRIPTIVE_TEMPORAL_WARNING = (
    "descriptive_only_overlapping_forward_horizons_no_significance_or_production_claim"
)
RAW_EXCESS_NONINDEPENDENCE = (
    "raw_and_excess_daily_rank_ic_are_not_independent_evidence_"
    "because_same_date_benchmark_subtraction_preserves_ranks"
)

COVERAGE_FLAG_DEFINITION = (
    "at_least_three_blocks_each_with_at_least_minimum_defined_ic_dates_and_"
    "at_least_three_blocks_each_with_at_least_minimum_defined_spread_dates"
)
DIRECTIONAL_IC_FLAG_DEFINITION = (
    "coverage_sufficient_and_at_least_three_ic_eligible_blocks_have_strictly_positive_mean_ic"
)
DIRECTIONAL_SPREAD_FLAG_DEFINITION = (
    "coverage_sufficient_and_at_least_three_spread_eligible_blocks_have_"
    "strictly_positive_mean_high_minus_low_mean_spread"
)
TEMPORAL_SUPPORT_FLAG_DEFINITION = "both_directional_consistency_flags_are_true"


def _date_text(value: str, *, name: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an ISO date") from exc


def _optional_finite(value: float | None, *, name: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite or None")
    return number


@dataclass(frozen=True, slots=True)
class TemporalBlock:
    name: str
    start_date: str
    end_date: str

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not name:
            raise ValueError("temporal block name is required")
        start = _date_text(self.start_date, name="block start_date")
        end = _date_text(self.end_date, name="block end_date")
        if start > end:
            raise ValueError("temporal block start_date must not be after end_date")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "start_date", start)
        object.__setattr__(self, "end_date", end)


@dataclass(frozen=True, slots=True)
class FactorTemporalStabilitySpec:
    name: str
    version: str
    factors: tuple[str, ...]
    horizons: tuple[int, ...]
    outcome_fields: tuple[str, ...]
    blocks: tuple[TemporalBlock, ...]
    overall_start_date: str
    overall_end_date: str
    minimum_defined_dates_per_eligible_block: int = 20
    block_assignment_method: str = BLOCK_ASSIGNMENT_METHOD
    aggregation_method: str = EQUAL_DATE_WEIGHT_METHOD
    median_method: str = MEDIAN_METHOD
    population_std_method: str = POPULATION_STD_METHOD
    concentration_method: str = CONCENTRATION_METHOD
    coverage_flag_definition: str = COVERAGE_FLAG_DEFINITION
    directional_ic_flag_definition: str = DIRECTIONAL_IC_FLAG_DEFINITION
    directional_spread_flag_definition: str = DIRECTIONAL_SPREAD_FLAG_DEFINITION
    temporal_support_flag_definition: str = TEMPORAL_SUPPORT_FLAG_DEFINITION
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        name, version = str(self.name).strip(), str(self.version).strip()
        if not name or not version:
            raise ValueError("temporal stability spec name and version are required")
        factors = tuple(str(item).strip() for item in self.factors)
        outcomes = tuple(str(item).strip() for item in self.outcome_fields)
        horizons = tuple(self.horizons)
        blocks = tuple(self.blocks)
        if not factors or any(not item for item in factors) or len(set(factors)) != len(factors):
            raise ValueError("temporal stability factors must be non-empty and unique")
        if not outcomes or any(not item for item in outcomes) or len(set(outcomes)) != len(outcomes):
            raise ValueError("temporal stability outcome fields must be non-empty and unique")
        if (
            not horizons
            or len(set(horizons)) != len(horizons)
            or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in horizons)
        ):
            raise ValueError("temporal stability horizons must be unique positive integers")
        if not blocks or any(not isinstance(item, TemporalBlock) for item in blocks):
            raise ValueError("temporal stability blocks must be non-empty TemporalBlock values")
        if len({item.name for item in blocks}) != len(blocks):
            raise ValueError("temporal block names must be unique")
        overall_start = _date_text(self.overall_start_date, name="overall_start_date")
        overall_end = _date_text(self.overall_end_date, name="overall_end_date")
        if overall_start > overall_end:
            raise ValueError("overall_start_date must not be after overall_end_date")
        if blocks[0].start_date != overall_start or blocks[-1].end_date != overall_end:
            raise ValueError("temporal blocks must cover the declared overall boundary")
        for previous, current in zip(blocks, blocks[1:], strict=False):
            expected = (date.fromisoformat(previous.end_date) + timedelta(days=1)).isoformat()
            if current.start_date < expected:
                raise ValueError("temporal blocks must not overlap")
            if current.start_date > expected:
                raise ValueError("temporal blocks must not contain calendar gaps")
        minimum = self.minimum_defined_dates_per_eligible_block
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum <= 0:
            raise ValueError("minimum_defined_dates_per_eligible_block must be a positive integer")
        methods = (
            self.block_assignment_method, self.aggregation_method, self.median_method,
            self.population_std_method, self.concentration_method,
            self.coverage_flag_definition, self.directional_ic_flag_definition,
            self.directional_spread_flag_definition, self.temporal_support_flag_definition,
        )
        expected_methods = (
            BLOCK_ASSIGNMENT_METHOD, EQUAL_DATE_WEIGHT_METHOD, MEDIAN_METHOD,
            POPULATION_STD_METHOD, CONCENTRATION_METHOD,
            COVERAGE_FLAG_DEFINITION, DIRECTIONAL_IC_FLAG_DEFINITION,
            DIRECTIONAL_SPREAD_FLAG_DEFINITION, TEMPORAL_SUPPORT_FLAG_DEFINITION,
        )
        if methods != expected_methods:
            raise ValueError("unsupported temporal-stability method or descriptive flag definition")
        payload = {
            "contract": {
                "name": TEMPORAL_STABILITY_CONTRACT,
                "version": TEMPORAL_STABILITY_CONTRACT_VERSION,
            },
            "name": name,
            "version": version,
            "factors": list(factors),
            "horizons": list(horizons),
            "outcome_fields": list(outcomes),
            "blocks": [
                {"name": item.name, "start_date": item.start_date, "end_date": item.end_date}
                for item in blocks
            ],
            "overall_start_date": overall_start,
            "overall_end_date": overall_end,
            "minimum_defined_dates_per_eligible_block": minimum,
            "methods": {
                "block_assignment": self.block_assignment_method,
                "aggregation": self.aggregation_method,
                "median": self.median_method,
                "population_std": self.population_std_method,
                "concentration": self.concentration_method,
            },
            "descriptive_flag_definitions": {
                "coverage_sufficient_for_stability_review": self.coverage_flag_definition,
                "directionally_consistent_ic": self.directional_ic_flag_definition,
                "directionally_consistent_spread": self.directional_spread_flag_definition,
                "descriptive_temporal_support": self.temporal_support_flag_definition,
            },
        }
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "factors", factors)
        object.__setattr__(self, "horizons", horizons)
        object.__setattr__(self, "outcome_fields", outcomes)
        object.__setattr__(self, "blocks", blocks)
        object.__setattr__(self, "overall_start_date", overall_start)
        object.__setattr__(self, "overall_end_date", overall_end)
        object.__setattr__(self, "fingerprint", sha256(canonical_json(payload)).hexdigest())


FROZEN_Q70_VOLUME_RSI_TEMPORAL_STABILITY_V1 = FactorTemporalStabilitySpec(
    name="FROZEN_Q70_VOLUME_RSI_TEMPORAL_STABILITY_V1",
    version="1",
    factors=("volume_ratio", "rsi14"),
    horizons=(5, 10, 20),
    outcome_fields=("stock_forward_return_pct", "excess_forward_return_pct_points"),
    blocks=(
        TemporalBlock("early_2018_2020", "2018-08-07", "2020-12-31"),
        TemporalBlock("middle_2021_2022", "2021-01-01", "2022-12-31"),
        TemporalBlock("middle_2023_2024", "2023-01-01", "2024-12-31"),
        TemporalBlock("recent_2025_2026", "2025-01-01", "2026-09-17"),
    ),
    overall_start_date="2018-08-07",
    overall_end_date="2026-09-17",
    minimum_defined_dates_per_eligible_block=20,
)


@dataclass(frozen=True, slots=True)
class FactorBlockStabilityResult:
    source_evaluation_identity: str
    specification_fingerprint: str
    factor: str
    horizon_sessions: int
    outcome_field: str
    block_name: str
    block_start_date: str
    block_end_date: str
    total_signal_date_rows: int
    dates_with_available_labels: int
    ic_defined_date_count: int
    bucket_defined_date_count: int
    mean_daily_rank_ic: float | None
    median_daily_rank_ic: float | None
    population_std_daily_rank_ic: float | None
    positive_ic_date_count: int
    zero_ic_date_count: int
    negative_ic_date_count: int
    positive_ic_rate: float | None
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
    undefined_reason: str | None
    warnings: tuple[str, ...]
    _included_daily_identities: tuple[str, ...] = field(repr=False, compare=False)
    included_daily_identity_count: int = field(init=False)
    included_daily_identities_sha256: str = field(init=False)
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("source_evaluation_identity", "specification_fingerprint", "factor", "outcome_field", "block_name"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a non-empty string")
        optional_names = (
            "mean_daily_rank_ic", "median_daily_rank_ic", "population_std_daily_rank_ic",
            "positive_ic_rate", "mean_daily_high_minus_low_mean_spread",
            "median_daily_high_minus_low_mean_spread",
            "mean_daily_high_minus_low_median_spread",
            "median_daily_high_minus_low_median_spread", "positive_mean_spread_rate",
            "average_low_bucket_size", "average_high_bucket_size",
        )
        numeric = {name: _optional_finite(getattr(self, name), name=name) for name in optional_names}
        identities = tuple(self._included_daily_identities)
        warnings = tuple(str(item) for item in self.warnings)
        identity_hash = sha256(canonical_json(list(identities))).hexdigest()
        content = {
            "source_evaluation_identity": self.source_evaluation_identity,
            "specification_fingerprint": self.specification_fingerprint,
            "factor": self.factor,
            "horizon_sessions": self.horizon_sessions,
            "outcome_field": self.outcome_field,
            "block": {
                "name": self.block_name,
                "start_date": self.block_start_date,
                "end_date": self.block_end_date,
            },
            "total_signal_date_rows": self.total_signal_date_rows,
            "dates_with_available_labels": self.dates_with_available_labels,
            "ic_defined_date_count": self.ic_defined_date_count,
            "bucket_defined_date_count": self.bucket_defined_date_count,
            **numeric,
            "positive_ic_date_count": self.positive_ic_date_count,
            "zero_ic_date_count": self.zero_ic_date_count,
            "negative_ic_date_count": self.negative_ic_date_count,
            "positive_mean_spread_date_count": self.positive_mean_spread_date_count,
            "zero_mean_spread_date_count": self.zero_mean_spread_date_count,
            "negative_mean_spread_date_count": self.negative_mean_spread_date_count,
            "included_daily_identities": list(identities),
            "included_daily_identity_count": len(identities),
            "included_daily_identities_sha256": identity_hash,
            "undefined_reason": self.undefined_reason,
            "warnings": list(warnings),
        }
        for name, value in numeric.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(self, "_included_daily_identities", identities)
        object.__setattr__(self, "included_daily_identity_count", len(identities))
        object.__setattr__(self, "included_daily_identities_sha256", identity_hash)
        object.__setattr__(self, "identity", sha256(canonical_json(canonical_identity_value(content))).hexdigest())


@dataclass(frozen=True, slots=True)
class FactorHorizonTemporalStabilitySummary:
    source_evaluation_identity: str
    specification_fingerprint: str
    factor: str
    horizon_sessions: int
    outcome_field: str
    block_results: tuple[FactorBlockStabilityResult, ...]
    blocks_with_defined_ic: int
    blocks_with_defined_spread: int
    blocks_with_at_least_minimum_ic_dates: int
    blocks_with_at_least_minimum_spread_dates: int
    positive_mean_ic_block_count: int
    zero_mean_ic_block_count: int
    negative_mean_ic_block_count: int
    positive_mean_spread_block_count: int
    zero_mean_spread_block_count: int
    negative_mean_spread_block_count: int
    minimum_block_mean_ic: float | None
    maximum_block_mean_ic: float | None
    range_block_mean_ic: float | None
    minimum_block_mean_spread: float | None
    maximum_block_mean_spread: float | None
    range_block_mean_spread: float | None
    largest_absolute_mean_ic_block_name: str | None
    largest_absolute_mean_ic_block_identity: str | None
    largest_absolute_mean_spread_block_name: str | None
    largest_absolute_mean_spread_block_identity: str | None
    absolute_mean_ic_concentration: float | None
    absolute_mean_spread_concentration: float | None
    coverage_sufficient_for_stability_review: bool
    directionally_consistent_ic: bool
    directionally_consistent_spread: bool
    descriptive_temporal_support: bool
    warnings: tuple[str, ...]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        blocks = tuple(self.block_results)
        warnings = tuple(str(item) for item in self.warnings)
        optional_names = (
            "minimum_block_mean_ic", "maximum_block_mean_ic", "range_block_mean_ic",
            "minimum_block_mean_spread", "maximum_block_mean_spread",
            "range_block_mean_spread", "absolute_mean_ic_concentration",
            "absolute_mean_spread_concentration",
        )
        numeric = {name: _optional_finite(getattr(self, name), name=name) for name in optional_names}
        content = {
            "source_evaluation_identity": self.source_evaluation_identity,
            "specification_fingerprint": self.specification_fingerprint,
            "factor": self.factor,
            "horizon_sessions": self.horizon_sessions,
            "outcome_field": self.outcome_field,
            "ordered_block_identities": [item.identity for item in blocks],
            "blocks_with_defined_ic": self.blocks_with_defined_ic,
            "blocks_with_defined_spread": self.blocks_with_defined_spread,
            "blocks_with_at_least_minimum_ic_dates": self.blocks_with_at_least_minimum_ic_dates,
            "blocks_with_at_least_minimum_spread_dates": self.blocks_with_at_least_minimum_spread_dates,
            "positive_mean_ic_block_count": self.positive_mean_ic_block_count,
            "zero_mean_ic_block_count": self.zero_mean_ic_block_count,
            "negative_mean_ic_block_count": self.negative_mean_ic_block_count,
            "positive_mean_spread_block_count": self.positive_mean_spread_block_count,
            "zero_mean_spread_block_count": self.zero_mean_spread_block_count,
            "negative_mean_spread_block_count": self.negative_mean_spread_block_count,
            **numeric,
            "largest_absolute_mean_ic_block_name": self.largest_absolute_mean_ic_block_name,
            "largest_absolute_mean_ic_block_identity": self.largest_absolute_mean_ic_block_identity,
            "largest_absolute_mean_spread_block_name": self.largest_absolute_mean_spread_block_name,
            "largest_absolute_mean_spread_block_identity": self.largest_absolute_mean_spread_block_identity,
            "coverage_sufficient_for_stability_review": self.coverage_sufficient_for_stability_review,
            "directionally_consistent_ic": self.directionally_consistent_ic,
            "directionally_consistent_spread": self.directionally_consistent_spread,
            "descriptive_temporal_support": self.descriptive_temporal_support,
            "warnings": list(warnings),
        }
        for name, value in numeric.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "block_results", blocks)
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(self, "identity", sha256(canonical_json(canonical_identity_value(content))).hexdigest())


@dataclass(frozen=True, slots=True)
class CandidateFactorTemporalStabilityResult:
    source_evaluation_identity: str
    source_candidate_batch_identity: str
    source_outcome_set_identity: str
    source_evaluation_spec_fingerprint: str
    temporal_stability_spec_fingerprint: str
    summaries: tuple[FactorHorizonTemporalStabilitySummary, ...]
    provenance_metadata: Mapping[str, Any]
    contract_name: str = TEMPORAL_STABILITY_CONTRACT
    contract_version: str = TEMPORAL_STABILITY_CONTRACT_VERSION
    result_identity: str = field(init=False)
    _summaries_by_key: Mapping[tuple[str, int, str], FactorHorizonTemporalStabilitySummary] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (self.contract_name, self.contract_version) != (
            TEMPORAL_STABILITY_CONTRACT, TEMPORAL_STABILITY_CONTRACT_VERSION,
        ):
            raise ValueError("unsupported candidate-factor temporal-stability contract")
        summaries = tuple(self.summaries)
        summary_map = {
            (item.factor, item.horizon_sessions, item.outcome_field): item
            for item in summaries
        }
        if len(summary_map) != len(summaries):
            raise ValueError("duplicate temporal-stability summary key")
        metadata = MappingProxyType(dict(self.provenance_metadata))
        payload = {
            "contract": {"name": self.contract_name, "version": self.contract_version},
            "source_evaluation_identity": self.source_evaluation_identity,
            "source_candidate_batch_identity": self.source_candidate_batch_identity,
            "source_outcome_set_identity": self.source_outcome_set_identity,
            "source_evaluation_spec_fingerprint": self.source_evaluation_spec_fingerprint,
            "temporal_stability_spec_fingerprint": self.temporal_stability_spec_fingerprint,
            "ordered_summary_identities": [item.identity for item in summaries],
            "provenance_metadata": canonical_identity_value(metadata),
        }
        object.__setattr__(self, "summaries", summaries)
        object.__setattr__(self, "provenance_metadata", metadata)
        object.__setattr__(self, "_summaries_by_key", MappingProxyType(summary_map))
        object.__setattr__(self, "result_identity", sha256(canonical_json(payload)).hexdigest())

    def summary_for(self, factor: str, horizon_sessions: int, outcome_field: str) -> FactorHorizonTemporalStabilitySummary:
        try:
            return self._summaries_by_key[(factor, horizon_sessions, outcome_field)]
        except KeyError as exc:
            raise KeyError(
                f"unknown temporal-stability summary: {factor}/{horizon_sessions}/{outcome_field}"
            ) from exc
