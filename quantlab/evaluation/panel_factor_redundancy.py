from __future__ import annotations

"""Pure predictor-only cross-sectional factor-redundancy diagnostics."""

from dataclasses import dataclass, field
from datetime import date, timedelta
from hashlib import sha256
import math
from numbers import Real
from statistics import median
from types import MappingProxyType
from typing import Any, Mapping

import pandas as pd

from quantlab.identity import canonical_identity_value, canonical_json

from .panel_factor_contracts import BUILTIN_FACTOR_FIELDS
from .panel_factor_temporal_stability import (
    NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2,
    PanelTemporalBlock,
)


REDUNDANCY_CONTRACT = "quantlab.panel_factor_redundancy"
REDUNDANCY_VERSION = "v1"
PAIR_METHOD = "canonical_unordered_pairs_in_factor_specification_order_v1"
RANK_METHOD = "ascending_average_ranks_for_ties_v1"
CORRELATION_METHOD = "pearson_correlation_of_same_date_pairwise_finite_average_ranks_v1"
PAIRWISE_RULE = "both_factor_values_finite_without_imputation_v1"
AGGREGATION_RULE = "defined_daily_correlations_equal_signal_date_weight_v1"
SIGN_RULE = "strict_positive_zero_strict_negative_v1"
SIGN_FLIP_RULE = "chronological_defined_blocks_only_zero_is_a_distinct_sign_v1"
CONCENTRATION_RULE = "largest_absolute_block_mean_divided_by_sum_absolute_block_means_v1"
SELECTION_RULE = "descriptive_only_no_redundancy_threshold_or_selection_authority_v1"
PREDICTOR_RULE = "point_in_time_research_dataset_predictor_frame_only_v1"
OVERALL_START_DATE = "2018-08-07"
OVERALL_END_DATE = "2026-09-17"

UNDEFINED_REASONS = (
    "fewer_than_minimum_pairwise_finite_observations",
    "constant_first_factor",
    "constant_second_factor",
    "constant_both_factors",
    "correlation_denominator_zero",
)


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


def _identity_value(value: Any) -> Any:
    if value is None:
        return None
    if value is pd.NA or value is pd.NaT:
        return {"__missing__": type(value).__name__}
    if hasattr(value, "item"):
        value = value.item()
    return canonical_identity_value(value)


def _identity_token(value: Any) -> str:
    """Return immutable, lossless canonical evidence for one predictor scalar."""
    return canonical_json(canonical_identity_value(_identity_value(value))).decode("utf-8")


def _finite_numeric(value: Any, *, column: str) -> float | None:
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, bool):
        raise TypeError(f"predictor {column} must use a numeric nullable contract")
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"predictor {column} must use a numeric nullable contract")
    number = float(value)
    return number if math.isfinite(number) else None


def _mean(values: tuple[float, ...]) -> float | None:
    return None if not values else sum(values) / len(values)


def _median(values: tuple[float, ...]) -> float | None:
    return None if not values else float(median(values))


def _population_std(values: tuple[float, ...]) -> float | None:
    if not values:
        return None
    center = sum(values) / len(values)
    return math.sqrt(sum((value - center) ** 2 for value in values) / len(values))


def _average_ranks(values: tuple[float, ...]) -> tuple[float, ...]:
    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and ordered[end][1] == ordered[cursor][1]:
            end += 1
        average_rank = (cursor + 1 + end) / 2.0
        for position in range(cursor, end):
            ranks[ordered[position][0]] = average_rank
        cursor = end
    return tuple(ranks)


def _pearson(first: tuple[float, ...], second: tuple[float, ...]) -> float | None:
    first_center = sum(first) / len(first)
    second_center = sum(second) / len(second)
    numerator = sum(
        (left - first_center) * (right - second_center)
        for left, right in zip(first, second, strict=True)
    )
    first_sum = sum((value - first_center) ** 2 for value in first)
    second_sum = sum((value - second_center) ** 2 for value in second)
    denominator = math.sqrt(first_sum * second_sum)
    if denominator == 0.0 or not math.isfinite(denominator):
        return None
    value = numerator / denominator
    return max(-1.0, min(1.0, value))


