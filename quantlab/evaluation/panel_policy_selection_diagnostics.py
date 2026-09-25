from __future__ import annotations

"""Outcome-free selection and membership-turnover diagnostics for frozen policies."""

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

from .panel_factor_temporal_stability import (
    NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2,
    OVERALL_END_DATE,
    OVERALL_START_DATE,
    PanelTemporalBlock,
)


SELECTION_DIAGNOSTICS_CONTRACT = "quantlab.panel_policy_selection_diagnostics"
SELECTION_DIAGNOSTICS_VERSION = "v1"
PERMITTED_USE = "offline_outcome_free_research_diagnostics_only"
COMMON_FACTORS = ("adx_14", "rsi_14")
SELECTION_BUDGETS = (5, 10, 20)
RANK_METHOD = "ascending_average_ranks_for_ties_v1"
PERCENTILE_METHOD = "average_rank_minus_one_over_n_minus_one_singleton_half_v1"
COMPOSITE_METHOD = "sum_fixed_weight_times_factor_percentile_v1"
ELIGIBILITY_RULE = "same_date_listwise_finite_adx_14_and_rsi_14_v1"
ORDERING_RULE = "composite_score_descending_then_symbol_ascending_v1"
BOUNDARY_RULE = "fixed_budget_symbol_tiebreak_report_equal_score_split_v1"
PREVIOUS_DATE_RULE = "immediately_preceding_available_benchmark_signal_date_v1"
ONE_WAY_TURNOVER_RULE = "entries_divided_by_previous_selected_count_v1"
SYMMETRIC_TURNOVER_RULE = "entries_plus_exits_divided_by_previous_plus_current_count_v1"
OVERLAP_COEFFICIENT_RULE = "intersection_divided_by_minimum_selection_size_v1"
JACCARD_RULE = "intersection_divided_by_union_size_v1"
ORDINAL_DISPLACEMENT_RULE = "absolute_one_based_rank_difference_for_shared_selected_symbols_v1"
DAILY_AGGREGATION_RULE = "defined_daily_metrics_equal_signal_date_weight_no_symbol_pooling_v1"

