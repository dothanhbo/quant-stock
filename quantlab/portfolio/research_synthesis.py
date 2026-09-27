from __future__ import annotations

"""Pure Phase 8 synthesis over frozen Phase 6 and Phase 7 evidence."""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import csv
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from quantlab.identity import canonical_identity_value, canonical_json

from .construction import BLOCKS, BUDGETS, SELECTION_POLICY, WeightingPolicy
from .outcome_evaluation import COST_GRID_BPS, HORIZONS, PATH_METRIC_REASON, PATH_METRIC_STATUS


CONTRACT = "quantlab.neutral_portfolio_research_synthesis"
VERSION = "v1"
DIMENSIONS = (
    "PROVENANCE_COMPLETENESS",
    "OUTCOME_COVERAGE",
    "GROSS_EXCESS_DIRECTION",
    "POSITIVE_EXCESS_FREQUENCY",
    "TEMPORAL_DIRECTION",
    "RECENT_BLOCK_BEHAVIOR",
    "ECONOMIC_MAGNITUDE",
    "STRUCTURAL_DIVERSIFICATION",
    "TURNOVER_BURDEN",
    "PATH_LIMITATION",
)


class EvidenceStatus(str, Enum):
    SUPPORTIVE = "SUPPORTIVE"
    NEUTRAL = "NEUTRAL"
    ADVERSE = "ADVERSE"
    INSUFFICIENT = "INSUFFICIENT"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class PortfolioResearchDecision(str, Enum):
    REJECT_FOR_NOW = "REJECT_FOR_NOW"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    HOLD = "HOLD"
    ADVANCE_TO_FORWARD_VALIDATION = "ADVANCE_TO_FORWARD_VALIDATION"


class RobustnessFlag(str, Enum):
    RECENT_WEAKENING = "RECENT_WEAKENING"
    TEMPORALLY_MIXED = "TEMPORALLY_MIXED"
    COST_SENSITIVE = "COST_SENSITIVE"
    LOW_COVERAGE = "LOW_COVERAGE"
    STRUCTURALLY_CONCENTRATED = "STRUCTURALLY_CONCENTRATED"
    HIGH_IMPLEMENTATION_BURDEN = "HIGH_IMPLEMENTATION_BURDEN"
    PATH_EVIDENCE_UNAVAILABLE = "PATH_EVIDENCE_UNAVAILABLE"


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("numeric evidence must be finite")
    return result


@dataclass(frozen=True, slots=True)
class FrozenStructuralEvidence:
    requested_budget: int
    evaluated_dates: int
    underfilled_date_count: int
    empty_date_count: int
    mean_fill_ratio: float | None
    mean_effective_n: float | None
    mean_max_single_name_weight: float | None
    mean_herfindahl: float | None
    defined_weight_turnover_dates: int
    mean_one_way_weight_turnover: float | None
    structural_state: str
    source_identity: str


@dataclass(frozen=True, slots=True)
class FrozenOutcomeSummaryEvidence:
    requested_budget: int
    horizon_sessions: int
    total_dates: int
    evaluable_dates: int
    mean_excess_return_pct_points: float | None
    median_excess_return_pct_points: float | None
    positive_excess_rate: float | None
    path_metric_status: str
    path_metric_reason: str
    source_identity: str


@dataclass(frozen=True, slots=True)
class FrozenOutcomeBlockEvidence:
    requested_budget: int
    horizon_sessions: int
    block_name: str
    evaluable_dates: int
    mean_excess_return_pct_points: float | None
    temporal_evidence: str
    source_identity: str


@dataclass(frozen=True, slots=True)
class FrozenCostEvidence:
    requested_budget: int
    horizon_sessions: int
    cost_rate_bps: int
    evaluable_date_count: int
    turnover_defined_date_count: int
    mean_gross_excess_return_pct_points: float | None
    mean_estimated_turnover_cost_pct_points: float | None
    mean_net_excess_return_pct_points: float | None
    label: str
    source_identity: str


