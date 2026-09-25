from __future__ import annotations

"""Pure conditional rank-association diagnostics over the Phase 5.3B dataset."""

from dataclasses import dataclass, field
from datetime import date
from hashlib import sha256
import math
from numbers import Real
from statistics import median
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np
import pandas as pd

from quantlab.identity import canonical_identity_value, canonical_json

from .panel_factor_contracts import BUILTIN_FACTOR_FIELDS, SUPPORTED_HORIZONS, SUPPORTED_OUTCOME_FIELDS
from .panel_factor_temporal_stability import (
    NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2,
    OVERALL_END_DATE,
    OVERALL_START_DATE,
    PanelTemporalBlock,
)


INCREMENTAL_ANALYSIS_CONTRACT = "quantlab.panel_factor_incremental_analysis"
INCREMENTAL_ANALYSIS_VERSION = "v1"
MINIMUM_LISTWISE_FINITE_COUNT = 20
TEMPORAL_REVIEW_MINIMUM_DEFINED_DATES = 30
OLS_RCOND = 1e-12
ZERO_TOLERANCE = 1e-12
LISTWISE_RULE = "outcome_status_available_and_target_controls_outcome_all_finite_v1"
RANK_METHOD = "ascending_average_ranks_for_ties_v1"
RAW_IC_METHOD = "pearson_correlation_of_same_date_listwise_finite_average_ranks_v1"
PARTIAL_IC_METHOD = "pearson_correlation_of_target_and_outcome_rank_ols_residuals_v1"
DESIGN_RULE = "float64_intercept_then_declared_control_ranks_v1"
OLS_METHOD = "numpy_linalg_lstsq_explicit_rcond_v1"
MATRIX_RANK_RULE = "lstsq_reported_rank_must_equal_intercept_plus_control_count_v1"
AGGREGATION_RULE = "defined_daily_statistics_equal_signal_date_weight_no_pooling_v1"
SIGN_RULE = "strict_positive_zero_strict_negative_v1"
SIGN_FLIP_RULE = "chronological_defined_blocks_only_zero_is_a_distinct_sign_v1"
CONCENTRATION_RULE = "largest_absolute_block_mean_divided_by_sum_absolute_block_means_v1"
RESTRICTION_RULE = "descriptive_only_no_selection_weighting_significance_or_production_authority_v1"
DATA_BOUNDARY_RULE = "phase_5_3b_joined_evaluation_frame_only_v1"
PERMITTED_RESULT_USE = "offline_research_evaluation_only"

_OUTCOME_COLUMN_TEMPLATES = {
    "stock_forward_return_pct": "stock_forward_return_{horizon}_pct",
    "excess_forward_return_pct_points": "excess_forward_return_{horizon}_pct_points",
}

_UNDEFINED_REASONS = {
    "fewer_than_minimum_listwise_finite_observations",
    "constant_target_rank",
    "constant_outcome_rank",
    "rank_deficient_control_design",
    "target_residual_variance_zero",
    "outcome_residual_variance_zero",
    "partial_correlation_denominator_zero",
    "unavailable_outcome_status",
}

_LIMITATIONS = MappingProxyType({
    "partial_rank_ic_is_causal": False,
    "residualization_scope": "linear relationships among same-date ranks only",
    "low_redundancy_guarantees_incremental_alpha": False,
    "positive_incremental_association_authorizes_factor_inclusion": False,
    "negative_partial_ic_is_automatically_a_short_signal": False,
    "hypotheses_are_independent_confirmation": False,
    "same_historical_dataset_reused": True,
    "database_coverage_is_historical_vn100": False,
    "p_values_significance_or_multiple_testing_correction_provided": False,
    "cost_turnover_liquidity_capacity_or_portfolio_utility_evaluated": False,
    "selection_weighting_composite_ranking_or_production_authority": False,
    "future_outcomes_used": True,
    "permitted_use": PERMITTED_RESULT_USE,
    "production_signal_safe": False,
    "excluded_v1_factors": (
        (
            "relative_strength_20d_pct_points",
            "Phase 5.6A found near-identical cross-sectional ranks to stock_return_20d_pct",
        ),
        (
            "return_3d_pct",
            "exploratory factor without stable aligned IC/spread evidence",
        ),
    ),
})


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


def _finite_numeric(value: Any, *, column: str) -> float | None:
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
    number = float(value)
    return number if math.isfinite(number) else None


def _identity_value(value: Any) -> Any:
    if value is None:
        return None
    if value is pd.NA or value is pd.NaT:
        return {"__missing__": type(value).__name__}
    if hasattr(value, "item"):
        value = value.item()
    return canonical_identity_value(value)


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _identity_hash(values: tuple[str, ...]) -> str:
    return _hash(list(values))


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
        average = (cursor + 1 + end) / 2.0
        for position in range(cursor, end):
            ranks[ordered[position][0]] = average
        cursor = end
    return tuple(ranks)


def _pearson(
    first: tuple[float, ...],
    second: tuple[float, ...],
    *,
    zero_tolerance: float,
) -> float | None:
    first_mean = sum(first) / len(first)
    second_mean = sum(second) / len(second)
    left = tuple(value - first_mean for value in first)
    right = tuple(value - second_mean for value in second)
    left_sum = sum(value * value for value in left)
    right_sum = sum(value * value for value in right)
    denominator = math.sqrt(left_sum * right_sum)
    if denominator <= zero_tolerance or not math.isfinite(denominator):
        return None
    result = sum(a * b for a, b in zip(left, right, strict=True)) / denominator
    return max(-1.0, min(1.0, result))


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


def _frozen(value: Any) -> Any:
    normalized = canonical_identity_value(value)
    if isinstance(normalized, dict):
        return MappingProxyType({key: _frozen(item) for key, item in sorted(normalized.items())})
    if isinstance(normalized, list):
        return tuple(_frozen(item) for item in normalized)
    return normalized


@dataclass(frozen=True, slots=True)
class IncrementalFactorHypothesis:
    name: str
    target_factor: str
    control_factors: tuple[str, ...]

    def __post_init__(self) -> None:
        name = _text(self.name, name="hypothesis name")
        target = _text(self.target_factor, name="target factor")
        controls = tuple(_text(value, name="control factor") for value in self.control_factors)
        if target not in BUILTIN_FACTOR_FIELDS:
            raise ValueError(f"unsupported target factor: {target}")
        if any(value not in BUILTIN_FACTOR_FIELDS for value in controls):
            raise ValueError("hypothesis contains an unsupported control factor")
        if target in controls:
            raise ValueError("hypothesis cannot control for its target factor")
        if len(set(controls)) != len(controls):
            raise ValueError("hypothesis control factors must be unique")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "target_factor", target)
        object.__setattr__(self, "control_factors", controls)

    def canonical_content(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "target_factor": self.target_factor,
            "control_factors": list(self.control_factors),
        }