_LIMITATIONS = MappingProxyType({
    "outcome_free_descriptive_selection_diagnostics_only": True,
    "alpha_pnl_portfolio_cost_liquidity_capacity_or_execution_conclusion": False,
    "database_coverage_is_historical_vn100": False,
    "turnover_is_membership_not_traded_portfolio_turnover": True,
    "deterministic_symbol_tie_break_is_economic_preference": False,
    "phase_5_8_same_historical_dataset_is_independent_confirmation": False,
    "factor_weight_threshold_policy_or_budget_optimization_occurred": False,
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


def _mean(values: tuple[float, ...]) -> float | None:
    return None if not values else sum(values) / len(values)


def _median(values: tuple[float, ...]) -> float | None:
    return None if not values else float(median(values))


def _std(values: tuple[float, ...]) -> float | None:
    if not values:
        return None
    center = sum(values) / len(values)
    return math.sqrt(sum((value - center) ** 2 for value in values) / len(values))


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


@dataclass(frozen=True, slots=True)
class PanelSelectionPolicy:
    name: str
    factor_weights: tuple[tuple[str, float, str], ...]
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        name = _text(self.name, name="selection policy name")
        normalized: list[tuple[str, float, str]] = []
        for item in tuple(self.factor_weights):
            if not isinstance(item, tuple) or len(item) != 3:
                raise TypeError("factor_weights must contain (factor, weight, direction) tuples")
            factor, raw_weight, direction = item
            factor = _text(factor, name="selection factor")
            direction = _text(direction, name="selection factor direction")
            if factor not in COMMON_FACTORS:
                raise ValueError(f"unsupported selection factor: {factor}")
            if isinstance(raw_weight, bool):
                raise ValueError("selection weights must be finite and positive")
            weight = float(raw_weight)
            if not math.isfinite(weight) or weight <= 0.0:
                raise ValueError("selection weights must be finite and positive")
            if direction != "HIGHER_IS_BETTER":
                raise ValueError("selection direction must be HIGHER_IS_BETTER")
            normalized.append((factor, weight, direction))
        weights = tuple(normalized)
        if not weights or len({item[0] for item in weights}) != len(weights):
            raise ValueError("selection policy factors must be non-empty and unique")
        if not math.isclose(sum(item[1] for item in weights), 1.0, abs_tol=1e-12):
            raise ValueError("selection policy weights must sum to one")
        payload = {
            "contract": {"name": SELECTION_DIAGNOSTICS_CONTRACT, "version": SELECTION_DIAGNOSTICS_VERSION},
            "policy": name,
            "factor_weights": [
                {"factor": factor, "weight": weight, "direction": direction}
                for factor, weight, direction in weights
            ],
        }
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "factor_weights", weights)
        object.__setattr__(self, "fingerprint", _hash(payload))

    @property
    def factors(self) -> tuple[str, ...]:
        return tuple(item[0] for item in self.factor_weights)

    def canonical_content(self) -> Mapping[str, Any]:
        return MappingProxyType({
            "name": self.name,
            "factor_weights": tuple(
                MappingProxyType({"factor": factor, "weight": weight, "direction": direction})
                for factor, weight, direction in self.factor_weights
            ),
            "fingerprint": self.fingerprint,
        })


@dataclass(frozen=True, slots=True)
class PanelSelectionDiagnosticsSpec:
    name: str
    version: str
    policies: tuple[PanelSelectionPolicy, ...]
    selection_budgets: tuple[int, ...]
    blocks: tuple[PanelTemporalBlock, ...]
    rank_method: str = RANK_METHOD
    percentile_method: str = PERCENTILE_METHOD
    composite_method: str = COMPOSITE_METHOD
    eligibility_rule: str = ELIGIBILITY_RULE
    ordering_rule: str = ORDERING_RULE
    boundary_rule: str = BOUNDARY_RULE
    previous_date_rule: str = PREVIOUS_DATE_RULE
    one_way_turnover_rule: str = ONE_WAY_TURNOVER_RULE
    symmetric_turnover_rule: str = SYMMETRIC_TURNOVER_RULE
    overlap_coefficient_rule: str = OVERLAP_COEFFICIENT_RULE
    jaccard_rule: str = JACCARD_RULE
    ordinal_displacement_rule: str = ORDINAL_DISPLACEMENT_RULE
    aggregation_rule: str = DAILY_AGGREGATION_RULE
    permitted_use: str = PERMITTED_USE
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        name = _text(self.name, name="selection diagnostics spec name")
        version = _text(self.version, name="selection diagnostics spec version")
        policies = tuple(self.policies)
        budgets = tuple(self.selection_budgets)
        blocks = tuple(self.blocks)
        if len(policies) != 2 or any(not isinstance(item, PanelSelectionPolicy) for item in policies):
            raise ValueError("selection diagnostics require exactly two PanelSelectionPolicy values")
        if tuple(item.name for item in policies) != ("ADX_ONLY", "ADX_RSI_EQUAL_WEIGHT"):
            raise ValueError("selection diagnostics policies and order are frozen")
        expected_weights = (
            (("adx_14", 1.0, "HIGHER_IS_BETTER"),),
            (("adx_14", 0.5, "HIGHER_IS_BETTER"), ("rsi_14", 0.5, "HIGHER_IS_BETTER")),
        )
        if tuple(item.factor_weights for item in policies) != expected_weights:
            raise ValueError("selection diagnostics policy definitions are frozen")
        if budgets != SELECTION_BUDGETS:
            raise ValueError("selection budgets must be exactly top 5, top 10, and top 20")
        if any(isinstance(item, bool) or not isinstance(item, int) or item <= 0 for item in budgets):
            raise ValueError("selection budgets must be positive integers")
        if blocks != NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2.blocks:
            raise ValueError("selection diagnostics must use the authoritative Phase 5.5 blocks")
        rules = (
            self.rank_method, self.percentile_method, self.composite_method,
            self.eligibility_rule, self.ordering_rule, self.boundary_rule,
            self.previous_date_rule, self.one_way_turnover_rule,
            self.symmetric_turnover_rule, self.overlap_coefficient_rule,
            self.jaccard_rule, self.ordinal_displacement_rule,
            self.aggregation_rule, self.permitted_use,
        )
        expected_rules = (
            RANK_METHOD, PERCENTILE_METHOD, COMPOSITE_METHOD, ELIGIBILITY_RULE,
            ORDERING_RULE, BOUNDARY_RULE, PREVIOUS_DATE_RULE,
            ONE_WAY_TURNOVER_RULE, SYMMETRIC_TURNOVER_RULE,
            OVERLAP_COEFFICIENT_RULE, JACCARD_RULE,
            ORDINAL_DISPLACEMENT_RULE, DAILY_AGGREGATION_RULE, PERMITTED_USE,
        )
        if rules != expected_rules or version != "1":
            raise ValueError("unsupported selection diagnostics methodology")
        payload = {
            "contract": {"name": SELECTION_DIAGNOSTICS_CONTRACT, "version": SELECTION_DIAGNOSTICS_VERSION},
            "name": name,
            "version": version,
            "policies": [dict(item.canonical_content()) for item in policies],
            "selection_budgets": list(budgets),
            "blocks": [item.canonical_content() for item in blocks],
            "rank_method": self.rank_method,
            "percentile_method": self.percentile_method,
            "composite_method": self.composite_method,
            "eligibility_rule": self.eligibility_rule,
            "ordering_rule": self.ordering_rule,
            "boundary_rule": self.boundary_rule,
            "previous_date_rule": self.previous_date_rule,
            "one_way_turnover_rule": self.one_way_turnover_rule,
            "symmetric_turnover_rule": self.symmetric_turnover_rule,
            "overlap_coefficient_rule": self.overlap_coefficient_rule,
            "jaccard_rule": self.jaccard_rule,
            "ordinal_displacement_rule": self.ordinal_displacement_rule,
            "aggregation_rule": self.aggregation_rule,
            "restrictions": dict(_LIMITATIONS),
            "permitted_use": self.permitted_use,
        }
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "policies", policies)
        object.__setattr__(self, "selection_budgets", budgets)
        object.__setattr__(self, "blocks", blocks)
        object.__setattr__(self, "fingerprint", _hash(payload))


NEUTRAL_ADX_RSI_SELECTION_DIAGNOSTICS_V1 = PanelSelectionDiagnosticsSpec(
    name="NEUTRAL_ADX_RSI_SELECTION_DIAGNOSTICS_V1",
    version="1",
    policies=(
        PanelSelectionPolicy("ADX_ONLY", (("adx_14", 1.0, "HIGHER_IS_BETTER"),)),
        PanelSelectionPolicy(
            "ADX_RSI_EQUAL_WEIGHT",
            (("adx_14", 0.5, "HIGHER_IS_BETTER"), ("rsi_14", 0.5, "HIGHER_IS_BETTER")),
        ),
    ),
    selection_budgets=SELECTION_BUDGETS,
    blocks=NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2.blocks,
)


