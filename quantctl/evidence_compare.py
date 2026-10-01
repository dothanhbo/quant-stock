from __future__ import annotations

import csv
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Mapping

from quantctl.factor_explore import (
    EvidenceState,
    ExplorePopulation,
    FactorExploreCatalog,
    SelectedFactorEvidence,
    inspect_factor_explore_catalog,
    select_factor_evidence,
)
from quantctl.registry import PROJECT_ROOT


class ComparisonFamily(str, Enum):
    NEUTRAL_FACTORS = "neutral_factors"
    FROZEN_POLICIES = "frozen_policies"


class ComparisonState(str, Enum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    INCOMPATIBLE = "INCOMPATIBLE"


@dataclass(frozen=True, slots=True)
class ComparableEvidence:
    name: str
    identity: str | None
    disposition: str | None
    mean_rank_ic: float | None
    positive_spread_rate: float | None
    defined_date_coverage_pct: float | None
    high_minus_low_spread: float | None
    temporal_support: str


@dataclass(frozen=True, slots=True)
class BlockPairEvidence:
    name: str
    a_mean_rank_ic: float | None
    b_mean_rank_ic: float | None
    a_spread: float | None
    b_spread: float | None
    a_coverage_pct: float | None
    b_coverage_pct: float | None


@dataclass(frozen=True, slots=True)
class SelectionOverlapEvidence:
    budget: int
    scope: str
    mean_overlap_coefficient: float | None
    mean_jaccard_similarity: float | None
    exact_set_equality_rate: float | None
    identity: str


@dataclass(frozen=True, slots=True)
class TurnoverEvidence:
    policy: str
    budget: int
    scope: str
    mean_one_way_turnover: float | None
    total_entry_count: int
    identity: str


@dataclass(frozen=True, slots=True)
class ComparisonDimension:
    name: str
    candidate_a: str
    candidate_b: str
    support: str


@dataclass(frozen=True, slots=True)
class EvidenceComparison:
    state: ComparisonState
    family: ComparisonFamily
    candidate_a: str
    candidate_b: str
    horizon_sessions: int
    outcome_field: str
    temporal_scope: str
    evidence_a: ComparableEvidence | None
    evidence_b: ComparableEvidence | None
    blocks: tuple[BlockPairEvidence, ...]
    mean_ic_delta_a_minus_b: float | None
    mean_spread_delta_a_minus_b: float | None
    incremental_notes: tuple[str, ...]
    redundancy_correlation: float | None
    overlap: tuple[SelectionOverlapEvidence, ...]
    turnover: tuple[TurnoverEvidence, ...]
    dimensions: tuple[ComparisonDimension, ...]
    descriptive_differences: tuple[str, ...]
    artifact_paths: tuple[Path, ...]
    artifact_identities: tuple[str, ...]
    limitations: tuple[str, ...]
    detail: str

    @property
    def evidence_population(self) -> str:
        if self.family is ComparisonFamily.NEUTRAL_FACTORS:
            return "complete point-in-time neutral factor population"
        return "fixed neutral composite-policy population"


@dataclass(frozen=True, slots=True)
class _PolicyRecord:
    policy: str
    horizon_sessions: int
    outcome_field: str
    scope: str
    mean_rank_ic: float | None
    positive_spread_rate: float | None
    coverage_pct: float | None
    spread: float | None
    temporal_support: str
    identity: str


@dataclass(frozen=True, slots=True)
class _ContrastRecord:
    variant: str
    reference: str
    horizon_sessions: int
    outcome_field: str
    scope: str
    mean_ic_delta: float | None
    mean_spread_delta: float | None
    identity: str


@dataclass(frozen=True, slots=True)
class FrozenPolicyCatalog:
    state: ComparisonState
    policies: tuple[_PolicyRecord, ...]
    contrasts: tuple[_ContrastRecord, ...]
    overlap: tuple[SelectionOverlapEvidence, ...]
    turnover: tuple[TurnoverEvidence, ...]
    dispositions: tuple[tuple[str, str], ...]
    artifact_paths: tuple[Path, ...]
    artifact_identities: tuple[str, ...]
    limitations: tuple[str, ...]
    detail: str

    @property
    def supported_pairs(self) -> tuple[tuple[str, str], ...]:
        return _SUPPORTED_POLICY_PAIRS

    @property
    def horizons(self) -> tuple[int, ...]:
        return tuple(sorted({item.horizon_sessions for item in self.policies}))

    @property
    def outcomes(self) -> tuple[str, ...]:
        return tuple(sorted({item.outcome_field for item in self.policies}))

    @property
    def temporal_scopes(self) -> tuple[str, ...]:
        scopes = tuple(dict.fromkeys(item.scope for item in self.policies))
        return tuple(sorted(scopes, key=lambda item: (item != "whole_period", item)))


@dataclass(frozen=True, slots=True)
class EvidenceComparisonCatalog:
    factors: FactorExploreCatalog
    policies: FrozenPolicyCatalog


_ROOT_NAME = "quantlab_neutral_panel_factor_evaluation_2018-08-07_2026-09-17"
_GATE_ROOT = "quantlab_research_decision_gate_2018-08-07_2026-09-17"
_SUPPORTED_POLICY_PAIRS = (
    ("ADX_RSI_EQUAL_WEIGHT", "ADX_ONLY"),
    ("ADX_RSI_EQUAL_WEIGHT", "RSI_ONLY"),
    ("ADX_RSI_VOLUME_EQUAL_WEIGHT", "ADX_RSI_EQUAL_WEIGHT"),
)


def _read_csv_file(path: Path) -> tuple[dict[str, str], ...]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return tuple(dict(row) for row in csv.DictReader(handle))


@lru_cache(maxsize=32)
def _read_csv_cached(path_text: str, modified_ns: int, size: int) -> tuple[dict[str, str], ...]:
    del modified_ns, size
    return _read_csv_file(Path(path_text))


def _read_csv(path: Path, required: frozenset[str]) -> tuple[dict[str, str], ...]:
    stat = path.stat()
    rows = _read_csv_cached(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    if not rows:
        raise ValueError(f"empty persisted artifact: {path.name}")
    missing = required - frozenset(rows[0])
    if missing:
        raise ValueError(f"{path.name} is missing columns: {', '.join(sorted(missing))}")
    return rows


def _read_manifest(path: Path) -> Mapping[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("manifest root must be an object")
    return payload


def _text(row: Mapping[str, Any], key: str) -> str:
    return str(row.get(key, "")).strip()


def _integer(row: Mapping[str, Any], key: str) -> int:
    value = _text(row, key)
    return int(float(value)) if value else 0


def _optional_float(row: Mapping[str, Any], key: str) -> float | None:
    value = _text(row, key)
    if not value or value.lower() in {"nan", "none", "null", "<na>"}:
        return None
    return float(value)


def _optional_bool(row: Mapping[str, Any], key: str) -> bool | None:
    value = _text(row, key).lower()
    if not value:
        return None
    if value in {"true", "1"}:
        return True
    if value in {"false", "0"}:
        return False
    raise ValueError(f"invalid boolean for {key}: {value}")


def _limitations(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("composite_comparison", "policy_selection_diagnostics"):
        section = manifest.get(key)
        raw = section.get("limitations") if isinstance(section, Mapping) else None
        if isinstance(raw, list):
            values.extend(str(item).strip() for item in raw if str(item).strip())
        elif isinstance(raw, Mapping):
            values.extend(str(name) for name, enabled in raw.items() if enabled is False)
    return tuple(dict.fromkeys(values))


def _dispositions(root: Path) -> tuple[tuple[str, str], ...]:
    path = root / "research_results" / _GATE_ROOT / "research_decision_summary.csv"
    if not path.is_file():
        return ()
    try:
        rows = _read_csv(path, frozenset({"candidate_type", "candidate", "decision"}))
    except (OSError, UnicodeError, ValueError):
        return ()
    return tuple(
        sorted(
            (_text(row, "candidate"), _text(row, "decision"))
            for row in rows
            if _text(row, "candidate_type").lower() == "policy"
        )
    )


def _policy_catalog(root: Path) -> FrozenPolicyCatalog:
    artifact_root = root / "research_results" / _ROOT_NAME
    manifest_path = artifact_root / "experiment_manifest.json"
    policy_summary = artifact_root / "composite_policy_summary.csv"
    policy_blocks = artifact_root / "composite_policy_by_block.csv"
    contrast_summary = artifact_root / "composite_contrast_summary.csv"
    contrast_blocks = artifact_root / "composite_contrast_by_block.csv"
    overlap_path = artifact_root / "policy_selection_overlap_summary.csv"
    turnover_path = artifact_root / "policy_selection_turnover_summary.csv"
    paths = (
        manifest_path, policy_summary, policy_blocks, contrast_summary,
        contrast_blocks, overlap_path, turnover_path,
    )
    if any(not path.is_file() for path in paths):
        return FrozenPolicyCatalog(
            ComparisonState.UNAVAILABLE, (), (), (), (), (), paths, (), (),
            "Required canonical policy-comparison artifacts are unavailable.",
        )
    try:
        manifest = _read_manifest(manifest_path)
        composite = manifest.get("composite_comparison")
        selection = manifest.get("policy_selection_diagnostics")
        if not isinstance(composite, Mapping) or composite.get("completed") is not True:
            raise ValueError("composite comparison is not complete")
        if not isinstance(selection, Mapping) or selection.get("completed") is not True:
            raise ValueError("selection diagnostics are not complete")
        identities = tuple(
            str(section.get("result_identity", "")).strip()
            for section in (composite, selection)
            if str(section.get("result_identity", "")).strip()
        )
        if len(identities) != 2:
            raise ValueError("comparison result identities are missing")
        summary_rows = _read_csv(
            policy_summary,
            frozenset({
                "policy_name", "horizon_sessions", "outcome_field", "mean_daily_rank_ic",
                "positive_spread_rate", "ic_coverage_pct", "mean_daily_mean_spread",
                "all_blocks_positive_ic", "all_blocks_positive_spread", "identity",
            }),
        )
        block_rows = _read_csv(
            policy_blocks,
            frozenset({
                "policy_name", "horizon_sessions", "outcome_field", "block_name",
                "mean_daily_rank_ic", "positive_spread_rate", "ic_coverage_pct",
                "mean_daily_mean_spread", "identity",
            }),
        )
        policies: list[_PolicyRecord] = []
        for row in summary_rows:
            positive_ic = _optional_bool(row, "all_blocks_positive_ic")
            positive_spread = _optional_bool(row, "all_blocks_positive_spread")
            support = (
                "CONSISTENT" if positive_ic is True and positive_spread is True
                else "MIXED" if positive_ic is not None and positive_spread is not None
                else "UNKNOWN"
            )
            policies.append(
                _PolicyRecord(
                    _text(row, "policy_name"), _integer(row, "horizon_sessions"),
                    _text(row, "outcome_field"), "whole_period",
                    _optional_float(row, "mean_daily_rank_ic"),
                    _optional_float(row, "positive_spread_rate"),
                    _optional_float(row, "ic_coverage_pct"),
                    _optional_float(row, "mean_daily_mean_spread"), support,
                    _text(row, "identity"),
                )
            )
        for row in block_rows:
            ic = _optional_float(row, "mean_daily_rank_ic")
            spread = _optional_float(row, "mean_daily_mean_spread")
            support = (
                "POSITIVE" if ic is not None and spread is not None and ic > 0 and spread > 0
                else "NEGATIVE" if ic is not None and spread is not None and ic < 0 and spread < 0
                else "MIXED" if ic is not None and spread is not None
                else "UNKNOWN"
            )
            policies.append(
                _PolicyRecord(
                    _text(row, "policy_name"), _integer(row, "horizon_sessions"),
                    _text(row, "outcome_field"), _text(row, "block_name"), ic,
                    _optional_float(row, "positive_spread_rate"),
                    _optional_float(row, "ic_coverage_pct"), spread, support,
                    _text(row, "identity"),
                )
            )
        contrasts: list[_ContrastRecord] = []
        for path, scope_key in ((contrast_summary, None), (contrast_blocks, "block_name")):
            rows = _read_csv(
                path,
                frozenset({
                    "variant_policy", "reference_policy", "horizon_sessions", "outcome_field",
                    "mean_daily_ic_delta", "mean_daily_spread_delta", "identity",
                }) | (frozenset({scope_key}) if scope_key else frozenset()),
            )
            contrasts.extend(
                _ContrastRecord(
                    _text(row, "variant_policy"), _text(row, "reference_policy"),
                    _integer(row, "horizon_sessions"), _text(row, "outcome_field"),
                    _text(row, scope_key) if scope_key else "whole_period",
                    _optional_float(row, "mean_daily_ic_delta"),
                    _optional_float(row, "mean_daily_spread_delta"), _text(row, "identity"),
                )
                for row in rows
            )
        overlap_rows = _read_csv(
            overlap_path,
            frozenset({
                "selection_budget", "scope_name", "mean_overlap_coefficient",
                "mean_jaccard_similarity", "exact_selected_set_equality_rate", "identity",
            }),
        )
        overlaps = tuple(
            SelectionOverlapEvidence(
                _integer(row, "selection_budget"), _text(row, "scope_name"),
                _optional_float(row, "mean_overlap_coefficient"),
                _optional_float(row, "mean_jaccard_similarity"),
                _optional_float(row, "exact_selected_set_equality_rate"), _text(row, "identity"),
            )
            for row in overlap_rows
        )
        turnover_rows = _read_csv(
            turnover_path,
            frozenset({
                "policy_name", "selection_budget", "scope_name", "mean_one_way_turnover",
                "total_entry_count", "identity",
            }),
        )
        turnover = tuple(
            TurnoverEvidence(
                _text(row, "policy_name"), _integer(row, "selection_budget"),
                _text(row, "scope_name"), _optional_float(row, "mean_one_way_turnover"),
                _integer(row, "total_entry_count"), _text(row, "identity"),
            )
            for row in turnover_rows
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return FrozenPolicyCatalog(
            ComparisonState.UNAVAILABLE, (), (), (), (), (), paths, (), (),
            f"Canonical policy-comparison evidence is incompatible: {exc}",
        )
    return FrozenPolicyCatalog(
        ComparisonState.AVAILABLE,
        tuple(sorted(policies, key=lambda item: (item.policy, item.horizon_sessions, item.outcome_field, item.scope))),
        tuple(sorted(contrasts, key=lambda item: (item.variant, item.reference, item.horizon_sessions, item.outcome_field, item.scope))),
        tuple(sorted(overlaps, key=lambda item: (item.budget, item.scope))),
        tuple(sorted(turnover, key=lambda item: (item.policy, item.budget, item.scope))),
        _dispositions(root), paths, identities, _limitations(manifest),
        "Only predeclared persisted policy contrasts are compatible.",
    )


def inspect_evidence_comparison_catalog(*, root: Path = PROJECT_ROOT) -> EvidenceComparisonCatalog:
    return EvidenceComparisonCatalog(inspect_factor_explore_catalog(root=root), _policy_catalog(root))


def _factor_item(selected: SelectedFactorEvidence) -> ComparableEvidence | None:
    if selected.summary is None:
        return None
    block = selected.selected_block
    return ComparableEvidence(
        selected.factor,
        block.identity if block is not None else selected.summary.identity,
        selected.gate_disposition,
        block.mean_daily_rank_ic if block is not None else selected.summary.mean_daily_rank_ic,
        block.positive_spread_date_rate if block is not None else selected.summary.positive_spread_date_rate,
        block.ic_coverage_pct if block is not None else selected.summary.ic_coverage_pct,
        block.high_minus_low_spread if block is not None else selected.summary.high_minus_low_spread,
        block.direction if block is not None else (
            "CONSISTENT" if selected.temporal_support is True
            else "MIXED" if selected.temporal_support is False
            else "UNKNOWN"
        ),
    )


def _difference_notes(a: ComparableEvidence, b: ComparableEvidence) -> tuple[str, ...]:
    notes: list[str] = []
    if a.mean_rank_ic is not None and b.mean_rank_ic is not None:
        delta = a.mean_rank_ic - b.mean_rank_ic
        relation = "higher" if delta > 0 else "lower" if delta < 0 else "the same"
        notes.append(f"{a.name} has {relation} persisted mean rank IC than {b.name} ({delta:+.4f}).")
    if a.defined_date_coverage_pct is not None and b.defined_date_coverage_pct is not None:
        notes.append(
            f"Defined-date coverage differs by {a.defined_date_coverage_pct - b.defined_date_coverage_pct:+.2f} percentage points (A − B)."
        )
    return tuple(notes)


def _incompatible(
    family: ComparisonFamily,
    a: str,
    b: str,
    horizon: int,
    outcome: str,
    scope: str,
    detail: str,
) -> EvidenceComparison:
    return EvidenceComparison(
        state=ComparisonState.INCOMPATIBLE,
        family=family,
        candidate_a=a,
        candidate_b=b,
        horizon_sessions=horizon,
        outcome_field=outcome,
        temporal_scope=scope,
        evidence_a=None,
        evidence_b=None,
        blocks=(),
        mean_ic_delta_a_minus_b=None,
        mean_spread_delta_a_minus_b=None,
        incremental_notes=(),
        redundancy_correlation=None,
        overlap=(),
        turnover=(),
        dimensions=(),
        descriptive_differences=(),
        artifact_paths=(),
        artifact_identities=(),
        limitations=(),
        detail=detail,
    )


def compare_neutral_factors(
    catalog: EvidenceComparisonCatalog,
    *,
    factor_a: str,
    factor_b: str,
    horizon_a: int,
    horizon_b: int,
    outcome_a: str,
    outcome_b: str,
    temporal_scope: str = "whole_period",
) -> EvidenceComparison:
    family = ComparisonFamily.NEUTRAL_FACTORS
    if factor_a == factor_b:
        return _incompatible(family, factor_a, factor_b, horizon_a, outcome_a, temporal_scope, "Choose two distinct neutral factors.")
    if horizon_a != horizon_b:
        return _incompatible(family, factor_a, factor_b, horizon_a, outcome_a, temporal_scope, "Horizons must match for a compatible comparison.")
    if outcome_a != outcome_b:
        return _incompatible(family, factor_a, factor_b, horizon_a, outcome_a, temporal_scope, "Outcomes must match for a compatible comparison.")
    selected_a = select_factor_evidence(
        catalog.factors, population=ExplorePopulation.NEUTRAL_PIT, factor=factor_a,
        horizon_sessions=horizon_a, outcome_field=outcome_a, temporal_scope=temporal_scope,
    )
    selected_b = select_factor_evidence(
        catalog.factors, population=ExplorePopulation.NEUTRAL_PIT, factor=factor_b,
        horizon_sessions=horizon_b, outcome_field=outcome_b, temporal_scope=temporal_scope,
    )
    if selected_a.state is EvidenceState.UNAVAILABLE or selected_b.state is EvidenceState.UNAVAILABLE:
        return EvidenceComparison(
            ComparisonState.UNAVAILABLE, family, factor_a, factor_b, horizon_a, outcome_a,
            temporal_scope, None, None, (), None, None, (), None, (), (), (), (),
            tuple(dict.fromkeys(selected_a.artifact_paths + selected_b.artifact_paths)),
            tuple(dict.fromkeys(selected_a.artifact_identities + selected_b.artifact_identities)),
            tuple(dict.fromkeys(selected_a.limitations + selected_b.limitations)),
            selected_a.detail if selected_a.state is EvidenceState.UNAVAILABLE else selected_b.detail,
        )
    a, b = _factor_item(selected_a), _factor_item(selected_b)
    assert a is not None and b is not None
    b_blocks = {item.name: item for item in selected_b.blocks}
    blocks = tuple(
        BlockPairEvidence(
            item.name, item.mean_daily_rank_ic, b_blocks[item.name].mean_daily_rank_ic,
            item.high_minus_low_spread, b_blocks[item.name].high_minus_low_spread,
            item.ic_coverage_pct, b_blocks[item.name].ic_coverage_pct,
        )
        for item in selected_a.blocks
        if item.name in b_blocks
    )
    incremental_notes: list[str] = []
    for owner, other, records in (
        (factor_a, factor_b, selected_a.incremental),
        (factor_b, factor_a, selected_b.incremental),
    ):
        for item in records:
            if other in item.controls:
                value = "UNKNOWN" if item.mean_partial_rank_ic is None else f"{item.mean_partial_rank_ic:.4f}"
                incremental_notes.append(f"{owner} conditional on {other}: persisted mean partial rank IC {value}.")
    redundancy = next(
        (item.correlation for item in selected_a.redundancy if item.other_factor == factor_b),
        None,
    )
    dimensions = (
        ComparisonDimension("Cross-sectional signal", "AVAILABLE", "AVAILABLE", "Same population, horizon, outcome, and scope."),
        ComparisonDimension("Temporal stability", a.temporal_support, b.temporal_support, "Persisted block evidence is directly comparable."),
        ComparisonDimension("Incremental value", "AVAILABLE" if incremental_notes else "UNAVAILABLE", "AVAILABLE" if incremental_notes else "UNAVAILABLE", "Only frozen conditional hypotheses are shown."),
        ComparisonDimension("Redundancy", "AVAILABLE" if redundancy is not None else "UNAVAILABLE", "AVAILABLE" if redundancy is not None else "UNAVAILABLE", "Exact persisted factor-pair correlation only."),
        ComparisonDimension("Coverage", "AVAILABLE", "AVAILABLE", "Defined-date coverage uses the same persisted date scope."),
        ComparisonDimension("Cost sensitivity", "UNAVAILABLE", "UNAVAILABLE", "No compatible persisted cost contrast exists."),
        ComparisonDimension("Portfolio consequence", "UNAVAILABLE", "UNAVAILABLE", "No compatible persisted portfolio contrast exists."),
    )
    return EvidenceComparison(
        ComparisonState.AVAILABLE, family, factor_a, factor_b, horizon_a, outcome_a,
        temporal_scope, a, b, blocks,
        a.mean_rank_ic - b.mean_rank_ic if a.mean_rank_ic is not None and b.mean_rank_ic is not None else None,
        a.high_minus_low_spread - b.high_minus_low_spread if a.high_minus_low_spread is not None and b.high_minus_low_spread is not None else None,
        tuple(incremental_notes), redundancy, (), (), dimensions, _difference_notes(a, b),
        tuple(dict.fromkeys(selected_a.artifact_paths + selected_b.artifact_paths)),
        tuple(dict.fromkeys(selected_a.artifact_identities + selected_b.artifact_identities)),
        tuple(dict.fromkeys(selected_a.limitations + selected_b.limitations)),
        "Descriptive neutral-factor comparison; no canonical preference is inferred.",
    )


def compare_frozen_policies(
    catalog: EvidenceComparisonCatalog,
    *,
    policy_a: str,
    policy_b: str,
    horizon_a: int,
    horizon_b: int,
    outcome_a: str,
    outcome_b: str,
    temporal_scope: str = "whole_period",
) -> EvidenceComparison:
    family = ComparisonFamily.FROZEN_POLICIES
    source = catalog.policies
    if (policy_a, policy_b) not in _SUPPORTED_POLICY_PAIRS:
        return _incompatible(family, policy_a, policy_b, horizon_a, outcome_a, temporal_scope, "This policy pair has no predeclared canonical contrast.")
    if horizon_a != horizon_b:
        return _incompatible(family, policy_a, policy_b, horizon_a, outcome_a, temporal_scope, "Horizons must match for a compatible comparison.")
    if outcome_a != outcome_b:
        return _incompatible(family, policy_a, policy_b, horizon_a, outcome_a, temporal_scope, "Outcomes must match for a compatible comparison.")
    if source.state is ComparisonState.UNAVAILABLE:
        return EvidenceComparison(
            ComparisonState.UNAVAILABLE, family, policy_a, policy_b, horizon_a, outcome_a,
            temporal_scope, None, None, (), None, None, (), None, (), (), (), (),
            source.artifact_paths, source.artifact_identities, source.limitations, source.detail,
        )
    def record(policy: str) -> _PolicyRecord | None:
        return next(
            (
                item for item in source.policies
                if (item.policy, item.horizon_sessions, item.outcome_field, item.scope)
                == (policy, horizon_a, outcome_a, temporal_scope)
            ),
            None,
        )
    record_a, record_b = record(policy_a), record(policy_b)
    contrast = next(
        (
            item for item in source.contrasts
            if (item.variant, item.reference, item.horizon_sessions, item.outcome_field, item.scope)
            == (policy_a, policy_b, horizon_a, outcome_a, temporal_scope)
        ),
        None,
    )
    if record_a is None or record_b is None or contrast is None:
        return EvidenceComparison(
            ComparisonState.UNAVAILABLE, family, policy_a, policy_b, horizon_a, outcome_a,
            temporal_scope, None, None, (), None, None, (), None, (), (), (), (),
            source.artifact_paths, source.artifact_identities, source.limitations,
            "Compatible persisted policy evidence is missing for this scope.",
        )
    disposition = dict(source.dispositions)
    a = ComparableEvidence(
        policy_a, record_a.identity, disposition.get(policy_a), record_a.mean_rank_ic,
        record_a.positive_spread_rate, record_a.coverage_pct, record_a.spread,
        record_a.temporal_support,
    )
    b = ComparableEvidence(
        policy_b, record_b.identity, disposition.get(policy_b), record_b.mean_rank_ic,
        record_b.positive_spread_rate, record_b.coverage_pct, record_b.spread,
        record_b.temporal_support,
    )
    blocks: tuple[BlockPairEvidence, ...] = ()
    if temporal_scope == "whole_period":
        block_names = tuple(
            dict.fromkeys(item.scope for item in source.policies if item.policy == policy_a and item.scope != "whole_period")
        )
        block_pairs: list[BlockPairEvidence] = []
        for name in block_names:
            pa = next((item for item in source.policies if item.policy == policy_a and item.horizon_sessions == horizon_a and item.outcome_field == outcome_a and item.scope == name), None)
            pb = next((item for item in source.policies if item.policy == policy_b and item.horizon_sessions == horizon_a and item.outcome_field == outcome_a and item.scope == name), None)
            if pa is not None and pb is not None:
                block_pairs.append(BlockPairEvidence(name, pa.mean_rank_ic, pb.mean_rank_ic, pa.spread, pb.spread, pa.coverage_pct, pb.coverage_pct))
        blocks = tuple(block_pairs)
    overlap = ()
    turnover = ()
    if {policy_a, policy_b} == {"ADX_ONLY", "ADX_RSI_EQUAL_WEIGHT"}:
        overlap = tuple(item for item in source.overlap if item.scope == temporal_scope)
        turnover = tuple(
            item for item in source.turnover
            if item.policy in {policy_a, policy_b} and item.scope == temporal_scope
        )
    dimensions = (
        ComparisonDimension("Cross-sectional signal", "AVAILABLE", "AVAILABLE", "Predeclared paired policy contrast."),
        ComparisonDimension("Temporal stability", a.temporal_support, b.temporal_support, "Same persisted blocks and sample."),
        ComparisonDimension("Incremental value", "AVAILABLE", "REFERENCE", "Persisted A − B contrast; no preference is inferred."),
        ComparisonDimension("Redundancy", "UNAVAILABLE", "UNAVAILABLE", "No canonical policy-redundancy contract."),
        ComparisonDimension("Coverage", "AVAILABLE", "AVAILABLE", "Same horizon, outcome, and scope."),
        ComparisonDimension("Selection overlap / turnover", "AVAILABLE" if overlap else "UNAVAILABLE", "AVAILABLE" if turnover else "UNAVAILABLE", "Persisted only for ADX and ADX+RSI."),
        ComparisonDimension("Cost sensitivity", "UNAVAILABLE", "UNAVAILABLE", "No compatible persisted cost contrast exists."),
        ComparisonDimension("Portfolio consequence", "UNAVAILABLE", "UNAVAILABLE", "No compatible persisted portfolio contrast exists."),
    )
    return EvidenceComparison(
        ComparisonState.AVAILABLE, family, policy_a, policy_b, horizon_a, outcome_a,
        temporal_scope, a, b, blocks, contrast.mean_ic_delta, contrast.mean_spread_delta,
        (f"Persisted A − B mean rank IC delta: {contrast.mean_ic_delta:+.4f}." if contrast.mean_ic_delta is not None else "Persisted IC delta is unavailable.",),
        None, overlap, turnover, dimensions, _difference_notes(a, b),
        source.artifact_paths, source.artifact_identities, source.limitations,
        "Descriptive frozen-policy contrast; canonical gate dispositions remain separate.",
    )