def _identity_hash(values: tuple[str, ...]) -> str:
    return sha256(canonical_json(list(values))).hexdigest()


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
class PanelFactorRedundancySpec:
    name: str
    version: str
    factors: tuple[str, ...]
    blocks: tuple[PanelTemporalBlock, ...]
    minimum_pairwise_finite_count: int = 20
    temporal_review_minimum_defined_dates: int = 30
    pair_generation_method: str = PAIR_METHOD
    rank_method: str = RANK_METHOD
    correlation_method: str = CORRELATION_METHOD
    pairwise_rule: str = PAIRWISE_RULE
    aggregation_rule: str = AGGREGATION_RULE
    population_std_ddof: int = 0
    sign_rule: str = SIGN_RULE
    sign_flip_rule: str = SIGN_FLIP_RULE
    concentration_rule: str = CONCENTRATION_RULE
    selection_rule: str = SELECTION_RULE
    predictor_rule: str = PREDICTOR_RULE
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        name = _text(self.name, name="redundancy specification name")
        version = _text(self.version, name="redundancy specification version")
        factors = tuple(_text(value, name="factor") for value in self.factors)
        blocks = tuple(self.blocks)
        if len(factors) < 2 or len(set(factors)) != len(factors):
            raise ValueError("redundancy factors must be unique and contain at least two fields")
        if any(value not in BUILTIN_FACTOR_FIELDS for value in factors):
            raise ValueError("redundancy specification contains an unsupported factor")
        if not blocks or any(not isinstance(value, PanelTemporalBlock) for value in blocks):
            raise TypeError("redundancy blocks must be PanelTemporalBlock instances")
        if len({value.name for value in blocks}) != len(blocks):
            raise ValueError("redundancy block names must be unique")
        if blocks[0].start_date != OVERALL_START_DATE or blocks[-1].end_date != OVERALL_END_DATE:
            raise ValueError("redundancy blocks must cover the exact built-in interval")
        for left, right in zip(blocks, blocks[1:]):
            expected = (date.fromisoformat(left.end_date) + timedelta(days=1)).isoformat()
            if right.start_date < expected:
                raise ValueError("redundancy blocks overlap")
            if right.start_date > expected:
                raise ValueError("redundancy blocks contain a gap")
        if (
            isinstance(self.minimum_pairwise_finite_count, bool)
            or not isinstance(self.minimum_pairwise_finite_count, int)
            or self.minimum_pairwise_finite_count <= 0
        ):
            raise ValueError("minimum_pairwise_finite_count must be positive")
        if (
            isinstance(self.temporal_review_minimum_defined_dates, bool)
            or not isinstance(self.temporal_review_minimum_defined_dates, int)
            or self.temporal_review_minimum_defined_dates <= 0
        ):
            raise ValueError("temporal review minimum must be positive")
        methods = (
            self.pair_generation_method,
            self.rank_method,
            self.correlation_method,
            self.pairwise_rule,
            self.aggregation_rule,
            self.population_std_ddof,
            self.sign_rule,
            self.sign_flip_rule,
            self.concentration_rule,
            self.selection_rule,
            self.predictor_rule,
        )
        expected_methods = (
            PAIR_METHOD,
            RANK_METHOD,
            CORRELATION_METHOD,
            PAIRWISE_RULE,
            AGGREGATION_RULE,
            0,
            SIGN_RULE,
            SIGN_FLIP_RULE,
            CONCENTRATION_RULE,
            SELECTION_RULE,
            PREDICTOR_RULE,
        )
        if methods != expected_methods:
            raise ValueError("unsupported factor-redundancy method contract")
        payload = {
            "contract": {"name": REDUNDANCY_CONTRACT, "version": REDUNDANCY_VERSION},
            "name": name,
            "version": version,
            "factors": list(factors),
            "pair_generation_method": self.pair_generation_method,
            "daily_spearman_method": self.correlation_method,
            "average_rank_tie_method": self.rank_method,
            "pairwise_finite_rule": self.pairwise_rule,
            "minimum_pairwise_finite_count": self.minimum_pairwise_finite_count,
            "equal_date_aggregation": self.aggregation_rule,
            "population_std_ddof": self.population_std_ddof,
            "blocks": [value.canonical_content() for value in blocks],
            "sign_rule": self.sign_rule,
            "sign_flip_rule": self.sign_flip_rule,
            "concentration_rule": self.concentration_rule,
            "selection_rule": self.selection_rule,
            "predictor_only_restriction": self.predictor_rule,
            "temporal_review_minimum_defined_dates": self.temporal_review_minimum_defined_dates,
        }
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "factors", factors)
        object.__setattr__(self, "blocks", blocks)
        object.__setattr__(self, "fingerprint", sha256(canonical_json(payload)).hexdigest())

    @property
    def pairs(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (self.factors[first], self.factors[second])
            for first in range(len(self.factors))
            for second in range(first + 1, len(self.factors))
        )


NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1 = PanelFactorRedundancySpec(
    name="NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1",
    version="1",
    factors=BUILTIN_FACTOR_FIELDS,
    blocks=NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2.blocks,
)