@dataclass(frozen=True, slots=True)
class PanelFactorIncrementalAnalysisSpec:
    name: str
    version: str
    hypotheses: tuple[IncrementalFactorHypothesis, ...]
    horizons: tuple[int, ...]
    outcome_fields: tuple[str, ...]
    blocks: tuple[PanelTemporalBlock, ...]
    minimum_listwise_finite_count: int = MINIMUM_LISTWISE_FINITE_COUNT
    temporal_review_minimum_defined_dates: int = TEMPORAL_REVIEW_MINIMUM_DEFINED_DATES
    ols_rcond: float = OLS_RCOND
    zero_tolerance: float = ZERO_TOLERANCE
    listwise_rule: str = LISTWISE_RULE
    rank_method: str = RANK_METHOD
    raw_ic_method: str = RAW_IC_METHOD
    partial_ic_method: str = PARTIAL_IC_METHOD
    design_rule: str = DESIGN_RULE
    ols_method: str = OLS_METHOD
    matrix_rank_rule: str = MATRIX_RANK_RULE
    aggregation_rule: str = AGGREGATION_RULE
    sign_rule: str = SIGN_RULE
    sign_flip_rule: str = SIGN_FLIP_RULE
    concentration_rule: str = CONCENTRATION_RULE
    restriction_rule: str = RESTRICTION_RULE
    data_boundary_rule: str = DATA_BOUNDARY_RULE
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        name = _text(self.name, name="incremental-analysis specification name")
        version = _text(self.version, name="incremental-analysis specification version")
        hypotheses = tuple(self.hypotheses)
        horizons = tuple(self.horizons)
        outcomes = tuple(_text(value, name="outcome field") for value in self.outcome_fields)
        blocks = tuple(self.blocks)
        if not hypotheses or any(not isinstance(value, IncrementalFactorHypothesis) for value in hypotheses):
            raise TypeError("hypotheses must be non-empty IncrementalFactorHypothesis values")
        names = tuple(value.name for value in hypotheses)
        if len(set(names)) != len(names):
            raise ValueError("incremental hypothesis names must be unique")
        if (
            not horizons
            or len(set(horizons)) != len(horizons)
            or any(isinstance(value, bool) or not isinstance(value, int) for value in horizons)
            or any(value not in SUPPORTED_HORIZONS for value in horizons)
        ):
            raise ValueError("unsupported incremental-analysis horizon")
        if (
            not outcomes
            or len(set(outcomes)) != len(outcomes)
            or any(value not in SUPPORTED_OUTCOME_FIELDS for value in outcomes)
        ):
            raise ValueError("unsupported incremental-analysis outcome field")
        authority = NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2.blocks
        if tuple(value.canonical_content() for value in blocks) != tuple(
            value.canonical_content() for value in authority
        ):
            raise ValueError("incremental-analysis blocks must match the four Phase 5.5 blocks")
        for attribute in (
            "minimum_listwise_finite_count", "temporal_review_minimum_defined_dates",
        ):
            value = getattr(self, attribute)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{attribute} must be a positive integer")
        rcond = float(self.ols_rcond)
        tolerance = float(self.zero_tolerance)
        if not math.isfinite(rcond) or rcond <= 0.0:
            raise ValueError("ols_rcond must be a positive finite value")
        if not math.isfinite(tolerance) or tolerance < 0.0:
            raise ValueError("zero_tolerance must be a non-negative finite value")
        methods = (
            self.listwise_rule, self.rank_method, self.raw_ic_method, self.partial_ic_method,
            self.design_rule, self.ols_method, self.matrix_rank_rule, self.aggregation_rule,
            self.sign_rule, self.sign_flip_rule, self.concentration_rule,
            self.restriction_rule, self.data_boundary_rule,
        )
        expected = (
            LISTWISE_RULE, RANK_METHOD, RAW_IC_METHOD, PARTIAL_IC_METHOD,
            DESIGN_RULE, OLS_METHOD, MATRIX_RANK_RULE, AGGREGATION_RULE,
            SIGN_RULE, SIGN_FLIP_RULE, CONCENTRATION_RULE,
            RESTRICTION_RULE, DATA_BOUNDARY_RULE,
        )
        if methods != expected:
            raise ValueError("unsupported incremental-analysis method contract")
        payload = {
            "contract": {
                "name": INCREMENTAL_ANALYSIS_CONTRACT,
                "version": INCREMENTAL_ANALYSIS_VERSION,
            },
            "name": name,
            "version": version,
            "hypotheses": [value.canonical_content() for value in hypotheses],
            "horizons": list(horizons),
            "outcome_fields": list(outcomes),
            "minimum_listwise_finite_count": self.minimum_listwise_finite_count,
            "temporal_review_minimum_defined_dates": (
                self.temporal_review_minimum_defined_dates
            ),
            "listwise_finite_rule": self.listwise_rule,
            "average_rank_method": self.rank_method,
            "raw_rank_ic_method": self.raw_ic_method,
            "partial_rank_ic_method": self.partial_ic_method,
            "residualization_formulas": {
                "target": "target_rank ~ intercept + ordered_control_ranks",
                "outcome": "outcome_rank ~ intercept + ordered_control_ranks",
            },
            "intercept_included": True,
            "design_column_order": "intercept_then_controls_in_declared_order",
            "ols_method": self.ols_method,
            "ols_rcond": rcond,
            "matrix_rank_rule": self.matrix_rank_rule,
            "zero_tolerance": tolerance,
            "population_std_ddof": 0,
            "equal_date_aggregation": self.aggregation_rule,
            "blocks": [value.canonical_content() for value in blocks],
            "sign_rule": self.sign_rule,
            "sign_flip_rule": self.sign_flip_rule,
            "concentration_rule": self.concentration_rule,
            "selection_or_significance_thresholds": None,
            "descriptive_restriction": self.restriction_rule,
            "data_boundary": self.data_boundary_rule,
        }
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "hypotheses", hypotheses)
        object.__setattr__(self, "horizons", horizons)
        object.__setattr__(self, "outcome_fields", outcomes)
        object.__setattr__(self, "blocks", blocks)
        object.__setattr__(self, "ols_rcond", rcond)
        object.__setattr__(self, "zero_tolerance", tolerance)
        object.__setattr__(self, "fingerprint", _hash(payload))

    def hypothesis(self, name: str) -> IncrementalFactorHypothesis:
        for value in self.hypotheses:
            if value.name == name:
                return value
        raise KeyError(f"unknown incremental hypothesis: {name}")


NEUTRAL_PANEL_INCREMENTAL_FACTOR_ANALYSIS_V1 = PanelFactorIncrementalAnalysisSpec(
    name="NEUTRAL_PANEL_INCREMENTAL_FACTOR_ANALYSIS_V1",
    version="1",
    hypotheses=(
        IncrementalFactorHypothesis("adx_given_rsi", "adx_14", ("rsi_14",)),
        IncrementalFactorHypothesis("rsi_given_adx", "rsi_14", ("adx_14",)),
        IncrementalFactorHypothesis(
            "volume_given_adx_rsi", "volume_ratio_20", ("adx_14", "rsi_14"),
        ),
        IncrementalFactorHypothesis(
            "stock_return_20d_given_rsi", "stock_return_20d_pct", ("rsi_14",),
        ),
        IncrementalFactorHypothesis(
            "rsi_given_stock_return_20d", "rsi_14", ("stock_return_20d_pct",),
        ),
        IncrementalFactorHypothesis(
            "ema20_distance_given_rsi", "ema20_distance_pct", ("rsi_14",),
        ),
        IncrementalFactorHypothesis(
            "rsi_given_ema20_distance", "rsi_14", ("ema20_distance_pct",),
        ),
        IncrementalFactorHypothesis(
            "atr_given_adx_rsi", "atr_percent_14", ("adx_14", "rsi_14"),
        ),
    ),
    horizons=(5, 10, 20),
    outcome_fields=("stock_forward_return_pct", "excess_forward_return_pct_points"),
    blocks=NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2.blocks,
)


