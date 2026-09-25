from __future__ import annotations

"""Pure, predeclared composite-factor comparisons over the Phase 5.3B dataset."""

from dataclasses import dataclass, field
from datetime import date
from hashlib import sha256
import math
from numbers import Real
from statistics import median
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    import pandas as pd

from quantlab.identity import canonical_identity_value, canonical_json

from .panel_factor_contracts import BUILTIN_FACTOR_FIELDS, SUPPORTED_HORIZONS, SUPPORTED_OUTCOME_FIELDS
from .panel_factor_temporal_stability import (
    NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2,
    OVERALL_END_DATE,
    OVERALL_START_DATE,
    PanelTemporalBlock,
)


COMPOSITE_CONTRACT = "quantlab.panel_composite_analysis"
COMPOSITE_VERSION = "v1"
PERMITTED_USE = "offline_research_evaluation_only"
COMMON_FACTORS = ("adx_14", "rsi_14", "volume_ratio_20")
MINIMUM_SHARED_COUNT = 20
MINIMUM_BLOCK_REVIEW_DATES = 30
WEIGHT_TOLERANCE = 1e-12
ZERO_TOLERANCE = 1e-12
LOW_BUCKET_MAX = 0.30
HIGH_BUCKET_MIN = 0.70
RANK_METHOD = "ascending_average_ranks_for_ties_v1"
PERCENTILE_METHOD = "average_rank_minus_one_over_n_minus_one_v1"
COMPOSITE_METHOD = "sum_declared_weight_times_factor_rank_percentile_v1"
IC_METHOD = "pearson_of_composite_and_outcome_average_ranks_v1"
COMMON_SAMPLE_RULE = "available_outcome_and_finite_outcome_adx_rsi_volume_v1"
AGGREGATION_RULE = "defined_daily_statistics_equal_signal_date_weight_no_pooling_v1"
SIGN_RULE = "strict_positive_zero_strict_negative_v1"
SIGN_FLIP_RULE = "chronological_defined_blocks_only_zero_is_distinct_v1"
CONCENTRATION_RULE = "largest_absolute_block_mean_over_sum_absolute_block_means_v1"

_OUTCOME_COLUMNS = {
    "stock_forward_return_pct": "stock_forward_return_{horizon}_pct",
    "excess_forward_return_pct_points": "excess_forward_return_{horizon}_pct_points",
}
_IC_REASONS = {
    "fewer_than_minimum_shared_observations", "constant_composite_score",
    "constant_outcome", "correlation_denominator_zero", "unavailable_outcome_status",
}
_SPREAD_REASONS = {
    "fewer_than_minimum_shared_observations", "empty_low_bucket",
    "empty_high_bucket", "overlapping_buckets", "unavailable_outcome_status",
}
_LIMITATIONS = MappingProxyType({
    "policies_and_equal_weights_fixed_before_evaluation": True,
    "descriptive_historical_comparison_not_independent_confirmation": True,
    "weight_optimization_or_policy_search_occurred": False,
    "stock_and_excess_rank_evidence_independent_when_benchmark_subtraction_preserves_ranks": False,
    "composite_ic_or_spread_improvement_proves_portfolio_improvement": False,
    "significance_or_multiple_testing_correction_included": False,
    "database_coverage_is_historical_vn100": False,
    "cost_turnover_liquidity_capacity_execution_or_risk_adjusted_portfolio_evaluated": False,
    "production_strategy_or_capital_allocation_authority": False,
})


def _text(value: Any, *, name: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{name} must be non-empty")
    return result


def _date_text(value: Any, *, name: str) -> str:
    result = _text(value, name=name)
    try:
        parsed = date.fromisoformat(result)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date") from exc
    if parsed.isoformat() != result:
        raise ValueError(f"{name} must use YYYY-MM-DD")
    return result


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _identity_hash(values: tuple[str, ...]) -> str:
    return _hash(list(values))


def _finite(value: Any, *, column: str) -> float | None:
    import pandas as pd

    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, bool):
        raise TypeError(f"{column} must use a numeric nullable contract")
    try:
        if bool(pd.isna(value)):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{column} must use a numeric nullable contract")
    result = float(value)
    return result if math.isfinite(result) else None


def _identity_value(value: Any) -> Any:
    import pandas as pd

    if value is pd.NA or value is pd.NaT:
        return {"__missing__": type(value).__name__}
    if hasattr(value, "item"):
        value = value.item()
    return canonical_identity_value(value)


def _mean(values: tuple[float, ...]) -> float | None:
    return None if not values else sum(values) / len(values)


def _median(values: tuple[float, ...]) -> float | None:
    return None if not values else float(median(values))


def _std(values: tuple[float, ...]) -> float | None:
    if not values:
        return None
    center = sum(values) / len(values)
    return math.sqrt(sum((value - center) ** 2 for value in values) / len(values))


def _average_ranks(values: tuple[float, ...]) -> tuple[float, ...]:
    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    result = [0.0] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and ordered[end][1] == ordered[cursor][1]:
            end += 1
        rank = (cursor + 1 + end) / 2.0
        for offset in range(cursor, end):
            result[ordered[offset][0]] = rank
        cursor = end
    return tuple(result)


def _percentiles(ranks: tuple[float, ...]) -> tuple[float, ...]:
    if len(ranks) <= 1:
        return tuple(0.5 for _ in ranks)
    return tuple((rank - 1.0) / (len(ranks) - 1.0) for rank in ranks)


def _composite_scores(
    policy: NeutralCompositePolicy,
    factor_percentiles: Mapping[str, tuple[float, ...]],
) -> tuple[float, ...]:
    """Apply one predeclared policy to already-computed factor percentiles."""
    lengths = {len(factor_percentiles[item.factor]) for item in policy.factor_weights}
    if len(lengths) != 1:
        raise ValueError("composite factor percentile arrays must have equal length")
    count = next(iter(lengths))
    return tuple(
        sum(
            item.weight * factor_percentiles[item.factor][index]
            for item in policy.factor_weights
        )
        for index in range(count)
    )


def _pearson(first: tuple[float, ...], second: tuple[float, ...]) -> float | None:
    left_center = sum(first) / len(first)
    right_center = sum(second) / len(second)
    left = tuple(value - left_center for value in first)
    right = tuple(value - right_center for value in second)
    denominator = math.sqrt(
        sum(value * value for value in left) * sum(value * value for value in right)
    )
    if denominator <= ZERO_TOLERANCE or not math.isfinite(denominator):
        return None
    result = sum(a * b for a, b in zip(left, right, strict=True)) / denominator
    return max(-1.0, min(1.0, result))


def _sign(value: float | None) -> str:
    if value is None:
        return "UNDEFINED"
    return "POSITIVE" if value > 0 else "NEGATIVE" if value < 0 else "ZERO"


def _sign_flips(values: tuple[float | None, ...]) -> int:
    signs = tuple(_sign(value) for value in values if value is not None)
    return sum(left != right for left, right in zip(signs, signs[1:]))


def _concentration(values: tuple[float, ...]) -> float | None:
    denominator = sum(abs(value) for value in values)
    if not values or denominator == 0.0 or not math.isfinite(denominator):
        return None
    return max(abs(value) for value in values) / denominator


def _frozen(value: Any) -> Any:
    normalized = canonical_identity_value(value)
    if isinstance(normalized, dict):
        return MappingProxyType({key: _frozen(item) for key, item in sorted(normalized.items())})
    if isinstance(normalized, list):
        return tuple(_frozen(item) for item in normalized)
    return normalized