@dataclass(frozen=True, slots=True)
class PortfolioResearchSynthesisInput:
    source_identities: Mapping[str, str]
    provenance_verified: bool
    structures: tuple[FrozenStructuralEvidence, ...]
    outcomes: tuple[FrozenOutcomeSummaryEvidence, ...]
    blocks: tuple[FrozenOutcomeBlockEvidence, ...]
    costs: tuple[FrozenCostEvidence, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_identities", MappingProxyType(dict(self.source_identities)))
        structural_keys = tuple(item.requested_budget for item in self.structures)
        outcome_keys = tuple((item.requested_budget, item.horizon_sessions) for item in self.outcomes)
        block_keys = tuple((item.requested_budget, item.horizon_sessions, item.block_name) for item in self.blocks)
        cost_keys = tuple((item.requested_budget, item.horizon_sessions, item.cost_rate_bps) for item in self.costs)
        expected_outcomes = tuple((budget, horizon) for budget in BUDGETS for horizon in HORIZONS)
        expected_blocks = tuple(
            (budget, horizon, block[0])
            for budget in BUDGETS for horizon in HORIZONS for block in BLOCKS
        )
        expected_costs = tuple(
            (budget, horizon, cost)
            for budget in BUDGETS for horizon in HORIZONS for cost in COST_GRID_BPS
        )
        if structural_keys != BUDGETS:
            raise ValueError("structural evidence must contain frozen budgets in order")
        if outcome_keys != expected_outcomes:
            raise ValueError("outcome evidence must contain every frozen scenario in order")
        if block_keys != expected_blocks:
            raise ValueError("block evidence must contain every frozen scenario and block in order")
        if cost_keys != expected_costs:
            raise ValueError("cost evidence must contain every frozen scenario and cost in order")


@dataclass(frozen=True, slots=True)
class PortfolioResearchSynthesisSpec:
    name: str = "NEUTRAL_PORTFOLIO_RESEARCH_SYNTHESIS_V1"
    version: str = "1"
    budgets: tuple[int, ...] = BUDGETS
    horizons: tuple[int, ...] = HORIZONS
    cost_grid_bps: tuple[int, ...] = COST_GRID_BPS
    blocks: tuple[tuple[str, str, str], ...] = BLOCKS
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            self.version != "1" or self.budgets != BUDGETS or self.horizons != HORIZONS
            or self.cost_grid_bps != COST_GRID_BPS or self.blocks != BLOCKS
        ):
            raise ValueError("Phase 8 synthesis specification is frozen")
        rules = {
            "provenance": "supportive_if_verified_else_insufficient",
            "coverage": "insufficient_if_zero;adverse_if_less_than_half;supportive_if_at_least_half_and_all_blocks_defined;otherwise_neutral",
            "gross_excess": "supportive_if_mean_and_median_positive;adverse_if_both_negative;otherwise_neutral",
            "positive_frequency": "supportive_above_one_half;adverse_below_one_half;neutral_at_one_half",
            "temporal": "adverse_if_any_reversal_or_all_block_excess_negative;supportive_if_all_same_direction_and_all_block_excess_positive;otherwise_neutral",
            "recent": "adverse_if_negative_or_reversal;supportive_if_positive_and_same_direction;otherwise_neutral",
            "economic": "maximum_frozen_cost;supportive_if_gross_and_net_positive;adverse_if_gross_nonpositive_or_net_negative;neutral_if_net_zero",
            "structure": "adverse_only_if_all_empty_or_effective_n_at_most_one_with_full_single_name_weight;otherwise_neutral",
            "turnover": "adverse_if_mean_one_way_turnover_at_least_one;otherwise_neutral",
            "path": f"{PATH_METRIC_STATUS}:{PATH_METRIC_REASON}",
            "low_coverage_flag": "evaluable_dates_less_than_half_total_dates",
            "recent_weakening_flag": "recent_mean_excess_below_every_defined_prior_block_mean",
            "temporal_mixed_flag": "block_excess_signs_or_temporal_labels_not_uniformly_positive_same_direction",
            "cost_sensitive_flag": "positive_gross_excess_nonpositive_at_any_nonzero_frozen_cost",
            "concentration_flag": "mean_effective_n_below_two_or_mean_max_single_name_weight_above_one_half",
            "implementation_burden_flag": "mean_one_way_turnover_at_least_one_half",
            "decision": "adverse_rejects;insufficient_is_insufficient;recent_or_temporal_contradiction_holds;otherwise_supportive_or_neutral_with_support_advances",
            "no_scores_ranking_winner_or_optimization": True,
        }
        object.__setattr__(self, "fingerprint", _hash({
            "contract": {"name": CONTRACT, "version": VERSION},
            "name": self.name, "budgets": self.budgets, "horizons": self.horizons,
            "cost_grid_bps": self.cost_grid_bps, "blocks": self.blocks,
            "dimensions": DIMENSIONS, "statuses": tuple(item.value for item in EvidenceStatus),
            "decisions": tuple(item.value for item in PortfolioResearchDecision),
            "flags": tuple(item.value for item in RobustnessFlag), "rules": rules,
        }))


