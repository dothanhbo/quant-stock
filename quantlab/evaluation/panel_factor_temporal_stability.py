from __future__ import annotations

"""Pure temporal stability summaries over neutral panel-factor daily records."""

from dataclasses import dataclass, field
from datetime import date, timedelta
from hashlib import sha256
import math
from statistics import median
from types import MappingProxyType
from typing import Any, Mapping

from quantlab.identity import canonical_identity_value, canonical_json

from .panel_factor_contracts import (
    NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1,
    PanelDailyFactorEvaluation,
    PointInTimePanelFactorEvaluationResult,
)


TEMPORAL_STABILITY_CONTRACT = "quantlab.panel_factor_temporal_stability"
TEMPORAL_STABILITY_VERSION = "v1"
OVERALL_START_DATE = "2018-08-07"
OVERALL_END_DATE = "2026-09-17"
DAILY_AGGREGATION_RULE = "defined_daily_statistics_equal_weight_within_calendar_block_v1"
BLOCK_AGGREGATION_RULE = "defined_block_means_equal_weight_across_calendar_blocks_v1"
SIGN_RULE = "strict_positive_zero_strict_negative_v1"
SIGN_FLIP_RULE = "chronological_defined_blocks_only_zero_is_a_distinct_sign_v1"
CONCENTRATION_RULE = "largest_absolute_block_mean_divided_by_sum_absolute_block_means_v1"
COVERAGE_RULE = "at_least_three_of_four_blocks_each_with_minimum_defined_dates_v1"
DIRECTION_RULE = "coverage_sufficient_and_at_least_three_eligible_blocks_strictly_positive_v1"
DESCRIPTIVE_RESTRICTION = "descriptive_only_no_selection_weighting_or_production_authority_v1"


def _text(value: Any, *, name: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{name} must be non-empty")
    return result


def _date_text(value: Any, *, name: str) -> str:
    text = _text(value, name=name)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"{name} must use YYYY-MM-DD")
    return text


def _optional_finite(value: float | None, *, name: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite or None")
    return number


def _mean(values: tuple[float, ...]) -> float | None:
    return None if not values else sum(values) / len(values)


def _median(values: tuple[float, ...]) -> float | None:
    return None if not values else float(median(values))


def _population_std(values: tuple[float, ...]) -> float | None:
    if not values:
        return None
    center = sum(values) / len(values)
    return math.sqrt(sum((item - center) ** 2 for item in values) / len(values))


def _sign(value: float | None) -> str:
    if value is None:
        return "UNDEFINED"
    if value > 0:
        return "POSITIVE"
    if value < 0:
        return "NEGATIVE"
    return "ZERO"


def _sign_flips(values: tuple[float | None, ...]) -> int:
    signs = tuple(_sign(value) for value in values if value is not None)
    return sum(left != right for left, right in zip(signs, signs[1:]))


def _concentration(values: tuple[float, ...]) -> float | None:
    denominator = sum(abs(value) for value in values)
    if not values or denominator == 0.0 or not math.isfinite(denominator):
        return None
    return max(abs(value) for value in values) / denominator


@dataclass(frozen=True, slots=True)
class PanelTemporalBlock:
    name: str
    start_date: str
    end_date: str

    def __post_init__(self) -> None:
        name = _text(self.name, name="block name")
        start = _date_text(self.start_date, name="block start_date")
        end = _date_text(self.end_date, name="block end_date")
        if start > end:
            raise ValueError("temporal block start_date must be on or before end_date")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "start_date", start)
        object.__setattr__(self, "end_date", end)

    def contains(self, signal_date: str) -> bool:
        return self.start_date <= signal_date <= self.end_date

    def canonical_content(self) -> dict[str, str]:
        return {"name": self.name, "start_date": self.start_date, "end_date": self.end_date}