@dataclass(frozen=True, slots=True)
class DailyIncrementalFactorEvaluation:
    source_dataset_identity: str
    source_dataset_content_identity: str
    source_bounded_content_identity: str
    specification_fingerprint: str
    signal_date: str
    hypothesis_name: str
    target_factor: str
    control_factors: tuple[str, ...]
    horizon_sessions: int
    outcome_field: str
    outcome_column: str
    total_observation_count: int
    outcome_available_count: int
    unavailable_outcome_status_count: int
    target_missing_or_nonfinite_count: int
    control_missing_or_nonfinite_counts: Mapping[str, int]
    outcome_missing_or_nonfinite_count: int
    listwise_finite_count: int
    listwise_coverage_pct: float
    raw_rank_ic: float | None
    partial_rank_ic: float | None
    absolute_raw_rank_ic: float | None
    absolute_partial_rank_ic: float | None
    target_residual_population_std: float | None
    outcome_residual_population_std: float | None
    control_design_rank: int
    expected_design_rank: int
    undefined_reason: str | None
    sample_evidence_sha256: str
    observation_evidence_sha256: str
    listwise_sample_evidence: tuple[tuple[Any, ...], ...] = field(repr=False)
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        optional_names = (
            "raw_rank_ic", "partial_rank_ic", "absolute_raw_rank_ic",
            "absolute_partial_rank_ic", "target_residual_population_std",
            "outcome_residual_population_std",
        )
        optional = {
            name: _optional_finite(getattr(self, name), name=name) for name in optional_names
        }
        reason = self.undefined_reason
        if reason is not None and not (
            reason in _UNDEFINED_REASONS or reason.startswith("constant_control:")
        ):
            raise ValueError("unsupported incremental-analysis undefined reason")
        if (optional["partial_rank_ic"] is None) != (reason is not None):
            raise ValueError("partial rank IC and undefined reason do not reconcile")
        if optional["raw_rank_ic"] is None and optional["absolute_raw_rank_ic"] is not None:
            raise ValueError("undefined raw rank IC cannot have an absolute value")
        if optional["raw_rank_ic"] is not None and (
            optional["absolute_raw_rank_ic"] != abs(optional["raw_rank_ic"])
        ):
            raise ValueError("absolute raw rank IC does not reconcile")
        if optional["partial_rank_ic"] is None and optional["absolute_partial_rank_ic"] is not None:
            raise ValueError("undefined partial rank IC cannot have an absolute value")
        if optional["partial_rank_ic"] is not None and (
            optional["absolute_partial_rank_ic"] != abs(optional["partial_rank_ic"])
        ):
            raise ValueError("absolute partial rank IC does not reconcile")
        controls = tuple(_text(value, name="control factor") for value in self.control_factors)
        if set(self.control_missing_or_nonfinite_counts) != set(controls):
            raise ValueError("control missing/nonfinite counts must match declared controls")
        control_counts = MappingProxyType({
            control: int(self.control_missing_or_nonfinite_counts[control])
            for control in controls
        })
        count_names = (
            "total_observation_count", "outcome_available_count",
            "unavailable_outcome_status_count", "target_missing_or_nonfinite_count",
            "outcome_missing_or_nonfinite_count", "listwise_finite_count",
            "control_design_rank", "expected_design_rank",
        )
        if any(
            isinstance(getattr(self, name), bool) or int(getattr(self, name)) < 0
            for name in count_names
        ) or any(value < 0 for value in control_counts.values()):
            raise ValueError("daily incremental-analysis counts must be non-negative")
        if self.outcome_available_count + self.unavailable_outcome_status_count != self.total_observation_count:
            raise ValueError("outcome availability counts do not reconcile")
        if self.listwise_finite_count > self.outcome_available_count:
            raise ValueError("listwise sample exceeds outcome-available observations")
        if len(self.listwise_sample_evidence) != self.listwise_finite_count:
            raise ValueError("listwise evidence count does not reconcile")
        evidence = tuple(tuple(value) for value in self.listwise_sample_evidence)
        sample_hash = _text(self.sample_evidence_sha256, name="sample evidence SHA-256")
        observation_hash = _text(
            self.observation_evidence_sha256, name="observation evidence SHA-256",
        )
        if len(sample_hash) != 64 or sample_hash != _hash(evidence):
            raise ValueError("sample evidence SHA-256 does not reconcile")
        if len(observation_hash) != 64:
            raise ValueError("observation evidence must use SHA-256")
        payload = {
            "source_dataset_identity": _text(
                self.source_dataset_identity, name="source dataset identity",
            ),
            "source_dataset_content_identity": _text(
                self.source_dataset_content_identity, name="source dataset content identity",
            ),
            "source_bounded_content_identity": _text(
                self.source_bounded_content_identity, name="source bounded content identity",
            ),
            "specification_fingerprint": _text(
                self.specification_fingerprint, name="specification fingerprint",
            ),
            "signal_date": _date_text(self.signal_date, name="signal date"),
            "hypothesis_name": _text(self.hypothesis_name, name="hypothesis name"),
            "target_factor": _text(self.target_factor, name="target factor"),
            "control_factors": list(controls),
            "horizon_sessions": self.horizon_sessions,
            "outcome_field": _text(self.outcome_field, name="outcome field"),
            "outcome_column": _text(self.outcome_column, name="outcome column"),
            "counts": {name: int(getattr(self, name)) for name in count_names},
            "control_missing_or_nonfinite_counts": control_counts,
            "listwise_coverage_pct": float(self.listwise_coverage_pct),
            **optional,
            "undefined_reason": reason,
            "sample_evidence_sha256": sample_hash,
            "observation_evidence_sha256": observation_hash,
            "ordered_listwise_sample_evidence": evidence,
        }
        for name, value in optional.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "control_factors", controls)
        object.__setattr__(self, "control_missing_or_nonfinite_counts", control_counts)
        object.__setattr__(self, "listwise_sample_evidence", evidence)
        object.__setattr__(self, "sample_evidence_sha256", sample_hash)
        object.__setattr__(self, "observation_evidence_sha256", observation_hash)
        object.__setattr__(self, "identity", _hash(payload))


_AGGREGATE_OPTIONAL_FIELDS = (
    "mean_daily_raw_rank_ic", "median_daily_raw_rank_ic",
    "population_std_daily_raw_rank_ic", "minimum_daily_raw_rank_ic",
    "maximum_daily_raw_rank_ic", "mean_daily_partial_rank_ic",
    "median_daily_partial_rank_ic", "population_std_daily_partial_rank_ic",
    "minimum_daily_partial_rank_ic", "maximum_daily_partial_rank_ic",
    "mean_absolute_daily_raw_rank_ic", "median_absolute_daily_raw_rank_ic",
    "mean_absolute_daily_partial_rank_ic", "median_absolute_daily_partial_rank_ic",
    "positive_raw_rank_ic_rate", "zero_raw_rank_ic_rate", "negative_raw_rank_ic_rate",
    "positive_partial_rank_ic_rate", "zero_partial_rank_ic_rate",
    "negative_partial_rank_ic_rate", "average_listwise_finite_count",
    "median_listwise_finite_count", "mean_partial_minus_raw_rank_ic",
    "median_partial_minus_raw_rank_ic",
    "mean_absolute_partial_minus_absolute_raw_rank_ic",
)