@dataclass(frozen=True, slots=True)
class DailyFactorPairCorrelation:
    source_dataset_identity: str
    source_bounded_content_identity: str
    specification_fingerprint: str
    signal_date: str
    first_factor: str
    second_factor: str
    observation_count: int
    pairwise_finite_count: int
    pairwise_coverage_pct: float
    spearman_correlation: float | None
    absolute_spearman_correlation: float | None
    undefined_reason: str | None
    pairwise_sample_evidence_sha256: str
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        optional = {
            "spearman_correlation": _optional_finite(
                self.spearman_correlation, name="spearman_correlation",
            ),
            "absolute_spearman_correlation": _optional_finite(
                self.absolute_spearman_correlation, name="absolute_spearman_correlation",
            ),
        }
        if self.undefined_reason is not None and self.undefined_reason not in UNDEFINED_REASONS:
            raise ValueError("unsupported daily redundancy undefined reason")
        if (optional["spearman_correlation"] is None) != (self.undefined_reason is not None):
            raise ValueError("daily correlation and undefined reason do not reconcile")
        if optional["spearman_correlation"] is None:
            if optional["absolute_spearman_correlation"] is not None:
                raise ValueError("undefined correlation cannot have an absolute value")
        elif optional["absolute_spearman_correlation"] != abs(optional["spearman_correlation"]):
            raise ValueError("absolute daily correlation does not reconcile")
        if min(self.observation_count, self.pairwise_finite_count) < 0:
            raise ValueError("daily correlation counts must be non-negative")
        if self.pairwise_finite_count > self.observation_count:
            raise ValueError("pairwise finite count exceeds observations")
        evidence_sha256 = _text(
            self.pairwise_sample_evidence_sha256,
            name="pairwise sample evidence SHA-256",
        )
        if len(evidence_sha256) != 64:
            raise ValueError("pairwise sample evidence must use SHA-256")
        payload = {
            "source_dataset_identity": _text(
                self.source_dataset_identity, name="source dataset identity",
            ),
            "source_bounded_content_identity": _text(
                self.source_bounded_content_identity, name="source bounded content identity",
            ),
            "specification_fingerprint": _text(
                self.specification_fingerprint, name="specification fingerprint",
            ),
            "signal_date": _date_text(self.signal_date, name="signal_date"),
            "first_factor": _text(self.first_factor, name="first_factor"),
            "second_factor": _text(self.second_factor, name="second_factor"),
            "observation_count": self.observation_count,
            "pairwise_finite_count": self.pairwise_finite_count,
            "pairwise_coverage_pct": float(self.pairwise_coverage_pct),
            **optional,
            "undefined_reason": self.undefined_reason,
            "pairwise_sample_evidence_sha256": evidence_sha256,
        }
        for name, value in optional.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "pairwise_sample_evidence_sha256", evidence_sha256)
        object.__setattr__(
            self, "identity", sha256(canonical_json(canonical_identity_value(payload))).hexdigest(),
        )


@dataclass(frozen=True, slots=True)
class FactorPairBlockCorrelation:
    source_dataset_identity: str
    source_bounded_content_identity: str
    source_daily_result_identity: str
    specification_fingerprint: str
    first_factor: str
    second_factor: str
    block_name: str
    block_start_date: str
    block_end_date: str
    total_signal_date_count: int
    minimum_sample_date_count: int
    defined_correlation_date_count: int
    correlation_coverage_pct: float
    mean_daily_correlation: float | None
    median_daily_correlation: float | None
    population_std_daily_correlation: float | None
    minimum_daily_correlation: float | None
    maximum_daily_correlation: float | None
    mean_daily_absolute_correlation: float | None
    median_daily_absolute_correlation: float | None
    positive_correlation_date_count: int
    zero_correlation_date_count: int
    negative_correlation_date_count: int
    positive_correlation_rate: float | None
    zero_correlation_rate: float | None
    negative_correlation_rate: float | None
    average_pairwise_finite_count: float | None
    median_pairwise_finite_count: float | None
    included_daily_identities: tuple[str, ...]
    warnings: tuple[str, ...]
    identity: str = field(init=False)
    included_daily_identity_count: int = field(init=False)
    included_daily_identities_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        identities = tuple(_text(value, name="daily identity") for value in self.included_daily_identities)
        warnings = tuple(_text(value, name="warning") for value in self.warnings)
        optional_names = (
            "mean_daily_correlation", "median_daily_correlation",
            "population_std_daily_correlation", "minimum_daily_correlation",
            "maximum_daily_correlation", "mean_daily_absolute_correlation",
            "median_daily_absolute_correlation", "positive_correlation_rate",
            "zero_correlation_rate", "negative_correlation_rate",
            "average_pairwise_finite_count", "median_pairwise_finite_count",
        )
        optional = {
            name: _optional_finite(getattr(self, name), name=name) for name in optional_names
        }
        payload = {
            name: canonical_identity_value(getattr(self, name))
            for name in self.__dataclass_fields__
            if name not in {
                "identity", "included_daily_identity_count",
                "included_daily_identities_sha256", *optional_names,
            }
        }
        payload.update(optional)
        payload["included_daily_identities"] = list(identities)
        payload["warnings"] = list(warnings)
        for name, value in optional.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "included_daily_identities", identities)
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(self, "included_daily_identity_count", len(identities))
        object.__setattr__(self, "included_daily_identities_sha256", _identity_hash(identities))
        object.__setattr__(
            self, "identity", sha256(canonical_json(canonical_identity_value(payload))).hexdigest(),
        )