@dataclass(frozen=True, slots=True)
class DailyPolicySelectionDiagnostics:
    source_dataset_identity: str
    source_bounded_content_identity: str
    specification_fingerprint: str
    signal_date: str
    policy_name: str
    policy_fingerprint: str
    requested_selection_count: int
    eligible_cross_section_count: int
    actual_selected_count: int
    ordered_selected_symbols: tuple[str, ...]
    ordered_selected_score_evidence: tuple[tuple[str, float], ...]
    complete_eligible_factor_evidence: tuple[tuple[str, float, float], ...]
    complete_policy_score_ordering: tuple[tuple[str, float], ...]
    boundary_score: float | None
    boundary_tie_count: int
    selected_from_boundary_tie_count: int
    boundary_split_equal_score_group: bool
    insufficient_cross_section: bool
    previous_signal_date: str | None
    previous_selection_identity: str | None
    previous_selected_count: int | None
    overlap_with_previous_count: int | None
    entries: tuple[str, ...]
    exits: tuple[str, ...]
    retained_symbols: tuple[str, ...]
    one_way_turnover: float | None
    symmetric_turnover: float | None
    one_way_turnover_undefined_reason: str | None
    symmetric_turnover_undefined_reason: str | None
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        selected = tuple(self.ordered_selected_symbols)
        selected_scores = tuple(self.ordered_selected_score_evidence)
        eligible = tuple(self.complete_eligible_factor_evidence)
        ordering = tuple(self.complete_policy_score_ordering)
        if self.actual_selected_count != len(selected) or len(selected_scores) != len(selected):
            raise ValueError("daily selected counts and evidence do not reconcile")
        if self.eligible_cross_section_count != len(eligible) or len(ordering) != len(eligible):
            raise ValueError("daily eligible counts and evidence do not reconcile")
        if selected != tuple(item[0] for item in selected_scores):
            raise ValueError("selected symbol and score ordering do not reconcile")
        if selected != tuple(item[0] for item in ordering[: len(selected)]):
            raise ValueError("selected symbols are not the leading policy ordering")
        if self.actual_selected_count != min(self.requested_selection_count, self.eligible_cross_section_count):
            raise ValueError("daily selection count violates the fixed budget")
        if len(set(selected)) != len(selected):
            raise ValueError("daily selection contains duplicate symbols")
        for value in (self.one_way_turnover, self.symmetric_turnover, self.boundary_score):
            if value is not None and not math.isfinite(float(value)):
                raise ValueError("daily numeric diagnostics must be finite or None")
        payload = {
            "contract": {"name": SELECTION_DIAGNOSTICS_CONTRACT, "version": SELECTION_DIAGNOSTICS_VERSION},
            "source_dataset_identity": self.source_dataset_identity,
            "source_bounded_content_identity": self.source_bounded_content_identity,
            "specification_fingerprint": self.specification_fingerprint,
            "signal_date": self.signal_date,
            "policy": {"name": self.policy_name, "fingerprint": self.policy_fingerprint},
            "budget": self.requested_selection_count,
            "eligible_evidence": eligible,
            "policy_score_ordering": ordering,
            "selected_score_evidence": selected_scores,
            "boundary": {
                "score": self.boundary_score,
                "tie_count": self.boundary_tie_count,
                "selected_from_tie_count": self.selected_from_boundary_tie_count,
                "split": self.boundary_split_equal_score_group,
                "insufficient_cross_section": self.insufficient_cross_section,
            },
            "previous": {
                "signal_date": self.previous_signal_date,
                "selection_identity": self.previous_selection_identity,
                "selected_count": self.previous_selected_count,
                "overlap_count": self.overlap_with_previous_count,
                "entries": self.entries,
                "exits": self.exits,
                "retained": self.retained_symbols,
                "one_way_turnover": self.one_way_turnover,
                "symmetric_turnover": self.symmetric_turnover,
                "one_way_undefined_reason": self.one_way_turnover_undefined_reason,
                "symmetric_undefined_reason": self.symmetric_turnover_undefined_reason,
            },
        }
        object.__setattr__(self, "ordered_selected_symbols", selected)
        object.__setattr__(self, "ordered_selected_score_evidence", selected_scores)
        object.__setattr__(self, "complete_eligible_factor_evidence", eligible)
        object.__setattr__(self, "complete_policy_score_ordering", ordering)
        object.__setattr__(self, "entries", tuple(self.entries))
        object.__setattr__(self, "exits", tuple(self.exits))
        object.__setattr__(self, "retained_symbols", tuple(self.retained_symbols))
        object.__setattr__(self, "identity", _hash(payload))