NEUTRAL_PORTFOLIO_RESEARCH_SYNTHESIS_V1 = PortfolioResearchSynthesisSpec()


@dataclass(frozen=True, slots=True)
class PortfolioResearchEvidence:
    requested_budget: int
    horizon_sessions: int
    dimension: str
    status: EvidenceStatus
    reason_code: str
    identity: str


@dataclass(frozen=True, slots=True)
class ScenarioResearchFlag:
    requested_budget: int
    horizon_sessions: int
    flag: RobustnessFlag
    reason_code: str
    identity: str


@dataclass(frozen=True, slots=True)
class ScenarioResearchDecision:
    requested_budget: int
    horizon_sessions: int
    decision: PortfolioResearchDecision
    reason_code: str
    evidence_identities: tuple[str, ...]
    flags: tuple[str, ...]
    identity: str


@dataclass(frozen=True, slots=True)
class PortfolioResearchSynthesisResult:
    contract_name: str
    contract_version: str
    specification_fingerprint: str
    source_identities: Mapping[str, str]
    decisions: tuple[ScenarioResearchDecision, ...]
    evidence: tuple[PortfolioResearchEvidence, ...]
    flags: tuple[ScenarioResearchFlag, ...]
    limitations: tuple[str, ...]
    identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_identities", MappingProxyType(dict(self.source_identities)))


def _evidence(budget: int, horizon: int, dimension: str, status: EvidenceStatus, reason: str) -> PortfolioResearchEvidence:
    payload = {"budget": budget, "horizon": horizon, "dimension": dimension, "status": status.value, "reason": reason}
    return PortfolioResearchEvidence(budget, horizon, dimension, status, reason, _hash(payload))


def _flag(budget: int, horizon: int, flag: RobustnessFlag, reason: str) -> ScenarioResearchFlag:
    payload = {"budget": budget, "horizon": horizon, "flag": flag.value, "reason": reason}
    return ScenarioResearchFlag(budget, horizon, flag, reason, _hash(payload))