@dataclass(frozen=True, slots=True)
class CompositeFactorWeight:
    factor: str
    weight: float
    direction: str = "HIGHER_IS_BETTER"

    def __post_init__(self) -> None:
        factor = _text(self.factor, name="composite factor")
        if isinstance(self.weight, bool):
            raise ValueError("composite weights must be finite and strictly positive")
        weight = float(self.weight)
        if factor not in COMMON_FACTORS or factor not in BUILTIN_FACTOR_FIELDS:
            raise ValueError(f"unsupported composite factor: {factor}")
        if not math.isfinite(weight) or weight <= 0.0:
            raise ValueError("composite weights must be finite and strictly positive")
        if self.direction != "HIGHER_IS_BETTER":
            raise ValueError("composite factor direction must be HIGHER_IS_BETTER")
        object.__setattr__(self, "factor", factor)
        object.__setattr__(self, "weight", weight)

    def canonical_content(self) -> dict[str, Any]:
        return {"factor": self.factor, "weight": self.weight, "direction": self.direction}


@dataclass(frozen=True, slots=True)
class NeutralCompositePolicy:
    name: str
    factor_weights: tuple[CompositeFactorWeight, ...]
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        name = _text(self.name, name="composite policy name")
        weights = tuple(self.factor_weights)
        if not weights or any(not isinstance(item, CompositeFactorWeight) for item in weights):
            raise TypeError("factor_weights must contain CompositeFactorWeight values")
        fields = tuple(item.factor for item in weights)
        if len(set(fields)) != len(fields):
            raise ValueError("composite policy factors must be unique")
        if not math.isclose(sum(item.weight for item in weights), 1.0, rel_tol=0.0, abs_tol=WEIGHT_TOLERANCE):
            raise ValueError("composite policy weights must sum to one")
        payload = {"name": name, "ordered_factor_weights": [item.canonical_content() for item in weights]}
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "factor_weights", weights)
        object.__setattr__(self, "fingerprint", _hash(payload))

    @property
    def factors(self) -> tuple[str, ...]:
        return tuple(item.factor for item in self.factor_weights)

    def canonical_content(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ordered_factor_weights": [item.canonical_content() for item in self.factor_weights],
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class _CompositeContrast:
    name: str
    variant_policy: str
    reference_policy: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, name="contrast name"))
        object.__setattr__(self, "variant_policy", _text(self.variant_policy, name="variant policy"))
        object.__setattr__(self, "reference_policy", _text(self.reference_policy, name="reference policy"))

    def canonical_content(self) -> dict[str, str]:
        return {
            "name": self.name,
            "variant_policy": self.variant_policy,
            "reference_policy": self.reference_policy,
        }


@dataclass(frozen=True, slots=True)
class PanelCompositeAnalysisSpec:
    name: str
    version: str
    policies: tuple[NeutralCompositePolicy, ...]
    contrasts: tuple[_CompositeContrast, ...]
    horizons: tuple[int, ...]
    outcome_fields: tuple[str, ...]
    blocks: tuple[PanelTemporalBlock, ...]
    rationale: Mapping[str, str]
    minimum_shared_count: int = MINIMUM_SHARED_COUNT
    low_bucket_max_percentile: float = LOW_BUCKET_MAX
    high_bucket_min_percentile: float = HIGH_BUCKET_MIN
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        policies = tuple(self.policies)
        contrasts = tuple(self.contrasts)
        horizons = tuple(self.horizons)
        outcomes = tuple(self.outcome_fields)
        blocks = tuple(self.blocks)
        if not policies or any(not isinstance(item, NeutralCompositePolicy) for item in policies):
            raise TypeError("policies must contain NeutralCompositePolicy values")
        if any(not isinstance(item, _CompositeContrast) for item in contrasts):
            raise TypeError("contrasts must contain declared composite contrast values")
        if len({item.name for item in policies}) != len(policies):
            raise ValueError("composite policy names must be unique and non-empty")
        if not contrasts or len({item.name for item in contrasts}) != len(contrasts):
            raise ValueError("composite contrast names must be unique and non-empty")
        names = {item.name for item in policies}
        if any(
            item.variant_policy not in names or item.reference_policy not in names
            or item.variant_policy == item.reference_policy
            for item in contrasts
        ):
            raise ValueError("composite contrasts must reference two distinct declared policies")
        expected_policies = (
            ("ADX_ONLY", (("adx_14", 1.0, "HIGHER_IS_BETTER"),)),
            ("RSI_ONLY", (("rsi_14", 1.0, "HIGHER_IS_BETTER"),)),
            ("ADX_RSI_EQUAL_WEIGHT", (
                ("adx_14", 0.5, "HIGHER_IS_BETTER"),
                ("rsi_14", 0.5, "HIGHER_IS_BETTER"),
            )),
            ("ADX_RSI_VOLUME_EQUAL_WEIGHT", (
                ("adx_14", 1.0 / 3.0, "HIGHER_IS_BETTER"),
                ("rsi_14", 1.0 / 3.0, "HIGHER_IS_BETTER"),
                ("volume_ratio_20", 1.0 / 3.0, "HIGHER_IS_BETTER"),
            )),
        )
        actual_policies = tuple(
            (
                policy.name,
                tuple((item.factor, item.weight, item.direction) for item in policy.factor_weights),
            )
            for policy in policies
        )
        if actual_policies != expected_policies:
            raise ValueError("composite specification must use the four frozen policies in order")
        expected_contrasts = (
            ("ADX_RSI_vs_ADX", "ADX_RSI_EQUAL_WEIGHT", "ADX_ONLY"),
            ("ADX_RSI_vs_RSI", "ADX_RSI_EQUAL_WEIGHT", "RSI_ONLY"),
            (
                "VOLUME_ADDON_vs_ADX_RSI",
                "ADX_RSI_VOLUME_EQUAL_WEIGHT",
                "ADX_RSI_EQUAL_WEIGHT",
            ),
        )
        if tuple(
            (item.name, item.variant_policy, item.reference_policy) for item in contrasts
        ) != expected_contrasts:
            raise ValueError("composite specification must use the three frozen contrasts in order")
        if horizons != (5, 10, 20) or outcomes != (
            "stock_forward_return_pct", "excess_forward_return_pct_points",
        ):
            raise ValueError("composite horizons and outcomes must use the frozen neutral scope")
        if tuple(item.canonical_content() for item in blocks) != tuple(
            item.canonical_content() for item in NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2.blocks
        ):
            raise ValueError("composite temporal blocks must match the Phase 5.5 authority")
        if (
            isinstance(self.minimum_shared_count, bool)
            or not isinstance(self.minimum_shared_count, int)
            or self.minimum_shared_count != MINIMUM_SHARED_COUNT
        ):
            raise ValueError("minimum_shared_count must use the frozen value of 20")
        low, high = float(self.low_bucket_max_percentile), float(self.high_bucket_min_percentile)
        if not (
            math.isfinite(low) and math.isfinite(high)
            and low == LOW_BUCKET_MAX and high == HIGH_BUCKET_MIN
        ):
            raise ValueError("composite bucket boundaries must use the frozen 30/70 definition")
        rationale = MappingProxyType({
            _text(key, name="rationale key"): _text(value, name="rationale value")
            for key, value in sorted(self.rationale.items())
        })
        payload = {
            "contract": {"name": COMPOSITE_CONTRACT, "version": COMPOSITE_VERSION},
            "name": _text(self.name, name="composite specification name"),
            "version": _text(self.version, name="composite specification version"),
            "ordered_policies": [item.canonical_content() for item in policies],
            "ordered_contrasts": [item.canonical_content() for item in contrasts],
            "ordered_horizons": list(horizons),
            "ordered_outcome_fields": list(outcomes),
            "common_sample_factor_union": list(COMMON_FACTORS),
            "common_sample_rule": COMMON_SAMPLE_RULE,
            "minimum_shared_count": self.minimum_shared_count,
            "weight_sum_tolerance": WEIGHT_TOLERANCE,
            "rank_method": RANK_METHOD,
            "rank_percentile_formula": PERCENTILE_METHOD,
            "composite_formula": COMPOSITE_METHOD,
            "rank_ic_formula": IC_METHOD,
            "bucket_boundaries": {"low_max": low, "high_min": high},
            "equal_date_aggregation": AGGREGATION_RULE,
            "population_std_ddof": 0,
            "minimum_defined_dates_per_temporal_review_block": MINIMUM_BLOCK_REVIEW_DATES,
            "blocks": [item.canonical_content() for item in blocks],
            "sign_rule": SIGN_RULE,
            "sign_flip_rule": SIGN_FLIP_RULE,
            "concentration_rule": CONCENTRATION_RULE,
            "optimization_or_selection_thresholds": None,
            "rationale": rationale,
        }
        object.__setattr__(self, "policies", policies)
        object.__setattr__(self, "contrasts", contrasts)
        object.__setattr__(self, "horizons", horizons)
        object.__setattr__(self, "outcome_fields", outcomes)
        object.__setattr__(self, "blocks", blocks)
        object.__setattr__(self, "rationale", rationale)
        object.__setattr__(self, "low_bucket_max_percentile", low)
        object.__setattr__(self, "high_bucket_min_percentile", high)
        object.__setattr__(self, "fingerprint", _hash(payload))