@dataclass(frozen=True, slots=True)
class IncrementalFactorBlockEvaluation:
    source_dataset_identity: str
    source_bounded_content_identity: str
    source_daily_result_identity: str
    specification_fingerprint: str
    hypothesis_name: str
    target_factor: str
    control_factors: tuple[str, ...]
    horizon_sessions: int
    outcome_field: str
    block_name: str
    block_start_date: str
    block_end_date: str
    total_signal_date_count: int
    minimum_sample_date_count: int
    raw_rank_ic_defined_date_count: int
    partial_rank_ic_defined_date_count: int
    raw_rank_ic_coverage_pct: float
    partial_rank_ic_coverage_pct: float
    mean_daily_raw_rank_ic: float | None
    median_daily_raw_rank_ic: float | None
    population_std_daily_raw_rank_ic: float | None
    minimum_daily_raw_rank_ic: float | None
    maximum_daily_raw_rank_ic: float | None
    mean_daily_partial_rank_ic: float | None
    median_daily_partial_rank_ic: float | None
    population_std_daily_partial_rank_ic: float | None
    minimum_daily_partial_rank_ic: float | None
    maximum_daily_partial_rank_ic: float | None
    mean_absolute_daily_raw_rank_ic: float | None
    median_absolute_daily_raw_rank_ic: float | None
    mean_absolute_daily_partial_rank_ic: float | None
    median_absolute_daily_partial_rank_ic: float | None
    positive_raw_rank_ic_date_count: int
    zero_raw_rank_ic_date_count: int
    negative_raw_rank_ic_date_count: int
    positive_raw_rank_ic_rate: float | None
    zero_raw_rank_ic_rate: float | None
    negative_raw_rank_ic_rate: float | None
    positive_partial_rank_ic_date_count: int
    zero_partial_rank_ic_date_count: int
    negative_partial_rank_ic_date_count: int
    positive_partial_rank_ic_rate: float | None
    zero_partial_rank_ic_rate: float | None
    negative_partial_rank_ic_rate: float | None
    average_listwise_finite_count: float | None
    median_listwise_finite_count: float | None
    mean_partial_minus_raw_rank_ic: float | None
    median_partial_minus_raw_rank_ic: float | None
    mean_absolute_partial_minus_absolute_raw_rank_ic: float | None
    included_daily_identities: tuple[str, ...]
    warnings: tuple[str, ...]
    identity: str = field(init=False)
    included_daily_identity_count: int = field(init=False)
    included_daily_identities_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        _finalize_aggregate_identity(self, block=True)


@dataclass(frozen=True, slots=True)
class IncrementalFactorEvaluationSummary:
    source_dataset_identity: str
    source_bounded_content_identity: str
    source_daily_result_identity: str
    specification_fingerprint: str
    hypothesis_name: str
    target_factor: str
    control_factors: tuple[str, ...]
    horizon_sessions: int
    outcome_field: str
    total_signal_date_count: int
    minimum_sample_date_count: int
    raw_rank_ic_defined_date_count: int
    partial_rank_ic_defined_date_count: int
    raw_rank_ic_coverage_pct: float
    partial_rank_ic_coverage_pct: float
    mean_daily_raw_rank_ic: float | None
    median_daily_raw_rank_ic: float | None
    population_std_daily_raw_rank_ic: float | None
    minimum_daily_raw_rank_ic: float | None
    maximum_daily_raw_rank_ic: float | None
    mean_daily_partial_rank_ic: float | None
    median_daily_partial_rank_ic: float | None
    population_std_daily_partial_rank_ic: float | None
    minimum_daily_partial_rank_ic: float | None
    maximum_daily_partial_rank_ic: float | None
    mean_absolute_daily_raw_rank_ic: float | None
    median_absolute_daily_raw_rank_ic: float | None
    mean_absolute_daily_partial_rank_ic: float | None
    median_absolute_daily_partial_rank_ic: float | None
    positive_raw_rank_ic_date_count: int
    zero_raw_rank_ic_date_count: int
    negative_raw_rank_ic_date_count: int
    positive_raw_rank_ic_rate: float | None
    zero_raw_rank_ic_rate: float | None
    negative_raw_rank_ic_rate: float | None
    positive_partial_rank_ic_date_count: int
    zero_partial_rank_ic_date_count: int
    negative_partial_rank_ic_date_count: int
    positive_partial_rank_ic_rate: float | None
    zero_partial_rank_ic_rate: float | None
    negative_partial_rank_ic_rate: float | None
    average_listwise_finite_count: float | None
    median_listwise_finite_count: float | None
    mean_partial_minus_raw_rank_ic: float | None
    median_partial_minus_raw_rank_ic: float | None
    mean_absolute_partial_minus_absolute_raw_rank_ic: float | None
    ordered_block_identities: tuple[str, ...]
    blocks_meeting_temporal_review_count: int
    chronological_partial_sign_flip_count: int
    all_blocks_positive_partial_rank_ic: bool
    all_blocks_negative_partial_rank_ic: bool
    minimum_block_mean_partial_rank_ic: float | None
    maximum_block_mean_partial_rank_ic: float | None
    range_block_mean_partial_rank_ic: float | None
    largest_absolute_block_mean_partial_rank_ic_concentration: float | None
    included_daily_identities: tuple[str, ...]
    warnings: tuple[str, ...]
    identity: str = field(init=False)
    included_daily_identity_count: int = field(init=False)
    included_daily_identities_sha256: str = field(init=False)
    ordered_block_identity_count: int = field(init=False)
    ordered_block_identities_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        _finalize_aggregate_identity(self, block=False)


def _finalize_aggregate_identity(
    value: IncrementalFactorBlockEvaluation | IncrementalFactorEvaluationSummary,
    *,
    block: bool,
) -> None:
    optional_names = list(_AGGREGATE_OPTIONAL_FIELDS)
    if not block:
        optional_names.extend((
            "minimum_block_mean_partial_rank_ic", "maximum_block_mean_partial_rank_ic",
            "range_block_mean_partial_rank_ic",
            "largest_absolute_block_mean_partial_rank_ic_concentration",
        ))
    optional = {
        name: _optional_finite(getattr(value, name), name=name) for name in optional_names
    }
    daily = tuple(_text(item, name="included daily identity") for item in value.included_daily_identities)
    warnings = tuple(_text(item, name="warning") for item in value.warnings)
    controls = tuple(_text(item, name="control factor") for item in value.control_factors)
    excluded = {
        "identity", "included_daily_identity_count", "included_daily_identities_sha256",
        *optional_names,
    }
    if not block:
        excluded.update({"ordered_block_identity_count", "ordered_block_identities_sha256"})
    payload = {
        name: canonical_identity_value(getattr(value, name))
        for name in value.__dataclass_fields__
        if name not in excluded
    }
    payload.update(optional)
    payload["control_factors"] = list(controls)
    payload["included_daily_identities"] = list(daily)
    payload["warnings"] = list(warnings)
    if not block:
        blocks = tuple(
            _text(item, name="ordered block identity")
            for item in value.ordered_block_identities
        )
        payload["ordered_block_identities"] = list(blocks)
        object.__setattr__(value, "ordered_block_identities", blocks)
        object.__setattr__(value, "ordered_block_identity_count", len(blocks))
        object.__setattr__(value, "ordered_block_identities_sha256", _identity_hash(blocks))
    for name, normalized in optional.items():
        object.__setattr__(value, name, normalized)
    object.__setattr__(value, "control_factors", controls)
    object.__setattr__(value, "included_daily_identities", daily)
    object.__setattr__(value, "warnings", warnings)
    object.__setattr__(value, "included_daily_identity_count", len(daily))
    object.__setattr__(value, "included_daily_identities_sha256", _identity_hash(daily))
    object.__setattr__(value, "identity", _hash(payload))