def _scenario_evidence(
    source: PortfolioResearchSynthesisInput,
    budget: int,
    horizon: int,
) -> tuple[tuple[PortfolioResearchEvidence, ...], tuple[ScenarioResearchFlag, ...]]:
    structure = next(item for item in source.structures if item.requested_budget == budget)
    outcome = next(item for item in source.outcomes if item.requested_budget == budget and item.horizon_sessions == horizon)
    blocks = tuple(item for item in source.blocks if item.requested_budget == budget and item.horizon_sessions == horizon)
    costs = tuple(item for item in source.costs if item.requested_budget == budget and item.horizon_sessions == horizon)
    evidence: list[PortfolioResearchEvidence] = []
    flags: list[ScenarioResearchFlag] = []

    evidence.append(_evidence(
        budget, horizon, DIMENSIONS[0],
        EvidenceStatus.SUPPORTIVE if source.provenance_verified else EvidenceStatus.INSUFFICIENT,
        "PROVENANCE_VERIFIED" if source.provenance_verified else "PROVENANCE_NOT_VERIFIED",
    ))

    if outcome.total_dates <= 0 or outcome.evaluable_dates <= 0:
        coverage_status, coverage_reason = EvidenceStatus.INSUFFICIENT, "NO_EVALUABLE_OUTCOME_DATES"
    elif outcome.evaluable_dates * 2 < outcome.total_dates:
        coverage_status, coverage_reason = EvidenceStatus.ADVERSE, "EVALUABLE_OUTCOMES_BELOW_HALF_OF_SOURCE_DATES"
        flags.append(_flag(budget, horizon, RobustnessFlag.LOW_COVERAGE, coverage_reason))
    elif all(item.evaluable_dates > 0 for item in blocks):
        coverage_status, coverage_reason = EvidenceStatus.SUPPORTIVE, "OUTCOMES_DEFINED_IN_EVERY_FROZEN_BLOCK"
    else:
        coverage_status, coverage_reason = EvidenceStatus.NEUTRAL, "OUTCOME_COVERAGE_PRESENT_BUT_BLOCK_INCOMPLETE"
    evidence.append(_evidence(budget, horizon, DIMENSIONS[1], coverage_status, coverage_reason))

    mean_excess, median_excess = outcome.mean_excess_return_pct_points, outcome.median_excess_return_pct_points
    if mean_excess is None or median_excess is None:
        status, reason = EvidenceStatus.INSUFFICIENT, "MEAN_OR_MEDIAN_EXCESS_UNDEFINED"
    elif mean_excess > 0 and median_excess > 0:
        status, reason = EvidenceStatus.SUPPORTIVE, "MEAN_AND_MEDIAN_EXCESS_POSITIVE"
    elif mean_excess < 0 and median_excess < 0:
        status, reason = EvidenceStatus.ADVERSE, "MEAN_AND_MEDIAN_EXCESS_NEGATIVE"
    else:
        status, reason = EvidenceStatus.NEUTRAL, "MEAN_AND_MEDIAN_EXCESS_DIRECTION_MIXED_OR_ZERO"
    evidence.append(_evidence(budget, horizon, DIMENSIONS[2], status, reason))

    frequency = outcome.positive_excess_rate
    if frequency is None:
        status, reason = EvidenceStatus.INSUFFICIENT, "POSITIVE_EXCESS_FREQUENCY_UNDEFINED"
    elif frequency > 0.5:
        status, reason = EvidenceStatus.SUPPORTIVE, "POSITIVE_EXCESS_FREQUENCY_ABOVE_HALF"
    elif frequency < 0.5:
        status, reason = EvidenceStatus.ADVERSE, "POSITIVE_EXCESS_FREQUENCY_BELOW_HALF"
    else:
        status, reason = EvidenceStatus.NEUTRAL, "POSITIVE_EXCESS_FREQUENCY_EQUALS_HALF"
    evidence.append(_evidence(budget, horizon, DIMENSIONS[3], status, reason))

    block_means = tuple(item.mean_excess_return_pct_points for item in blocks)
    block_labels = tuple(item.temporal_evidence for item in blocks)
    if any(value is None for value in block_means) or any(label == "UNDEFINED" for label in block_labels):
        status, reason = EvidenceStatus.INSUFFICIENT, "FROZEN_TEMPORAL_BLOCK_EVIDENCE_UNDEFINED"
    elif any(label == "REVERSAL" for label in block_labels) or all(float(value) < 0 for value in block_means):
        status, reason = EvidenceStatus.ADVERSE, "TEMPORAL_REVERSAL_OR_ALL_BLOCK_EXCESS_NEGATIVE"
    elif all(label == "SAME_DIRECTION" for label in block_labels) and all(float(value) > 0 for value in block_means):
        status, reason = EvidenceStatus.SUPPORTIVE, "ALL_BLOCKS_POSITIVE_AND_SAME_DIRECTION"
    else:
        status, reason = EvidenceStatus.NEUTRAL, "TEMPORAL_BLOCK_EVIDENCE_MIXED"
        flags.append(_flag(budget, horizon, RobustnessFlag.TEMPORALLY_MIXED, reason))
    evidence.append(_evidence(budget, horizon, DIMENSIONS[4], status, reason))

    recent = blocks[-1]
    recent_mean = recent.mean_excess_return_pct_points
    if recent_mean is None or recent.temporal_evidence == "UNDEFINED":
        status, reason = EvidenceStatus.INSUFFICIENT, "RECENT_BLOCK_EVIDENCE_UNDEFINED"
    elif recent_mean < 0 or recent.temporal_evidence == "REVERSAL":
        status, reason = EvidenceStatus.ADVERSE, "RECENT_BLOCK_NEGATIVE_OR_REVERSAL"
    elif recent_mean > 0 and recent.temporal_evidence == "SAME_DIRECTION":
        status, reason = EvidenceStatus.SUPPORTIVE, "RECENT_BLOCK_POSITIVE_AND_SAME_DIRECTION"
    else:
        status, reason = EvidenceStatus.NEUTRAL, "RECENT_BLOCK_DIRECTION_MIXED_OR_ZERO"
    prior_means = tuple(item.mean_excess_return_pct_points for item in blocks[:-1])
    if recent_mean is not None and prior_means and all(value is not None for value in prior_means):
        if recent_mean < min(float(value) for value in prior_means):
            flags.append(_flag(
                budget, horizon, RobustnessFlag.RECENT_WEAKENING,
                "RECENT_BLOCK_MEAN_EXCESS_BELOW_EVERY_PRIOR_BLOCK",
            ))
    evidence.append(_evidence(budget, horizon, DIMENSIONS[5], status, reason))

    maximum_cost = costs[-1]
    gross, net = maximum_cost.mean_gross_excess_return_pct_points, maximum_cost.mean_net_excess_return_pct_points
    if gross is None or net is None:
        status, reason = EvidenceStatus.INSUFFICIENT, "MAXIMUM_COST_EVIDENCE_UNDEFINED"
    elif gross <= 0 or net < 0:
        status, reason = EvidenceStatus.ADVERSE, "GROSS_NONPOSITIVE_OR_MAXIMUM_COST_NET_NEGATIVE"
    elif net == 0:
        status, reason = EvidenceStatus.NEUTRAL, "MAXIMUM_COST_NET_EXCESS_ZERO"
    else:
        status, reason = EvidenceStatus.SUPPORTIVE, "MAXIMUM_COST_NET_EXCESS_POSITIVE"
    if gross is not None and gross > 0 and any(
        item.cost_rate_bps > 0
        and item.mean_net_excess_return_pct_points is not None
        and item.mean_net_excess_return_pct_points <= 0
        for item in costs
    ):
        flags.append(_flag(
            budget, horizon, RobustnessFlag.COST_SENSITIVE,
            "POSITIVE_GROSS_EXCESS_BECOMES_NONPOSITIVE_UNDER_FROZEN_COST_GRID",
        ))
    evidence.append(_evidence(budget, horizon, DIMENSIONS[6], status, reason))

    if structure.evaluated_dates <= 0 or structure.mean_effective_n is None or structure.mean_max_single_name_weight is None:
        status, reason = EvidenceStatus.INSUFFICIENT, "STRUCTURAL_DIVERSIFICATION_UNDEFINED"
    elif structure.empty_date_count == structure.evaluated_dates or (
        structure.mean_effective_n <= 1.0 and structure.mean_max_single_name_weight >= 1.0
    ):
        status, reason = EvidenceStatus.ADVERSE, "STRUCTURE_EMPTY_OR_EFFECTIVELY_SINGLE_NAME"
    else:
        status, reason = EvidenceStatus.NEUTRAL, "STRUCTURE_DEFINED_WITHOUT_PERFORMANCE_PREFERENCE"
    if (
        structure.mean_effective_n is not None and structure.mean_effective_n < 2.0
    ) or (
        structure.mean_max_single_name_weight is not None and structure.mean_max_single_name_weight > 0.5
    ):
        flags.append(_flag(
            budget, horizon, RobustnessFlag.STRUCTURALLY_CONCENTRATED,
            "EFFECTIVE_N_BELOW_TWO_OR_SINGLE_NAME_WEIGHT_ABOVE_HALF",
        ))
    evidence.append(_evidence(budget, horizon, DIMENSIONS[7], status, reason))

    turnover = structure.mean_one_way_weight_turnover
    if structure.defined_weight_turnover_dates <= 0 or turnover is None:
        status, reason = EvidenceStatus.INSUFFICIENT, "TURNOVER_BURDEN_UNDEFINED"
    elif turnover >= 1.0:
        status, reason = EvidenceStatus.ADVERSE, "MEAN_ONE_WAY_TURNOVER_AT_LEAST_COMPLETE_REPLACEMENT"
    else:
        status, reason = EvidenceStatus.NEUTRAL, "TURNOVER_IS_IMPLEMENTATION_BURDEN_NOT_PERFORMANCE_EVIDENCE"
    if turnover is not None and turnover >= 0.5:
        flags.append(_flag(
            budget, horizon, RobustnessFlag.HIGH_IMPLEMENTATION_BURDEN,
            "MEAN_ONE_WAY_TURNOVER_AT_LEAST_HALF_PORTFOLIO",
        ))
    evidence.append(_evidence(budget, horizon, DIMENSIONS[8], status, reason))

    path_valid = outcome.path_metric_status == PATH_METRIC_STATUS and outcome.path_metric_reason == PATH_METRIC_REASON
    evidence.append(_evidence(
        budget, horizon, DIMENSIONS[9], EvidenceStatus.NOT_APPLICABLE,
        "OVERLAPPING_FORWARD_HORIZONS" if path_valid else "PATH_LIMITATION_PROVENANCE_MISMATCH",
    ))
    flags.append(_flag(
        budget, horizon, RobustnessFlag.PATH_EVIDENCE_UNAVAILABLE,
        "OVERLAPPING_FORWARD_HORIZONS_PRECLUDE_EXECUTABLE_PATH_METRICS",
    ))
    return tuple(evidence), tuple(sorted(flags, key=lambda item: item.flag.value))


