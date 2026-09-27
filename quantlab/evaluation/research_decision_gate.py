from __future__ import annotations

"""Deterministic research-advancement decisions over frozen Phase 5.x evidence."""

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


DECISION_GATE_CONTRACT = "quantlab.neutral_research_decision_gate"
DECISION_GATE_VERSION = "v1"
EXPECTED_RUNNER_CONTRACT = "quantlab.neutral_panel_factor_evaluation_runner"
EXPECTED_RUNNER_VERSION = "v6"

FACTOR_CANDIDATES = (
    "atr_percent_14",
    "rsi_14",
    "adx_14",
    "volume_ratio_20",
    "stock_return_20d_pct",
    "relative_strength_20d_pct_points",
    "ema20_distance_pct",
    "return_3d_pct",
)
POLICY_CANDIDATES = (
    "ADX_ONLY",
    "ADX_RSI_EQUAL_WEIGHT",
    "ADX_RSI_VOLUME_EQUAL_WEIGHT",
    "RSI_ONLY",
)
HORIZONS = (5, 10, 20)
OUTCOMES = ("stock_forward_return_pct", "excess_forward_return_pct_points")
SELECTION_BUDGETS = (5, 10, 20)

SOURCE_ARTIFACTS = (
    "factor_summary.csv",
    "factor_coverage.csv",
    "temporal_stability_summary.csv",
    "factor_redundancy_summary.csv",
    "factor_incremental_summary.csv",
    "composite_policy_summary.csv",
    "composite_contrast_summary.csv",
    "policy_selection_turnover_summary.csv",
    "policy_selection_overlap_summary.csv",
)

LIMITATIONS = (
    "research advancement is not production readiness or strategy selection",
    "the complete point-in-time Phase 5.x population is used without a candidate or strategy filter",
    "full-sample discovery and temporal review use the same historical dataset and are not independent confirmation",
    "stock and excess outcomes are not independent evidence",
    "database coverage is not necessarily historical VN100 membership",
    "turnover and overlap are descriptive operational evidence only",
    "no PnL, portfolio return, cost, liquidity, capacity, tradability, p-value, or multiple-testing claim is made",
)


class ResearchDecision(str, Enum):
    ADVANCE = "ADVANCE"
    HOLD = "HOLD"
    REJECT_FOR_NOW = "REJECT_FOR_NOW"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class EvidenceStatus(str, Enum):
    SUPPORTIVE = "SUPPORTIVE"
    ADVERSE = "ADVERSE"
    MIXED = "MIXED"
    NEUTRAL = "NEUTRAL"
    INSUFFICIENT = "INSUFFICIENT"