NEUTRAL_ADX_RSI_COMPOSITE_COMPARISON_V1 = PanelCompositeAnalysisSpec(
    name="NEUTRAL_ADX_RSI_COMPOSITE_COMPARISON_V1",
    version="1",
    policies=(
        NeutralCompositePolicy("ADX_ONLY", (CompositeFactorWeight("adx_14", 1.0),)),
        NeutralCompositePolicy("RSI_ONLY", (CompositeFactorWeight("rsi_14", 1.0),)),
        NeutralCompositePolicy("ADX_RSI_EQUAL_WEIGHT", (
            CompositeFactorWeight("adx_14", 0.5), CompositeFactorWeight("rsi_14", 0.5),
        )),
        NeutralCompositePolicy("ADX_RSI_VOLUME_EQUAL_WEIGHT", (
            CompositeFactorWeight("adx_14", 1.0 / 3.0),
            CompositeFactorWeight("rsi_14", 1.0 / 3.0),
            CompositeFactorWeight("volume_ratio_20", 1.0 / 3.0),
        )),
    ),
    contrasts=(
        _CompositeContrast("ADX_RSI_vs_ADX", "ADX_RSI_EQUAL_WEIGHT", "ADX_ONLY"),
        _CompositeContrast("ADX_RSI_vs_RSI", "ADX_RSI_EQUAL_WEIGHT", "RSI_ONLY"),
        _CompositeContrast(
            "VOLUME_ADDON_vs_ADX_RSI", "ADX_RSI_VOLUME_EQUAL_WEIGHT", "ADX_RSI_EQUAL_WEIGHT",
        ),
    ),
    horizons=(5, 10, 20),
    outcome_fields=("stock_forward_return_pct", "excess_forward_return_pct_points"),
    blocks=NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2.blocks,
    rationale=MappingProxyType({
        "adx": "ADX retained the strongest independent temporal association.",
        "rsi": "RSI retained incremental association beyond ADX.",
        "volume": "Volume was relatively independent but had weak incremental IC.",
        "excluded": "EMA-distance and momentum-family factors are excluded from this deliberately minimal comparison.",
    }),
)


@dataclass(frozen=True, slots=True)
class DailyCompositeEvaluation:
    source_dataset_identity: str
    source_dataset_content_identity: str
    source_bounded_content_identity: str
    specification_fingerprint: str
    signal_date: str
    policy_name: str
    factor_weights: tuple[CompositeFactorWeight, ...]
    policy_fingerprint: str
    horizon_sessions: int
    outcome_field: str
    total_observation_count: int
    outcome_available_count: int
    outcome_unavailable_count: int
    outcome_missing_or_nonfinite_available_count: int
    factor_missing_or_nonfinite_counts: Mapping[str, int]
    shared_listwise_finite_count: int
    shared_excluded_count: int
    shared_coverage_pct: float
    rank_ic: float | None
    absolute_rank_ic: float | None
    low_bucket_count: int
    high_bucket_count: int
    low_bucket_mean_outcome: float | None
    high_bucket_mean_outcome: float | None
    low_bucket_median_outcome: float | None
    high_bucket_median_outcome: float | None
    high_minus_low_mean_spread: float | None
    high_minus_low_median_spread: float | None
    ic_undefined_reason: str | None
    spread_undefined_reason: str | None
    shared_sample_evidence_sha256: str
    identity: str

    def __post_init__(self) -> None:
        if (self.rank_ic is None) != (self.ic_undefined_reason is not None):
            raise ValueError("rank IC and undefined reason do not reconcile")
        if (self.high_minus_low_mean_spread is None) != (self.spread_undefined_reason is not None):
            raise ValueError("spread and undefined reason do not reconcile")
        if self.ic_undefined_reason is not None and self.ic_undefined_reason not in _IC_REASONS:
            raise ValueError("unsupported composite IC undefined reason")
        if self.spread_undefined_reason is not None and self.spread_undefined_reason not in _SPREAD_REASONS:
            raise ValueError("unsupported composite spread undefined reason")
        object.__setattr__(self, "factor_weights", tuple(self.factor_weights))
        if set(self.factor_missing_or_nonfinite_counts) != set(COMMON_FACTORS):
            raise ValueError("factor missing/nonfinite diagnostics do not reconcile")
        factor_counts = MappingProxyType({
            factor: int(self.factor_missing_or_nonfinite_counts[factor])
            for factor in COMMON_FACTORS
        })
        object.__setattr__(self, "factor_missing_or_nonfinite_counts", factor_counts)
        _text(self.identity, name="daily composite identity")


@dataclass(frozen=True, slots=True)
class CompositeBlockEvaluation:
    policy_name: str
    factor_weights: tuple[CompositeFactorWeight, ...]
    policy_fingerprint: str
    horizon_sessions: int
    outcome_field: str
    block_name: str
    block_start_date: str
    block_end_date: str
    total_signal_date_count: int
    minimum_sample_date_count: int
    ic_defined_date_count: int
    ic_coverage_pct: float
    spread_defined_date_count: int
    spread_coverage_pct: float
    mean_daily_rank_ic: float | None
    median_daily_rank_ic: float | None
    population_std_daily_rank_ic: float | None
    minimum_daily_rank_ic: float | None
    maximum_daily_rank_ic: float | None
    mean_absolute_daily_rank_ic: float | None
    median_absolute_daily_rank_ic: float | None
    positive_ic_date_count: int
    zero_ic_date_count: int
    negative_ic_date_count: int
    positive_ic_rate: float | None
    zero_ic_rate: float | None
    negative_ic_rate: float | None
    mean_daily_mean_spread: float | None
    median_daily_mean_spread: float | None
    population_std_daily_mean_spread: float | None
    minimum_daily_mean_spread: float | None
    maximum_daily_mean_spread: float | None
    mean_daily_median_spread: float | None
    median_daily_median_spread: float | None
    positive_spread_date_count: int
    zero_spread_date_count: int
    negative_spread_date_count: int
    positive_spread_rate: float | None
    zero_spread_rate: float | None
    negative_spread_rate: float | None
    average_low_bucket_size: float | None
    average_high_bucket_size: float | None
    average_shared_cross_section_size: float | None
    median_shared_cross_section_size: float | None
    included_daily_identity_count: int
    included_daily_identities_sha256: str
    warnings: tuple[str, ...]
    identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "factor_weights", tuple(self.factor_weights))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        _text(self.identity, name="composite block identity")