@dataclass(frozen=True, slots=True)
class _DailyPolicySelectionComparison:
    source_bounded_content_identity: str
    specification_fingerprint: str
    signal_date: str
    requested_selection_count: int
    reference_policy_name: str
    challenger_policy_name: str
    reference_selection_identity: str
    challenger_selection_identity: str
    intersection_count: int
    union_count: int
    overlap_coefficient: float | None
    jaccard_similarity: float | None
    symbols_unique_to_reference: tuple[str, ...]
    symbols_unique_to_challenger: tuple[str, ...]
    shared_ordinal_displacements: tuple[tuple[str, int, int, int, int], ...]
    mean_absolute_ordinal_displacement: float | None
    maximum_absolute_ordinal_displacement: int | None
    exact_ordered_list_equality: bool
    exact_selected_set_equality: bool
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        payload = {
            "contract": {"name": SELECTION_DIAGNOSTICS_CONTRACT, "version": SELECTION_DIAGNOSTICS_VERSION},
            "source_bounded_content_identity": self.source_bounded_content_identity,
            "specification_fingerprint": self.specification_fingerprint,
            "signal_date": self.signal_date,
            "budget": self.requested_selection_count,
            "reference_policy": self.reference_policy_name,
            "challenger_policy": self.challenger_policy_name,
            "selection_identities": [self.reference_selection_identity, self.challenger_selection_identity],
            "intersection_count": self.intersection_count,
            "union_count": self.union_count,
            "overlap_coefficient": self.overlap_coefficient,
            "jaccard_similarity": self.jaccard_similarity,
            "unique_reference": self.symbols_unique_to_reference,
            "unique_challenger": self.symbols_unique_to_challenger,
            "ordinal_displacements": self.shared_ordinal_displacements,
            "mean_absolute_ordinal_displacement": self.mean_absolute_ordinal_displacement,
            "maximum_absolute_ordinal_displacement": self.maximum_absolute_ordinal_displacement,
            "exact_ordered_list_equality": self.exact_ordered_list_equality,
            "exact_selected_set_equality": self.exact_selected_set_equality,
        }
        object.__setattr__(self, "symbols_unique_to_reference", tuple(self.symbols_unique_to_reference))
        object.__setattr__(self, "symbols_unique_to_challenger", tuple(self.symbols_unique_to_challenger))
        object.__setattr__(self, "shared_ordinal_displacements", tuple(self.shared_ordinal_displacements))
        object.__setattr__(self, "identity", _hash(payload))


@dataclass(frozen=True, slots=True)
class PolicySelectionTurnoverSummary:
    specification_fingerprint: str
    policy_name: str
    requested_selection_count: int
    scope_name: str
    scope_start_date: str
    scope_end_date: str
    dates_evaluated: int
    defined_one_way_turnover_date_count: int
    defined_symmetric_turnover_date_count: int
    mean_actual_selection_size: float | None
    median_actual_selection_size: float | None
    mean_one_way_turnover: float | None
    median_one_way_turnover: float | None
    population_std_one_way_turnover: float | None
    mean_symmetric_turnover: float | None
    median_symmetric_turnover: float | None
    mean_retained_count: float | None
    total_entry_count: int
    boundary_tie_date_count: int
    boundary_split_date_count: int
    warnings: tuple[str, ...]
    included_daily_identities: tuple[str, ...]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        identities = tuple(self.included_daily_identities)
        if len(identities) != self.dates_evaluated:
            raise ValueError("turnover summary daily identities do not reconcile")
        payload = {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "identity"}
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "included_daily_identities", identities)
        object.__setattr__(self, "identity", _hash(payload))


@dataclass(frozen=True, slots=True)
class PolicySelectionOverlapSummary:
    specification_fingerprint: str
    requested_selection_count: int
    scope_name: str
    scope_start_date: str
    scope_end_date: str
    dates_evaluated: int
    defined_overlap_date_count: int
    defined_jaccard_date_count: int
    defined_ordinal_displacement_date_count: int
    mean_overlap_coefficient: float | None
    median_overlap_coefficient: float | None
    population_std_overlap_coefficient: float | None
    minimum_overlap_coefficient: float | None
    maximum_overlap_coefficient: float | None
    mean_jaccard_similarity: float | None
    median_jaccard_similarity: float | None
    population_std_jaccard_similarity: float | None
    minimum_jaccard_similarity: float | None
    maximum_jaccard_similarity: float | None
    mean_ordinal_displacement: float | None
    median_ordinal_displacement: float | None
    population_std_ordinal_displacement: float | None
    exact_selected_set_equality_date_count: int
    exact_selected_set_equality_rate: float | None
    exact_ordered_list_equality_date_count: int
    exact_ordered_list_equality_rate: float | None
    warnings: tuple[str, ...]
    included_comparison_identities: tuple[str, ...]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        identities = tuple(self.included_comparison_identities)
        if len(identities) != self.dates_evaluated:
            raise ValueError("overlap summary comparison identities do not reconcile")
        payload = {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "identity"}
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "included_comparison_identities", identities)
        object.__setattr__(self, "identity", _hash(payload))