@dataclass(frozen=True, slots=True)
class FactorPairRedundancySummary:
    source_dataset_identity: str
    source_bounded_content_identity: str
    source_daily_result_identity: str
    specification_fingerprint: str
    first_factor: str
    second_factor: str
    total_signal_date_count: int
    minimum_sample_date_count: int
    defined_correlation_date_count: int
    correlation_coverage_pct: float
    mean_daily_correlation: float | None
    median_daily_correlation: float | None
    population_std_daily_correlation: float | None
    minimum_daily_correlation: float | None
    maximum_daily_correlation: float | None
    mean_daily_absolute_correlation: float | None
    median_daily_absolute_correlation: float | None
    positive_correlation_date_count: int
    zero_correlation_date_count: int
    negative_correlation_date_count: int
    positive_correlation_rate: float | None
    zero_correlation_rate: float | None
    negative_correlation_rate: float | None
    average_pairwise_finite_count: float | None
    median_pairwise_finite_count: float | None
    included_daily_identities: tuple[str, ...]
    ordered_block_identities: tuple[str, ...]
    blocks_meeting_temporal_review_count: int
    chronological_sign_flip_count: int
    largest_absolute_block_mean_concentration: float | None
    minimum_block_mean_correlation: float | None
    maximum_block_mean_correlation: float | None
    range_block_mean_correlation: float | None
    minimum_block_mean_absolute_daily_correlation: float | None
    maximum_block_mean_absolute_daily_correlation: float | None
    range_block_mean_absolute_daily_correlation: float | None
    all_blocks_positive: bool
    all_blocks_negative: bool
    warnings: tuple[str, ...]
    identity: str = field(init=False)
    included_daily_identity_count: int = field(init=False)
    included_daily_identities_sha256: str = field(init=False)
    ordered_block_identity_count: int = field(init=False)
    ordered_block_identities_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        daily = tuple(_text(value, name="daily identity") for value in self.included_daily_identities)
        blocks = tuple(_text(value, name="block identity") for value in self.ordered_block_identities)
        warnings = tuple(_text(value, name="warning") for value in self.warnings)
        optional_names = (
            "mean_daily_correlation", "median_daily_correlation",
            "population_std_daily_correlation", "minimum_daily_correlation",
            "maximum_daily_correlation", "mean_daily_absolute_correlation",
            "median_daily_absolute_correlation", "positive_correlation_rate",
            "zero_correlation_rate", "negative_correlation_rate",
            "average_pairwise_finite_count", "median_pairwise_finite_count",
            "largest_absolute_block_mean_concentration", "minimum_block_mean_correlation",
            "maximum_block_mean_correlation", "range_block_mean_correlation",
            "minimum_block_mean_absolute_daily_correlation",
            "maximum_block_mean_absolute_daily_correlation",
            "range_block_mean_absolute_daily_correlation",
        )
        optional = {
            name: _optional_finite(getattr(self, name), name=name) for name in optional_names
        }
        payload = {
            name: canonical_identity_value(getattr(self, name))
            for name in self.__dataclass_fields__
            if name not in {
                "identity", "included_daily_identity_count",
                "included_daily_identities_sha256", "ordered_block_identity_count",
                "ordered_block_identities_sha256", *optional_names,
            }
        }
        payload.update(optional)
        payload["included_daily_identities"] = list(daily)
        payload["ordered_block_identities"] = list(blocks)
        payload["warnings"] = list(warnings)
        for name, value in optional.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "included_daily_identities", daily)
        object.__setattr__(self, "ordered_block_identities", blocks)
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(self, "included_daily_identity_count", len(daily))
        object.__setattr__(self, "included_daily_identities_sha256", _identity_hash(daily))
        object.__setattr__(self, "ordered_block_identity_count", len(blocks))
        object.__setattr__(self, "ordered_block_identities_sha256", _identity_hash(blocks))
        object.__setattr__(
            self, "identity", sha256(canonical_json(canonical_identity_value(payload))).hexdigest(),
        )