@dataclass(frozen=True, slots=True)
class CompositeEvaluationSummary:
    policy_name: str
    factor_weights: tuple[CompositeFactorWeight, ...]
    policy_fingerprint: str
    horizon_sessions: int
    outcome_field: str
    total_signal_date_count: int
    minimum_sample_date_count: int
    ic_defined_date_count: int
    ic_coverage_pct: float
    spread_defined_date_count: int
    spread_coverage_pct: float
    mean_daily_rank_ic: float | None
    median_daily_rank_ic: float | None
    population_std_daily_rank_ic: float | None
    minimum_daily_rank_ic: float | None
    maximum_daily_rank_ic: float | None
    mean_absolute_daily_rank_ic: float | None
    median_absolute_daily_rank_ic: float | None
    positive_ic_date_count: int
    zero_ic_date_count: int
    negative_ic_date_count: int
    positive_ic_rate: float | None
    zero_ic_rate: float | None
    negative_ic_rate: float | None
    mean_daily_mean_spread: float | None
    median_daily_mean_spread: float | None
    population_std_daily_mean_spread: float | None
    minimum_daily_mean_spread: float | None
    maximum_daily_mean_spread: float | None
    mean_daily_median_spread: float | None
    median_daily_median_spread: float | None
    positive_spread_date_count: int
    zero_spread_date_count: int
    negative_spread_date_count: int
    positive_spread_rate: float | None
    zero_spread_rate: float | None
    negative_spread_rate: float | None
    average_low_bucket_size: float | None
    average_high_bucket_size: float | None
    average_shared_cross_section_size: float | None
    median_shared_cross_section_size: float | None
    included_daily_identity_count: int
    included_daily_identities_sha256: str
    ordered_block_identities: tuple[str, ...]
    blocks_meeting_ic_review_count: int
    blocks_meeting_spread_review_count: int
    chronological_block_mean_ic_sign_flip_count: int
    chronological_block_mean_spread_sign_flip_count: int
    all_blocks_positive_ic: bool
    all_blocks_negative_ic: bool
    all_blocks_positive_spread: bool
    all_blocks_negative_spread: bool
    minimum_block_mean_ic: float | None
    maximum_block_mean_ic: float | None
    range_block_mean_ic: float | None
    minimum_block_mean_spread: float | None
    maximum_block_mean_spread: float | None
    range_block_mean_spread: float | None
    largest_absolute_block_mean_ic_concentration: float | None
    largest_absolute_block_mean_spread_concentration: float | None
    warnings: tuple[str, ...]
    identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "factor_weights", tuple(self.factor_weights))
        object.__setattr__(self, "ordered_block_identities", tuple(self.ordered_block_identities))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        _text(self.identity, name="composite summary identity")


@dataclass(frozen=True, slots=True)
class _ContrastBlockEvaluation:
    contrast_name: str
    variant_policy: str
    reference_policy: str
    horizon_sessions: int
    outcome_field: str
    block_name: str
    block_start_date: str
    block_end_date: str
    paired_ic_date_count: int
    mean_daily_ic_delta: float | None
    median_daily_ic_delta: float | None
    positive_ic_delta_count: int
    zero_ic_delta_count: int
    negative_ic_delta_count: int
    positive_ic_delta_rate: float | None
    zero_ic_delta_rate: float | None
    negative_ic_delta_rate: float | None
    paired_spread_date_count: int
    mean_daily_spread_delta: float | None
    median_daily_spread_delta: float | None
    positive_spread_delta_count: int
    zero_spread_delta_count: int
    negative_spread_delta_count: int
    positive_spread_delta_rate: float | None
    zero_spread_delta_rate: float | None
    negative_spread_delta_rate: float | None
    included_daily_pair_identity_count: int
    included_daily_pair_identities_sha256: str
    identity: str

    def __post_init__(self) -> None:
        _text(self.identity, name="composite contrast block identity")


@dataclass(frozen=True, slots=True)
class _ContrastSummary:
    contrast_name: str
    variant_policy: str
    reference_policy: str
    horizon_sessions: int
    outcome_field: str
    paired_ic_date_count: int
    mean_daily_ic_delta: float | None
    median_daily_ic_delta: float | None
    positive_ic_delta_count: int
    zero_ic_delta_count: int
    negative_ic_delta_count: int
    positive_ic_delta_rate: float | None
    zero_ic_delta_rate: float | None
    negative_ic_delta_rate: float | None
    paired_spread_date_count: int
    mean_daily_spread_delta: float | None
    median_daily_spread_delta: float | None
    positive_spread_delta_count: int
    zero_spread_delta_count: int
    negative_spread_delta_count: int
    positive_spread_delta_rate: float | None
    zero_spread_delta_rate: float | None
    negative_spread_delta_rate: float | None
    ordered_block_identities: tuple[str, ...]
    identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "ordered_block_identities", tuple(self.ordered_block_identities))
        _text(self.identity, name="composite contrast summary identity")


@dataclass(frozen=True, slots=True)
class PanelCompositeAnalysisResult:
    source_dataset_identity: str
    source_dataset_content_identity: str
    source_bounded_content_identity: str
    source_observation_index_identity: str
    source_observation_content_identity: str
    source_feature_panel_identity: str
    source_feature_content_identity: str
    source_outcome_panel_identity: str
    source_outcome_content_identity: str
    specification_fingerprint: str
    daily_evaluations: tuple[DailyCompositeEvaluation, ...]
    block_evaluations: tuple[CompositeBlockEvaluation, ...]
    summaries: tuple[CompositeEvaluationSummary, ...]
    contrast_block_evaluations: tuple[_ContrastBlockEvaluation, ...]
    contrast_summaries: tuple[_ContrastSummary, ...]
    limitations_metadata: Mapping[str, Any]
    future_looking: bool = True
    permitted_use: str = PERMITTED_USE
    production_signal_safe: bool = False
    identity: str = field(init=False)
    _daily_by_key: Mapping[tuple[str, str, int, str], DailyCompositeEvaluation] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.future_looking is not True or self.permitted_use != PERMITTED_USE or self.production_signal_safe is not False:
            raise ValueError("composite analysis is restricted to offline research")
        daily = tuple(self.daily_evaluations)
        blocks = tuple(self.block_evaluations)
        summaries = tuple(self.summaries)
        contrast_blocks = tuple(self.contrast_block_evaluations)
        contrasts = tuple(self.contrast_summaries)
        daily_map = {
            (item.signal_date, item.policy_name, item.horizon_sessions, item.outcome_field): item
            for item in daily
        }
        if len(daily_map) != len(daily):
            raise ValueError("duplicate daily composite key")
        for name in (
            "source_dataset_identity", "source_dataset_content_identity",
            "source_bounded_content_identity", "source_observation_index_identity",
            "source_observation_content_identity", "source_feature_panel_identity",
            "source_feature_content_identity", "source_outcome_panel_identity",
            "source_outcome_content_identity", "specification_fingerprint",
        ):
            _text(getattr(self, name), name=name)
        if any(
            item.source_dataset_identity != self.source_dataset_identity
            or item.source_dataset_content_identity != self.source_dataset_content_identity
            or item.source_bounded_content_identity != self.source_bounded_content_identity
            or item.specification_fingerprint != self.specification_fingerprint
            for item in daily
        ):
            raise ValueError("daily composite provenance is inconsistent with the result")
        metadata = MappingProxyType({
            str(key): _frozen(value) for key, value in sorted(self.limitations_metadata.items())
        })
        payload = {
            "contract": {"name": COMPOSITE_CONTRACT, "version": COMPOSITE_VERSION},
            "source_dataset_identity": self.source_dataset_identity,
            "source_dataset_content_identity": self.source_dataset_content_identity,
            "source_bounded_content_identity": self.source_bounded_content_identity,
            "source_provenance": {
                "observation_index": self.source_observation_index_identity,
                "observation_content": self.source_observation_content_identity,
                "feature_panel": self.source_feature_panel_identity,
                "feature_content": self.source_feature_content_identity,
                "outcome_panel": self.source_outcome_panel_identity,
                "outcome_content": self.source_outcome_content_identity,
            },
            "specification_fingerprint": self.specification_fingerprint,
            "ordered_daily_identities": [item.identity for item in daily],
            "ordered_block_identities": [item.identity for item in blocks],
            "ordered_summary_identities": [item.identity for item in summaries],
            "ordered_contrast_block_identities": [item.identity for item in contrast_blocks],
            "ordered_contrast_summary_identities": [item.identity for item in contrasts],
            "counts": {
                "daily": len(daily), "blocks": len(blocks), "summaries": len(summaries),
                "contrast_blocks": len(contrast_blocks), "contrast_summaries": len(contrasts),
            },
            "research_boundary": {
                "future_looking": True, "permitted_use": PERMITTED_USE,
                "production_signal_safe": False,
            },
            "limitations": metadata,
        }
        object.__setattr__(self, "daily_evaluations", daily)
        object.__setattr__(self, "block_evaluations", blocks)
        object.__setattr__(self, "summaries", summaries)
        object.__setattr__(self, "contrast_block_evaluations", contrast_blocks)
        object.__setattr__(self, "contrast_summaries", contrasts)
        object.__setattr__(self, "limitations_metadata", metadata)
        object.__setattr__(self, "_daily_by_key", MappingProxyType(daily_map))
        object.__setattr__(self, "identity", _hash(payload))

    def daily_for(self, signal_date: str, policy_name: str, horizon_sessions: int, outcome_field: str) -> DailyCompositeEvaluation:
        return self._daily_by_key[(signal_date, policy_name, horizon_sessions, outcome_field)]


