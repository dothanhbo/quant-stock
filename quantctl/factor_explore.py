from __future__ import annotations

import csv
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Mapping

from quantctl.historical_qualification import qualify_historical_limitations
from quantctl.registry import PROJECT_ROOT


class ExplorePopulation(str, Enum):
    NEUTRAL_PIT = "neutral_pit"
    FROZEN_Q70_ACCEPTED = "frozen_q70_accepted"


class EvidenceState(str, Enum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True, slots=True)
class FactorSummaryEvidence:
    factor: str
    horizon_sessions: int
    outcome_field: str
    total_signal_dates: int
    ic_defined_date_count: int
    ic_coverage_pct: float | None
    mean_daily_rank_ic: float | None
    positive_spread_date_rate: float | None
    high_minus_low_spread: float | None
    identity: str


@dataclass(frozen=True, slots=True)
class TemporalBlockEvidence:
    name: str
    start_date: str
    end_date: str
    ic_defined_date_count: int
    ic_coverage_pct: float | None
    mean_daily_rank_ic: float | None
    spread_defined_date_count: int
    spread_coverage_pct: float | None
    high_minus_low_spread: float | None
    positive_spread_date_rate: float | None
    direction: str
    undefined_reason: str | None
    identity: str


@dataclass(frozen=True, slots=True)
class IncrementalEvidence:
    hypothesis: str
    controls: str
    partial_ic_coverage_pct: float | None
    mean_partial_rank_ic: float | None
    partial_minus_raw_rank_ic: float | None
    all_blocks_positive: bool | None
    identity: str


@dataclass(frozen=True, slots=True)
class RedundancyEvidence:
    other_factor: str
    correlation: float | None
    correlation_coverage_pct: float | None
    scope: str
    identity: str | None


@dataclass(frozen=True, slots=True)
class DailyFactorEvidence:
    signal_date: str
    rank_ic: float | None
    high_minus_low_spread: float | None
    identity: str


@dataclass(frozen=True, slots=True)
class FactorPopulationEvidence:
    population: ExplorePopulation
    label: str
    state: EvidenceState
    factors: tuple[str, ...]
    horizons: tuple[int, ...]
    outcomes: tuple[str, ...]
    summaries: tuple[FactorSummaryEvidence, ...]
    blocks: tuple[tuple[str, int, str, TemporalBlockEvidence], ...]
    temporal_support: tuple[tuple[str, int, str, bool | None], ...]
    incremental: tuple[tuple[str, int, str, IncrementalEvidence], ...]
    redundancy: tuple[tuple[str, RedundancyEvidence], ...]
    gate_dispositions: tuple[tuple[str, str], ...]
    daily_path: Path
    artifact_paths: tuple[Path, ...]
    artifact_identities: tuple[str, ...]
    as_of: str | None
    universe: str
    limitations: tuple[str, ...]
    detail: str


@dataclass(frozen=True, slots=True)
class FactorExploreCatalog:
    populations: tuple[FactorPopulationEvidence, ...]

    def population(self, value: ExplorePopulation | str) -> FactorPopulationEvidence | None:
        normalized = value.value if isinstance(value, ExplorePopulation) else str(value)
        return next((item for item in self.populations if item.population.value == normalized), None)


@dataclass(frozen=True, slots=True)
class SelectedFactorEvidence:
    state: EvidenceState
    population: ExplorePopulation
    population_label: str
    factor: str
    horizon_sessions: int
    outcome_field: str
    temporal_scope: str
    summary: FactorSummaryEvidence | None
    selected_block: TemporalBlockEvidence | None
    blocks: tuple[TemporalBlockEvidence, ...]
    temporal_support: bool | None
    incremental: tuple[IncrementalEvidence, ...]
    redundancy: tuple[RedundancyEvidence, ...]
    gate_disposition: str | None
    artifact_paths: tuple[Path, ...]
    artifact_identities: tuple[str, ...]
    as_of: str | None
    universe: str
    limitations: tuple[str, ...]
    detail: str