@dataclass(frozen=True, slots=True)
class PanelFactorRedundancyResult:
    source_dataset_identity: str
    source_bounded_content_identity: str
    source_observation_index_identity: str
    source_observation_content_identity: str
    source_feature_panel_identity: str
    source_feature_content_identity: str
    specification_fingerprint: str
    source_daily_result_identity: str
    daily_correlations: tuple[DailyFactorPairCorrelation, ...]
    block_correlations: tuple[FactorPairBlockCorrelation, ...]
    summaries: tuple[FactorPairRedundancySummary, ...]
    limitations_metadata: Mapping[str, Any]
    contract_name: str = REDUNDANCY_CONTRACT
    contract_version: str = REDUNDANCY_VERSION
    identity: str = field(init=False)
    _daily_by_key: Mapping[tuple[str, str, str], DailyFactorPairCorrelation] = field(
        init=False, repr=False, compare=False,
    )
    _block_by_key: Mapping[tuple[str, str, str], FactorPairBlockCorrelation] = field(
        init=False, repr=False, compare=False,
    )
    _summary_by_key: Mapping[tuple[str, str], FactorPairRedundancySummary] = field(
        init=False, repr=False, compare=False,
    )

    def __post_init__(self) -> None:
        if (self.contract_name, self.contract_version) != (
            REDUNDANCY_CONTRACT, REDUNDANCY_VERSION,
        ):
            raise ValueError("unsupported panel factor-redundancy result contract")
        daily = tuple(self.daily_correlations)
        blocks = tuple(self.block_correlations)
        summaries = tuple(self.summaries)
        daily_map = {
            (value.signal_date, value.first_factor, value.second_factor): value for value in daily
        }
        block_map = {
            (value.first_factor, value.second_factor, value.block_name): value for value in blocks
        }
        summary_map = {
            (value.first_factor, value.second_factor): value for value in summaries
        }
        if len(daily_map) != len(daily):
            raise ValueError("duplicate daily factor-pair key")
        if len(block_map) != len(blocks):
            raise ValueError("duplicate factor-pair block key")
        if len(summary_map) != len(summaries):
            raise ValueError("duplicate factor-pair summary key")
        metadata = MappingProxyType({
            str(key): canonical_identity_value(value)
            for key, value in sorted(self.limitations_metadata.items())
        })
        payload = {
            "contract": {"name": self.contract_name, "version": self.contract_version},
            "source_dataset_identity": _text(
                self.source_dataset_identity, name="source dataset identity",
            ),
            "source_bounded_content_identity": _text(
                self.source_bounded_content_identity, name="source bounded content identity",
            ),
            "source_observation_index_identity": _text(
                self.source_observation_index_identity, name="source observation identity",
            ),
            "source_observation_content_identity": _text(
                self.source_observation_content_identity, name="source observation content",
            ),
            "source_feature_panel_identity": _text(
                self.source_feature_panel_identity, name="source feature identity",
            ),
            "source_feature_content_identity": _text(
                self.source_feature_content_identity, name="source feature content",
            ),
            "specification_fingerprint": _text(
                self.specification_fingerprint, name="specification fingerprint",
            ),
            "source_daily_result_identity": _text(
                self.source_daily_result_identity, name="source daily result identity",
            ),
            "daily_identities": [value.identity for value in daily],
            "block_identities": [value.identity for value in blocks],
            "summary_identities": [value.identity for value in summaries],
            "counts": {
                "daily": len(daily), "blocks": len(blocks), "summaries": len(summaries),
            },
            "limitations_metadata": metadata,
        }
        object.__setattr__(self, "daily_correlations", daily)
        object.__setattr__(self, "block_correlations", blocks)
        object.__setattr__(self, "summaries", summaries)
        object.__setattr__(self, "limitations_metadata", metadata)
        object.__setattr__(self, "_daily_by_key", MappingProxyType(daily_map))
        object.__setattr__(self, "_block_by_key", MappingProxyType(block_map))
        object.__setattr__(self, "_summary_by_key", MappingProxyType(summary_map))
        object.__setattr__(
            self, "identity", sha256(canonical_json(canonical_identity_value(payload))).hexdigest(),
        )

    def daily_for(
        self, signal_date: str, first_factor: str, second_factor: str,
    ) -> DailyFactorPairCorrelation:
        return self._daily_by_key[(signal_date, first_factor, second_factor)]

    def block_for(
        self, first_factor: str, second_factor: str, block_name: str,
    ) -> FactorPairBlockCorrelation:
        return self._block_by_key[(first_factor, second_factor, block_name)]

    def summary_for(
        self, first_factor: str, second_factor: str,
    ) -> FactorPairRedundancySummary:
        return self._summary_by_key[(first_factor, second_factor)]


def _daily_correlation(
    rows: tuple[tuple[str, Any, Any], ...],
    *,
    source_dataset_identity: str,
    source_bounded_content_identity: str,
    spec: PanelFactorRedundancySpec,
    signal_date: str,
    first_factor: str,
    second_factor: str,
) -> DailyFactorPairCorrelation:
    pairwise: list[tuple[float, float]] = []
    evidence: list[tuple[Any, ...]] = []
    for symbol, raw_first, raw_second in rows:
        first = _finite_numeric(raw_first, column=first_factor)
        second = _finite_numeric(raw_second, column=second_factor)
        eligible = first is not None and second is not None
        evidence.append((
            symbol, _identity_token(raw_first), _identity_token(raw_second),
            first, second, eligible,
        ))
        if eligible:
            pairwise.append((first, second))
    reason: str | None = None
    correlation: float | None = None
    if len(pairwise) < spec.minimum_pairwise_finite_count:
        reason = "fewer_than_minimum_pairwise_finite_observations"
    else:
        first_values = tuple(value[0] for value in pairwise)
        second_values = tuple(value[1] for value in pairwise)
        first_constant = len(set(first_values)) == 1
        second_constant = len(set(second_values)) == 1
        if first_constant and second_constant:
            reason = "constant_both_factors"
        elif first_constant:
            reason = "constant_first_factor"
        elif second_constant:
            reason = "constant_second_factor"
        else:
            correlation = _pearson(_average_ranks(first_values), _average_ranks(second_values))
            if correlation is None:
                reason = "correlation_denominator_zero"
    return DailyFactorPairCorrelation(
        source_dataset_identity=source_dataset_identity,
        source_bounded_content_identity=source_bounded_content_identity,
        specification_fingerprint=spec.fingerprint,
        signal_date=signal_date,
        first_factor=first_factor,
        second_factor=second_factor,
        observation_count=len(rows),
        pairwise_finite_count=len(pairwise),
        pairwise_coverage_pct=0.0 if not rows else len(pairwise) / len(rows) * 100.0,
        spearman_correlation=correlation,
        absolute_spearman_correlation=None if correlation is None else abs(correlation),
        undefined_reason=reason,
        pairwise_sample_evidence_sha256=sha256(
            canonical_json(canonical_identity_value(evidence))
        ).hexdigest(),
    )