def _validated_dataset(dataset: Any, spec: PanelCompositeAnalysisSpec) -> tuple[pd.DataFrame, tuple[str, ...], str]:
    import pandas as pd

    from quantlab.panels.outcome_contracts import PanelForwardOutcomeAvailability
    from quantlab.panels.research_dataset_contracts import (
        PERMITTED_USE as DATASET_PERMITTED_USE,
        POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1,
        PointInTimeResearchDataset,
    )

    if not isinstance(dataset, PointInTimeResearchDataset):
        raise TypeError("dataset must be PointInTimeResearchDataset")
    if dataset.spec.fingerprint != POINT_IN_TIME_NEUTRAL_RESEARCH_DATASET_V1.fingerprint:
        raise ValueError("dataset specification is not the neutral Phase 5.3B contract")
    if (
        dataset.spec.future_looking is not True
        or dataset.spec.production_signal_safe is not False
        or dataset.spec.permitted_use != DATASET_PERMITTED_USE
        or dataset.metadata.get("future_looking") is not True
        or dataset.metadata.get("production_signal_safe") is not False
        or dataset.metadata.get("permitted_use") != DATASET_PERMITTED_USE
    ):
        raise ValueError("dataset must retain its future-looking offline-research boundary")
    for name in (
        "identity", "content_identity", "observation_index_identity",
        "observation_content_identity", "feature_panel_identity", "feature_content_identity",
        "outcome_panel_identity", "outcome_content_identity",
    ):
        _text(getattr(dataset, name), name=f"dataset {name}")
    features = set(dataset.spec.feature_columns)
    forbidden = set(dataset.spec.forbidden_predictor_columns)
    if any(factor not in features or factor in forbidden for factor in COMMON_FACTORS):
        raise ValueError("composite factor is absent from point-in-time predictor fields")
    frame = dataset.evaluation_frame()
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("dataset.evaluation_frame() must return a DataFrame")
    frame = frame.copy(deep=True)
    if tuple(frame.columns) != tuple(dataset.spec.output_columns):
        raise ValueError("evaluation frame schema is inconsistent with Phase 5.3B")
    if len(frame) != dataset.observation_row_count or dataset.metadata.get("observation_row_count") != len(frame):
        raise ValueError("dataset population count does not reconcile")
    keys = tuple(
        (_date_text(row.session_date, name="source signal date"), _text(row.symbol, name="source symbol").upper())
        for row in frame.loc[:, ["session_date", "symbol"]].itertuples(index=False)
    )
    if len(set(keys)) != len(keys):
        raise ValueError("dataset contains duplicate observation keys")
    frame.loc[:, "session_date"] = tuple(item[0] for item in keys)
    frame.loc[:, "symbol"] = tuple(item[1] for item in keys)
    frame = frame.sort_values(["session_date", "symbol"], kind="mergesort").reset_index(drop=True)
    signal_dates = tuple(_date_text(item.session_date, name="benchmark signal date") for item in dataset.session_audit)
    if (
        not signal_dates or signal_dates != tuple(sorted(signal_dates))
        or len(set(signal_dates)) != len(signal_dates)
        or signal_dates[0] < OVERALL_START_DATE or signal_dates[-1] > OVERALL_END_DATE
        or not set(frame["session_date"]).issubset(signal_dates)
    ):
        raise ValueError("source signal dates are invalid or outside the composite interval")
    supported_statuses = {item.value for item in PanelForwardOutcomeAvailability}
    required = set(COMMON_FACTORS)
    for horizon in spec.horizons:
        status = f"outcome_{horizon}__availability"
        required.add(status)
        required.update(_OUTCOME_COLUMNS[outcome].format(horizon=horizon) for outcome in spec.outcome_fields)
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError("dataset is missing composite-analysis columns: " + ", ".join(missing))
    for horizon in spec.horizons:
        status = f"outcome_{horizon}__availability"
        invalid = set(frame[status].astype(str)).difference(supported_statuses)
        if invalid:
            raise ValueError(f"invalid outcome availability label: {sorted(invalid)}")
    for column in required.difference({f"outcome_{h}__availability" for h in spec.horizons}):
        for value in frame[column]:
            _finite(value, column=column)
    bounded_rows = tuple(
        (
            row.session_date, row.symbol,
            tuple(_identity_value(getattr(row, factor)) for factor in COMMON_FACTORS),
            tuple(
                (
                    horizon, str(getattr(row, f"outcome_{horizon}__availability")),
                    tuple(_identity_value(getattr(row, _OUTCOME_COLUMNS[outcome].format(horizon=horizon))) for outcome in spec.outcome_fields),
                )
                for horizon in spec.horizons
            ),
        )
        for row in frame.itertuples(index=False)
    )
    bounded = _hash({
        "source_content": dataset.content_identity,
        "observation_content": dataset.observation_content_identity,
        "feature_content": dataset.feature_content_identity,
        "outcome_content": dataset.outcome_content_identity,
        "dataset_specification": dataset.spec.fingerprint,
        "composite_specification": spec.fingerprint,
        "signal_dates": list(signal_dates),
        "common_factor_union": list(COMMON_FACTORS),
        "rows": bounded_rows,
    })
    return frame, signal_dates, bounded