def _hash(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _text(value: Any, *, name: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{name} must be non-empty")
    return result


def _bool(value: Any, *, name: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"{name} must be true or false")


def _finite(value: Any, *, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _int(value: Any, *, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if str(value).strip() not in {str(result), f"{result}.0"}:
        raise ValueError(f"{name} must be an integer")
    return result


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(value[key]) for key in sorted(value)})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return canonical_identity_value(value)


def _immutable_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return _freeze(value)


@dataclass(frozen=True, slots=True)
class ResearchDecisionGateSpec:
    name: str = "NEUTRAL_PHASE_5_RESEARCH_DECISION_GATE_V1"
    version: str = "1"
    factors: tuple[str, ...] = FACTOR_CANDIDATES
    policies: tuple[str, ...] = POLICY_CANDIDATES
    horizons: tuple[int, ...] = HORIZONS
    outcomes: tuple[str, ...] = OUTCOMES
    selection_budgets: tuple[int, ...] = SELECTION_BUDGETS
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if tuple(self.factors) != FACTOR_CANDIDATES:
            raise ValueError("decision-gate factor scope is frozen")
        if tuple(self.policies) != POLICY_CANDIDATES:
            raise ValueError("decision-gate policy scope is frozen")
        if tuple(self.horizons) != HORIZONS or tuple(self.outcomes) != OUTCOMES:
            raise ValueError("decision-gate horizon/outcome scope is frozen")
        if tuple(self.selection_budgets) != SELECTION_BUDGETS:
            raise ValueError("decision-gate selection budgets are frozen")
        if self.version != "1":
            raise ValueError("unsupported decision-gate specification version")
        payload = {
            "contract": {"name": DECISION_GATE_CONTRACT, "version": DECISION_GATE_VERSION},
            "name": _text(self.name, name="specification name"),
            "version": self.version,
            "factors": self.factors,
            "policies": self.policies,
            "horizons": self.horizons,
            "outcomes": self.outcomes,
            "selection_budgets": self.selection_budgets,
            "evidence_rules": {
                "coverage": "reuse_frozen_descriptive_review_eligible_boolean",
                "direction": "strict_sign_only_no_numeric_magnitude_threshold",
                "temporal": "reuse_frozen_temporal_support_and_all_block_sign_flags",
                "incremental": "all_frozen_means_same_sign_and_support_requires_all_blocks_positive",
                "contrast": "paired_frozen_candidate_signs_only",
                "turnover_overlap": "descriptive_non_decision_relevant",
            },
            "decision_precedence": (
                "adverse_relevant=>REJECT_FOR_NOW",
                "insufficient_relevant=>INSUFFICIENT_EVIDENCE",
                "mixed_relevant=>HOLD",
                "all_relevant_supportive_or_neutral_with_support=>ADVANCE",
            ),
            "restrictions": LIMITATIONS,
        }
        object.__setattr__(self, "fingerprint", _hash(payload))


NEUTRAL_RESEARCH_DECISION_GATE_V1 = ResearchDecisionGateSpec()


@dataclass(frozen=True, slots=True)
class ResearchDecisionEvidenceInput:
    manifest_identity: str
    runner_contract: str
    runner_version: str
    start_date: str
    end_date: str
    source_result_identities: Mapping[str, str]
    tables: Mapping[str, tuple[Mapping[str, str], ...]]

    def __post_init__(self) -> None:
        _text(self.manifest_identity, name="manifest identity")
        if self.runner_contract != EXPECTED_RUNNER_CONTRACT or self.runner_version != EXPECTED_RUNNER_VERSION:
            raise ValueError("Phase 5.11 requires the canonical Phase 5.9B v6 runner contract")
        identities = {str(key): _text(value, name=f"source identity {key}") for key, value in self.source_result_identities.items()}
        tables: dict[str, tuple[Mapping[str, str], ...]] = {}
        if set(self.tables) != set(SOURCE_ARTIFACTS):
            raise ValueError("decision evidence tables do not match the frozen artifact set")
        for name in SOURCE_ARTIFACTS:
            rows = []
            for row in self.tables[name]:
                if not isinstance(row, Mapping):
                    raise TypeError("decision evidence table rows must be mappings")
                rows.append(MappingProxyType({str(key): str(value) for key, value in sorted(row.items())}))
            tables[name] = tuple(rows)
        object.__setattr__(self, "source_result_identities", MappingProxyType(dict(sorted(identities.items()))))
        object.__setattr__(self, "tables", MappingProxyType(tables))


@dataclass(frozen=True, slots=True)
class DecisionEvidence:
    candidate_type: str
    candidate: str
    dimension: str
    status: EvidenceStatus
    defined: bool
    decision_relevant: bool
    source_artifact: str
    source_identity: str
    observation_summary: Mapping[str, Any]
    reason_code: str
    rationale: str
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        summary = _immutable_mapping(self.observation_summary)
        payload = {
            "contract": {"name": DECISION_GATE_CONTRACT, "version": DECISION_GATE_VERSION},
            "candidate_type": self.candidate_type,
            "candidate": self.candidate,
            "dimension": self.dimension,
            "status": self.status.value,
            "defined": self.defined,
            "decision_relevant": self.decision_relevant,
            "source_artifact": self.source_artifact,
            "source_identity": self.source_identity,
            "observation_summary": summary,
            "reason_code": self.reason_code,
            "rationale": self.rationale,
        }
        object.__setattr__(self, "observation_summary", summary)
        object.__setattr__(self, "identity", _hash(payload))


@dataclass(frozen=True, slots=True)
class CandidateResearchDecision:
    candidate_type: str
    candidate: str
    candidate_identity: str
    decision: ResearchDecision
    supportive_dimensions: tuple[str, ...]
    adverse_dimensions: tuple[str, ...]
    mixed_dimensions: tuple[str, ...]
    insufficient_dimensions: tuple[str, ...]
    neutral_dimensions: tuple[str, ...]
    reason_codes: tuple[str, ...]
    rationale: str
    evidence_identities: tuple[str, ...]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        payload = {
            "contract": {"name": DECISION_GATE_CONTRACT, "version": DECISION_GATE_VERSION},
            "candidate_type": self.candidate_type,
            "candidate": self.candidate,
            "candidate_identity": self.candidate_identity,
            "decision": self.decision.value,
            "supportive_dimensions": self.supportive_dimensions,
            "adverse_dimensions": self.adverse_dimensions,
            "mixed_dimensions": self.mixed_dimensions,
            "insufficient_dimensions": self.insufficient_dimensions,
            "neutral_dimensions": self.neutral_dimensions,
            "reason_codes": self.reason_codes,
            "rationale": self.rationale,
            "evidence_identities": self.evidence_identities,
        }
        object.__setattr__(self, "identity", _hash(payload))


@dataclass(frozen=True, slots=True)
class ResearchDecisionGateResult:
    contract_name: str
    contract_version: str
    specification_fingerprint: str
    source_manifest_identity: str
    source_runner_contract: str
    source_runner_version: str
    start_date: str
    end_date: str
    source_result_identities: Mapping[str, str]
    evidence: tuple[DecisionEvidence, ...]
    decisions: tuple[CandidateResearchDecision, ...]
    limitations: tuple[str, ...]
    identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_result_identities", MappingProxyType(dict(self.source_result_identities)))
        if self.contract_name != DECISION_GATE_CONTRACT or self.contract_version != DECISION_GATE_VERSION:
            raise ValueError("unsupported decision-gate result contract")
        if len({item.identity for item in self.evidence}) != len(self.evidence):
            raise ValueError("duplicate decision evidence identity")
        if len({(item.candidate_type, item.candidate) for item in self.decisions}) != len(self.decisions):
            raise ValueError("duplicate candidate decision")


def load_phase59b_decision_evidence(root: str | Path) -> ResearchDecisionEvidenceInput:
    """Translate an existing canonical v6 artifact root without recomputation."""
    path = Path(root).expanduser().resolve()
    manifest_path = path / "experiment_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing canonical manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("completed") is not True:
        raise ValueError("canonical Phase 5.9B manifest is not complete")
    if manifest.get("runner_contract") != EXPECTED_RUNNER_CONTRACT or manifest.get("runner_version") != EXPECTED_RUNNER_VERSION:
        raise ValueError("canonical manifest is not the Phase 5.9B v6 contract")
    bounds = manifest.get("requested_bounds") or {}
    artifacts = manifest.get("artifacts") or {}
    tables: dict[str, tuple[Mapping[str, str], ...]] = {}
    for name in SOURCE_ARTIFACTS:
        artifact_path = path / name
        if not artifact_path.is_file():
            raise FileNotFoundError(f"missing canonical evidence artifact: {artifact_path}")
        with artifact_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = tuple(dict(row) for row in csv.DictReader(handle))
        if artifacts.get(name) != len(rows):
            raise ValueError(f"canonical artifact count mismatch: {name}")
        tables[name] = rows
    identities = {
        "factor_evaluation": manifest.get("evaluation", {}).get("result_identity"),
        "temporal_stability": manifest.get("temporal_stability", {}).get("temporal_result_identity"),
        "factor_redundancy": manifest.get("factor_redundancy", {}).get("result_identity"),
        "factor_incremental": manifest.get("factor_incremental_analysis", {}).get("result_identity"),
        "composite_comparison": manifest.get("composite_comparison", {}).get("result_identity"),
        "policy_selection": manifest.get("policy_selection_diagnostics", {}).get("result_identity"),
        "research_dataset": manifest.get("research_dataset", {}).get("identity"),
        "research_dataset_content": manifest.get("research_dataset", {}).get("content_identity"),
    }
    return ResearchDecisionEvidenceInput(
        manifest_identity=_hash(manifest),
        runner_contract=manifest["runner_contract"],
        runner_version=manifest["runner_version"],
        start_date=_text(bounds.get("start_date"), name="manifest start date"),
        end_date=_text(bounds.get("end_date"), name="manifest end date"),
        source_result_identities=identities,
        tables=tables,
    )


def _source_identity(rows: tuple[Mapping[str, str], ...]) -> str:
    identities = tuple(row.get("identity", "") for row in rows)
    return _hash(identities if any(identities) else rows)


def _expected_grid(rows: tuple[Mapping[str, str], ...], candidate_field: str, candidate: str) -> tuple[Mapping[str, str], ...]:
    selected = tuple(
        sorted(
            (row for row in rows if row.get(candidate_field) == candidate),
            key=lambda row: (_int(row["horizon_sessions"], name="horizon"), row["outcome_field"]),
        )
    )
    actual = {(_int(row["horizon_sessions"], name="horizon"), row["outcome_field"]) for row in selected}
    expected = {(horizon, outcome) for horizon in HORIZONS for outcome in OUTCOMES}
    if actual != expected or len(selected) != len(expected):
        raise ValueError(f"incomplete or duplicate evidence grid for {candidate}")
    return selected


def _evidence(
    candidate_type: str, candidate: str, dimension: str, status: EvidenceStatus,
    *, defined: bool, relevant: bool, artifact: str, rows: tuple[Mapping[str, str], ...],
    summary: Mapping[str, Any], reason: str, rationale: str,
) -> DecisionEvidence:
    return DecisionEvidence(
        candidate_type, candidate, dimension, status, defined, relevant,
        artifact, _source_identity(rows), summary, reason, rationale,
    )


def _factor_evidence(source: ResearchDecisionEvidenceInput, factor: str) -> tuple[DecisionEvidence, ...]:
    coverage_rows = _expected_grid(source.tables["factor_coverage.csv"], "factor", factor)
    summary_rows = _expected_grid(source.tables["factor_summary.csv"], "factor", factor)
    temporal_rows = _expected_grid(source.tables["temporal_stability_summary.csv"], "factor", factor)
    coverage_ok = all(_bool(row["descriptive_review_eligible"], name="descriptive review eligibility") for row in coverage_rows)
    coverage = _evidence(
        "factor", factor, "coverage", EvidenceStatus.SUPPORTIVE if coverage_ok else EvidenceStatus.INSUFFICIENT,
        defined=True, relevant=True, artifact="factor_coverage.csv", rows=coverage_rows,
        summary={"eligible_combinations": sum(_bool(row["descriptive_review_eligible"], name="eligibility") for row in coverage_rows), "total_combinations": 6},
        reason="FROZEN_COVERAGE_ELIGIBLE" if coverage_ok else "FROZEN_COVERAGE_INSUFFICIENT",
        rationale="All frozen factor/horizon/outcome combinations meet the prior descriptive-review contract." if coverage_ok else "At least one frozen combination fails the prior descriptive-review contract.",
    )
    ic = tuple(_finite(row["mean_daily_rank_ic"], name="mean daily rank IC") for row in summary_rows)
    spread = tuple(_finite(row["mean_daily_high_minus_low_mean_spread"], name="mean spread") for row in summary_rows)
    if all(value > 0 for value in (*ic, *spread)):
        signal_status, signal_reason = EvidenceStatus.SUPPORTIVE, "ALL_FROZEN_SIGNAL_DIRECTIONS_POSITIVE"
    elif all(value < 0 for value in (*ic, *spread)):
        signal_status, signal_reason = EvidenceStatus.ADVERSE, "ALL_FROZEN_SIGNAL_DIRECTIONS_NEGATIVE"
    else:
        signal_status, signal_reason = EvidenceStatus.MIXED, "FROZEN_SIGNAL_DIRECTIONS_MIXED"
    signal = _evidence(
        "factor", factor, "cross_sectional_signal", signal_status, defined=True, relevant=True,
        artifact="factor_summary.csv", rows=summary_rows,
        summary={"positive_mean_ic": sum(value > 0 for value in ic), "negative_mean_ic": sum(value < 0 for value in ic), "positive_mean_spread": sum(value > 0 for value in spread), "negative_mean_spread": sum(value < 0 for value in spread)},
        reason=signal_reason, rationale="Direction is based only on the sign of frozen full-sample IC and spread summaries.",
    )
    temporal_support = tuple(_bool(row["descriptive_temporal_support"], name="temporal support") for row in temporal_rows)
    temporal_coverage = tuple(_bool(row["coverage_sufficient_for_temporal_review"], name="temporal coverage") for row in temporal_rows)
    all_negative = tuple(_bool(row["all_blocks_negative_ic"], name="all-block IC") and _bool(row["all_blocks_negative_spread"], name="all-block spread") for row in temporal_rows)
    if not all(temporal_coverage):
        temporal_status, temporal_reason, temporal_defined = EvidenceStatus.INSUFFICIENT, "TEMPORAL_COVERAGE_INSUFFICIENT", False
    elif all(temporal_support):
        temporal_status, temporal_reason, temporal_defined = EvidenceStatus.SUPPORTIVE, "ALL_FROZEN_TEMPORAL_COMBINATIONS_SUPPORTIVE", True
    elif all(all_negative):
        temporal_status, temporal_reason, temporal_defined = EvidenceStatus.ADVERSE, "ALL_FROZEN_TEMPORAL_COMBINATIONS_NEGATIVE", True
    else:
        temporal_status, temporal_reason, temporal_defined = EvidenceStatus.MIXED, "FROZEN_TEMPORAL_SUPPORT_MIXED", True
    temporal = _evidence(
        "factor", factor, "temporal_stability", temporal_status, defined=temporal_defined, relevant=True,
        artifact="temporal_stability_summary.csv", rows=temporal_rows,
        summary={"supportive_combinations": sum(temporal_support), "coverage_sufficient_combinations": sum(temporal_coverage), "total_combinations": 6},
        reason=temporal_reason, rationale="Uses only the prior frozen four-block coverage and directional-support flags.",
    )
    redundancy_rows = tuple(
        sorted(
            (row for row in source.tables["factor_redundancy_summary.csv"] if factor in (row.get("first_factor"), row.get("second_factor"))),
            key=lambda row: (row["first_factor"], row["second_factor"]),
        )
    )
    redundancy = _evidence(
        "factor", factor, "redundancy", EvidenceStatus.NEUTRAL if redundancy_rows else EvidenceStatus.INSUFFICIENT,
        defined=bool(redundancy_rows), relevant=False, artifact="factor_redundancy_summary.csv", rows=redundancy_rows,
        summary={"pair_count": len(redundancy_rows)},
        reason="REDUNDANCY_DESCRIPTIVE_NO_FROZEN_CUTOFF" if redundancy_rows else "REDUNDANCY_EVIDENCE_MISSING",
        rationale="The prior contract intentionally defines no redundancy cutoff, so pair correlations remain descriptive.",
    )
    incremental_rows = tuple(
        sorted(
            (row for row in source.tables["factor_incremental_summary.csv"] if row.get("target_factor") == factor),
            key=lambda row: (row["hypothesis_name"], _int(row["horizon_sessions"], name="horizon"), row["outcome_field"]),
        )
    )
    if not incremental_rows:
        incremental_status, incremental_reason, incremental_defined = EvidenceStatus.INSUFFICIENT, "NO_FROZEN_INCREMENTAL_HYPOTHESIS", False
        incremental_summary: Mapping[str, Any] = {"summary_count": 0}
    else:
        means = tuple(_finite(row["mean_daily_partial_rank_ic"], name="partial rank IC") for row in incremental_rows)
        all_blocks_positive = tuple(_bool(row["all_blocks_positive_partial_rank_ic"], name="incremental all-block positive") for row in incremental_rows)
        if all(value > 0 for value in means) and all(all_blocks_positive):
            incremental_status, incremental_reason = EvidenceStatus.SUPPORTIVE, "INCREMENTAL_POSITIVE_ACROSS_ALL_FROZEN_SUMMARIES_AND_BLOCKS"
        elif all(value < 0 for value in means):
            incremental_status, incremental_reason = EvidenceStatus.ADVERSE, "INCREMENTAL_MEAN_NEGATIVE_ACROSS_ALL_FROZEN_SUMMARIES"
        else:
            incremental_status, incremental_reason = EvidenceStatus.MIXED, "FROZEN_INCREMENTAL_EVIDENCE_MIXED"
        incremental_defined = True
        incremental_summary = {"summary_count": len(means), "positive_means": sum(value > 0 for value in means), "negative_means": sum(value < 0 for value in means), "all_blocks_positive_count": sum(all_blocks_positive)}
    incremental = _evidence(
        "factor", factor, "incremental_contribution", incremental_status, defined=incremental_defined, relevant=True,
        artifact="factor_incremental_summary.csv", rows=incremental_rows, summary=incremental_summary,
        reason=incremental_reason, rationale="Uses frozen conditional hypotheses and signs; no magnitude cutoff or score is introduced.",
    )
    return coverage, signal, temporal, redundancy, incremental


def _policy_evidence(source: ResearchDecisionEvidenceInput, policy: str) -> tuple[DecisionEvidence, ...]:
    policy_rows = _expected_grid(source.tables["composite_policy_summary.csv"], "policy_name", policy)
    ic = tuple(_finite(row["mean_daily_rank_ic"], name="policy mean IC") for row in policy_rows)
    spread = tuple(_finite(row["mean_daily_mean_spread"], name="policy mean spread") for row in policy_rows)
    if all(value > 0 for value in (*ic, *spread)):
        signal_status, signal_reason = EvidenceStatus.SUPPORTIVE, "ALL_FROZEN_POLICY_SIGNAL_DIRECTIONS_POSITIVE"
    elif all(value < 0 for value in (*ic, *spread)):
        signal_status, signal_reason = EvidenceStatus.ADVERSE, "ALL_FROZEN_POLICY_SIGNAL_DIRECTIONS_NEGATIVE"
    else:
        signal_status, signal_reason = EvidenceStatus.MIXED, "FROZEN_POLICY_SIGNAL_DIRECTIONS_MIXED"
    signal = _evidence(
        "policy", policy, "composite_signal", signal_status, defined=True, relevant=True,
        artifact="composite_policy_summary.csv", rows=policy_rows,
        summary={"positive_mean_ic": sum(value > 0 for value in ic), "negative_mean_ic": sum(value < 0 for value in ic), "positive_mean_spread": sum(value > 0 for value in spread), "negative_mean_spread": sum(value < 0 for value in spread)},
        reason=signal_reason, rationale="Uses signs from the frozen common-population composite summaries.",
    )
    all_positive = tuple(_bool(row["all_blocks_positive_ic"], name="policy temporal IC") and _bool(row["all_blocks_positive_spread"], name="policy temporal spread") for row in policy_rows)
    all_negative = tuple(_bool(row["all_blocks_negative_ic"], name="policy temporal IC") and _bool(row["all_blocks_negative_spread"], name="policy temporal spread") for row in policy_rows)
    if all(all_positive):
        temporal_status, temporal_reason = EvidenceStatus.SUPPORTIVE, "ALL_POLICY_COMBINATIONS_POSITIVE_IN_ALL_BLOCKS"
    elif all(all_negative):
        temporal_status, temporal_reason = EvidenceStatus.ADVERSE, "ALL_POLICY_COMBINATIONS_NEGATIVE_IN_ALL_BLOCKS"
    else:
        temporal_status, temporal_reason = EvidenceStatus.MIXED, "POLICY_BLOCK_DIRECTIONS_MIXED"
    temporal = _evidence(
        "policy", policy, "temporal_stability", temporal_status, defined=True, relevant=True,
        artifact="composite_policy_summary.csv", rows=policy_rows,
        summary={"all_blocks_positive_combinations": sum(all_positive), "all_blocks_negative_combinations": sum(all_negative), "total_combinations": 6},
        reason=temporal_reason, rationale="Uses frozen all-block sign flags without a new count threshold.",
    )
    contrast_rows = tuple(
        sorted(
            (row for row in source.tables["composite_contrast_summary.csv"] if policy in (row.get("variant_policy"), row.get("reference_policy"))),
            key=lambda row: (row["contrast_name"], _int(row["horizon_sessions"], name="horizon"), row["outcome_field"]),
        )
    )
    signed: list[tuple[float, float]] = []
    for row in contrast_rows:
        multiplier = 1.0 if row["variant_policy"] == policy else -1.0
        signed.append((multiplier * _finite(row["mean_daily_ic_delta"], name="contrast IC"), multiplier * _finite(row["mean_daily_spread_delta"], name="contrast spread")))
    if not signed:
        contrast_status, contrast_reason, contrast_defined = EvidenceStatus.INSUFFICIENT, "NO_FROZEN_POLICY_CONTRAST", False
    elif all(ic_delta > 0 and spread_delta > 0 for ic_delta, spread_delta in signed):
        contrast_status, contrast_reason, contrast_defined = EvidenceStatus.SUPPORTIVE, "POLICY_DOMINATES_ALL_FROZEN_PAIRED_CONTRASTS", True
    elif all(ic_delta < 0 and spread_delta < 0 for ic_delta, spread_delta in signed):
        contrast_status, contrast_reason, contrast_defined = EvidenceStatus.ADVERSE, "POLICY_TRAILS_ALL_FROZEN_PAIRED_CONTRASTS", True
    else:
        contrast_status, contrast_reason, contrast_defined = EvidenceStatus.MIXED, "FROZEN_POLICY_CONTRASTS_MIXED", True
    contrast = _evidence(
        "policy", policy, "paired_contrasts", contrast_status, defined=contrast_defined, relevant=True,
        artifact="composite_contrast_summary.csv", rows=contrast_rows,
        summary={"paired_rows": len(signed), "jointly_positive": sum(a > 0 and b > 0 for a, b in signed), "jointly_negative": sum(a < 0 and b < 0 for a, b in signed)},
        reason=contrast_reason, rationale="Each fixed paired contrast is interpreted from this candidate's perspective; no metric magnitude is scored.",
    )
    turnover_rows = tuple(sorted((row for row in source.tables["policy_selection_turnover_summary.csv"] if row.get("policy_name") == policy), key=lambda row: (_int(row["selection_budget"], name="selection budget"), row["scope_name"])))
    selection_defined = bool(turnover_rows)
    turnover = _evidence(
        "policy", policy, "selection_turnover", EvidenceStatus.NEUTRAL if selection_defined else EvidenceStatus.INSUFFICIENT,
        defined=selection_defined, relevant=False, artifact="policy_selection_turnover_summary.csv", rows=turnover_rows,
        summary={"summary_rows": len(turnover_rows), "budgets": tuple(sorted({_int(row["selection_budget"], name="budget") for row in turnover_rows}))},
        reason="TURNOVER_DESCRIPTIVE_ONLY" if selection_defined else "TURNOVER_NOT_EVALUATED_FOR_POLICY",
        rationale="Membership turnover has no frozen universally favorable direction and cannot advance or reject a policy.",
    )
    overlap_rows = tuple(sorted(
        source.tables["policy_selection_overlap_summary.csv"],
        key=lambda row: (_int(row["selection_budget"], name="budget"), row["scope_name"]),
    )) if selection_defined else ()
    overlap = _evidence(
        "policy", policy, "selection_overlap", EvidenceStatus.NEUTRAL if overlap_rows else EvidenceStatus.INSUFFICIENT,
        defined=bool(overlap_rows), relevant=False, artifact="policy_selection_overlap_summary.csv", rows=overlap_rows,
        summary={"summary_rows": len(overlap_rows), "budgets": tuple(sorted({_int(row["selection_budget"], name="budget") for row in overlap_rows}))},
        reason="OVERLAP_DESCRIPTIVE_ONLY" if overlap_rows else "OVERLAP_NOT_EVALUATED_FOR_POLICY",
        rationale="Selection overlap is comparative operational evidence, not an alpha or rejection criterion.",
    )
    budgets = tuple(sorted({_int(row["selection_budget"], name="budget") for row in turnover_rows}))
    budget = _evidence(
        "policy", policy, "budget_sensitivity", EvidenceStatus.NEUTRAL if budgets == SELECTION_BUDGETS else EvidenceStatus.INSUFFICIENT,
        defined=budgets == SELECTION_BUDGETS, relevant=False, artifact="policy_selection_turnover_summary.csv", rows=turnover_rows,
        summary={"observed_budgets": budgets, "expected_budgets": SELECTION_BUDGETS},
        reason="FROZEN_BUDGETS_DESCRIPTIVE_ONLY" if budgets == SELECTION_BUDGETS else "FROZEN_BUDGET_EVIDENCE_UNAVAILABLE",
        rationale="Budgets 5, 10, and 20 are ordered deterministically but are not optimized or assigned a preferred value.",
    )
    return signal, temporal, contrast, turnover, overlap, budget


def _decision(candidate_type: str, candidate: str, candidate_identity: str, evidence: tuple[DecisionEvidence, ...]) -> CandidateResearchDecision:
    relevant = tuple(item for item in evidence if item.decision_relevant)
    statuses = {item.status for item in relevant}
    if EvidenceStatus.ADVERSE in statuses:
        decision = ResearchDecision.REJECT_FOR_NOW
        rationale = "At least one frozen, decision-relevant structural evidence dimension is adverse."
    elif EvidenceStatus.INSUFFICIENT in statuses:
        decision = ResearchDecision.INSUFFICIENT_EVIDENCE
        rationale = "At least one required structural evidence dimension is unavailable or undefined."
    elif EvidenceStatus.MIXED in statuses:
        decision = ResearchDecision.HOLD
        rationale = "Defined structural evidence is mixed; advancement is unresolved without inventing a cutoff."
    elif EvidenceStatus.SUPPORTIVE in statuses:
        decision = ResearchDecision.ADVANCE
        rationale = "All decision-relevant frozen dimensions are supportive or neutral; later portfolio research is justified."
    else:
        decision = ResearchDecision.INSUFFICIENT_EVIDENCE
        rationale = "No decision-relevant supportive evidence is defined."
    by_status = {status: tuple(item.dimension for item in evidence if item.status is status) for status in EvidenceStatus}
    return CandidateResearchDecision(
        candidate_type=candidate_type,
        candidate=candidate,
        candidate_identity=candidate_identity,
        decision=decision,
        supportive_dimensions=by_status[EvidenceStatus.SUPPORTIVE],
        adverse_dimensions=by_status[EvidenceStatus.ADVERSE],
        mixed_dimensions=by_status[EvidenceStatus.MIXED],
        insufficient_dimensions=by_status[EvidenceStatus.INSUFFICIENT],
        neutral_dimensions=by_status[EvidenceStatus.NEUTRAL],
        reason_codes=tuple(item.reason_code for item in evidence),
        rationale=rationale,
        evidence_identities=tuple(item.identity for item in evidence),
    )


def evaluate_research_decision_gate(
    source: ResearchDecisionEvidenceInput,
    spec: ResearchDecisionGateSpec = NEUTRAL_RESEARCH_DECISION_GATE_V1,
) -> ResearchDecisionGateResult:
    """Evaluate advancement evidence without scores, ranking, PnL, or I/O."""
    if not isinstance(source, ResearchDecisionEvidenceInput):
        raise TypeError("source must be ResearchDecisionEvidenceInput")
    evidence: list[DecisionEvidence] = []
    decisions: list[CandidateResearchDecision] = []
    for factor in spec.factors:
        items = _factor_evidence(source, factor)
        evidence.extend(items)
        decisions.append(_decision("factor", factor, _hash({"factor": factor}), items))
    policy_rows = source.tables["composite_policy_summary.csv"]
    for policy in spec.policies:
        items = _policy_evidence(source, policy)
        evidence.extend(items)
        rows = tuple(row for row in policy_rows if row.get("policy_name") == policy)
        fingerprints = {row.get("policy_fingerprint") for row in rows}
        if len(fingerprints) != 1 or not next(iter(fingerprints), ""):
            raise ValueError(f"policy fingerprint does not reconcile: {policy}")
        decisions.append(_decision("policy", policy, next(iter(fingerprints)), items))
    expected_order = tuple(("factor", item) for item in spec.factors) + tuple(("policy", item) for item in spec.policies)
    if tuple((item.candidate_type, item.candidate) for item in decisions) != expected_order:
        raise ValueError("decision ordering does not reconcile")
    payload = {
        "contract": {"name": DECISION_GATE_CONTRACT, "version": DECISION_GATE_VERSION},
        "specification_fingerprint": spec.fingerprint,
        "source_manifest_identity": source.manifest_identity,
        "source_runner_contract": source.runner_contract,
        "source_runner_version": source.runner_version,
        "bounds": {"start_date": source.start_date, "end_date": source.end_date},
        "source_result_identities": source.source_result_identities,
        "evidence_identities": tuple(item.identity for item in evidence),
        "decision_identities": tuple(item.identity for item in decisions),
        "limitations": LIMITATIONS,
    }
    return ResearchDecisionGateResult(
        contract_name=DECISION_GATE_CONTRACT,
        contract_version=DECISION_GATE_VERSION,
        specification_fingerprint=spec.fingerprint,
        source_manifest_identity=source.manifest_identity,
        source_runner_contract=source.runner_contract,
        source_runner_version=source.runner_version,
        start_date=source.start_date,
        end_date=source.end_date,
        source_result_identities=source.source_result_identities,
        evidence=tuple(evidence),
        decisions=tuple(decisions),
        limitations=LIMITATIONS,
        identity=_hash(payload),
    )