@dataclass(frozen=True, slots=True)
class PanelFactorIncrementalAnalysisResult:
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
    source_daily_result_identity: str
    daily_evaluations: tuple[DailyIncrementalFactorEvaluation, ...]
    block_evaluations: tuple[IncrementalFactorBlockEvaluation, ...]
    summaries: tuple[IncrementalFactorEvaluationSummary, ...]
    limitations_metadata: Mapping[str, Any]
    contract_name: str = INCREMENTAL_ANALYSIS_CONTRACT
    contract_version: str = INCREMENTAL_ANALYSIS_VERSION
    future_looking: bool = True
    permitted_use: str = PERMITTED_RESULT_USE
    production_signal_safe: bool = False
    identity: str = field(init=False)
    _daily_by_key: Mapping[tuple[str, str, int, str], DailyIncrementalFactorEvaluation] = field(
        init=False, repr=False, compare=False,
    )
    _block_by_key: Mapping[tuple[str, int, str, str], IncrementalFactorBlockEvaluation] = field(
        init=False, repr=False, compare=False,
    )
    _summary_by_key: Mapping[tuple[str, int, str], IncrementalFactorEvaluationSummary] = field(
        init=False, repr=False, compare=False,
    )

    def __post_init__(self) -> None:
        if (self.contract_name, self.contract_version) != (
            INCREMENTAL_ANALYSIS_CONTRACT, INCREMENTAL_ANALYSIS_VERSION,
        ):
            raise ValueError("unsupported incremental-analysis result contract")
        if (
            self.future_looking is not True
            or self.permitted_use != PERMITTED_RESULT_USE
            or self.production_signal_safe is not False
        ):
            raise ValueError("incremental analysis is restricted to offline research")
        daily = tuple(self.daily_evaluations)
        blocks = tuple(self.block_evaluations)
        summaries = tuple(self.summaries)
        daily_map = {
            (item.signal_date, item.hypothesis_name, item.horizon_sessions, item.outcome_field): item
            for item in daily
        }
        block_map = {
            (item.hypothesis_name, item.horizon_sessions, item.outcome_field, item.block_name): item
            for item in blocks
        }
        summary_map = {
            (item.hypothesis_name, item.horizon_sessions, item.outcome_field): item
            for item in summaries
        }
        if len(daily_map) != len(daily):
            raise ValueError("duplicate daily incremental-analysis key")
        if len(block_map) != len(blocks):
            raise ValueError("duplicate incremental-analysis block key")
        if len(summary_map) != len(summaries):
            raise ValueError("duplicate incremental-analysis summary key")
        for item in daily:
            if (
                item.source_dataset_identity != self.source_dataset_identity
                or item.source_dataset_content_identity != self.source_dataset_content_identity
                or item.source_bounded_content_identity != self.source_bounded_content_identity
                or item.specification_fingerprint != self.specification_fingerprint
            ):
                raise ValueError("daily incremental-analysis provenance does not reconcile")
        for item in (*blocks, *summaries):
            if (
                item.source_dataset_identity != self.source_dataset_identity
                or item.source_bounded_content_identity != self.source_bounded_content_identity
                or item.source_daily_result_identity != self.source_daily_result_identity
                or item.specification_fingerprint != self.specification_fingerprint
            ):
                raise ValueError("aggregate incremental-analysis provenance does not reconcile")
        metadata = MappingProxyType({
            str(key): _frozen(value) for key, value in sorted(self.limitations_metadata.items())
        })
        provenance_names = (
            "source_dataset_identity", "source_dataset_content_identity",
            "source_bounded_content_identity", "source_observation_index_identity",
            "source_observation_content_identity", "source_feature_panel_identity",
            "source_feature_content_identity", "source_outcome_panel_identity",
            "source_outcome_content_identity", "specification_fingerprint",
            "source_daily_result_identity",
        )
        payload = {
            "contract": {"name": self.contract_name, "version": self.contract_version},
            **{
                name: _text(getattr(self, name), name=name.replace("_", " "))
                for name in provenance_names
            },
            "ordered_daily_identities": [item.identity for item in daily],
            "ordered_block_identities": [item.identity for item in blocks],
            "ordered_summary_identities": [item.identity for item in summaries],
            "counts": {
                "daily": len(daily), "blocks": len(blocks), "summaries": len(summaries),
            },
            "research_boundary": {
                "future_looking": True,
                "permitted_use": PERMITTED_RESULT_USE,
                "production_signal_safe": False,
            },
            "limitations_metadata": metadata,
        }
        object.__setattr__(self, "daily_evaluations", daily)
        object.__setattr__(self, "block_evaluations", blocks)
        object.__setattr__(self, "summaries", summaries)
        object.__setattr__(self, "limitations_metadata", metadata)
        object.__setattr__(self, "_daily_by_key", MappingProxyType(daily_map))
        object.__setattr__(self, "_block_by_key", MappingProxyType(block_map))
        object.__setattr__(self, "_summary_by_key", MappingProxyType(summary_map))
        object.__setattr__(self, "identity", _hash(payload))

    def daily_for(
        self, signal_date: str, hypothesis_name: str, horizon_sessions: int, outcome_field: str,
    ) -> DailyIncrementalFactorEvaluation:
        return self._daily_by_key[(signal_date, hypothesis_name, horizon_sessions, outcome_field)]

    def block_for(
        self, hypothesis_name: str, horizon_sessions: int, outcome_field: str, block_name: str,
    ) -> IncrementalFactorBlockEvaluation:
        return self._block_by_key[(hypothesis_name, horizon_sessions, outcome_field, block_name)]

    def summary_for(
        self, hypothesis_name: str, horizon_sessions: int, outcome_field: str,
    ) -> IncrementalFactorEvaluationSummary:
        return self._summary_by_key[(hypothesis_name, horizon_sessions, outcome_field)]