def _daily_records(
    rows: pd.DataFrame,
    *,
    dataset: Any,
    bounded_identity: str,
    spec: PanelCompositeAnalysisSpec,
    signal_date: str,
    horizon: int,
    outcome_field: str,
) -> tuple[DailyCompositeEvaluation, ...]:
    status_column = f"outcome_{horizon}__availability"
    outcome_column = _OUTCOME_COLUMNS[outcome_field].format(horizon=horizon)
    ordered = rows.sort_values("symbol", kind="mergesort")
    eligible: list[tuple[str, tuple[float, float, float], float]] = []
    observation_evidence = []
    outcome_available = 0
    outcome_missing_or_nonfinite_available = 0
    factor_missing_or_nonfinite = {factor: 0 for factor in COMMON_FACTORS}
    for row in ordered.itertuples(index=False):
        values = row._asdict()
        status = str(values[status_column])
        factors = tuple(_finite(values[factor], column=factor) for factor in COMMON_FACTORS)
        outcome = _finite(values[outcome_column], column=outcome_column)
        available = status == "AVAILABLE"
        outcome_available += available
        outcome_missing_or_nonfinite_available += available and outcome is None
        for factor, value in zip(COMMON_FACTORS, factors, strict=True):
            factor_missing_or_nonfinite[factor] += value is None
        listwise = available and outcome is not None and all(value is not None for value in factors)
        observation_evidence.append((values["symbol"], status, factors, outcome, listwise))
        if listwise:
            eligible.append((
                str(values["symbol"]),
                tuple(float(value) for value in factors),  # type: ignore[arg-type]
                float(outcome),
            ))
    factor_values = tuple(tuple(item[1][index] for item in eligible) for index in range(3))
    factor_ranks = tuple(_average_ranks(values) for values in factor_values)
    factor_percentiles = tuple(_percentiles(ranks) for ranks in factor_ranks)
    outcome_values = tuple(item[2] for item in eligible)
    outcome_ranks = _average_ranks(outcome_values)
    shared_hash = _hash(tuple(
        (symbol, factor_values_for_symbol, outcome)
        for symbol, factor_values_for_symbol, outcome in eligible
    ))
    records = []
    percentiles_by_factor = {
        factor: factor_percentiles[index] for index, factor in enumerate(COMMON_FACTORS)
    }
    for policy in spec.policies:
        scores = _composite_scores(policy, percentiles_by_factor)
        score_ranks = _average_ranks(scores)
        score_percentiles = _percentiles(score_ranks)
        rank_ic = None
        ic_reason = None
        if outcome_available == 0:
            ic_reason = "unavailable_outcome_status"
        elif len(eligible) < spec.minimum_shared_count:
            ic_reason = "fewer_than_minimum_shared_observations"
        elif len(set(scores)) == 1:
            ic_reason = "constant_composite_score"
        elif len(set(outcome_values)) == 1:
            ic_reason = "constant_outcome"
        else:
            rank_ic = _pearson(score_ranks, outcome_ranks)
            if rank_ic is None:
                ic_reason = "correlation_denominator_zero"
        low_indexes: tuple[int, ...] = ()
        high_indexes: tuple[int, ...] = ()
        spread_reason = None
        if outcome_available == 0:
            spread_reason = "unavailable_outcome_status"
        elif len(eligible) < spec.minimum_shared_count:
            spread_reason = "fewer_than_minimum_shared_observations"
        else:
            low_indexes = tuple(index for index, value in enumerate(score_percentiles) if value <= spec.low_bucket_max_percentile)
            high_indexes = tuple(index for index, value in enumerate(score_percentiles) if value >= spec.high_bucket_min_percentile)
            if set(low_indexes).intersection(high_indexes):
                spread_reason = "overlapping_buckets"
            elif not low_indexes:
                spread_reason = "empty_low_bucket"
            elif not high_indexes:
                spread_reason = "empty_high_bucket"
        low = tuple(outcome_values[index] for index in low_indexes) if spread_reason is None else ()
        high = tuple(outcome_values[index] for index in high_indexes) if spread_reason is None else ()
        low_mean, high_mean = _mean(low), _mean(high)
        low_median, high_median = _median(low), _median(high)
        mean_spread = None if low_mean is None or high_mean is None else high_mean - low_mean
        median_spread = None if low_median is None or high_median is None else high_median - low_median
        evidence = tuple(
            (
                symbol, factor_values_for_symbol,
                tuple(ranks[index] for ranks in factor_ranks),
                tuple(values[index] for values in factor_percentiles),
                scores[index], score_ranks[index], score_percentiles[index],
                outcome, outcome_ranks[index], index in low_indexes, index in high_indexes,
            )
            for index, (symbol, factor_values_for_symbol, outcome) in enumerate(eligible)
        )
        identity = _hash({
            "source_dataset_identity": dataset.identity,
            "source_dataset_content_identity": dataset.content_identity,
            "source_bounded_content_identity": bounded_identity,
            "specification_fingerprint": spec.fingerprint,
            "signal_date": signal_date,
            "policy": policy.canonical_content(),
            "horizon_sessions": horizon,
            "outcome_field": outcome_field,
            "counts": {
                "total": len(ordered), "outcome_available": outcome_available,
                "outcome_unavailable": len(ordered) - outcome_available,
                "outcome_missing_or_nonfinite_available": outcome_missing_or_nonfinite_available,
                "factor_missing_or_nonfinite": factor_missing_or_nonfinite,
                "shared_listwise": len(eligible),
                "shared_excluded": len(ordered) - len(eligible),
            },
            "complete_observation_availability_and_missingness_evidence": tuple(
                observation_evidence
            ),
            "complete_shared_sample_rank_composite_outcome_bucket_evidence": evidence,
            "shared_sample_evidence_sha256": shared_hash,
            "rank_ic": rank_ic,
            "absolute_rank_ic": None if rank_ic is None else abs(rank_ic),
            "bucket_statistics": {
                "low_count": len(low), "high_count": len(high),
                "low_mean": low_mean, "high_mean": high_mean,
                "low_median": low_median, "high_median": high_median,
                "mean_spread": mean_spread, "median_spread": median_spread,
            },
            "undefined_reasons": {"ic": ic_reason, "spread": spread_reason},
        })
        records.append(DailyCompositeEvaluation(
            source_dataset_identity=dataset.identity,
            source_dataset_content_identity=dataset.content_identity,
            source_bounded_content_identity=bounded_identity,
            specification_fingerprint=spec.fingerprint,
            signal_date=signal_date,
            policy_name=policy.name,
            factor_weights=policy.factor_weights,
            policy_fingerprint=policy.fingerprint,
            horizon_sessions=horizon,
            outcome_field=outcome_field,
            total_observation_count=len(ordered),
            outcome_available_count=outcome_available,
            outcome_unavailable_count=len(ordered) - outcome_available,
            outcome_missing_or_nonfinite_available_count=outcome_missing_or_nonfinite_available,
            factor_missing_or_nonfinite_counts=factor_missing_or_nonfinite,
            shared_listwise_finite_count=len(eligible),
            shared_excluded_count=len(ordered) - len(eligible),
            shared_coverage_pct=0.0 if not len(ordered) else len(eligible) / len(ordered) * 100.0,
            rank_ic=rank_ic,
            absolute_rank_ic=None if rank_ic is None else abs(rank_ic),
            low_bucket_count=len(low), high_bucket_count=len(high),
            low_bucket_mean_outcome=low_mean, high_bucket_mean_outcome=high_mean,
            low_bucket_median_outcome=low_median, high_bucket_median_outcome=high_median,
            high_minus_low_mean_spread=mean_spread,
            high_minus_low_median_spread=median_spread,
            ic_undefined_reason=ic_reason, spread_undefined_reason=spread_reason,
            shared_sample_evidence_sha256=shared_hash, identity=identity,
        ))
    return tuple(records)


def _aggregate(items: tuple[DailyCompositeEvaluation, ...], minimum: int) -> dict[str, Any]:
    ic = tuple(item.rank_ic for item in items if item.rank_ic is not None)
    spreads = tuple(item.high_minus_low_mean_spread for item in items if item.high_minus_low_mean_spread is not None)
    median_spreads = tuple(item.high_minus_low_median_spread for item in items if item.high_minus_low_median_spread is not None)
    bucket_items = tuple(item for item in items if item.spread_undefined_reason is None)
    sizes = tuple(float(item.shared_listwise_finite_count) for item in items)
    total = len(items)
    rate = lambda values, predicate: None if not values else sum(predicate(value) for value in values) / len(values)
    return {
        "total_signal_date_count": total,
        "minimum_sample_date_count": sum(item.shared_listwise_finite_count >= minimum for item in items),
        "ic_defined_date_count": len(ic), "ic_coverage_pct": 0.0 if not total else len(ic) / total * 100.0,
        "spread_defined_date_count": len(spreads), "spread_coverage_pct": 0.0 if not total else len(spreads) / total * 100.0,
        "mean_daily_rank_ic": _mean(ic), "median_daily_rank_ic": _median(ic),
        "population_std_daily_rank_ic": _std(ic),
        "minimum_daily_rank_ic": None if not ic else min(ic), "maximum_daily_rank_ic": None if not ic else max(ic),
        "mean_absolute_daily_rank_ic": _mean(tuple(abs(value) for value in ic)),
        "median_absolute_daily_rank_ic": _median(tuple(abs(value) for value in ic)),
        "positive_ic_date_count": sum(value > 0 for value in ic), "zero_ic_date_count": sum(value == 0 for value in ic),
        "negative_ic_date_count": sum(value < 0 for value in ic),
        "positive_ic_rate": rate(ic, lambda value: value > 0), "zero_ic_rate": rate(ic, lambda value: value == 0),
        "negative_ic_rate": rate(ic, lambda value: value < 0),
        "mean_daily_mean_spread": _mean(spreads), "median_daily_mean_spread": _median(spreads),
        "population_std_daily_mean_spread": _std(spreads),
        "minimum_daily_mean_spread": None if not spreads else min(spreads),
        "maximum_daily_mean_spread": None if not spreads else max(spreads),
        "mean_daily_median_spread": _mean(median_spreads), "median_daily_median_spread": _median(median_spreads),
        "positive_spread_date_count": sum(value > 0 for value in spreads), "zero_spread_date_count": sum(value == 0 for value in spreads),
        "negative_spread_date_count": sum(value < 0 for value in spreads),
        "positive_spread_rate": rate(spreads, lambda value: value > 0), "zero_spread_rate": rate(spreads, lambda value: value == 0),
        "negative_spread_rate": rate(spreads, lambda value: value < 0),
        "average_low_bucket_size": _mean(tuple(float(item.low_bucket_count) for item in bucket_items)),
        "average_high_bucket_size": _mean(tuple(float(item.high_bucket_count) for item in bucket_items)),
        "average_shared_cross_section_size": _mean(sizes), "median_shared_cross_section_size": _median(sizes),
    }