def _aggregate(daily: tuple[DailyFactorPairCorrelation, ...]) -> dict[str, Any]:
    correlations = tuple(
        value.spearman_correlation
        for value in daily if value.spearman_correlation is not None
    )
    absolute = tuple(abs(value) for value in correlations)
    pairwise_counts = tuple(float(value.pairwise_finite_count) for value in daily)
    defined = len(correlations)
    return {
        "total_signal_date_count": len(daily),
        "minimum_sample_date_count": sum(
            value.undefined_reason != "fewer_than_minimum_pairwise_finite_observations"
            for value in daily
        ),
        "defined_correlation_date_count": defined,
        "correlation_coverage_pct": 0.0 if not daily else defined / len(daily) * 100.0,
        "mean_daily_correlation": _mean(correlations),
        "median_daily_correlation": _median(correlations),
        "population_std_daily_correlation": _population_std(correlations),
        "minimum_daily_correlation": None if not correlations else min(correlations),
        "maximum_daily_correlation": None if not correlations else max(correlations),
        "mean_daily_absolute_correlation": _mean(absolute),
        "median_daily_absolute_correlation": _median(absolute),
        "positive_correlation_date_count": sum(value > 0 for value in correlations),
        "zero_correlation_date_count": sum(value == 0 for value in correlations),
        "negative_correlation_date_count": sum(value < 0 for value in correlations),
        "positive_correlation_rate": (
            None if not correlations else sum(value > 0 for value in correlations) / defined
        ),
        "zero_correlation_rate": (
            None if not correlations else sum(value == 0 for value in correlations) / defined
        ),
        "negative_correlation_rate": (
            None if not correlations else sum(value < 0 for value in correlations) / defined
        ),
        "average_pairwise_finite_count": _mean(pairwise_counts),
        "median_pairwise_finite_count": _median(pairwise_counts),
    }


def _warnings(aggregate: Mapping[str, Any], *, minimum_dates: int | None = None) -> tuple[str, ...]:
    warnings: list[str] = []
    if aggregate["defined_correlation_date_count"] == 0:
        warnings.append("correlation_undefined_for_entire_scope")
    if minimum_dates is not None and aggregate["defined_correlation_date_count"] < minimum_dates:
        warnings.append("defined_dates_below_temporal_review_threshold")
    return tuple(warnings)


def _bounded_predictor_content_identity(
    *,
    dataset: Any,
    spec: PanelFactorRedundancySpec,
    signal_dates: tuple[str, ...],
    rows: tuple[tuple[str, str, tuple[Any, ...]], ...],
) -> str:
    payload = {
        "source_observation_content_identity": dataset.observation_content_identity,
        "source_feature_content_identity": dataset.feature_content_identity,
        "source_dataset_specification_fingerprint": dataset.spec.fingerprint,
        "redundancy_specification_fingerprint": spec.fingerprint,
        "signal_dates": list(signal_dates),
        "predictor_rows": [
            [
                signal_date,
                symbol,
                [_identity_value(value) for value in values],
            ]
            for signal_date, symbol, values in rows
        ],
    }
    return sha256(canonical_json(canonical_identity_value(payload))).hexdigest()


def _validated_predictors(
    dataset: Any,
    spec: PanelFactorRedundancySpec,
) -> tuple[tuple[str, ...], tuple[tuple[str, str, tuple[Any, ...]], ...], str]:
    from quantlab.panels.research_dataset_contracts import (
        POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1,
        PointInTimeResearchDataset,
    )

    if not isinstance(dataset, PointInTimeResearchDataset):
        raise TypeError("dataset must be PointInTimeResearchDataset")
    if dataset.spec.fingerprint != POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1.fingerprint:
        raise ValueError("dataset specification is not the neutral Phase 5.3B contract")
    for name in (
        "identity", "content_identity", "observation_index_identity",
        "observation_content_identity", "feature_panel_identity", "feature_content_identity",
    ):
        _text(getattr(dataset, name), name=f"dataset {name}")
    frame = dataset.predictor_frame()
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("dataset.predictor_frame() must return a DataFrame")
    frame = frame.copy(deep=True)
    forbidden = set(dataset.forbidden_predictor_columns)
    if forbidden.intersection(frame.columns):
        raise ValueError("future-looking forbidden field entered predictor projection")
    if tuple(frame.columns) != tuple(dataset.spec.predictor_columns):
        raise ValueError("predictor projection schema does not match Phase 5.3B")
    if any(frame.columns.tolist().count(factor) != 1 for factor in spec.factors):
        raise ValueError("each requested redundancy factor must exist exactly once")
    normalized_keys: list[tuple[str, str]] = []
    for row in frame.loc[:, ["session_date", "symbol"]].itertuples(index=False):
        signal_date = _date_text(row.session_date, name="predictor signal date")
        symbol = _text(row.symbol, name="predictor symbol").upper()
        normalized_keys.append((signal_date, symbol))
    if len(set(normalized_keys)) != len(normalized_keys):
        raise ValueError("predictor projection contains duplicate date/symbol keys")
    frame.loc[:, "session_date"] = tuple(value[0] for value in normalized_keys)
    frame.loc[:, "symbol"] = tuple(value[1] for value in normalized_keys)
    frame = frame.sort_values(["session_date", "symbol"], kind="mergesort").reset_index(drop=True)
    signal_dates = tuple(
        _date_text(value.session_date, name="benchmark signal date")
        for value in dataset.session_audit
    )
    if not signal_dates or len(set(signal_dates)) != len(signal_dates):
        raise ValueError("benchmark signal dates must be non-empty and unique")
    if signal_dates != tuple(sorted(signal_dates)):
        raise ValueError("benchmark signal dates must be chronologically ordered")
    if signal_dates[0] < OVERALL_START_DATE or signal_dates[-1] > OVERALL_END_DATE:
        raise ValueError("benchmark signal date is outside the redundancy interval")
    if not set(frame["session_date"]).issubset(signal_dates):
        raise ValueError("predictor row date is absent from benchmark signal dates")
    rows = tuple(
        (
            str(row.session_date),
            str(row.symbol),
            tuple(getattr(row, factor) for factor in spec.factors),
        )
        for row in frame.loc[:, ["session_date", "symbol", *spec.factors]].itertuples(
            index=False,
        )
    )
    for factor_index, factor in enumerate(spec.factors):
        for _signal_date, _symbol, values in rows:
            _finite_numeric(values[factor_index], column=factor)
    bounded = _bounded_predictor_content_identity(
        dataset=dataset, spec=spec, signal_dates=signal_dates, rows=rows,
    )
    return signal_dates, rows, bounded