def _validated_dataset(
    dataset: Any,
    spec: PanelFactorIncrementalAnalysisSpec,
) -> tuple[pd.DataFrame, tuple[str, ...], str]:
    from quantlab.panels.outcome_contracts import PanelForwardOutcomeAvailability
    from quantlab.panels.research_dataset_contracts import (
        PERMITTED_USE,
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
        or dataset.spec.permitted_use != PERMITTED_USE
        or dataset.metadata.get("future_looking") is not True
        or dataset.metadata.get("production_signal_safe") is not False
        or dataset.metadata.get("permitted_use") != PERMITTED_USE
    ):
        raise ValueError("dataset must retain its future-looking offline-research boundary")
    provenance = (
        "identity", "content_identity", "observation_index_identity",
        "observation_content_identity", "feature_panel_identity", "feature_content_identity",
        "outcome_panel_identity", "outcome_content_identity",
    )
    for name in provenance:
        _text(getattr(dataset, name), name=f"dataset {name}")
    feature_fields = set(dataset.spec.feature_columns)
    forbidden = set(dataset.spec.forbidden_predictor_columns)
    for hypothesis in spec.hypotheses:
        predictors = (hypothesis.target_factor, *hypothesis.control_factors)
        if any(value not in feature_fields for value in predictors):
            raise ValueError("hypothesis predictor is absent from point-in-time feature fields")
        if any(value in forbidden for value in predictors):
            raise ValueError("future-looking field cannot be used as target or control")
    if any(value not in dataset.spec.outcome_panel_spec.horizons for value in spec.horizons):
        raise ValueError("requested horizon is absent from the source outcome contract")

    frame = dataset.evaluation_frame()
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("dataset.evaluation_frame() must return a DataFrame")
    frame = frame.copy(deep=True)
    if tuple(frame.columns) != tuple(dataset.spec.output_columns):
        raise ValueError("evaluation frame schema is inconsistent with Phase 5.3B")
    if len(frame) != dataset.observation_row_count:
        raise ValueError("dataset population count does not match its evaluation frame")
    if dataset.metadata.get("observation_row_count") != len(frame):
        raise ValueError("dataset metadata population count does not reconcile")
    keys: list[tuple[str, str]] = []
    for row in frame.loc[:, ["session_date", "symbol"]].itertuples(index=False):
        session_date = _date_text(row.session_date, name="source signal date")
        symbol = _text(row.symbol, name="source symbol").upper()
        keys.append((session_date, symbol))
    if len(set(keys)) != len(keys):
        raise ValueError("dataset contains duplicate (session_date, symbol) keys")
    frame.loc[:, "session_date"] = tuple(value[0] for value in keys)
    frame.loc[:, "symbol"] = tuple(value[1] for value in keys)
    frame = frame.sort_values(["session_date", "symbol"], kind="mergesort").reset_index(drop=True)

    signal_dates = tuple(
        _date_text(value.session_date, name="benchmark signal date")
        for value in dataset.session_audit
    )
    if not signal_dates or signal_dates != tuple(sorted(signal_dates)) or len(set(signal_dates)) != len(signal_dates):
        raise ValueError("benchmark signal dates must be non-empty, unique, and ordered")
    if signal_dates[0] < OVERALL_START_DATE or signal_dates[-1] > OVERALL_END_DATE:
        raise ValueError("source signal date is outside the incremental-analysis interval")
    if not set(frame["session_date"]).issubset(signal_dates):
        raise ValueError("evaluation row date is absent from benchmark signal dates")

    required: set[str] = set()
    predictor_fields = tuple(dict.fromkeys(
        value
        for hypothesis in spec.hypotheses
        for value in (hypothesis.target_factor, *hypothesis.control_factors)
    ))
    required.update(predictor_fields)
    for horizon in spec.horizons:
        required.add(f"outcome_{horizon}__availability")
        required.update(
            _OUTCOME_COLUMN_TEMPLATES[outcome].format(horizon=horizon)
            for outcome in spec.outcome_fields
        )
    absent = sorted(required.difference(frame.columns))
    if absent:
        raise ValueError("dataset is missing incremental-analysis columns: " + ", ".join(absent))
    for field_name in predictor_fields:
        for value in frame[field_name]:
            _finite_numeric(value, column=field_name)
    supported_statuses = {value.value for value in PanelForwardOutcomeAvailability}
    for horizon in spec.horizons:
        status_column = f"outcome_{horizon}__availability"
        statuses = set(frame[status_column].astype(str))
        invalid = sorted(statuses.difference(supported_statuses))
        if invalid:
            raise ValueError(f"invalid outcome availability label for horizon {horizon}: {invalid}")
        for outcome in spec.outcome_fields:
            column = _OUTCOME_COLUMN_TEMPLATES[outcome].format(horizon=horizon)
            for value in frame[column]:
                _finite_numeric(value, column=column)

    bounded_rows = tuple(
        (
            str(row.session_date),
            str(row.symbol),
            tuple(_identity_value(getattr(row, name)) for name in predictor_fields),
            tuple(
                (
                    horizon,
                    str(getattr(row, f"outcome_{horizon}__availability")),
                    tuple(
                        _identity_value(getattr(
                            row, _OUTCOME_COLUMN_TEMPLATES[outcome].format(horizon=horizon),
                        ))
                        for outcome in spec.outcome_fields
                    ),
                )
                for horizon in spec.horizons
            ),
        )
        for row in frame.itertuples(index=False)
    )
    bounded_identity = _hash({
        "source_observation_content_identity": dataset.observation_content_identity,
        "source_feature_content_identity": dataset.feature_content_identity,
        "source_outcome_content_identity": dataset.outcome_content_identity,
        "source_dataset_specification_fingerprint": dataset.spec.fingerprint,
        "incremental_specification_fingerprint": spec.fingerprint,
        "signal_dates": list(signal_dates),
        "predictor_fields": list(predictor_fields),
        "rows": bounded_rows,
    })
    return frame, signal_dates, bounded_identity


def _daily_evaluation(
    rows: pd.DataFrame,
    *,
    dataset: Any,
    source_bounded_content_identity: str,
    spec: PanelFactorIncrementalAnalysisSpec,
    hypothesis: IncrementalFactorHypothesis,
    horizon: int,
    outcome_field: str,
    signal_date: str,
) -> DailyIncrementalFactorEvaluation:
    status_column = f"outcome_{horizon}__availability"
    outcome_column = _OUTCOME_COLUMN_TEMPLATES[outcome_field].format(horizon=horizon)
    ordered = rows.sort_values("symbol", kind="mergesort")
    eligible: list[tuple[str, float, tuple[float, ...], float]] = []
    observation_evidence: list[tuple[Any, ...]] = []
    outcome_available = 0
    target_missing = 0
    control_missing = {value: 0 for value in hypothesis.control_factors}
    outcome_missing = 0
    for row in ordered.itertuples(index=False):
        values = row._asdict()
        symbol = str(values["symbol"])
        status = str(values[status_column])
        target = _finite_numeric(values[hypothesis.target_factor], column=hypothesis.target_factor)
        controls = tuple(
            _finite_numeric(values[field], column=field) for field in hypothesis.control_factors
        )
        outcome = _finite_numeric(values[outcome_column], column=outcome_column)
        available = status == "AVAILABLE"
        outcome_available += available
        target_missing += target is None
        for field_name, value in zip(hypothesis.control_factors, controls, strict=True):
            control_missing[field_name] += value is None
        outcome_missing += outcome is None
        listwise = available and target is not None and outcome is not None and all(
            value is not None for value in controls
        )
        observation_evidence.append((
            symbol, status, target, controls, outcome, listwise,
        ))
        if listwise:
            eligible.append((
                symbol,
                target,
                tuple(value for value in controls if value is not None),
                outcome,
            ))

    target_values = tuple(value[1] for value in eligible)
    control_values = tuple(
        tuple(value[2][index] for value in eligible)
        for index in range(len(hypothesis.control_factors))
    )
    outcome_values = tuple(value[3] for value in eligible)
    target_ranks = _average_ranks(target_values)
    control_ranks = tuple(_average_ranks(value) for value in control_values)
    outcome_ranks = _average_ranks(outcome_values)
    raw = None
    partial = None
    target_std = None
    outcome_std = None
    design_rank = 0
    expected_rank = 1 + len(hypothesis.control_factors)
    reason: str | None
    if outcome_available == 0:
        reason = "unavailable_outcome_status"
    elif len(eligible) < spec.minimum_listwise_finite_count:
        reason = "fewer_than_minimum_listwise_finite_observations"
    elif len(set(target_ranks)) == 1:
        reason = "constant_target_rank"
    elif len(set(outcome_ranks)) == 1:
        reason = "constant_outcome_rank"
    else:
        raw = _pearson(target_ranks, outcome_ranks, zero_tolerance=spec.zero_tolerance)
        constant_control = next((
            field_name
            for field_name, ranks in zip(hypothesis.control_factors, control_ranks, strict=True)
            if len(set(ranks)) == 1
        ), None)
        design = np.column_stack((
            np.ones(len(eligible), dtype=np.float64),
            *(np.asarray(ranks, dtype=np.float64) for ranks in control_ranks),
        ))
        response = np.column_stack((
            np.asarray(target_ranks, dtype=np.float64),
            np.asarray(outcome_ranks, dtype=np.float64),
        ))
        coefficients, _residuals, design_rank, _singular = np.linalg.lstsq(
            design, response, rcond=spec.ols_rcond,
        )
        design_rank = int(design_rank)
        residualized = response - design @ coefficients
        target_residuals = tuple(float(value) for value in residualized[:, 0])
        outcome_residuals = tuple(float(value) for value in residualized[:, 1])
        target_std = float(np.std(residualized[:, 0], ddof=0))
        outcome_std = float(np.std(residualized[:, 1], ddof=0))
        if constant_control is not None:
            reason = f"constant_control:{constant_control}"
        elif design_rank != expected_rank:
            reason = "rank_deficient_control_design"
        elif target_std <= spec.zero_tolerance:
            reason = "target_residual_variance_zero"
        elif outcome_std <= spec.zero_tolerance:
            reason = "outcome_residual_variance_zero"
        else:
            partial = _pearson(
                target_residuals,
                outcome_residuals,
                zero_tolerance=spec.zero_tolerance,
            )
            reason = None if partial is not None else "partial_correlation_denominator_zero"

    evidence = tuple(
        (
            symbol,
            target,
            controls,
            outcome,
            target_ranks[index],
            tuple(ranks[index] for ranks in control_ranks),
            outcome_ranks[index],
        )
        for index, (symbol, target, controls, outcome) in enumerate(eligible)
    )
    return DailyIncrementalFactorEvaluation(
        source_dataset_identity=dataset.identity,
        source_dataset_content_identity=dataset.content_identity,
        source_bounded_content_identity=source_bounded_content_identity,
        specification_fingerprint=spec.fingerprint,
        signal_date=signal_date,
        hypothesis_name=hypothesis.name,
        target_factor=hypothesis.target_factor,
        control_factors=hypothesis.control_factors,
        horizon_sessions=horizon,
        outcome_field=outcome_field,
        outcome_column=outcome_column,
        total_observation_count=len(ordered),
        outcome_available_count=outcome_available,
        unavailable_outcome_status_count=len(ordered) - outcome_available,
        target_missing_or_nonfinite_count=target_missing,
        control_missing_or_nonfinite_counts=control_missing,
        outcome_missing_or_nonfinite_count=outcome_missing,
        listwise_finite_count=len(eligible),
        listwise_coverage_pct=0.0 if not len(ordered) else len(eligible) / len(ordered) * 100.0,
        raw_rank_ic=raw,
        partial_rank_ic=partial,
        absolute_raw_rank_ic=None if raw is None else abs(raw),
        absolute_partial_rank_ic=None if partial is None else abs(partial),
        target_residual_population_std=target_std,
        outcome_residual_population_std=outcome_std,
        control_design_rank=design_rank,
        expected_design_rank=expected_rank,
        undefined_reason=reason,
        sample_evidence_sha256=_hash(evidence),
        observation_evidence_sha256=_hash(tuple(observation_evidence)),
        listwise_sample_evidence=evidence,
    )