@dataclass(frozen=True, slots=True)
class PanelFactorTemporalStabilitySpec:
    name: str
    version: str
    factors: tuple[str, ...]
    horizons: tuple[int, ...]
    outcome_fields: tuple[str, ...]
    blocks: tuple[PanelTemporalBlock, ...]
    minimum_defined_dates_per_block: int = 30
    source_evaluation_specification_fingerprint: str = (
        NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1.fingerprint
    )
    daily_aggregation_rule: str = DAILY_AGGREGATION_RULE
    block_aggregation_rule: str = BLOCK_AGGREGATION_RULE
    sign_rule: str = SIGN_RULE
    sign_flip_rule: str = SIGN_FLIP_RULE
    concentration_rule: str = CONCENTRATION_RULE
    coverage_rule: str = COVERAGE_RULE
    direction_rule: str = DIRECTION_RULE
    descriptive_restriction: str = DESCRIPTIVE_RESTRICTION
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        name = _text(self.name, name="temporal specification name")
        version = _text(self.version, name="temporal specification version")
        factors = tuple(_text(item, name="factor") for item in self.factors)
        horizons = tuple(self.horizons)
        outcomes = tuple(_text(item, name="outcome field") for item in self.outcome_fields)
        blocks = tuple(self.blocks)
        if not factors or len(set(factors)) != len(factors):
            raise ValueError("temporal factors must be non-empty and unique")
        if (
            not horizons
            or len(set(horizons)) != len(horizons)
            or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in horizons)
        ):
            raise ValueError("temporal horizons must be unique positive integers")
        if not outcomes or len(set(outcomes)) != len(outcomes):
            raise ValueError("temporal outcomes must be non-empty and unique")
        if not blocks or any(not isinstance(item, PanelTemporalBlock) for item in blocks):
            raise TypeError("blocks must be PanelTemporalBlock instances")
        if len({item.name for item in blocks}) != len(blocks):
            raise ValueError("duplicate temporal block name")
        if blocks[0].start_date != OVERALL_START_DATE or blocks[-1].end_date != OVERALL_END_DATE:
            raise ValueError("temporal blocks must exactly cover the required overall interval")
        for left, right in zip(blocks, blocks[1:]):
            expected = (date.fromisoformat(left.end_date) + timedelta(days=1)).isoformat()
            if right.start_date < expected:
                raise ValueError("temporal blocks overlap")
            if right.start_date > expected:
                raise ValueError("temporal blocks contain a gap")
        if (
            isinstance(self.minimum_defined_dates_per_block, bool)
            or not isinstance(self.minimum_defined_dates_per_block, int)
            or self.minimum_defined_dates_per_block <= 0
        ):
            raise ValueError("minimum_defined_dates_per_block must be positive")
        fingerprint = _text(
            self.source_evaluation_specification_fingerprint,
            name="source evaluation specification fingerprint",
        )
        rules = (
            self.daily_aggregation_rule,
            self.block_aggregation_rule,
            self.sign_rule,
            self.sign_flip_rule,
            self.concentration_rule,
            self.coverage_rule,
            self.direction_rule,
            self.descriptive_restriction,
        )
        expected_rules = (
            DAILY_AGGREGATION_RULE,
            BLOCK_AGGREGATION_RULE,
            SIGN_RULE,
            SIGN_FLIP_RULE,
            CONCENTRATION_RULE,
            COVERAGE_RULE,
            DIRECTION_RULE,
            DESCRIPTIVE_RESTRICTION,
        )
        if rules != expected_rules:
            raise ValueError("unsupported temporal stability aggregation contract")
        payload = {
            "contract": {"name": TEMPORAL_STABILITY_CONTRACT, "version": TEMPORAL_STABILITY_VERSION},
            "name": name,
            "version": version,
            "factors": list(factors),
            "horizons": list(horizons),
            "outcome_fields": list(outcomes),
            "blocks": [item.canonical_content() for item in blocks],
            "minimum_defined_dates_per_block": self.minimum_defined_dates_per_block,
            "source_evaluation_specification_fingerprint": fingerprint,
            "daily_aggregation_rule": self.daily_aggregation_rule,
            "block_aggregation_rule": self.block_aggregation_rule,
            "sign_rule": self.sign_rule,
            "sign_flip_rule": self.sign_flip_rule,
            "concentration_rule": self.concentration_rule,
            "coverage_rule": self.coverage_rule,
            "direction_rule": self.direction_rule,
            "descriptive_restriction": self.descriptive_restriction,
        }
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "factors", factors)
        object.__setattr__(self, "horizons", horizons)
        object.__setattr__(self, "outcome_fields", outcomes)
        object.__setattr__(self, "blocks", blocks)
        object.__setattr__(self, "source_evaluation_specification_fingerprint", fingerprint)
        object.__setattr__(self, "fingerprint", sha256(canonical_json(payload)).hexdigest())


NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V1 = PanelFactorTemporalStabilitySpec(
    name="NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V1",
    version="1",
    factors=NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1.factors,
    horizons=NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1.horizons,
    outcome_fields=NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1.outcome_fields,
    blocks=(
        PanelTemporalBlock("early_2018_2020", "2018-08-07", "2020-12-31"),
        PanelTemporalBlock("middle_2021_2022", "2021-01-01", "2022-12-31"),
        PanelTemporalBlock("middle_2023_2024", "2023-01-01", "2024-12-31"),
        PanelTemporalBlock("recent_2025_2026", "2025-01-01", "2026-09-17"),
    ),
)


@dataclass(frozen=True, slots=True)
class PanelFactorBlockStability:
    source_result_identity: str
    source_specification_fingerprint: str
    temporal_specification_fingerprint: str
    factor: str
    horizon_sessions: int
    outcome_field: str
    block_name: str
    block_start_date: str
    block_end_date: str
    total_source_signal_dates: int
    dates_with_any_eligible_observations: int
    ic_defined_date_count: int
    ic_coverage_pct: float
    mean_daily_rank_ic: float | None
    median_daily_rank_ic: float | None
    population_std_daily_ic: float | None
    minimum_daily_ic: float | None
    maximum_daily_ic: float | None
    positive_ic_date_count: int
    zero_ic_date_count: int
    negative_ic_date_count: int
    positive_ic_rate: float | None
    spread_defined_date_count: int
    spread_coverage_pct: float
    mean_daily_mean_spread: float | None
    median_daily_mean_spread: float | None
    population_std_daily_mean_spread: float | None
    minimum_daily_mean_spread: float | None
    maximum_daily_mean_spread: float | None
    positive_spread_date_count: int
    zero_spread_date_count: int
    negative_spread_date_count: int
    positive_spread_rate: float | None
    mean_daily_median_spread: float | None
    median_daily_median_spread: float | None
    average_low_bucket_size: float | None
    average_high_bucket_size: float | None
    average_eligible_observation_count: float | None
    minimum_eligible_observation_count: int | None
    maximum_eligible_observation_count: int | None
    average_factor_availability_coverage_pct: float | None
    average_outcome_availability_coverage_pct: float | None
    ic_review_eligible: bool
    spread_review_eligible: bool
    ic_undefined_reason: str | None
    spread_undefined_reason: str | None
    warnings: tuple[str, ...]
    included_daily_identities: tuple[str, ...]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        optional_fields = (
            "mean_daily_rank_ic", "median_daily_rank_ic", "population_std_daily_ic",
            "minimum_daily_ic", "maximum_daily_ic", "positive_ic_rate",
            "mean_daily_mean_spread", "median_daily_mean_spread",
            "population_std_daily_mean_spread", "minimum_daily_mean_spread",
            "maximum_daily_mean_spread", "positive_spread_rate",
            "mean_daily_median_spread", "median_daily_median_spread",
            "average_low_bucket_size", "average_high_bucket_size",
            "average_eligible_observation_count",
            "average_factor_availability_coverage_pct",
            "average_outcome_availability_coverage_pct",
        )
        optional = {name: _optional_finite(getattr(self, name), name=name) for name in optional_fields}
        identities = tuple(_text(item, name="included daily identity") for item in self.included_daily_identities)
        warnings = tuple(_text(item, name="warning") for item in self.warnings)
        payload = {
            name: canonical_identity_value(getattr(self, name))
            for name in self.__dataclass_fields__
            if name != "identity" and name not in optional_fields
        }
        payload.update(optional)
        payload["included_daily_identities"] = list(identities)
        payload["warnings"] = list(warnings)
        for name, value in optional.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "included_daily_identities", identities)
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(self, "identity", sha256(canonical_json(canonical_identity_value(payload))).hexdigest())