@dataclass(frozen=True, slots=True)
class PanelPolicySelectionDiagnosticsResult:
    source_dataset_identity: str
    source_dataset_content_identity: str
    source_bounded_content_identity: str
    source_observation_index_identity: str
    source_observation_content_identity: str
    source_feature_panel_identity: str
    source_feature_content_identity: str
    source_snapshot_id: str
    source_universe_membership_identity: str
    specification_fingerprint: str
    daily_selections: tuple[DailyPolicySelectionDiagnostics, ...]
    daily_policy_comparisons: tuple[_DailyPolicySelectionComparison, ...]
    turnover_summaries: tuple[PolicySelectionTurnoverSummary, ...]
    overlap_summaries: tuple[PolicySelectionOverlapSummary, ...]
    limitations_metadata: Mapping[str, Any]
    contract_version: str = SELECTION_DIAGNOSTICS_VERSION
    identity: str = field(init=False)
    _daily_by_key: Mapping[tuple[str, str, int], DailyPolicySelectionDiagnostics] = field(init=False, repr=False, compare=False)
    _comparison_by_key: Mapping[tuple[str, int], _DailyPolicySelectionComparison] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        daily = tuple(self.daily_selections)
        comparisons = tuple(self.daily_policy_comparisons)
        turnover = tuple(self.turnover_summaries)
        overlaps = tuple(self.overlap_summaries)
        daily_map = {(item.signal_date, item.policy_name, item.requested_selection_count): item for item in daily}
        comparison_map = {(item.signal_date, item.requested_selection_count): item for item in comparisons}
        if len(daily_map) != len(daily) or len(comparison_map) != len(comparisons):
            raise ValueError("selection diagnostics contain duplicate daily keys")
        metadata = self.limitations_metadata
        if not isinstance(metadata, MappingProxyType) or dict(metadata) != dict(_LIMITATIONS):
            raise ValueError("selection diagnostics limitations metadata is not canonical")
        payload = {
            "contract": {"name": SELECTION_DIAGNOSTICS_CONTRACT, "version": self.contract_version},
            "source": {
                "dataset_identity": self.source_dataset_identity,
                "dataset_content_identity": self.source_dataset_content_identity,
                "bounded_content_identity": self.source_bounded_content_identity,
                "observation_index_identity": self.source_observation_index_identity,
                "observation_content_identity": self.source_observation_content_identity,
                "feature_panel_identity": self.source_feature_panel_identity,
                "feature_content_identity": self.source_feature_content_identity,
                "snapshot_id": self.source_snapshot_id,
                "universe_membership_identity": self.source_universe_membership_identity,
            },
            "specification_fingerprint": self.specification_fingerprint,
            "daily_selection_identities": [item.identity for item in daily],
            "daily_comparison_identities": [item.identity for item in comparisons],
            "turnover_summary_identities": [item.identity for item in turnover],
            "overlap_summary_identities": [item.identity for item in overlaps],
            "limitations": metadata,
        }
        object.__setattr__(self, "daily_selections", daily)
        object.__setattr__(self, "daily_policy_comparisons", comparisons)
        object.__setattr__(self, "turnover_summaries", turnover)
        object.__setattr__(self, "overlap_summaries", overlaps)
        object.__setattr__(self, "_daily_by_key", MappingProxyType(daily_map))
        object.__setattr__(self, "_comparison_by_key", MappingProxyType(comparison_map))
        object.__setattr__(self, "identity", _hash(payload))

    def selection_for(self, signal_date: str, policy_name: str, budget: int) -> DailyPolicySelectionDiagnostics:
        return self._daily_by_key[(signal_date, policy_name, budget)]

    def comparison_for(self, signal_date: str, budget: int) -> _DailyPolicySelectionComparison:
        return self._comparison_by_key[(signal_date, budget)]


def _validated_predictors(
    dataset: Any,
    spec: PanelSelectionDiagnosticsSpec,
) -> tuple[tuple[str, ...], Mapping[str, tuple[tuple[str, float, float], ...]], str]:
    import pandas as pd

    from quantlab.panels.research_dataset_contracts import (
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
        or dataset.metadata.get("future_looking") is not True
        or dataset.metadata.get("production_signal_safe") is not False
        or dataset.metadata.get("permitted_use") != dataset.spec.permitted_use
    ):
        raise ValueError("dataset must retain its immutable offline-research provenance boundary")
    for name in (
        "identity", "content_identity", "observation_index_identity",
        "observation_content_identity", "feature_panel_identity", "feature_content_identity",
        "snapshot_id", "universe_membership_identity",
    ):
        _text(getattr(dataset, name), name=f"dataset {name}")
    frame = dataset.predictor_frame()
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("dataset.predictor_frame() must return a DataFrame")
    frame = frame.copy(deep=True)
    forbidden = set(dataset.spec.forbidden_predictor_columns)
    leaked = tuple(sorted(forbidden.intersection(frame.columns)))
    if leaked:
        raise ValueError("future-looking forbidden field entered predictor projection: " + ", ".join(leaked))
    if tuple(frame.columns) != tuple(dataset.spec.predictor_columns):
        raise ValueError("predictor projection schema does not match Phase 5.3B")
    if len(frame) != dataset.observation_row_count or dataset.metadata.get("observation_row_count") != len(frame):
        raise ValueError("dataset predictor population count does not reconcile")
    if any(frame.columns.tolist().count(factor) != 1 for factor in COMMON_FACTORS):
        raise ValueError("ADX and RSI must each exist exactly once in the predictor projection")
    normalized_keys: list[tuple[str, str]] = []
    for row in frame.loc[:, ["session_date", "symbol"]].itertuples(index=False):
        signal_date = _date_text(row.session_date, name="predictor signal date")
        symbol = _text(row.symbol, name="predictor symbol").upper()
        normalized_keys.append((signal_date, symbol))
    if len(set(normalized_keys)) != len(normalized_keys):
        raise ValueError("predictor projection contains duplicate date/symbol keys")
    frame.loc[:, "session_date"] = tuple(item[0] for item in normalized_keys)
    frame.loc[:, "symbol"] = tuple(item[1] for item in normalized_keys)
    frame = frame.sort_values(["session_date", "symbol"], kind="mergesort").reset_index(drop=True)
    signal_dates = tuple(
        _date_text(item.session_date, name="benchmark signal date")
        for item in dataset.session_audit
    )
    if (
        not signal_dates or signal_dates != tuple(sorted(signal_dates))
        or len(set(signal_dates)) != len(signal_dates)
        or signal_dates[0] < OVERALL_START_DATE or signal_dates[-1] > OVERALL_END_DATE
        or not set(frame["session_date"]).issubset(signal_dates)
    ):
        raise ValueError("benchmark signal dates are invalid or outside the Phase 5.5 interval")
    rows_by_date: dict[str, list[tuple[str, float, float]]] = {item: [] for item in signal_dates}
    complete_evidence: list[tuple[str, str, Any, Any]] = []
    for row in frame.loc[:, ["session_date", "symbol", *COMMON_FACTORS]].itertuples(index=False):
        adx = _finite(row.adx_14, column="adx_14")
        rsi = _finite(row.rsi_14, column="rsi_14")
        complete_evidence.append((row.session_date, row.symbol, _identity_value(row.adx_14), _identity_value(row.rsi_14)))
        if adx is not None and rsi is not None:
            rows_by_date[row.session_date].append((row.symbol, adx, rsi))
    frozen_rows = MappingProxyType({key: tuple(value) for key, value in rows_by_date.items()})
    bounded = _hash({
        "source_dataset_content_identity": dataset.content_identity,
        "source_observation_content_identity": dataset.observation_content_identity,
        "source_feature_content_identity": dataset.feature_content_identity,
        "source_dataset_specification_fingerprint": dataset.spec.fingerprint,
        "selection_specification_fingerprint": spec.fingerprint,
        "signal_dates": signal_dates,
        "required_common_factors": COMMON_FACTORS,
        "complete_predictor_evidence": complete_evidence,
    })
    return signal_dates, frozen_rows, bounded