def _aggregate(
    items: tuple[DailyIncrementalFactorEvaluation, ...],
    *,
    minimum_listwise_finite_count: int,
) -> dict[str, Any]:
    raw = tuple(item.raw_rank_ic for item in items if item.raw_rank_ic is not None)
    partial = tuple(item.partial_rank_ic for item in items if item.partial_rank_ic is not None)
    raw_absolute = tuple(abs(value) for value in raw)
    partial_absolute = tuple(abs(value) for value in partial)
    paired = tuple(
        (item.raw_rank_ic, item.partial_rank_ic)
        for item in items
        if item.raw_rank_ic is not None and item.partial_rank_ic is not None
    )
    deltas = tuple(partial_value - raw_value for raw_value, partial_value in paired)
    absolute_deltas = tuple(
        abs(partial_value) - abs(raw_value) for raw_value, partial_value in paired
    )
    sizes = tuple(float(item.listwise_finite_count) for item in items)
    total = len(items)
    return {
        "total_signal_date_count": total,
        "minimum_sample_date_count": sum(
            item.listwise_finite_count >= minimum_listwise_finite_count for item in items
        ),
        "raw_rank_ic_defined_date_count": len(raw),
        "partial_rank_ic_defined_date_count": len(partial),
        "raw_rank_ic_coverage_pct": 0.0 if total == 0 else len(raw) / total * 100.0,
        "partial_rank_ic_coverage_pct": 0.0 if total == 0 else len(partial) / total * 100.0,
        "mean_daily_raw_rank_ic": _mean(raw),
        "median_daily_raw_rank_ic": _median(raw),
        "population_std_daily_raw_rank_ic": _population_std(raw),
        "minimum_daily_raw_rank_ic": None if not raw else min(raw),
        "maximum_daily_raw_rank_ic": None if not raw else max(raw),
        "mean_daily_partial_rank_ic": _mean(partial),
        "median_daily_partial_rank_ic": _median(partial),
        "population_std_daily_partial_rank_ic": _population_std(partial),
        "minimum_daily_partial_rank_ic": None if not partial else min(partial),
        "maximum_daily_partial_rank_ic": None if not partial else max(partial),
        "mean_absolute_daily_raw_rank_ic": _mean(raw_absolute),
        "median_absolute_daily_raw_rank_ic": _median(raw_absolute),
        "mean_absolute_daily_partial_rank_ic": _mean(partial_absolute),
        "median_absolute_daily_partial_rank_ic": _median(partial_absolute),
        "positive_raw_rank_ic_date_count": sum(value > 0 for value in raw),
        "zero_raw_rank_ic_date_count": sum(value == 0 for value in raw),
        "negative_raw_rank_ic_date_count": sum(value < 0 for value in raw),
        "positive_raw_rank_ic_rate": None if not raw else sum(value > 0 for value in raw) / len(raw),
        "zero_raw_rank_ic_rate": None if not raw else sum(value == 0 for value in raw) / len(raw),
        "negative_raw_rank_ic_rate": None if not raw else sum(value < 0 for value in raw) / len(raw),
        "positive_partial_rank_ic_date_count": sum(value > 0 for value in partial),
        "zero_partial_rank_ic_date_count": sum(value == 0 for value in partial),
        "negative_partial_rank_ic_date_count": sum(value < 0 for value in partial),
        "positive_partial_rank_ic_rate": (
            None if not partial else sum(value > 0 for value in partial) / len(partial)
        ),
        "zero_partial_rank_ic_rate": (
            None if not partial else sum(value == 0 for value in partial) / len(partial)
        ),
        "negative_partial_rank_ic_rate": (
            None if not partial else sum(value < 0 for value in partial) / len(partial)
        ),
        "average_listwise_finite_count": _mean(sizes),
        "median_listwise_finite_count": _median(sizes),
        "mean_partial_minus_raw_rank_ic": _mean(deltas),
        "median_partial_minus_raw_rank_ic": _median(deltas),
        "mean_absolute_partial_minus_absolute_raw_rank_ic": _mean(absolute_deltas),
    }


def _warnings(
    aggregate: Mapping[str, Any],
    *,
    temporal_minimum: int | None = None,
) -> tuple[str, ...]:
    warnings = ["descriptive_conditional_association_only"]
    if aggregate["raw_rank_ic_defined_date_count"] == 0:
        warnings.append("raw_rank_ic_undefined_for_entire_scope")
    if aggregate["partial_rank_ic_defined_date_count"] == 0:
        warnings.append("partial_rank_ic_undefined_for_entire_scope")
    if (
        temporal_minimum is not None
        and aggregate["partial_rank_ic_defined_date_count"] < temporal_minimum
    ):
        warnings.append("partial_defined_dates_below_temporal_review_threshold")
    return tuple(warnings)