@dataclass(frozen=True, slots=True)
class PanelFactorTemporalStabilitySummary:
    factor: str
    horizon_sessions: int
    outcome_field: str
    total_block_count: int
    ic_review_eligible_block_count: int
    spread_review_eligible_block_count: int
    coverage_sufficient_for_temporal_review: bool
    positive_mean_ic_block_count: int
    zero_mean_ic_block_count: int
    negative_mean_ic_block_count: int
    positive_mean_spread_block_count: int
    zero_mean_spread_block_count: int
    negative_mean_spread_block_count: int
    mean_ic_signs_by_block: tuple[tuple[str, str], ...]
    mean_spread_signs_by_block: tuple[tuple[str, str], ...]
    mean_ic_across_block_means: float | None
    median_ic_across_block_means: float | None
    minimum_block_mean_ic: float | None
    maximum_block_mean_ic: float | None
    range_block_mean_ic: float | None
    mean_spread_across_block_means: float | None
    median_spread_across_block_means: float | None
    minimum_block_mean_spread: float | None
    maximum_block_mean_spread: float | None
    range_block_mean_spread: float | None
    largest_absolute_mean_ic_block_concentration: float | None
    largest_absolute_mean_spread_block_concentration: float | None
    directionally_consistent_ic: bool
    directionally_consistent_spread: bool
    descriptive_temporal_support: bool
    all_blocks_positive_ic: bool
    all_blocks_positive_spread: bool
    ic_sign_flip_count: int
    spread_sign_flip_count: int
    warnings: tuple[str, ...]
    included_block_identities: tuple[str, ...]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        optional_fields = (
            "mean_ic_across_block_means", "median_ic_across_block_means",
            "minimum_block_mean_ic", "maximum_block_mean_ic", "range_block_mean_ic",
            "mean_spread_across_block_means", "median_spread_across_block_means",
            "minimum_block_mean_spread", "maximum_block_mean_spread",
            "range_block_mean_spread", "largest_absolute_mean_ic_block_concentration",
            "largest_absolute_mean_spread_block_concentration",
        )
        optional = {name: _optional_finite(getattr(self, name), name=name) for name in optional_fields}
        block_ids = tuple(_text(item, name="block identity") for item in self.included_block_identities)
        warnings = tuple(_text(item, name="warning") for item in self.warnings)
        payload = {
            name: canonical_identity_value(getattr(self, name))
            for name in self.__dataclass_fields__
            if name != "identity" and name not in optional_fields
        }
        payload.update(optional)
        payload["included_block_identities"] = list(block_ids)
        payload["warnings"] = list(warnings)
        for name, value in optional.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "included_block_identities", block_ids)
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(self, "identity", sha256(canonical_json(canonical_identity_value(payload))).hexdigest())


@dataclass(frozen=True, slots=True)
class PanelFactorTemporalStabilityResult:
    source_result_identity: str
    source_dataset_identity: str
    source_dataset_content_identity: str
    source_specification_fingerprint: str
    temporal_specification_fingerprint: str
    block_results: tuple[PanelFactorBlockStability, ...]
    summaries: tuple[PanelFactorTemporalStabilitySummary, ...]
    limitations_metadata: Mapping[str, Any]
    contract_name: str = TEMPORAL_STABILITY_CONTRACT
    contract_version: str = TEMPORAL_STABILITY_VERSION
    identity: str = field(init=False)
    _blocks_by_key: Mapping[tuple[str, int, str, str], PanelFactorBlockStability] = field(init=False, repr=False, compare=False)
    _summaries_by_key: Mapping[tuple[str, int, str], PanelFactorTemporalStabilitySummary] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (self.contract_name, self.contract_version) != (
            TEMPORAL_STABILITY_CONTRACT, TEMPORAL_STABILITY_VERSION,
        ):
            raise ValueError("unsupported panel temporal stability result contract")
        for name in (
            "source_result_identity", "source_dataset_identity",
            "source_dataset_content_identity", "source_specification_fingerprint",
            "temporal_specification_fingerprint",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name=name))
        blocks, summaries = tuple(self.block_results), tuple(self.summaries)
        block_map = {
            (item.factor, item.horizon_sessions, item.outcome_field, item.block_name): item
            for item in blocks
        }
        summary_map = {
            (item.factor, item.horizon_sessions, item.outcome_field): item for item in summaries
        }
        if len(block_map) != len(blocks):
            raise ValueError("duplicate temporal block result key")
        if len(summary_map) != len(summaries):
            raise ValueError("duplicate temporal summary key")
        metadata = MappingProxyType({
            str(key): canonical_identity_value(value)
            for key, value in sorted(self.limitations_metadata.items())
        })
        payload = {
            "contract": {"name": self.contract_name, "version": self.contract_version},
            "source_result_identity": self.source_result_identity,
            "source_dataset_identity": self.source_dataset_identity,
            "source_dataset_content_identity": self.source_dataset_content_identity,
            "source_specification_fingerprint": self.source_specification_fingerprint,
            "temporal_specification_fingerprint": self.temporal_specification_fingerprint,
            "ordered_block_identities": [item.identity for item in blocks],
            "ordered_summary_identities": [item.identity for item in summaries],
            "limitations_metadata": metadata,
        }
        object.__setattr__(self, "block_results", blocks)
        object.__setattr__(self, "summaries", summaries)
        object.__setattr__(self, "limitations_metadata", metadata)
        object.__setattr__(self, "_blocks_by_key", MappingProxyType(block_map))
        object.__setattr__(self, "_summaries_by_key", MappingProxyType(summary_map))
        object.__setattr__(self, "identity", sha256(canonical_json(canonical_identity_value(payload))).hexdigest())

    def block_for(self, factor: str, horizon: int, outcome: str, block: str) -> PanelFactorBlockStability:
        return self._blocks_by_key[(factor, horizon, outcome, block)]

    def summary_for(self, factor: str, horizon: int, outcome: str) -> PanelFactorTemporalStabilitySummary:
        return self._summaries_by_key[(factor, horizon, outcome)]