def _warning(aggregate: Mapping[str, Any]) -> tuple[str, ...]:
    result = ["descriptive_predeclared_composite_comparison_only"]
    if aggregate["ic_defined_date_count"] == 0:
        result.append("rank_ic_undefined_for_entire_scope")
    if aggregate["spread_defined_date_count"] == 0:
        result.append("bucket_spread_undefined_for_entire_scope")
    return tuple(result)


def _aggregate_identity(kind: str, payload: Mapping[str, Any]) -> str:
    return _hash({"kind": kind, **dict(payload)})


def _contrast_metrics(pairs: tuple[tuple[DailyCompositeEvaluation, DailyCompositeEvaluation], ...]) -> dict[str, Any]:
    ic = tuple(
        variant.rank_ic - reference.rank_ic
        for variant, reference in pairs
        if variant.rank_ic is not None and reference.rank_ic is not None
    )
    spread = tuple(
        variant.high_minus_low_mean_spread - reference.high_minus_low_mean_spread
        for variant, reference in pairs
        if variant.high_minus_low_mean_spread is not None and reference.high_minus_low_mean_spread is not None
    )
    def fields(prefix: str, values: tuple[float, ...]) -> dict[str, Any]:
        return {
            f"paired_{prefix}_date_count": len(values),
            f"mean_daily_{prefix}_delta": _mean(values),
            f"median_daily_{prefix}_delta": _median(values),
            f"positive_{prefix}_delta_count": sum(value > 0 for value in values),
            f"zero_{prefix}_delta_count": sum(value == 0 for value in values),
            f"negative_{prefix}_delta_count": sum(value < 0 for value in values),
            f"positive_{prefix}_delta_rate": None if not values else sum(value > 0 for value in values) / len(values),
            f"zero_{prefix}_delta_rate": None if not values else sum(value == 0 for value in values) / len(values),
            f"negative_{prefix}_delta_rate": None if not values else sum(value < 0 for value in values) / len(values),
        }
    return {**fields("ic", ic), **fields("spread", spread)}