def _policy_ordering(
    eligible: tuple[tuple[str, float, float], ...],
    policy: PanelSelectionPolicy,
) -> tuple[tuple[str, float], ...]:
    values = {
        "adx_14": tuple(item[1] for item in eligible),
        "rsi_14": tuple(item[2] for item in eligible),
    }
    percentiles = {
        factor: _percentiles(_average_ranks(values[factor]))
        for factor in COMMON_FACTORS
    }
    scored = tuple(
        (
            eligible[index][0],
            sum(weight * percentiles[factor][index] for factor, weight, _direction in policy.factor_weights),
        )
        for index in range(len(eligible))
    )
    return tuple(sorted(scored, key=lambda item: (-item[1], item[0])))


def _daily_selection(
    *,
    dataset: Any,
    bounded_identity: str,
    spec: PanelSelectionDiagnosticsSpec,
    policy: PanelSelectionPolicy,
    signal_date: str,
    budget: int,
    eligible: tuple[tuple[str, float, float], ...],
    ordering: tuple[tuple[str, float], ...],
    previous: DailyPolicySelectionDiagnostics | None,
) -> DailyPolicySelectionDiagnostics:
    selected_scores = ordering[: min(budget, len(ordering))]
    selected = tuple(item[0] for item in selected_scores)
    boundary_score = selected_scores[-1][1] if selected_scores else None
    boundary_group = () if boundary_score is None else tuple(item for item in ordering if item[1] == boundary_score)
    selected_group = () if boundary_score is None else tuple(item for item in selected_scores if item[1] == boundary_score)
    if previous is None:
        previous_date = None
        previous_identity = None
        previous_count = overlap = None
        entries = exits = retained = ()
        one_way = symmetric = None
        one_way_reason = symmetric_reason = "first_available_date"
    else:
        previous_date = previous.signal_date
        previous_identity = previous.identity
        previous_symbols = previous.ordered_selected_symbols
        previous_set, current_set = set(previous_symbols), set(selected)
        entries = tuple(symbol for symbol in selected if symbol not in previous_set)
        exits = tuple(symbol for symbol in previous_symbols if symbol not in current_set)
        retained = tuple(symbol for symbol in selected if symbol in previous_set)
        previous_count = len(previous_symbols)
        overlap = len(retained)
        if previous_count:
            one_way = len(entries) / previous_count
            one_way_reason = None
        else:
            one_way = None
            one_way_reason = "previous_selection_empty"
        symmetric_denominator = previous_count + len(selected)
        if symmetric_denominator:
            symmetric = (len(entries) + len(exits)) / symmetric_denominator
            symmetric_reason = None
        else:
            symmetric = None
            symmetric_reason = "combined_selection_empty"
    return DailyPolicySelectionDiagnostics(
        source_dataset_identity=dataset.identity,
        source_bounded_content_identity=bounded_identity,
        specification_fingerprint=spec.fingerprint,
        signal_date=signal_date,
        policy_name=policy.name,
        policy_fingerprint=policy.fingerprint,
        requested_selection_count=budget,
        eligible_cross_section_count=len(eligible),
        actual_selected_count=len(selected),
        ordered_selected_symbols=selected,
        ordered_selected_score_evidence=selected_scores,
        complete_eligible_factor_evidence=eligible,
        complete_policy_score_ordering=ordering,
        boundary_score=boundary_score,
        boundary_tie_count=len(boundary_group),
        selected_from_boundary_tie_count=len(selected_group),
        boundary_split_equal_score_group=len(selected_group) < len(boundary_group),
        insufficient_cross_section=len(eligible) < budget,
        previous_signal_date=previous_date,
        previous_selection_identity=previous_identity,
        previous_selected_count=previous_count,
        overlap_with_previous_count=overlap,
        entries=entries,
        exits=exits,
        retained_symbols=retained,
        one_way_turnover=one_way,
        symmetric_turnover=symmetric,
        one_way_turnover_undefined_reason=one_way_reason,
        symmetric_turnover_undefined_reason=symmetric_reason,
    )