def _decision(
    budget: int,
    horizon: int,
    evidence: tuple[PortfolioResearchEvidence, ...],
    flags: tuple[ScenarioResearchFlag, ...],
) -> ScenarioResearchDecision:
    relevant = tuple(item for item in evidence if item.dimension != "PATH_LIMITATION")
    if any(item.status is EvidenceStatus.ADVERSE for item in relevant):
        decision, reason = PortfolioResearchDecision.REJECT_FOR_NOW, "AT_LEAST_ONE_DECISION_RELEVANT_DIMENSION_ADVERSE"
    elif any(item.status is EvidenceStatus.INSUFFICIENT for item in relevant):
        decision, reason = PortfolioResearchDecision.INSUFFICIENT_EVIDENCE, "AT_LEAST_ONE_REQUIRED_DIMENSION_INSUFFICIENT"
    elif any(item.flag in {RobustnessFlag.RECENT_WEAKENING, RobustnessFlag.TEMPORALLY_MIXED} for item in flags):
        decision, reason = PortfolioResearchDecision.HOLD, "TEMPORAL_CONTRADICTION_REQUIRES_FORWARD_EVIDENCE"
    elif any(item.status is EvidenceStatus.SUPPORTIVE for item in relevant):
        decision, reason = PortfolioResearchDecision.ADVANCE_TO_FORWARD_VALIDATION, "COHERENT_DEFINED_EVIDENCE_WITH_NO_ADVERSE_DIMENSION"
    else:
        decision, reason = PortfolioResearchDecision.HOLD, "DEFINED_EVIDENCE_HAS_NO_SUPPORTIVE_DIMENSION"
    flag_values = tuple(item.flag.value for item in flags)
    evidence_ids = tuple(item.identity for item in evidence)
    payload = {"budget": budget, "horizon": horizon, "decision": decision.value, "reason": reason, "evidence": evidence_ids, "flags": flag_values}
    return ScenarioResearchDecision(budget, horizon, decision, reason, evidence_ids, flag_values, _hash(payload))