def evaluate_panel_composites(
    dataset: Any,
    spec: PanelCompositeAnalysisSpec = NEUTRAL_ADX_RSI_COMPOSITE_COMPARISON_V1,
) -> PanelCompositeAnalysisResult:
    """Evaluate four frozen composite policies without I/O or policy selection."""
    if not isinstance(spec, PanelCompositeAnalysisSpec):
        raise TypeError("spec must be PanelCompositeAnalysisSpec")
    frame, signal_dates, bounded = _validated_dataset(dataset, spec)
    by_date = {
        signal_date: frame.loc[frame["session_date"].eq(signal_date)].copy(deep=True)
        for signal_date in signal_dates
    }
    daily = tuple(
        item
        for signal_date in signal_dates
        for horizon in spec.horizons
        for outcome in spec.outcome_fields
        for item in _daily_records(
            by_date[signal_date], dataset=dataset, bounded_identity=bounded, spec=spec,
            signal_date=signal_date, horizon=horizon, outcome_field=outcome,
        )
    )
    daily_map = {
        (item.signal_date, item.policy_name, item.horizon_sessions, item.outcome_field): item
        for item in daily
    }
    expected_daily = tuple(
        (signal_date, policy.name, horizon, outcome)
        for signal_date in signal_dates for horizon in spec.horizons
        for outcome in spec.outcome_fields for policy in spec.policies
    )
    if tuple(daily_map) != expected_daily or len(daily_map) != len(signal_dates) * 24:
        raise ValueError("composite daily coverage or ordering does not reconcile")
    for signal_date in signal_dates:
        for horizon in spec.horizons:
            for outcome in spec.outcome_fields:
                group = tuple(daily_map[(signal_date, policy.name, horizon, outcome)] for policy in spec.policies)
                if len({item.shared_sample_evidence_sha256 for item in group}) != 1 or len({item.shared_listwise_finite_count for item in group}) != 1:
                    raise ValueError("composite policies do not share an identical comparison sample")

    blocks: list[CompositeBlockEvaluation] = []
    summaries: list[CompositeEvaluationSummary] = []
    for policy in spec.policies:
        for horizon in spec.horizons:
            for outcome in spec.outcome_fields:
                items = tuple(daily_map[(signal_date, policy.name, horizon, outcome)] for signal_date in signal_dates)
                policy_blocks = []
                for definition in spec.blocks:
                    included = tuple(item for item in items if definition.contains(item.signal_date))
                    aggregate = _aggregate(included, spec.minimum_shared_count)
                    ids = tuple(item.identity for item in included)
                    payload = {
                        "source_dataset_identity": dataset.identity, "source_bounded_content_identity": bounded,
                        "specification_fingerprint": spec.fingerprint, "policy": policy.canonical_content(),
                        "horizon": horizon, "outcome": outcome, "block": definition.canonical_content(),
                        "aggregate": aggregate, "ordered_daily_identities": list(ids),
                        "warnings": list(_warning(aggregate)),
                    }
                    block = CompositeBlockEvaluation(
                        policy_name=policy.name, factor_weights=policy.factor_weights,
                        policy_fingerprint=policy.fingerprint, horizon_sessions=horizon,
                        outcome_field=outcome, block_name=definition.name,
                        block_start_date=definition.start_date, block_end_date=definition.end_date,
                        **aggregate, included_daily_identity_count=len(ids),
                        included_daily_identities_sha256=_identity_hash(ids),
                        warnings=_warning(aggregate), identity=_aggregate_identity("composite_block", payload),
                    )
                    blocks.append(block)
                    policy_blocks.append(block)
                aggregate = _aggregate(items, spec.minimum_shared_count)
                ids = tuple(item.identity for item in items)
                block_ids = tuple(item.identity for item in policy_blocks)
                ic_means = tuple(item.mean_daily_rank_ic for item in policy_blocks if item.mean_daily_rank_ic is not None)
                spread_means = tuple(item.mean_daily_mean_spread for item in policy_blocks if item.mean_daily_mean_spread is not None)
                summary_extra = {
                    "ordered_block_identities": block_ids,
                    "blocks_meeting_ic_review_count": sum(
                        item.ic_defined_date_count >= MINIMUM_BLOCK_REVIEW_DATES
                        for item in policy_blocks
                    ),
                    "blocks_meeting_spread_review_count": sum(
                        item.spread_defined_date_count >= MINIMUM_BLOCK_REVIEW_DATES
                        for item in policy_blocks
                    ),
                    "chronological_block_mean_ic_sign_flip_count": _sign_flips(tuple(item.mean_daily_rank_ic for item in policy_blocks)),
                    "chronological_block_mean_spread_sign_flip_count": _sign_flips(tuple(item.mean_daily_mean_spread for item in policy_blocks)),
                    "all_blocks_positive_ic": len(ic_means) == 4 and all(value > 0 for value in ic_means),
                    "all_blocks_negative_ic": len(ic_means) == 4 and all(value < 0 for value in ic_means),
                    "all_blocks_positive_spread": len(spread_means) == 4 and all(value > 0 for value in spread_means),
                    "all_blocks_negative_spread": len(spread_means) == 4 and all(value < 0 for value in spread_means),
                    "minimum_block_mean_ic": None if not ic_means else min(ic_means),
                    "maximum_block_mean_ic": None if not ic_means else max(ic_means),
                    "range_block_mean_ic": None if not ic_means else max(ic_means) - min(ic_means),
                    "minimum_block_mean_spread": None if not spread_means else min(spread_means),
                    "maximum_block_mean_spread": None if not spread_means else max(spread_means),
                    "range_block_mean_spread": None if not spread_means else max(spread_means) - min(spread_means),
                    "largest_absolute_block_mean_ic_concentration": _concentration(ic_means),
                    "largest_absolute_block_mean_spread_concentration": _concentration(spread_means),
                }
                payload = {
                    "source_dataset_identity": dataset.identity, "source_bounded_content_identity": bounded,
                    "specification_fingerprint": spec.fingerprint, "policy": policy.canonical_content(),
                    "horizon": horizon, "outcome": outcome, "aggregate": aggregate,
                    "ordered_daily_identities": list(ids), **summary_extra,
                    "warnings": list(_warning(aggregate)),
                }
                summaries.append(CompositeEvaluationSummary(
                    policy_name=policy.name, factor_weights=policy.factor_weights,
                    policy_fingerprint=policy.fingerprint, horizon_sessions=horizon,
                    outcome_field=outcome, **aggregate,
                    included_daily_identity_count=len(ids),
                    included_daily_identities_sha256=_identity_hash(ids), **summary_extra,
                    warnings=_warning(aggregate), identity=_aggregate_identity("composite_summary", payload),
                ))

    contrast_blocks: list[_ContrastBlockEvaluation] = []
    contrast_summaries: list[_ContrastSummary] = []
    for contrast in spec.contrasts:
        for horizon in spec.horizons:
            for outcome in spec.outcome_fields:
                pairs = tuple(
                    (
                        daily_map[(signal_date, contrast.variant_policy, horizon, outcome)],
                        daily_map[(signal_date, contrast.reference_policy, horizon, outcome)],
                    )
                    for signal_date in signal_dates
                )
                block_ids = []
                for definition in spec.blocks:
                    included = tuple(pair for pair in pairs if definition.contains(pair[0].signal_date))
                    metrics = _contrast_metrics(included)
                    pair_ids = tuple(_hash([left.identity, right.identity]) for left, right in included)
                    payload = {
                        "source_dataset_identity": dataset.identity,
                        "source_dataset_content_identity": dataset.content_identity,
                        "source_bounded_content_identity": bounded,
                        "specification_fingerprint": spec.fingerprint,
                        "contrast": contrast.canonical_content(), "horizon": horizon,
                        "outcome": outcome, "block": definition.canonical_content(),
                        "metrics": metrics, "ordered_daily_pair_identities": list(pair_ids),
                    }
                    identity = _aggregate_identity("composite_contrast_block", payload)
                    block_ids.append(identity)
                    contrast_blocks.append(_ContrastBlockEvaluation(
                        contrast_name=contrast.name, variant_policy=contrast.variant_policy,
                        reference_policy=contrast.reference_policy, horizon_sessions=horizon,
                        outcome_field=outcome, block_name=definition.name,
                        block_start_date=definition.start_date, block_end_date=definition.end_date,
                        **metrics, included_daily_pair_identity_count=len(pair_ids),
                        included_daily_pair_identities_sha256=_identity_hash(pair_ids), identity=identity,
                    ))
                metrics = _contrast_metrics(pairs)
                payload = {
                    "source_dataset_identity": dataset.identity,
                    "source_dataset_content_identity": dataset.content_identity,
                    "source_bounded_content_identity": bounded,
                    "specification_fingerprint": spec.fingerprint,
                    "contrast": contrast.canonical_content(), "horizon": horizon,
                    "outcome": outcome, "metrics": metrics,
                    "ordered_block_identities": block_ids,
                    "ordered_daily_pairs": [[left.identity, right.identity] for left, right in pairs],
                }
                contrast_summaries.append(_ContrastSummary(
                    contrast_name=contrast.name, variant_policy=contrast.variant_policy,
                    reference_policy=contrast.reference_policy, horizon_sessions=horizon,
                    outcome_field=outcome, **metrics,
                    ordered_block_identities=tuple(block_ids),
                    identity=_aggregate_identity("composite_contrast_summary", payload),
                ))

    expected_summary_keys = tuple(
        (policy.name, horizon, outcome)
        for policy in spec.policies
        for horizon in spec.horizons
        for outcome in spec.outcome_fields
    )
    summary_map = {
        (item.policy_name, item.horizon_sessions, item.outcome_field): item
        for item in summaries
    }
    expected_block_keys = tuple(
        (*key, block.name) for key in expected_summary_keys for block in spec.blocks
    )
    block_map = {
        (item.policy_name, item.horizon_sessions, item.outcome_field, item.block_name): item
        for item in blocks
    }
    expected_contrast_keys = tuple(
        (contrast.name, horizon, outcome)
        for contrast in spec.contrasts
        for horizon in spec.horizons
        for outcome in spec.outcome_fields
    )
    contrast_summary_map = {
        (item.contrast_name, item.horizon_sessions, item.outcome_field): item
        for item in contrast_summaries
    }
    expected_contrast_block_keys = tuple(
        (*key, block.name) for key in expected_contrast_keys for block in spec.blocks
    )
    contrast_block_map = {
        (item.contrast_name, item.horizon_sessions, item.outcome_field, item.block_name): item
        for item in contrast_blocks
    }
    if not (
        tuple(summary_map) == expected_summary_keys
        and tuple(block_map) == expected_block_keys
        and tuple(contrast_summary_map) == expected_contrast_keys
        and tuple(contrast_block_map) == expected_contrast_block_keys
        and len(summaries) == 24 and len(blocks) == 96
        and len(contrast_summaries) == 18 and len(contrast_blocks) == 72
    ):
        raise ValueError("composite result combinations, dimensions, or ordering do not reconcile")
    for key, summary in summary_map.items():
        expected_ids = tuple(block_map[(*key, block.name)].identity for block in spec.blocks)
        if summary.ordered_block_identities != expected_ids:
            raise ValueError("composite summary block identities do not reconcile")
    for key, summary in contrast_summary_map.items():
        expected_ids = tuple(
            contrast_block_map[(*key, block.name)].identity for block in spec.blocks
        )
        if summary.ordered_block_identities != expected_ids:
            raise ValueError("composite contrast block identities do not reconcile")
    return PanelCompositeAnalysisResult(
        source_dataset_identity=dataset.identity,
        source_dataset_content_identity=dataset.content_identity,
        source_bounded_content_identity=bounded,
        source_observation_index_identity=dataset.observation_index_identity,
        source_observation_content_identity=dataset.observation_content_identity,
        source_feature_panel_identity=dataset.feature_panel_identity,
        source_feature_content_identity=dataset.feature_content_identity,
        source_outcome_panel_identity=dataset.outcome_panel_identity,
        source_outcome_content_identity=dataset.outcome_content_identity,
        specification_fingerprint=spec.fingerprint,
        daily_evaluations=daily, block_evaluations=tuple(blocks), summaries=tuple(summaries),
        contrast_block_evaluations=tuple(contrast_blocks),
        contrast_summaries=tuple(contrast_summaries), limitations_metadata=_LIMITATIONS,
    )