def _comparison(
    reference: DailyPolicySelectionDiagnostics,
    challenger: DailyPolicySelectionDiagnostics,
) -> _DailyPolicySelectionComparison:
    left, right = reference.ordered_selected_symbols, challenger.ordered_selected_symbols
    left_set, right_set = set(left), set(right)
    shared = left_set.intersection(right_set)
    intersection, union = len(shared), len(left_set.union(right_set))
    minimum = min(len(left), len(right))
    left_positions = {symbol: index + 1 for index, symbol in enumerate(left)}
    right_positions = {symbol: index + 1 for index, symbol in enumerate(right)}
    displacements = tuple(
        (
            symbol,
            left_positions[symbol],
            right_positions[symbol],
            right_positions[symbol] - left_positions[symbol],
            abs(right_positions[symbol] - left_positions[symbol]),
        )
        for symbol in left if symbol in shared
    )
    absolute = tuple(float(item[4]) for item in displacements)
    return _DailyPolicySelectionComparison(
        source_bounded_content_identity=reference.source_bounded_content_identity,
        specification_fingerprint=reference.specification_fingerprint,
        signal_date=reference.signal_date,
        requested_selection_count=reference.requested_selection_count,
        reference_policy_name=reference.policy_name,
        challenger_policy_name=challenger.policy_name,
        reference_selection_identity=reference.identity,
        challenger_selection_identity=challenger.identity,
        intersection_count=intersection,
        union_count=union,
        overlap_coefficient=None if minimum == 0 else intersection / minimum,
        jaccard_similarity=None if union == 0 else intersection / union,
        symbols_unique_to_reference=tuple(symbol for symbol in left if symbol not in right_set),
        symbols_unique_to_challenger=tuple(symbol for symbol in right if symbol not in left_set),
        shared_ordinal_displacements=displacements,
        mean_absolute_ordinal_displacement=_mean(absolute),
        maximum_absolute_ordinal_displacement=None if not absolute else int(max(absolute)),
        exact_ordered_list_equality=left == right,
        exact_selected_set_equality=left_set == right_set,
    )


def _scopes(blocks: tuple[PanelTemporalBlock, ...]) -> tuple[tuple[str, str, str], ...]:
    return (
        ("whole_period", OVERALL_START_DATE, OVERALL_END_DATE),
        *((item.name, item.start_date, item.end_date) for item in blocks),
    )


def _turnover_summary(
    items: tuple[DailyPolicySelectionDiagnostics, ...],
    *,
    spec: PanelSelectionDiagnosticsSpec,
    policy: str,
    budget: int,
    scope: tuple[str, str, str],
) -> PolicySelectionTurnoverSummary:
    one_way = tuple(item.one_way_turnover for item in items if item.one_way_turnover is not None)
    symmetric = tuple(item.symmetric_turnover for item in items if item.symmetric_turnover is not None)
    sizes = tuple(float(item.actual_selected_count) for item in items)
    retained = tuple(float(len(item.retained_symbols)) for item in items if item.previous_signal_date is not None)
    warnings = () if items else ("no_signal_dates_in_scope",)
    return PolicySelectionTurnoverSummary(
        specification_fingerprint=spec.fingerprint,
        policy_name=policy,
        requested_selection_count=budget,
        scope_name=scope[0],
        scope_start_date=scope[1],
        scope_end_date=scope[2],
        dates_evaluated=len(items),
        defined_one_way_turnover_date_count=len(one_way),
        defined_symmetric_turnover_date_count=len(symmetric),
        mean_actual_selection_size=_mean(sizes),
        median_actual_selection_size=_median(sizes),
        mean_one_way_turnover=_mean(one_way),
        median_one_way_turnover=_median(one_way),
        population_std_one_way_turnover=_std(one_way),
        mean_symmetric_turnover=_mean(symmetric),
        median_symmetric_turnover=_median(symmetric),
        mean_retained_count=_mean(retained),
        total_entry_count=sum(len(item.entries) for item in items),
        boundary_tie_date_count=sum(item.boundary_tie_count > 1 for item in items),
        boundary_split_date_count=sum(item.boundary_split_equal_score_group for item in items),
        warnings=warnings,
        included_daily_identities=tuple(item.identity for item in items),
    )