def evaluate_panel_factor_incremental_analysis(
    dataset: Any,
    spec: PanelFactorIncrementalAnalysisSpec = NEUTRAL_PANEL_INCREMENTAL_FACTOR_ANALYSIS_V1,
) -> PanelFactorIncrementalAnalysisResult:
    """Evaluate frozen same-date rank-residual hypotheses without any I/O."""
    if not isinstance(spec, PanelFactorIncrementalAnalysisSpec):
        raise TypeError("spec must be PanelFactorIncrementalAnalysisSpec")
    frame, signal_dates, bounded_identity = _validated_dataset(dataset, spec)
    by_date = {
        signal_date: frame.loc[frame["session_date"].eq(signal_date)].copy(deep=True)
        for signal_date in signal_dates
    }
    daily = tuple(
        _daily_evaluation(
            by_date[signal_date],
            dataset=dataset,
            source_bounded_content_identity=bounded_identity,
            spec=spec,
            hypothesis=hypothesis,
            horizon=horizon,
            outcome_field=outcome,
            signal_date=signal_date,
        )
        for hypothesis in spec.hypotheses
        for horizon in spec.horizons
        for outcome in spec.outcome_fields
        for signal_date in signal_dates
    )
    daily_lookup = {
        (item.hypothesis_name, item.horizon_sessions, item.outcome_field, item.signal_date): item
        for item in daily
    }
    daily_result_identity = _hash({
        "source_dataset_identity": dataset.identity,
        "source_dataset_content_identity": dataset.content_identity,
        "source_bounded_content_identity": bounded_identity,
        "specification_fingerprint": spec.fingerprint,
        "ordered_daily_identities": [item.identity for item in daily],
    })
    blocks: list[IncrementalFactorBlockEvaluation] = []
    summaries: list[IncrementalFactorEvaluationSummary] = []
    for hypothesis in spec.hypotheses:
        for horizon in spec.horizons:
            for outcome in spec.outcome_fields:
                combination_daily = tuple(
                    daily_lookup[(hypothesis.name, horizon, outcome, signal_date)]
                    for signal_date in signal_dates
                )
                combination_blocks: list[IncrementalFactorBlockEvaluation] = []
                for block in spec.blocks:
                    included = tuple(
                        item for item in combination_daily if block.contains(item.signal_date)
                    )
                    aggregate = _aggregate(
                        included,
                        minimum_listwise_finite_count=spec.minimum_listwise_finite_count,
                    )
                    block_result = IncrementalFactorBlockEvaluation(
                        source_dataset_identity=dataset.identity,
                        source_bounded_content_identity=bounded_identity,
                        source_daily_result_identity=daily_result_identity,
                        specification_fingerprint=spec.fingerprint,
                        hypothesis_name=hypothesis.name,
                        target_factor=hypothesis.target_factor,
                        control_factors=hypothesis.control_factors,
                        horizon_sessions=horizon,
                        outcome_field=outcome,
                        block_name=block.name,
                        block_start_date=block.start_date,
                        block_end_date=block.end_date,
                        **aggregate,
                        included_daily_identities=tuple(item.identity for item in included),
                        warnings=_warnings(
                            aggregate,
                            temporal_minimum=spec.temporal_review_minimum_defined_dates,
                        ),
                    )
                    blocks.append(block_result)
                    combination_blocks.append(block_result)
                aggregate = _aggregate(
                    combination_daily,
                    minimum_listwise_finite_count=spec.minimum_listwise_finite_count,
                )
                block_means = tuple(
                    value.mean_daily_partial_rank_ic
                    for value in combination_blocks
                    if value.mean_daily_partial_rank_ic is not None
                )
                block_minimum = None if not block_means else min(block_means)
                block_maximum = None if not block_means else max(block_means)
                summaries.append(IncrementalFactorEvaluationSummary(
                    source_dataset_identity=dataset.identity,
                    source_bounded_content_identity=bounded_identity,
                    source_daily_result_identity=daily_result_identity,
                    specification_fingerprint=spec.fingerprint,
                    hypothesis_name=hypothesis.name,
                    target_factor=hypothesis.target_factor,
                    control_factors=hypothesis.control_factors,
                    horizon_sessions=horizon,
                    outcome_field=outcome,
                    **aggregate,
                    ordered_block_identities=tuple(value.identity for value in combination_blocks),
                    blocks_meeting_temporal_review_count=sum(
                        value.partial_rank_ic_defined_date_count
                        >= spec.temporal_review_minimum_defined_dates
                        for value in combination_blocks
                    ),
                    chronological_partial_sign_flip_count=_sign_flips(tuple(
                        value.mean_daily_partial_rank_ic for value in combination_blocks
                    )),
                    all_blocks_positive_partial_rank_ic=(
                        len(block_means) == len(spec.blocks)
                        and all(value > 0 for value in block_means)
                    ),
                    all_blocks_negative_partial_rank_ic=(
                        len(block_means) == len(spec.blocks)
                        and all(value < 0 for value in block_means)
                    ),
                    minimum_block_mean_partial_rank_ic=block_minimum,
                    maximum_block_mean_partial_rank_ic=block_maximum,
                    range_block_mean_partial_rank_ic=(
                        None
                        if block_minimum is None or block_maximum is None
                        else block_maximum - block_minimum
                    ),
                    largest_absolute_block_mean_partial_rank_ic_concentration=(
                        _concentration(block_means)
                    ),
                    included_daily_identities=tuple(
                        item.identity for item in combination_daily
                    ),
                    warnings=_warnings(aggregate),
                ))

    block_tuple = tuple(blocks)
    summary_tuple = tuple(summaries)
    expected_daily = tuple(
        (signal_date, hypothesis.name, horizon, outcome)
        for hypothesis in spec.hypotheses
        for horizon in spec.horizons
        for outcome in spec.outcome_fields
        for signal_date in signal_dates
    )
    actual_daily = tuple(
        (item.signal_date, item.hypothesis_name, item.horizon_sessions, item.outcome_field)
        for item in daily
    )
    if actual_daily != expected_daily:
        raise ValueError("daily incremental-analysis coverage or ordering does not reconcile")
    expected_blocks = tuple(
        (hypothesis.name, horizon, outcome, block.name)
        for hypothesis in spec.hypotheses
        for horizon in spec.horizons
        for outcome in spec.outcome_fields
        for block in spec.blocks
    )
    actual_blocks = tuple(
        (item.hypothesis_name, item.horizon_sessions, item.outcome_field, item.block_name)
        for item in block_tuple
    )
    if actual_blocks != expected_blocks:
        raise ValueError("incremental-analysis block coverage or ordering does not reconcile")
    expected_summaries = tuple(
        (hypothesis.name, horizon, outcome)
        for hypothesis in spec.hypotheses
        for horizon in spec.horizons
        for outcome in spec.outcome_fields
    )
    actual_summaries = tuple(
        (item.hypothesis_name, item.horizon_sessions, item.outcome_field)
        for item in summary_tuple
    )
    if actual_summaries != expected_summaries:
        raise ValueError("incremental-analysis summary coverage or ordering does not reconcile")
    block_lookup = {
        (item.hypothesis_name, item.horizon_sessions, item.outcome_field, item.block_name): item
        for item in block_tuple
    }
    for summary in summary_tuple:
        key = (summary.hypothesis_name, summary.horizon_sessions, summary.outcome_field)
        expected_ids = tuple(block_lookup[(*key, block.name)].identity for block in spec.blocks)
        if summary.ordered_block_identities != expected_ids:
            raise ValueError("summary does not bind the four ordered temporal blocks")

    return PanelFactorIncrementalAnalysisResult(
        source_dataset_identity=dataset.identity,
        source_dataset_content_identity=dataset.content_identity,
        source_bounded_content_identity=bounded_identity,
        source_observation_index_identity=dataset.observation_index_identity,
        source_observation_content_identity=dataset.observation_content_identity,
        source_feature_panel_identity=dataset.feature_panel_identity,
        source_feature_content_identity=dataset.feature_content_identity,
        source_outcome_panel_identity=dataset.outcome_panel_identity,
        source_outcome_content_identity=dataset.outcome_content_identity,
        specification_fingerprint=spec.fingerprint,
        source_daily_result_identity=daily_result_identity,
        daily_evaluations=daily,
        block_evaluations=block_tuple,
        summaries=summary_tuple,
        limitations_metadata=_LIMITATIONS,
    )