_NEUTRAL_ROOT = "quantlab_neutral_panel_factor_evaluation_2018-08-07_2026-09-17"
_CANDIDATE_OUTCOME_ROOT = "quantlab_factor_outcome_evaluation_2018_2026"
_CANDIDATE_TEMPORAL_ROOT = "quantlab_factor_temporal_stability_2018_2026"
_CANDIDATE_DIAGNOSTIC_ROOT = "quantlab_candidate_factor_diagnostics_2018_2026"
_GATE_ROOT = "quantlab_research_decision_gate_2018-08-07_2026-09-17"
_UNIVERSE = "point-in-time database coverage; not historical VN100 membership"


def _read_csv_file(path: Path) -> tuple[dict[str, str], ...]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return tuple(dict(row) for row in csv.DictReader(handle))


@lru_cache(maxsize=96)
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
        raise ValueError(f"{path.name} root must be an object")
    return payload


def _text(row: Mapping[str, Any], key: str) -> str:
    return str(row.get(key, "")).strip()


def _optional_float(row: Mapping[str, Any], key: str) -> float | None:
    value = _text(row, key)
    if not value or value.lower() in {"nan", "none", "null", "<na>"}:
        return None
    return float(value)


def _integer(row: Mapping[str, Any], key: str) -> int:
    value = _text(row, key)
    return int(float(value)) if value else 0


def _optional_bool(row: Mapping[str, Any], key: str) -> bool | None:
    value = _text(row, key).lower()
    if not value:
        return None
    if value in {"true", "1"}:
        return True
    if value in {"false", "0"}:
        return False
    raise ValueError(f"invalid boolean for {key}: {value}")


def _identity(payload: Mapping[str, Any], *path: str) -> str | None:
    value: Any = payload
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    normalized = str(value or "").strip()
    return normalized or None


def _limitations(*payloads: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for payload in payloads:
        raw = payload.get("limitations")
        if isinstance(raw, list):
            values.extend(str(item).strip() for item in raw if str(item).strip())
    return tuple(dict.fromkeys(values))


def _direction(ic: float | None, spread: float | None) -> str:
    if ic is None or spread is None:
        return "UNKNOWN"
    if ic > 0 and spread > 0:
        return "POSITIVE"
    if ic < 0 and spread < 0:
        return "NEGATIVE"
    if ic == 0 and spread == 0:
        return "FLAT"
    return "MIXED"


def _summary(row: Mapping[str, Any]) -> FactorSummaryEvidence:
    return FactorSummaryEvidence(
        _text(row, "factor"),
        _integer(row, "horizon_sessions"),
        _text(row, "outcome_field"),
        _integer(row, "total_signal_dates"),
        _integer(row, "ic_defined_date_count"),
        _optional_float(row, "ic_coverage_pct"),
        _optional_float(row, "mean_daily_rank_ic"),
        _optional_float(row, "positive_mean_spread_rate")
        if "positive_mean_spread_rate" in row
        else _optional_float(row, "positive_spread_rate"),
        _optional_float(row, "mean_daily_high_minus_low_mean_spread"),
        _text(row, "identity"),
    )


def _block(row: Mapping[str, Any], *, candidate: bool) -> TemporalBlockEvidence:
    total = _integer(row, "total_signal_date_rows" if candidate else "total_source_signal_dates")
    ic_count = _integer(row, "ic_defined_date_count")
    spread_count = _integer(row, "bucket_defined_date_count" if candidate else "spread_defined_date_count")
    ic_coverage = (100.0 * ic_count / total) if candidate and total else _optional_float(row, "ic_coverage_pct")
    spread_coverage = (
        (100.0 * spread_count / total)
        if candidate and total
        else _optional_float(row, "spread_coverage_pct")
    )
    ic = _optional_float(row, "mean_daily_rank_ic")
    spread = _optional_float(
        row,
        "mean_daily_high_minus_low_mean_spread" if candidate else "mean_daily_mean_spread",
    )
    undefined = _text(row, "undefined_reason")
    if not undefined and not candidate:
        undefined = " | ".join(
            item
            for item in (_text(row, "ic_undefined_reason"), _text(row, "spread_undefined_reason"))
            if item
        )
    return TemporalBlockEvidence(
        _text(row, "block_name"),
        _text(row, "block_start_date"),
        _text(row, "block_end_date"),
        ic_count,
        ic_coverage,
        ic,
        spread_count,
        spread_coverage,
        spread,
        _optional_float(row, "positive_mean_spread_rate")
        if candidate
        else _optional_float(row, "positive_spread_rate"),
        _direction(ic, spread),
        undefined or None,
        _text(row, "identity"),
    )


def _gate_dispositions(root: Path) -> tuple[tuple[str, str], ...]:
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
            if _text(row, "candidate_type").lower() == "factor"
        )
    )