def _validate_complete_result(
    result: PanelFactorRedundancyResult,
    *,
    spec: PanelFactorRedundancySpec,
    signal_dates: tuple[str, ...],
) -> None:
    expected_daily = tuple(
        (signal_date, first_factor, second_factor)
        for first_factor, second_factor in spec.pairs
        for signal_date in signal_dates
    )
    actual_daily = tuple(
        (value.signal_date, value.first_factor, value.second_factor)
        for value in result.daily_correlations
    )
    if actual_daily != expected_daily:
        raise ValueError("daily factor-pair coverage or ordering does not reconcile")

    expected_blocks = tuple(
        (first_factor, second_factor, block.name)
        for first_factor, second_factor in spec.pairs
        for block in spec.blocks
    )
    actual_blocks = tuple(
        (value.first_factor, value.second_factor, value.block_name)
        for value in result.block_correlations
    )
    if actual_blocks != expected_blocks:
        raise ValueError("factor-pair block coverage or ordering does not reconcile")

    expected_summaries = spec.pairs
    actual_summaries = tuple(
        (value.first_factor, value.second_factor) for value in result.summaries
    )
    if actual_summaries != expected_summaries:
        raise ValueError("factor-pair summary coverage or ordering does not reconcile")

    blocks_by_pair = {
        pair: tuple(
            result.block_for(*pair, block.name).identity for block in spec.blocks
        )
        for pair in spec.pairs
    }
    for summary in result.summaries:
        pair = (summary.first_factor, summary.second_factor)
        if summary.ordered_block_identities != blocks_by_pair[pair]:
            raise ValueError("summary does not own the four canonical block results")