def _overlap_summary(
    items: tuple[_DailyPolicySelectionComparison, ...],
    *,
    spec: PanelSelectionDiagnosticsSpec,
    budget: int,
    scope: tuple[str, str, str],
) -> PolicySelectionOverlapSummary:
    overlaps = tuple(item.overlap_coefficient for item in items if item.overlap_coefficient is not None)
    jaccards = tuple(item.jaccard_similarity for item in items if item.jaccard_similarity is not None)
    displacements = tuple(
        item.mean_absolute_ordinal_displacement
        for item in items if item.mean_absolute_ordinal_displacement is not None
    )
    set_equal = sum(item.exact_selected_set_equality for item in items)
    order_equal = sum(item.exact_ordered_list_equality for item in items)
    warnings = () if items else ("no_signal_dates_in_scope",)
    return PolicySelectionOverlapSummary(
        specification_fingerprint=spec.fingerprint,
        requested_selection_count=budget,
        scope_name=scope[0],
        scope_start_date=scope[1],
        scope_end_date=scope[2],
        dates_evaluated=len(items),
        defined_overlap_date_count=len(overlaps),
        defined_jaccard_date_count=len(jaccards),
        defined_ordinal_displacement_date_count=len(displacements),
        mean_overlap_coefficient=_mean(overlaps),
        median_overlap_coefficient=_median(overlaps),
        population_std_overlap_coefficient=_std(overlaps),
        minimum_overlap_coefficient=None if not overlaps else min(overlaps),
        maximum_overlap_coefficient=None if not overlaps else max(overlaps),
        mean_jaccard_similarity=_mean(jaccards),
        median_jaccard_similarity=_median(jaccards),
        population_std_jaccard_similarity=_std(jaccards),
        minimum_jaccard_similarity=None if not jaccards else min(jaccards),
        maximum_jaccard_similarity=None if not jaccards else max(jaccards),
        mean_ordinal_displacement=_mean(displacements),
        median_ordinal_displacement=_median(displacements),
        population_std_ordinal_displacement=_std(displacements),
        exact_selected_set_equality_date_count=set_equal,
        exact_selected_set_equality_rate=None if not items else set_equal / len(items),
        exact_ordered_list_equality_date_count=order_equal,
        exact_ordered_list_equality_rate=None if not items else order_equal / len(items),
        warnings=warnings,
        included_comparison_identities=tuple(item.identity for item in items),
    )


def evaluate_panel_policy_selection_diagnostics(
    dataset: Any,
    spec: PanelSelectionDiagnosticsSpec = NEUTRAL_ADX_RSI_SELECTION_DIAGNOSTICS_V1,
) -> PanelPolicySelectionDiagnosticsResult:
    """Evaluate two frozen predictor-only policies without future outcomes."""
    if not isinstance(spec, PanelSelectionDiagnosticsSpec):
        raise TypeError("spec must be PanelSelectionDiagnosticsSpec")
    signal_dates, rows_by_date, bounded = _validated_predictors(dataset, spec)
    daily: list[DailyPolicySelectionDiagnostics] = []
    previous: dict[tuple[str, int], DailyPolicySelectionDiagnostics] = {}
    by_key: dict[tuple[str, str, int], DailyPolicySelectionDiagnostics] = {}
    for signal_date in signal_dates:
        eligible = rows_by_date[signal_date]
        ordering_by_policy = {
            policy.name: _policy_ordering(eligible, policy)
            for policy in spec.policies
        }
        for policy in spec.policies:
            for budget in spec.selection_budgets:
                key = (policy.name, budget)
                record = _daily_selection(
                    dataset=dataset,
                    bounded_identity=bounded,
                    spec=spec,
                    policy=policy,
                    signal_date=signal_date,
                    budget=budget,
                    eligible=eligible,
                    ordering=ordering_by_policy[policy.name],
                    previous=previous.get(key),
                )
                daily.append(record)
                by_key[(signal_date, policy.name, budget)] = record
                previous[key] = record
    comparisons = tuple(
        _comparison(
            by_key[(signal_date, spec.policies[0].name, budget)],
            by_key[(signal_date, spec.policies[1].name, budget)],
        )
        for signal_date in signal_dates
        for budget in spec.selection_budgets
    )
    daily_tuple = tuple(daily)
    scopes = _scopes(spec.blocks)
    turnover = tuple(
        _turnover_summary(
            tuple(
                item for item in daily_tuple
                if item.policy_name == policy.name
                and item.requested_selection_count == budget
                and scope[1] <= item.signal_date <= scope[2]
            ),
            spec=spec,
            policy=policy.name,
            budget=budget,
            scope=scope,
        )
        for policy in spec.policies
        for budget in spec.selection_budgets
        for scope in scopes
    )
    overlaps = tuple(
        _overlap_summary(
            tuple(
                item for item in comparisons
                if item.requested_selection_count == budget
                and scope[1] <= item.signal_date <= scope[2]
            ),
            spec=spec,
            budget=budget,
            scope=scope,
        )
        for budget in spec.selection_budgets
        for scope in scopes
    )
    return PanelPolicySelectionDiagnosticsResult(
        source_dataset_identity=dataset.identity,
        source_dataset_content_identity=dataset.content_identity,
        source_bounded_content_identity=bounded,
        source_observation_index_identity=dataset.observation_index_identity,
        source_observation_content_identity=dataset.observation_content_identity,
        source_feature_panel_identity=dataset.feature_panel_identity,
        source_feature_content_identity=dataset.feature_content_identity,
        source_snapshot_id=dataset.snapshot_id,
        source_universe_membership_identity=dataset.universe_membership_identity,
        specification_fingerprint=spec.fingerprint,
        daily_selections=daily_tuple,
        daily_policy_comparisons=comparisons,
        turnover_summaries=turnover,
        overlap_summaries=overlaps,
        limitations_metadata=_LIMITATIONS,
    )