def _validated_source(
    evaluation_result: PointInTimePanelFactorEvaluationResult,
    spec: PanelFactorTemporalStabilitySpec,
) -> tuple[tuple[str, ...], Mapping[tuple[str, int, str, str], PanelDailyFactorEvaluation]]:
    if not isinstance(evaluation_result, PointInTimePanelFactorEvaluationResult):
        raise TypeError("evaluation_result must be PointInTimePanelFactorEvaluationResult")
    if not isinstance(spec, PanelFactorTemporalStabilitySpec):
        raise TypeError("spec must be PanelFactorTemporalStabilitySpec")
    for name in (
        "identity", "source_dataset_identity", "source_dataset_content_identity",
        "specification_fingerprint",
    ):
        _text(getattr(evaluation_result, name), name=f"source {name}")
    if evaluation_result.specification_fingerprint != spec.source_evaluation_specification_fingerprint:
        raise ValueError("source Phase 5.4A specification fingerprint mismatch")
    daily = tuple(evaluation_result.daily_evaluations)
    keys = [
        (item.factor, item.horizon_sessions, item.outcome_field, item.signal_date)
        for item in daily
    ]
    if len(set(keys)) != len(keys):
        raise ValueError("source contains duplicate daily keys")
    requested_factors, requested_horizons, requested_outcomes = (
        set(spec.factors), set(spec.horizons), set(spec.outcome_fields),
    )
    for item in daily:
        if (
            item.factor not in requested_factors
            or item.horizon_sessions not in requested_horizons
            or item.outcome_field not in requested_outcomes
        ):
            raise ValueError(
                "source daily record contains a factor, horizon, or outcome "
                "outside the temporal specification"
            )
        if (
            item.source_dataset_identity != evaluation_result.source_dataset_identity
            or item.source_dataset_content_identity
            != evaluation_result.source_dataset_content_identity
            or item.specification_fingerprint
            != evaluation_result.specification_fingerprint
        ):
            raise ValueError("source daily provenance is inconsistent")
    summaries = tuple(evaluation_result.summaries)
    source_factors = {item.factor for item in summaries}
    source_horizons = {item.horizon_sessions for item in summaries}
    source_outcomes = {item.outcome_field for item in summaries}
    if not requested_factors.issubset(source_factors):
        raise ValueError("requested temporal factor is absent from the source evaluation")
    if not requested_horizons.issubset(source_horizons):
        raise ValueError("requested temporal horizon is absent from the source evaluation")
    if not requested_outcomes.issubset(source_outcomes):
        raise ValueError("requested temporal outcome is absent from the source evaluation")
    if (
        source_factors != requested_factors
        or source_horizons != requested_horizons
        or source_outcomes != requested_outcomes
    ):
        raise ValueError("source contains factor, horizon, or outcome combinations outside the temporal scope")
    signal_dates = tuple(sorted({item.signal_date for item in daily}))
    for signal_date in signal_dates:
        normalized = _date_text(signal_date, name="source signal date")
        if normalized < OVERALL_START_DATE or normalized > OVERALL_END_DATE:
            raise ValueError("source signal date is outside the temporal specification")
        if sum(block.contains(normalized) for block in spec.blocks) != 1:
            raise ValueError("source signal date is not assigned to exactly one temporal block")
    lookup = {
        (item.factor, item.horizon_sessions, item.outcome_field, item.signal_date): item
        for item in daily
        if item.factor in requested_factors
        and item.horizon_sessions in requested_horizons
        and item.outcome_field in requested_outcomes
    }
    expected = {
        (factor, horizon, outcome, signal_date)
        for factor in spec.factors
        for horizon in spec.horizons
        for outcome in spec.outcome_fields
        for signal_date in signal_dates
    }
    missing = sorted(expected.difference(lookup))
    if missing:
        raise ValueError(f"source is missing requested daily combination: {missing[0]}")
    extra = sorted(set(lookup).difference(expected))
    if extra:
        raise ValueError(f"source contains an extra requested-scope daily combination: {extra[0]}")
    summary_map = {
        (item.factor, item.horizon_sessions, item.outcome_field): item
        for item in summaries
    }
    if len(summary_map) != len(summaries):
        raise ValueError("source contains duplicate Phase 5.4A summary keys")
    summary_keys = set(summary_map)
    expected_summaries = {
        (factor, horizon, outcome)
        for factor in spec.factors for horizon in spec.horizons for outcome in spec.outcome_fields
    }
    if not expected_summaries.issubset(summary_keys):
        raise ValueError("source is missing a requested Phase 5.4A summary")
    if summary_keys != expected_summaries:
        raise ValueError("source contains an extra Phase 5.4A summary")
    for key in expected_summaries:
        expected_identities = tuple(
            item.identity for item in daily
            if (item.factor, item.horizon_sessions, item.outcome_field) == key
        )
        if summary_map[key].included_daily_identities != expected_identities:
            raise ValueError("source summary and daily provenance are inconsistent")
    return signal_dates, MappingProxyType(lookup)