def evaluate_portfolio_research_synthesis(
    source: PortfolioResearchSynthesisInput,
    spec: PortfolioResearchSynthesisSpec = NEUTRAL_PORTFOLIO_RESEARCH_SYNTHESIS_V1,
) -> PortfolioResearchSynthesisResult:
    if not isinstance(source, PortfolioResearchSynthesisInput):
        raise TypeError("source must be PortfolioResearchSynthesisInput")
    if not isinstance(spec, PortfolioResearchSynthesisSpec):
        raise TypeError("spec must be PortfolioResearchSynthesisSpec")
    evidence: list[PortfolioResearchEvidence] = []
    flags: list[ScenarioResearchFlag] = []
    decisions: list[ScenarioResearchDecision] = []
    for budget in spec.budgets:
        for horizon in spec.horizons:
            scenario_evidence, scenario_flags = _scenario_evidence(source, budget, horizon)
            evidence.extend(scenario_evidence)
            flags.extend(scenario_flags)
            decisions.append(_decision(budget, horizon, scenario_evidence, scenario_flags))
    limitations = (
        "historical synthesis is descriptive and does not select or rank budgets or horizons",
        "overlapping forward outcomes do not define an executable return path",
        "hypothetical turnover costs are sensitivity evidence, not actual Vietnam implementation costs",
        "recent block evidence is preserved without forecasting or extrapolation",
        "database coverage is not necessarily historical VN100 membership",
        "advance means prospective research readiness only, not profitability or production authorization",
    )
    payload = {
        "contract": {"name": CONTRACT, "version": VERSION}, "specification": spec.fingerprint,
        "sources": source.source_identities, "decisions": tuple(item.identity for item in decisions),
        "evidence": tuple(item.identity for item in evidence), "flags": tuple(item.identity for item in flags),
        "limitations": limitations,
    }
    return PortfolioResearchSynthesisResult(
        CONTRACT, VERSION, spec.fingerprint, source.source_identities,
        tuple(decisions), tuple(evidence), tuple(flags), limitations, _hash(payload),
    )