def _unavailable_population(
    population: ExplorePopulation,
    label: str,
    paths: tuple[Path, ...],
    detail: str,
) -> FactorPopulationEvidence:
    return FactorPopulationEvidence(
        population, label, EvidenceState.UNAVAILABLE, (), (), (), (), (), (), (), (), (),
        Path(), paths, (), None, _UNIVERSE, (), detail,
    )


def _neutral_population(root: Path) -> FactorPopulationEvidence:
    artifact_root = root / "research_results" / _NEUTRAL_ROOT
    manifest_path = artifact_root / "experiment_manifest.json"
    summary_path = artifact_root / "factor_summary.csv"
    block_path = artifact_root / "temporal_stability_by_block.csv"
    temporal_path = artifact_root / "temporal_stability_summary.csv"
    incremental_path = artifact_root / "factor_incremental_summary.csv"
    redundancy_path = artifact_root / "factor_redundancy_summary.csv"
    daily_path = artifact_root / "factor_by_date.csv"
    paths = (
        manifest_path, summary_path, block_path, temporal_path,
        incremental_path, redundancy_path, daily_path,
    )
    if any(not path.is_file() for path in paths[:-1]):
        return _unavailable_population(
            ExplorePopulation.NEUTRAL_PIT, "Neutral PIT factors", paths,
            "Required neutral factor evidence is unavailable.",
        )
    try:
        manifest = _read_manifest(manifest_path)
        if manifest.get("completed") is not True:
            raise ValueError("neutral manifest is not complete")
        result_identity = _identity(manifest, "evaluation", "result_identity")
        if result_identity is None:
            raise ValueError("neutral evaluation identity is missing")
        summary_rows = _read_csv(
            summary_path,
            frozenset({
                "factor", "horizon_sessions", "outcome_field", "total_signal_dates",
                "ic_defined_date_count", "ic_coverage_pct", "mean_daily_rank_ic",
                "positive_mean_spread_rate", "mean_daily_high_minus_low_mean_spread", "identity",
            }),
        )
        block_rows = _read_csv(
            block_path,
            frozenset({
                "factor", "horizon_sessions", "outcome_field", "block_name",
                "block_start_date", "block_end_date", "total_source_signal_dates",
                "ic_defined_date_count", "ic_coverage_pct", "mean_daily_rank_ic",
                "spread_defined_date_count", "spread_coverage_pct", "mean_daily_mean_spread", "identity",
            }),
        )
        temporal_rows = _read_csv(
            temporal_path,
            frozenset({"factor", "horizon_sessions", "outcome_field", "descriptive_temporal_support"}),
        )
        incremental_rows = _read_csv(
            incremental_path,
            frozenset({
                "hypothesis_name", "target_factor", "control_factors", "horizon_sessions",
                "outcome_field", "partial_rank_ic_coverage_pct", "mean_daily_partial_rank_ic",
                "mean_partial_minus_raw_rank_ic", "all_blocks_positive_partial_rank_ic", "identity",
            }),
        )
        redundancy_rows = _read_csv(
            redundancy_path,
            frozenset({
                "first_factor", "second_factor", "correlation_coverage_pct",
                "mean_daily_correlation", "identity",
            }),
        )
        summaries = tuple(_summary(row) for row in summary_rows)
        blocks = tuple(
            (_text(row, "factor"), _integer(row, "horizon_sessions"), _text(row, "outcome_field"), _block(row, candidate=False))
            for row in block_rows
        )
        incremental = tuple(
            (
                _text(row, "target_factor"),
                _integer(row, "horizon_sessions"),
                _text(row, "outcome_field"),
                IncrementalEvidence(
                    _text(row, "hypothesis_name"), _text(row, "control_factors"),
                    _optional_float(row, "partial_rank_ic_coverage_pct"),
                    _optional_float(row, "mean_daily_partial_rank_ic"),
                    _optional_float(row, "mean_partial_minus_raw_rank_ic"),
                    _optional_bool(row, "all_blocks_positive_partial_rank_ic"),
                    _text(row, "identity"),
                ),
            )
            for row in incremental_rows
        )
        redundancy: list[tuple[str, RedundancyEvidence]] = []
        for row in redundancy_rows:
            first, second = _text(row, "first_factor"), _text(row, "second_factor")
            for factor, other in ((first, second), (second, first)):
                redundancy.append(
                    (
                        factor,
                        RedundancyEvidence(
                            other,
                            _optional_float(row, "mean_daily_correlation"),
                            _optional_float(row, "correlation_coverage_pct"),
                            "same-date cross-sectional correlation",
                            _text(row, "identity") or None,
                        ),
                    )
                )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return _unavailable_population(
            ExplorePopulation.NEUTRAL_PIT, "Neutral PIT factors", paths,
            f"Neutral factor evidence is incompatible: {exc}",
        )
    return FactorPopulationEvidence(
        ExplorePopulation.NEUTRAL_PIT,
        "Neutral PIT factors",
        EvidenceState.AVAILABLE if daily_path.is_file() else EvidenceState.PARTIAL,
        tuple(sorted({item.factor for item in summaries})),
        tuple(sorted({item.horizon_sessions for item in summaries})),
        tuple(sorted({item.outcome_field for item in summaries})),
        tuple(sorted(summaries, key=lambda item: (item.factor, item.horizon_sessions, item.outcome_field))),
        tuple(sorted(blocks, key=lambda item: (item[0], item[1], item[2], item[3].start_date))),
        tuple(
            sorted(
                (
                    _text(row, "factor"), _integer(row, "horizon_sessions"),
                    _text(row, "outcome_field"), _optional_bool(row, "descriptive_temporal_support"),
                )
                for row in temporal_rows
            )
        ),
        tuple(sorted(incremental, key=lambda item: (item[0], item[1], item[2], item[3].hypothesis))),
        tuple(sorted(redundancy, key=lambda item: (item[0], item[1].other_factor))),
        _gate_dispositions(root),
        daily_path,
        paths,
        (result_identity,),
        _identity(manifest, "effective_bounds", "outcome_data_through_session")
        or _identity(manifest, "requested_bounds", "end_date"),
        _UNIVERSE,
        qualify_historical_limitations(_limitations(manifest)),
        "Complete point-in-time neutral factor population.",
    )