def _block_result(
    items: tuple[PanelDailyFactorEvaluation, ...],
    *,
    source: PointInTimePanelFactorEvaluationResult,
    spec: PanelFactorTemporalStabilitySpec,
    factor: str,
    horizon: int,
    outcome: str,
    block: PanelTemporalBlock,
) -> PanelFactorBlockStability:
    ic_values = tuple(item.rank_ic for item in items if item.rank_ic is not None)
    spread_values = tuple(
        item.high_minus_low_mean_spread
        for item in items if item.high_minus_low_mean_spread is not None
    )
    median_spreads = tuple(
        item.high_minus_low_median_spread
        for item in items if item.high_minus_low_median_spread is not None
    )
    eligible_counts = tuple(item.pairwise_finite_eligible_count for item in items)
    factor_coverage = tuple(
        item.factor_available_count / item.total_observation_count * 100.0
        for item in items if item.total_observation_count > 0
    )
    outcome_coverage = tuple(
        item.outcome_available_count / item.total_observation_count * 100.0
        for item in items if item.total_observation_count > 0
    )
    ic_eligible = len(ic_values) >= spec.minimum_defined_dates_per_block
    spread_eligible = len(spread_values) >= spec.minimum_defined_dates_per_block
    warnings: list[str] = []
    if not items:
        warnings.append("no_source_signal_dates_in_block")
    if not ic_values:
        warnings.append("daily_ic_undefined_for_entire_block")
    elif not ic_eligible:
        warnings.append("defined_ic_dates_below_temporal_review_threshold")
    if not spread_values:
        warnings.append("daily_spread_undefined_for_entire_block")
    elif not spread_eligible:
        warnings.append("defined_spread_dates_below_temporal_review_threshold")
    return PanelFactorBlockStability(
        source_result_identity=source.identity,
        source_specification_fingerprint=source.specification_fingerprint,
        temporal_specification_fingerprint=spec.fingerprint,
        factor=factor,
        horizon_sessions=horizon,
        outcome_field=outcome,
        block_name=block.name,
        block_start_date=block.start_date,
        block_end_date=block.end_date,
        total_source_signal_dates=len(items),
        dates_with_any_eligible_observations=sum(
            item.pairwise_finite_eligible_count > 0 for item in items
        ),
        ic_defined_date_count=len(ic_values),
        ic_coverage_pct=0.0 if not items else len(ic_values) / len(items) * 100.0,
        mean_daily_rank_ic=_mean(ic_values),
        median_daily_rank_ic=_median(ic_values),
        population_std_daily_ic=_population_std(ic_values),
        minimum_daily_ic=None if not ic_values else min(ic_values),
        maximum_daily_ic=None if not ic_values else max(ic_values),
        positive_ic_date_count=sum(value > 0 for value in ic_values),
        zero_ic_date_count=sum(value == 0 for value in ic_values),
        negative_ic_date_count=sum(value < 0 for value in ic_values),
        positive_ic_rate=None if not ic_values else sum(value > 0 for value in ic_values) / len(ic_values),
        spread_defined_date_count=len(spread_values),
        spread_coverage_pct=0.0 if not items else len(spread_values) / len(items) * 100.0,
        mean_daily_mean_spread=_mean(spread_values),
        median_daily_mean_spread=_median(spread_values),
        population_std_daily_mean_spread=_population_std(spread_values),
        minimum_daily_mean_spread=None if not spread_values else min(spread_values),
        maximum_daily_mean_spread=None if not spread_values else max(spread_values),
        positive_spread_date_count=sum(value > 0 for value in spread_values),
        zero_spread_date_count=sum(value == 0 for value in spread_values),
        negative_spread_date_count=sum(value < 0 for value in spread_values),
        positive_spread_rate=(
            None if not spread_values else sum(value > 0 for value in spread_values) / len(spread_values)
        ),
        mean_daily_median_spread=_mean(median_spreads),
        median_daily_median_spread=_median(median_spreads),
        average_low_bucket_size=_mean(
            tuple(
                float(item.low_bucket_count)
                for item in items
                if item.high_minus_low_mean_spread is not None
            )
        ),
        average_high_bucket_size=_mean(
            tuple(
                float(item.high_bucket_count)
                for item in items
                if item.high_minus_low_mean_spread is not None
            )
        ),
        average_eligible_observation_count=_mean(tuple(float(value) for value in eligible_counts)),
        minimum_eligible_observation_count=None if not eligible_counts else min(eligible_counts),
        maximum_eligible_observation_count=None if not eligible_counts else max(eligible_counts),
        average_factor_availability_coverage_pct=_mean(factor_coverage),
        average_outcome_availability_coverage_pct=_mean(outcome_coverage),
        ic_review_eligible=ic_eligible,
        spread_review_eligible=spread_eligible,
        ic_undefined_reason=None if ic_values else "no_defined_daily_ic",
        spread_undefined_reason=None if spread_values else "no_defined_daily_spread",
        warnings=tuple(warnings),
        included_daily_identities=tuple(item.identity for item in items),
    )