def load_portfolio_research_synthesis_input(
    phase6_root: str | Path,
    phase7_root: str | Path,
) -> PortfolioResearchSynthesisInput:
    """Load and verify compact Phase 6/7 artifacts; never access market data."""
    phase6, phase7 = Path(phase6_root).resolve(), Path(phase7_root).resolve()
    manifest6_path = phase6 / "portfolio_construction_manifest.json"
    manifest7_path = phase7 / "portfolio_outcome_manifest.json"
    manifest6 = json.loads(manifest6_path.read_text(encoding="utf-8"))
    manifest7 = json.loads(manifest7_path.read_text(encoding="utf-8"))
    if (
        manifest6.get("completed") is not True or manifest6.get("budgets") != list(BUDGETS)
        or manifest6.get("candidate_source") != SELECTION_POLICY
        or manifest6.get("weighting_policies") != [WeightingPolicy.EQUAL_WEIGHT.value]
    ):
        raise ValueError("Phase 6 provenance is incomplete or unsupported")
    if (
        manifest7.get("completed") is not True or manifest7.get("budgets") != list(BUDGETS)
        or manifest7.get("horizons") != list(HORIZONS)
        or manifest7.get("selection_policy") != SELECTION_POLICY
        or manifest7.get("weighting_policy") != WeightingPolicy.EQUAL_WEIGHT.value
        or manifest7.get("path_metric_applicability") != PATH_METRIC_STATUS
        or manifest7.get("path_metric_reason") != PATH_METRIC_REASON
        or manifest7.get("cost_sensitivity_grid_bps") != list(COST_GRID_BPS)
    ):
        raise ValueError("Phase 7 provenance is incomplete or unsupported")
    manifest6_hash = _file_hash(manifest6_path)
    if manifest7.get("source_identities", {}).get("phase6_manifest_sha256") != manifest6_hash:
        raise ValueError("Phase 7 does not reference the supplied Phase 6 manifest")
    if manifest7.get("source_identities", {}).get("phase6_result_identity") != manifest6.get("result_identity"):
        raise ValueError("Phase 6 result identity does not match Phase 7 provenance")

    required7 = (
        "portfolio_outcome_summary.csv", "portfolio_outcome_by_block.csv",
        "portfolio_cost_sensitivity.csv",
    )
    for name in required7:
        expected = manifest7.get("artifacts", {}).get(name, {}).get("sha256")
        if not expected or _file_hash(phase7 / name) != expected:
            raise ValueError(f"Phase 7 artifact SHA-256 mismatch: {name}")

    structural_path = phase6 / "portfolio_construction_summary.csv"
    with structural_path.open("r", encoding="utf-8", newline="") as handle:
        structural_rows = tuple(csv.DictReader(handle))
    expected_structural_rows = manifest6.get("artifacts", {}).get(structural_path.name)
    if expected_structural_rows is not None and len(structural_rows) != expected_structural_rows:
        raise ValueError("Phase 6 structural summary row count does not match manifest")
    structures = tuple(
        FrozenStructuralEvidence(
            int(row["requested_budget"]), int(row["evaluated_dates"]),
            int(row["underfilled_date_count"]), int(row["empty_date_count"]),
            _optional_float(row["mean_fill_ratio"]), _optional_float(row["mean_effective_n"]),
            _optional_float(row["mean_max_single_name_weight"]), _optional_float(row["mean_herfindahl"]),
            int(row["defined_weight_turnover_dates"]), _optional_float(row["mean_one_way_weight_turnover"]),
            row["structural_state"], row["identity"],
        )
        for row in structural_rows if row["scope_name"] == "whole_period"
    )

    with (phase7 / required7[0]).open("r", encoding="utf-8", newline="") as handle:
        outcomes = tuple(
            FrozenOutcomeSummaryEvidence(
                int(row["requested_budget"]), int(row["horizon_sessions"]),
                int(row["total_dates"]), int(row["evaluable_dates"]),
                _optional_float(row["mean_excess_return_pct_points"]),
                _optional_float(row["median_excess_return_pct_points"]),
                _optional_float(row["positive_excess_rate"]), row["path_metric_status"],
                row["path_metric_reason"], row["identity"],
            ) for row in csv.DictReader(handle)
        )
    with (phase7 / required7[1]).open("r", encoding="utf-8", newline="") as handle:
        blocks = tuple(
            FrozenOutcomeBlockEvidence(
                int(row["requested_budget"]), int(row["horizon_sessions"]), row["block_name"],
                int(row["evaluable_dates"]), _optional_float(row["mean_excess_return_pct_points"]),
                row["temporal_evidence"], row["identity"],
            ) for row in csv.DictReader(handle)
        )
    with (phase7 / required7[2]).open("r", encoding="utf-8", newline="") as handle:
        costs = tuple(
            FrozenCostEvidence(
                int(row["requested_budget"]), int(row["horizon_sessions"]), int(row["cost_rate_bps"]),
                int(row["evaluable_date_count"]), int(row["turnover_defined_date_count"]),
                _optional_float(row["mean_gross_excess_return_pct_points"]),
                _optional_float(row["mean_estimated_turnover_cost_pct_points"]),
                _optional_float(row["mean_net_excess_return_pct_points"]), row["label"], row["identity"],
            ) for row in csv.DictReader(handle)
        )
    source_ids = {
        "phase6_manifest_sha256": manifest6_hash,
        "phase6_manifest_identity": _hash(manifest6),
        "phase6_result_identity": manifest6["result_identity"],
        "phase6_structural_summary_sha256": _file_hash(structural_path),
        "phase7_manifest_sha256": _file_hash(manifest7_path),
        "phase7_manifest_identity": _hash(manifest7),
        "phase7_result_identity": manifest7["result_identity"],
        **{f"phase7_{name}_sha256": manifest7["artifacts"][name]["sha256"] for name in required7},
    }
    return PortfolioResearchSynthesisInput(source_ids, True, structures, outcomes, blocks, costs)