def _candidate_population(root: Path) -> FactorPopulationEvidence:
    outcome_root = root / "research_results" / _CANDIDATE_OUTCOME_ROOT
    temporal_root = root / "research_results" / _CANDIDATE_TEMPORAL_ROOT
    diagnostic_root = root / "research_results" / _CANDIDATE_DIAGNOSTIC_ROOT
    outcome_manifest_path = outcome_root / "experiment_manifest.json"
    temporal_manifest_path = temporal_root / "experiment_manifest.json"
    diagnostic_manifest_path = diagnostic_root / "experiment_manifest.json"
    summary_path = outcome_root / "factor_outcome_summary.csv"
    block_path = temporal_root / "temporal_stability_by_block.csv"
    temporal_path = temporal_root / "temporal_stability_summary.csv"
    redundancy_path = diagnostic_root / "factor_associations_aggregate.csv"
    daily_path = outcome_root / "factor_outcome_by_date.csv"
    paths = (
        outcome_manifest_path, temporal_manifest_path, diagnostic_manifest_path,
        summary_path, block_path, temporal_path, redundancy_path, daily_path,
    )
    if any(not path.is_file() for path in (outcome_manifest_path, temporal_manifest_path, summary_path, block_path, temporal_path)):
        return _unavailable_population(
            ExplorePopulation.FROZEN_Q70_ACCEPTED, "Frozen-Q70 accepted candidates", paths,
            "Required accepted-candidate evidence is unavailable.",
        )
    try:
        outcome_manifest = _read_manifest(outcome_manifest_path)
        temporal_manifest = _read_manifest(temporal_manifest_path)
        if outcome_manifest.get("completion_status") != "complete" or temporal_manifest.get("completed") is not True:
            raise ValueError("accepted-candidate manifests are not complete")
        outcome_identity = _identity(outcome_manifest, "evaluation_result_identity")
        temporal_identity = _identity(temporal_manifest, "phase_4_7a_temporal_result_identity")
        if outcome_identity is None or temporal_identity is None:
            raise ValueError("accepted-candidate result identity is missing")
        summary_rows = _read_csv(
            summary_path,
            frozenset({
                "factor", "horizon_sessions", "outcome_field", "total_signal_dates",
                "ic_defined_date_count", "ic_coverage_pct", "mean_daily_rank_ic",
                "positive_spread_date_rate", "mean_daily_high_minus_low_mean_spread", "identity",
            }),
        )
        block_rows = _read_csv(
            block_path,
            frozenset({
                "factor", "horizon_sessions", "outcome_field", "block_name", "block_start_date",
                "block_end_date", "total_signal_date_rows", "ic_defined_date_count",
                "bucket_defined_date_count", "mean_daily_rank_ic",
                "mean_daily_high_minus_low_mean_spread", "identity",
            }),
        )
        temporal_rows = _read_csv(
            temporal_path,
            frozenset({"factor", "horizon_sessions", "outcome_field", "descriptive_temporal_support"}),
        )
        summaries = tuple(_summary(row) for row in summary_rows)
        blocks = tuple(
            (_text(row, "factor"), _integer(row, "horizon_sessions"), _text(row, "outcome_field"), _block(row, candidate=True))
            for row in block_rows
        )
        diagnostic_manifest: Mapping[str, Any] = {}
        redundancy: list[tuple[str, RedundancyEvidence]] = []
        diagnostic_identity: str | None = None
        if diagnostic_manifest_path.is_file() and redundancy_path.is_file():
            diagnostic_manifest = _read_manifest(diagnostic_manifest_path)
            diagnostic_identity = _identity(diagnostic_manifest, "diagnostics_result_identity")
            association_rows = _read_csv(
                redundancy_path,
                frozenset({
                    "first_factor", "second_factor", "pairwise_finite_count",
                    "spearman_correlation", "undefined_reason", "scope",
                }),
            )
            for row in association_rows:
                first, second = _text(row, "first_factor"), _text(row, "second_factor")
                for factor, other in ((first, second), (second, first)):
                    redundancy.append(
                        (
                            factor,
                            RedundancyEvidence(
                                other,
                                _optional_float(row, "spearman_correlation"),
                                None,
                                _text(row, "scope") or "pooled descriptive association",
                                None,
                            ),
                        )
                    )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return _unavailable_population(
            ExplorePopulation.FROZEN_Q70_ACCEPTED, "Frozen-Q70 accepted candidates", paths,
            f"Accepted-candidate evidence is incompatible: {exc}",
        )
    state = EvidenceState.AVAILABLE
    if not daily_path.is_file() or not redundancy:
        state = EvidenceState.PARTIAL
    identities = tuple(item for item in (outcome_identity, temporal_identity, diagnostic_identity) if item)
    return FactorPopulationEvidence(
        ExplorePopulation.FROZEN_Q70_ACCEPTED,
        "Frozen-Q70 accepted candidates",
        state,
        tuple(sorted({item.factor for item in summaries})),
        tuple(sorted({item.horizon_sessions for item in summaries})),
        tuple(sorted({item.outcome_field for item in summaries})),
        tuple(sorted(summaries, key=lambda item: (item.factor, item.horizon_sessions, item.outcome_field))),
        tuple(sorted(blocks, key=lambda item: (item[0], item[1], item[2], item[3].start_date))),
        tuple(
            sorted(
                (
                    _text(row, "factor"), _integer(row, "horizon_sessions"),
                    _text(row, "outcome_field"), _optional_bool(row, "descriptive_temporal_support"),
                )
                for row in temporal_rows
            )
        ),
        (),
        tuple(sorted(redundancy, key=lambda item: (item[0], item[1].other_factor))),
        (),
        daily_path,
        paths,
        identities,
        _identity(outcome_manifest, "effective_coverage_end_date")
        or _identity(outcome_manifest, "requested_signal_end_date"),
        _UNIVERSE,
        qualify_historical_limitations(
            _limitations(outcome_manifest, temporal_manifest, diagnostic_manifest)
        ),
        "Selection-conditioned evidence for candidates accepted by Frozen-Q70.",
    )