def _summary(
    blocks: tuple[PanelFactorBlockStability, ...],
) -> PanelFactorTemporalStabilitySummary:
    first = blocks[0]
    ic_means = tuple(item.mean_daily_rank_ic for item in blocks if item.mean_daily_rank_ic is not None)
    spread_means = tuple(
        item.mean_daily_mean_spread for item in blocks if item.mean_daily_mean_spread is not None
    )
    ic_eligible = tuple(item for item in blocks if item.ic_review_eligible)
    spread_eligible = tuple(item for item in blocks if item.spread_review_eligible)
    coverage = len(ic_eligible) >= 3 and len(spread_eligible) >= 3
    ic_consistent = coverage and sum(
        item.mean_daily_rank_ic is not None and item.mean_daily_rank_ic > 0 for item in ic_eligible
    ) >= 3
    spread_consistent = coverage and sum(
        item.mean_daily_mean_spread is not None and item.mean_daily_mean_spread > 0
        for item in spread_eligible
    ) >= 3
    warnings = ["descriptive_only_same_historical_dataset_not_independent_confirmation"]
    if not coverage:
        warnings.append("insufficient_block_coverage_for_temporal_review")
    ic_min, ic_max = (None, None) if not ic_means else (min(ic_means), max(ic_means))
    spread_min, spread_max = (
        (None, None) if not spread_means else (min(spread_means), max(spread_means))
    )
    return PanelFactorTemporalStabilitySummary(
        factor=first.factor,
        horizon_sessions=first.horizon_sessions,
        outcome_field=first.outcome_field,
        total_block_count=len(blocks),
        ic_review_eligible_block_count=len(ic_eligible),
        spread_review_eligible_block_count=len(spread_eligible),
        coverage_sufficient_for_temporal_review=coverage,
        positive_mean_ic_block_count=sum(value > 0 for value in ic_means),
        zero_mean_ic_block_count=sum(value == 0 for value in ic_means),
        negative_mean_ic_block_count=sum(value < 0 for value in ic_means),
        positive_mean_spread_block_count=sum(value > 0 for value in spread_means),
        zero_mean_spread_block_count=sum(value == 0 for value in spread_means),
        negative_mean_spread_block_count=sum(value < 0 for value in spread_means),
        mean_ic_signs_by_block=tuple((item.block_name, _sign(item.mean_daily_rank_ic)) for item in blocks),
        mean_spread_signs_by_block=tuple((item.block_name, _sign(item.mean_daily_mean_spread)) for item in blocks),
        mean_ic_across_block_means=_mean(ic_means),
        median_ic_across_block_means=_median(ic_means),
        minimum_block_mean_ic=ic_min,
        maximum_block_mean_ic=ic_max,
        range_block_mean_ic=None if ic_min is None or ic_max is None else ic_max - ic_min,
        mean_spread_across_block_means=_mean(spread_means),
        median_spread_across_block_means=_median(spread_means),
        minimum_block_mean_spread=spread_min,
        maximum_block_mean_spread=spread_max,
        range_block_mean_spread=(
            None if spread_min is None or spread_max is None else spread_max - spread_min
        ),
        largest_absolute_mean_ic_block_concentration=_concentration(ic_means),
        largest_absolute_mean_spread_block_concentration=_concentration(spread_means),
        directionally_consistent_ic=ic_consistent,
        directionally_consistent_spread=spread_consistent,
        descriptive_temporal_support=ic_consistent and spread_consistent,
        all_blocks_positive_ic=len(ic_means) == len(blocks) and all(value > 0 for value in ic_means),
        all_blocks_positive_spread=(
            len(spread_means) == len(blocks) and all(value > 0 for value in spread_means)
        ),
        ic_sign_flip_count=_sign_flips(tuple(item.mean_daily_rank_ic for item in blocks)),
        spread_sign_flip_count=_sign_flips(tuple(item.mean_daily_mean_spread for item in blocks)),
        warnings=tuple(warnings),
        included_block_identities=tuple(item.identity for item in blocks),
    )