def evaluate_panel_factor_redundancy(
    dataset: Any,
    spec: PanelFactorRedundancySpec = NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1,
) -> PanelFactorRedundancyResult:
    """Evaluate same-date predictor overlap without reading future outcomes."""
    if not isinstance(spec, PanelFactorRedundancySpec):
        raise TypeError("spec must be PanelFactorRedundancySpec")
    signal_dates, predictor_rows, bounded_identity = _validated_predictors(dataset, spec)
    factor_index = {factor: index for index, factor in enumerate(spec.factors)}
    rows_by_date: dict[str, list[tuple[str, tuple[Any, ...]]]] = {
        signal_date: [] for signal_date in signal_dates
    }
    for signal_date, symbol, values in predictor_rows:
        rows_by_date[signal_date].append((symbol, values))
    daily = tuple(
        _daily_correlation(
            tuple(
                (
                    symbol,
                    values[factor_index[first_factor]],
                    values[factor_index[second_factor]],
                )
                for symbol, values in rows_by_date[signal_date]
            ),
            source_dataset_identity=dataset.identity,
            source_bounded_content_identity=bounded_identity,
            spec=spec,
            signal_date=signal_date,
            first_factor=first_factor,
            second_factor=second_factor,
        )
        for first_factor, second_factor in spec.pairs
        for signal_date in signal_dates
    )
    daily_map = {
        (value.first_factor, value.second_factor, value.signal_date): value for value in daily
    }
    daily_result_identity = sha256(canonical_json({
        "source_dataset_identity": dataset.identity,
        "source_bounded_content_identity": bounded_identity,
        "specification_fingerprint": spec.fingerprint,
        "ordered_daily_identities": [value.identity for value in daily],
    })).hexdigest()
    blocks: list[FactorPairBlockCorrelation] = []
    summaries: list[FactorPairRedundancySummary] = []
    for first_factor, second_factor in spec.pairs:
        pair_daily = tuple(
            daily_map[(first_factor, second_factor, signal_date)]
            for signal_date in signal_dates
        )
        pair_blocks: list[FactorPairBlockCorrelation] = []
        for block in spec.blocks:
            included = tuple(
                value for value in pair_daily
                if block.start_date <= value.signal_date <= block.end_date
            )
            aggregate = _aggregate(included)
            block_result = FactorPairBlockCorrelation(
                source_dataset_identity=dataset.identity,
                source_bounded_content_identity=bounded_identity,
                source_daily_result_identity=daily_result_identity,
                specification_fingerprint=spec.fingerprint,
                first_factor=first_factor,
                second_factor=second_factor,
                block_name=block.name,
                block_start_date=block.start_date,
                block_end_date=block.end_date,
                **aggregate,
                included_daily_identities=tuple(value.identity for value in included),
                warnings=_warnings(
                    aggregate, minimum_dates=spec.temporal_review_minimum_defined_dates,
                ),
            )
            blocks.append(block_result)
            pair_blocks.append(block_result)
        aggregate = _aggregate(pair_daily)
        block_means = tuple(
            value.mean_daily_correlation
            for value in pair_blocks if value.mean_daily_correlation is not None
        )
        block_absolute_means = tuple(
            value.mean_daily_absolute_correlation
            for value in pair_blocks if value.mean_daily_absolute_correlation is not None
        )
        minimum_block = None if not block_means else min(block_means)
        maximum_block = None if not block_means else max(block_means)
        minimum_absolute = None if not block_absolute_means else min(block_absolute_means)
        maximum_absolute = None if not block_absolute_means else max(block_absolute_means)
        summaries.append(FactorPairRedundancySummary(
            source_dataset_identity=dataset.identity,
            source_bounded_content_identity=bounded_identity,
            source_daily_result_identity=daily_result_identity,
            specification_fingerprint=spec.fingerprint,
            first_factor=first_factor,
            second_factor=second_factor,
            **aggregate,
            included_daily_identities=tuple(value.identity for value in pair_daily),
            ordered_block_identities=tuple(value.identity for value in pair_blocks),
            blocks_meeting_temporal_review_count=sum(
                value.defined_correlation_date_count
                >= spec.temporal_review_minimum_defined_dates
                for value in pair_blocks
            ),
            chronological_sign_flip_count=_sign_flips(tuple(
                value.mean_daily_correlation for value in pair_blocks
            )),
            largest_absolute_block_mean_concentration=_concentration(block_means),
            minimum_block_mean_correlation=minimum_block,
            maximum_block_mean_correlation=maximum_block,
            range_block_mean_correlation=(
                None if minimum_block is None else maximum_block - minimum_block
            ),
            minimum_block_mean_absolute_daily_correlation=minimum_absolute,
            maximum_block_mean_absolute_daily_correlation=maximum_absolute,
            range_block_mean_absolute_daily_correlation=(
                None if minimum_absolute is None else maximum_absolute - minimum_absolute
            ),
            all_blocks_positive=(
                len(block_means) == len(spec.blocks) and all(value > 0 for value in block_means)
            ),
            all_blocks_negative=(
                len(block_means) == len(spec.blocks) and all(value < 0 for value in block_means)
            ),
            warnings=(
                *_warnings(aggregate),
                *(
                    ()
                    if len(block_means) == len(spec.blocks)
                    else ("one_or_more_block_mean_correlations_undefined",)
                ),
                "descriptive_only_no_factor_selection_or_redundancy_threshold",
            ),
        ))
    metadata = MappingProxyType({
        "descriptive_not_causal": True,
        "high_correlation_authorizes_factor_removal": False,
        "low_correlation_proves_incremental_alpha": False,
        "cross_sectional_and_equal_signal_date_weighted": True,
        "database_coverage_not_historical_vn100_membership": True,
        "same_historical_dataset_not_independent_out_of_sample_confirmation": True,
        "future_outcomes_used": False,
        "statistical_significance_claim": False,
        "factor_selection_weighting_ranking_strategy_or_portfolio_authority": False,
    })
    result = PanelFactorRedundancyResult(
        source_dataset_identity=dataset.identity,
        source_bounded_content_identity=bounded_identity,
        source_observation_index_identity=dataset.observation_index_identity,
        source_observation_content_identity=dataset.observation_content_identity,
        source_feature_panel_identity=dataset.feature_panel_identity,
        source_feature_content_identity=dataset.feature_content_identity,
        specification_fingerprint=spec.fingerprint,
        source_daily_result_identity=daily_result_identity,
        daily_correlations=daily,
        block_correlations=tuple(blocks),
        summaries=tuple(summaries),
        limitations_metadata=metadata,
    )
    _validate_complete_result(result, spec=spec, signal_dates=signal_dates)
    if spec.fingerprint == NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1.fingerprint:
        if len(result.summaries) != 28 or len(result.block_correlations) != 112:
            raise ValueError("built-in redundancy dimensions do not reconcile")
    return result