def inspect_factor_explore_catalog(*, root: Path = PROJECT_ROOT) -> FactorExploreCatalog:
    """Read fixed persisted Explore summaries; never read daily detail or run research."""

    return FactorExploreCatalog((_neutral_population(root), _candidate_population(root)))


def select_factor_evidence(
    catalog: FactorExploreCatalog,
    *,
    population: ExplorePopulation | str,
    factor: str,
    horizon_sessions: int,
    outcome_field: str,
    temporal_scope: str = "whole_period",
) -> SelectedFactorEvidence:
    source = catalog.population(population)
    population_value = population if isinstance(population, ExplorePopulation) else ExplorePopulation(population)
    if source is None or source.state is EvidenceState.UNAVAILABLE:
        return SelectedFactorEvidence(
            EvidenceState.UNAVAILABLE, population_value, source.label if source else str(population),
            factor, horizon_sessions, outcome_field, temporal_scope, None, None, (), None, (), (), None,
            source.artifact_paths if source else (), source.artifact_identities if source else (),
            source.as_of if source else None, source.universe if source else _UNIVERSE,
            source.limitations if source else (), source.detail if source else "Evidence population is unavailable.",
        )
    if factor not in source.factors:
        detail = f"Factor is unsupported for {source.label}."
    elif horizon_sessions not in source.horizons:
        detail = f"Horizon {horizon_sessions} is unsupported for {source.label}."
    elif outcome_field not in source.outcomes:
        detail = f"Outcome is unsupported for {source.label}."
    else:
        detail = ""
    if detail:
        return SelectedFactorEvidence(
            EvidenceState.UNAVAILABLE, source.population, source.label, factor, horizon_sessions,
            outcome_field, temporal_scope, None, None, (), None, (), (), None,
            source.artifact_paths, source.artifact_identities, source.as_of, source.universe,
            source.limitations, detail,
        )
    summary = next(
        (
            item for item in source.summaries
            if (item.factor, item.horizon_sessions, item.outcome_field)
            == (factor, horizon_sessions, outcome_field)
        ),
        None,
    )
    blocks = tuple(
        item for row_factor, row_horizon, row_outcome, item in source.blocks
        if (row_factor, row_horizon, row_outcome) == (factor, horizon_sessions, outcome_field)
    )
    selected_block = None
    if temporal_scope != "whole_period":
        selected_block = next((item for item in blocks if item.name == temporal_scope), None)
        if selected_block is None:
            detail = f"Temporal block {temporal_scope} is unavailable."
    support = next(
        (
            value for row_factor, row_horizon, row_outcome, value in source.temporal_support
            if (row_factor, row_horizon, row_outcome) == (factor, horizon_sessions, outcome_field)
        ),
        None,
    )
    incremental = tuple(
        item for row_factor, row_horizon, row_outcome, item in source.incremental
        if (row_factor, row_horizon, row_outcome) == (factor, horizon_sessions, outcome_field)
    )
    redundancy = tuple(item for row_factor, item in source.redundancy if row_factor == factor)
    disposition = dict(source.gate_dispositions).get(factor)
    state = EvidenceState.AVAILABLE if summary is not None and (temporal_scope == "whole_period" or selected_block is not None) else EvidenceState.UNAVAILABLE
    return SelectedFactorEvidence(
        state, source.population, source.label, factor, horizon_sessions, outcome_field,
        temporal_scope, summary, selected_block, blocks, support, incremental, redundancy,
        disposition, source.artifact_paths, source.artifact_identities, source.as_of,
        source.universe, source.limitations, detail or source.detail,
    )


def load_daily_factor_evidence(
    catalog: FactorExploreCatalog,
    *,
    population: ExplorePopulation | str,
    factor: str,
    horizon_sessions: int,
    outcome_field: str,
) -> tuple[DailyFactorEvidence, ...]:
    """Lazily read one population's persisted daily evidence after a valid selection."""

    source = catalog.population(population)
    if source is None or not source.daily_path.is_file():
        return ()
    required = frozenset({
        "signal_date", "factor", "horizon_sessions", "outcome_field", "rank_ic",
        "high_minus_low_mean_spread", "identity",
    })
    try:
        rows = _read_csv(source.daily_path, required)
        return tuple(
            DailyFactorEvidence(
                _text(row, "signal_date"),
                _optional_float(row, "rank_ic"),
                _optional_float(row, "high_minus_low_mean_spread"),
                _text(row, "identity"),
            )
            for row in rows
            if _text(row, "factor") == factor
            and _integer(row, "horizon_sessions") == horizon_sessions
            and _text(row, "outcome_field") == outcome_field
        )
    except (OSError, UnicodeError, TypeError, ValueError):
        return ()