def evaluate_panel_factor_temporal_stability(
    evaluation_result: PointInTimePanelFactorEvaluationResult,
    spec: PanelFactorTemporalStabilitySpec = NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V1,
) -> PanelFactorTemporalStabilityResult:
    """Aggregate existing same-date diagnostics into fixed descriptive blocks."""
    signal_dates, daily_lookup = _validated_source(evaluation_result, spec)
    block_results = tuple(
        _block_result(
            tuple(
                daily_lookup[(factor, horizon, outcome, signal_date)]
                for signal_date in signal_dates if block.contains(signal_date)
            ),
            source=evaluation_result,
            spec=spec,
            factor=factor,
            horizon=horizon,
            outcome=outcome,
            block=block,
        )
        for factor in spec.factors
        for horizon in spec.horizons
        for outcome in spec.outcome_fields
        for block in spec.blocks
    )
    block_lookup = {
        (item.factor, item.horizon_sessions, item.outcome_field, item.block_name): item
        for item in block_results
    }
    summaries = tuple(
        _summary(tuple(
            block_lookup[(factor, horizon, outcome, block.name)] for block in spec.blocks
        ))
        for factor in spec.factors
        for horizon in spec.horizons
        for outcome in spec.outcome_fields
    )
    metadata = MappingProxyType({
        "descriptive_only": True,
        "population": "complete PIT population carried by Phase 5.4A",
        "candidate_or_strategy_filter_applied": False,
        "independent_out_of_sample_confirmation": False,
        "same_historical_dataset_used_for_discovery_and_temporal_review": True,
        "stock_and_excess_outcomes_are_independent_evidence": False,
        "database_coverage_is_historical_vn100": False,
        "p_values_or_multiple_testing_correction_provided": False,
        "portfolio_cost_turnover_liquidity_or_tradability_conclusion_provided": False,
        "factor_selection_or_weighting_authority": False,
    })
    return PanelFactorTemporalStabilityResult(
        source_result_identity=evaluation_result.identity,
        source_dataset_identity=evaluation_result.source_dataset_identity,
        source_dataset_content_identity=evaluation_result.source_dataset_content_identity,
        source_specification_fingerprint=evaluation_result.specification_fingerprint,
        temporal_specification_fingerprint=spec.fingerprint,
        block_results=block_results,
        summaries=summaries,
        limitations_metadata=metadata,
    )
