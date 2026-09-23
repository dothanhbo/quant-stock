from __future__ import annotations

"""Reproducible local Frozen-Q70 candidate-factor outcome evaluation."""

import argparse
import csv
from datetime import date
from hashlib import sha256
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Iterable
from uuid import uuid4

# Support the documented direct-script invocation without installation.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database_coverage import CoverageUniverseIndex, build_database_coverage_index
from core.paths import PROJECT_ROOT, resolve_market_database_path
from quantlab.adapters.frozen_q70_candidate_records import build_frozen_q70_candidate_records
from quantlab.alpha import FrozenQ70Policy
from quantlab.catalog.market_data_snapshot import MarketDataSnapshot, build_market_data_snapshot
from quantlab.evaluation import (
    FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1,
    CandidateFactorOutcomeEvaluationResult,
    evaluate_candidate_factor_outcomes,
)
from quantlab.features import PointInTimeUniverseContext
from quantlab.identity import canonical_json
from quantlab.outcomes import (
    FORWARD_CLOSE_RETURNS_5_10_20_V1,
    CandidateForwardOutcomeSet,
    ForwardOutcomeStatus,
    label_candidate_forward_outcomes,
)
from research.run_quantlab_candidate_factor_diagnostics import (
    FROZEN_ENTRY_POLICY_IDENTITY,
    _ObservedPreparedFeatureCache,
    _entry_model,
)


RUNNER_CONTRACT = "quantlab.factor_outcome_evaluation_runner"
RUNNER_VERSION = "v1"
INTERPRETATION_MINIMUM_DEFINED_DATES = 30

_SUMMARY_COLUMNS = (
    "factor", "horizon_sessions", "outcome_field", "total_signal_dates",
    "dates_with_available_labels", "ic_defined_date_count", "ic_coverage_pct",
    "mean_daily_rank_ic", "median_daily_rank_ic", "population_std_daily_ic",
    "positive_ic_date_count", "zero_ic_date_count", "negative_ic_date_count",
    "positive_ic_rate", "bucket_defined_date_count", "bucket_coverage_pct",
    "mean_daily_high_minus_low_mean_spread", "median_daily_high_minus_low_mean_spread",
    "mean_daily_high_minus_low_median_spread", "median_daily_high_minus_low_median_spread",
    "positive_spread_date_count", "zero_spread_date_count", "negative_spread_date_count",
    "positive_spread_rate", "average_low_bucket_size", "average_high_bucket_size",
    "included_daily_identity_count", "included_daily_identities_sha256",
    "warning", "identity",
)
_DAILY_COLUMNS = (
    "signal_date", "factor", "horizon_sessions", "outcome_field",
    "total_same_date_candidates", "available_labeled_candidates",
    "pairwise_finite_count", "factor_unique_count", "factor_constant",
    "rank_ic", "ic_undefined_reason", "low_bucket_count", "high_bucket_count",
    "low_bucket_mean_outcome", "low_bucket_median_outcome",
    "high_bucket_mean_outcome", "high_bucket_median_outcome",
    "high_minus_low_mean_spread", "high_minus_low_median_spread",
    "bucket_undefined_reason", "joined_input_identity", "identity",
)
_STATUS_COLUMNS = ("horizon_sessions", "status", "count", "percentage")
_COUNT_COLUMNS = (
    "signal_date", "horizon_sessions", "accepted_candidate_count",
    "coverage_universe_member_count", "available_outcome_count",
    "censored_outcome_count", "missing_outcome_count",
)
_COVERAGE_COLUMNS = (
    "factor", "horizon_sessions", "outcome_field", "ic_defined_date_count",
    "bucket_defined_date_count", "total_signal_dates", "ic_coverage_pct",
    "bucket_coverage_pct", "minimum_defined_dates_rule",
    "sufficient_for_interpretation",
)
_REQUIRED_FILENAMES = (
    "experiment_manifest.json",
    "factor_outcome_summary.csv",
    "factor_outcome_by_date.csv",
    "outcome_status_counts.csv",
    "candidate_counts_by_date.csv",
    "evaluation_coverage_summary.csv",
    "assumptions.md",
)
_SUMMARY_COMPACT_IDENTITY_COLUMNS = (
    "included_daily_identity_count", "included_daily_identities_sha256",
)
_SUMMARY_DIRECT_COLUMNS = tuple(
    column for column in _SUMMARY_COLUMNS
    if column not in _SUMMARY_COMPACT_IDENTITY_COLUMNS
)


class _ObservedMarketDataSnapshot:
    """Transparent runner-local load counter around one immutable snapshot."""

    def __init__(self, snapshot: MarketDataSnapshot) -> None:
        self._snapshot = snapshot
        self.load_ohlcv_count = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._snapshot, name)

    def load_ohlcv(self, *args: Any, **kwargs: Any):
        self.load_ohlcv_count += 1
        return self._snapshot.load_ohlcv(*args, **kwargs)


def _date_text(value: str, *, name: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an ISO date") from exc


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _output_path(output_root: str | Path | None, *, start_date: str, end_date: str) -> Path:
    if output_root is None:
        return (
            PROJECT_ROOT / "research_results" /
            f"quantlab_factor_outcome_evaluation_{start_date}_{end_date}"
        ).absolute()
    candidate = Path(output_root).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.absolute()


def _validate_output_target(path: Path, *, database_path: Path, overwrite: bool) -> None:
    if path.is_symlink():
        raise ValueError("output_root must not be a symlink")
    resolved = path.resolve()
    repository = PROJECT_ROOT.resolve()
    forbidden_exact = {
        Path(resolved.anchor), repository, repository.parent,
        repository / "research_results", database_path.parent.resolve(),
    }
    forbidden_trees = (repository / ".git", repository / "data", repository / "research")
    if resolved in forbidden_exact or any(_is_within(resolved, tree.resolve()) for tree in forbidden_trees):
        raise ValueError("output_root must be a dedicated experiment directory")
    if resolved.exists():
        if not resolved.is_dir():
            raise ValueError("output_root exists but is not a directory")
        if not overwrite:
            raise FileExistsError(f"output directory already exists: {resolved}")


def _safe_json(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_safe_json(item) for item in value]
    if hasattr(value, "item"):
        return _safe_json(value.item())
    raise TypeError(f"unsupported manifest value: {type(value).__name__}")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(
            _safe_json(payload), ensure_ascii=False, sort_keys=True,
            indent=2, allow_nan=False,
        ) + "\n",
        encoding="utf-8",
    )


def _csv_value(value: Any) -> Any:
    if value is None or isinstance(value, float) and not math.isfinite(value):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _write_csv(path: Path, columns: tuple[str, ...], rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=list(columns), extrasaction="raise", lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _csv_value(row.get(column)) for column in columns})


def _status_counts_by_date(outcomes: CandidateForwardOutcomeSet) -> dict[tuple[str, int], dict[ForwardOutcomeStatus, int]]:
    result: dict[tuple[str, int], dict[ForwardOutcomeStatus, int]] = {}
    for outcome in outcomes.outcomes:
        key = (outcome.signal_date, outcome.horizon_sessions)
        counts = result.setdefault(key, {status: 0 for status in ForwardOutcomeStatus})
        counts[outcome.status] += 1
    return result


def _included_daily_identities_sha256(identities: tuple[str, ...]) -> str:
    """Hash the ordered immutable identity tuple without expanding it in CSV."""
    return sha256(canonical_json(list(identities))).hexdigest()


def _artifact_rows(
    evaluation: CandidateFactorOutcomeEvaluationResult,
    outcomes: CandidateForwardOutcomeSet,
    coverage: CoverageUniverseIndex,
    candidate_batch: Any,
) -> dict[str, tuple[tuple[str, ...], list[dict[str, Any]]]]:
    summaries = [
        {
            **{column: getattr(item, column) for column in _SUMMARY_DIRECT_COLUMNS},
            "included_daily_identity_count": len(item.included_daily_identities),
            "included_daily_identities_sha256": _included_daily_identities_sha256(
                item.included_daily_identities,
            ),
        }
        for item in evaluation.summaries
    ]
    daily = [
        {column: getattr(item, column) for column in _DAILY_COLUMNS}
        for item in evaluation.daily_evaluations
    ]
    statuses = []
    for horizon in FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1.horizons:
        counts = evaluation.status_counts_by_horizon[horizon]
        total = sum(counts.values())
        for status in ForwardOutcomeStatus:
            count = counts[status]
            statuses.append({
                "horizon_sessions": horizon,
                "status": status.value,
                "count": count,
                "percentage": 0.0 if total == 0 else count / total * 100.0,
            })

    date_statuses = _status_counts_by_date(outcomes)
    candidate_counts = {
        signal_date: len(candidate_batch.candidates_for_signal_date(signal_date))
        for signal_date in candidate_batch.signal_dates
    }
    counts_by_date = []
    for signal_date in candidate_batch.signal_dates:
        for horizon in FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1.horizons:
            counts = date_statuses[(signal_date, horizon)]
            missing = sum(
                count for status, count in counts.items()
                if status not in {
                    ForwardOutcomeStatus.AVAILABLE,
                    ForwardOutcomeStatus.CENSORED_AFTER_DATA_END,
                }
            )
            counts_by_date.append({
                "signal_date": signal_date,
                "horizon_sessions": horizon,
                "accepted_candidate_count": candidate_counts[signal_date],
                "coverage_universe_member_count": len(coverage.members_as_of(signal_date)),
                "available_outcome_count": counts[ForwardOutcomeStatus.AVAILABLE],
                "censored_outcome_count": counts[ForwardOutcomeStatus.CENSORED_AFTER_DATA_END],
                "missing_outcome_count": missing,
            })

    coverage_rows = [
        {
            "factor": item.factor,
            "horizon_sessions": item.horizon_sessions,
            "outcome_field": item.outcome_field,
            "ic_defined_date_count": item.ic_defined_date_count,
            "bucket_defined_date_count": item.bucket_defined_date_count,
            "total_signal_dates": item.total_signal_dates,
            "ic_coverage_pct": item.ic_coverage_pct,
            "bucket_coverage_pct": item.bucket_coverage_pct,
            "minimum_defined_dates_rule": INTERPRETATION_MINIMUM_DEFINED_DATES,
            "sufficient_for_interpretation": (
                item.ic_defined_date_count >= INTERPRETATION_MINIMUM_DEFINED_DATES
                and item.bucket_defined_date_count >= INTERPRETATION_MINIMUM_DEFINED_DATES
            ),
        }
        for item in evaluation.summaries
    ]
    return {
        "factor_outcome_summary.csv": (_SUMMARY_COLUMNS, summaries),
        "factor_outcome_by_date.csv": (_DAILY_COLUMNS, daily),
        "outcome_status_counts.csv": (_STATUS_COLUMNS, statuses),
        "candidate_counts_by_date.csv": (_COUNT_COLUMNS, counts_by_date),
        "evaluation_coverage_summary.csv": (_COVERAGE_COLUMNS, coverage_rows),
    }


def _validate_reconciliation(
    candidate_batch: Any,
    outcomes: CandidateForwardOutcomeSet,
    evaluation: CandidateFactorOutcomeEvaluationResult,
    rows: dict[str, tuple[tuple[str, ...], list[dict[str, Any]]]],
) -> None:
    spec = FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1
    expected_outcomes = candidate_batch.candidate_count * len(spec.horizons)
    if len(outcomes.outcomes) != expected_outcomes:
        raise ValueError("outcome cardinality does not equal candidate count × horizon count")
    if outcomes.source_candidate_batch_identity != candidate_batch.batch_identity:
        raise ValueError("outcome source candidate identity does not reconcile")
    if outcomes.requested_horizons != spec.horizons:
        raise ValueError("outcome horizons do not reconcile with evaluation spec")
    for horizon in spec.horizons:
        calculated = {
            status: sum(
                item.horizon_sessions == horizon and item.status is status
                for item in outcomes.outcomes
            )
            for status in ForwardOutcomeStatus
        }
        if dict(outcomes.status_counts_by_horizon[horizon]) != calculated:
            raise ValueError(f"outcome status counts do not reconcile for horizon {horizon}")
        if dict(evaluation.status_counts_by_horizon[horizon]) != calculated:
            raise ValueError(f"evaluation status counts do not reconcile for horizon {horizon}")
    if evaluation.source_candidate_batch_identity != candidate_batch.batch_identity:
        raise ValueError("evaluation source candidate identity does not reconcile")
    if evaluation.source_outcome_set_identity != outcomes.set_identity:
        raise ValueError("evaluation source outcome identity does not reconcile")
    if evaluation.evaluation_spec_fingerprint != spec.fingerprint:
        raise ValueError("evaluation spec fingerprint does not reconcile")
    expected_summaries = len(spec.factors) * len(spec.horizons) * len(spec.outcome_fields)
    if len(evaluation.summaries) != expected_summaries:
        raise ValueError("evaluation summary cardinality does not reconcile")
    expected_order = tuple(
        (factor, horizon, outcome_field)
        for factor in spec.factors
        for horizon in spec.horizons
        for outcome_field in spec.outcome_fields
    )
    actual_order = tuple(
        (item.factor, item.horizon_sessions, item.outcome_field)
        for item in evaluation.summaries
    )
    if actual_order != expected_order:
        raise ValueError("evaluation summaries are not in canonical spec order")
    expected_daily = len(candidate_batch.signal_dates) * expected_summaries
    if len(evaluation.daily_evaluations) != expected_daily:
        raise ValueError("daily evaluation cardinality does not reconcile")
    expected_daily_order = tuple(
        (signal_date, factor, horizon, outcome_field)
        for signal_date in candidate_batch.signal_dates
        for factor in spec.factors
        for horizon in spec.horizons
        for outcome_field in spec.outcome_fields
    )
    actual_daily_order = tuple(
        (item.signal_date, item.factor, item.horizon_sessions, item.outcome_field)
        for item in evaluation.daily_evaluations
    )
    if actual_daily_order != expected_daily_order:
        raise ValueError("daily evaluations are not in canonical date/spec order")
    if len(rows["factor_outcome_summary.csv"][1]) != len(evaluation.summaries):
        raise ValueError("summary artifact rows do not reconcile")
    if len(rows["factor_outcome_by_date.csv"][1]) != len(evaluation.daily_evaluations):
        raise ValueError("daily artifact rows do not reconcile")
    for signal_date in candidate_batch.signal_dates:
        expected = sum(item.signal_date == signal_date for item in candidate_batch.candidates)
        if len(candidate_batch.candidates_for_signal_date(signal_date)) != expected:
            raise ValueError(f"candidate per-date count does not reconcile: {signal_date}")
        for horizon in spec.horizons:
            outcome_count = sum(
                item.signal_date == signal_date and item.horizon_sessions == horizon
                for item in outcomes.outcomes
            )
            if outcome_count != expected:
                raise ValueError(
                    f"candidate/outcome per-date count does not reconcile: {signal_date}/{horizon}"
                )


def _validate_emitted(
    directory: Path,
    rows: dict[str, tuple[tuple[str, ...], list[dict[str, Any]]]],
    evaluation: CandidateFactorOutcomeEvaluationResult,
) -> None:
    manifest = json.loads((directory / "experiment_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("completion_status") != "complete":
        raise ValueError("experiment manifest is not complete")
    emitted_rows: dict[str, list[dict[str, str]]] = {}
    for filename, (columns, expected_rows) in rows.items():
        with (directory / filename).open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            actual_rows = list(reader)
            if tuple(reader.fieldnames or ()) != columns:
                raise ValueError(f"emitted CSV schema mismatch: {filename}")
            if len(actual_rows) != len(expected_rows):
                raise ValueError(f"emitted CSV row count mismatch: {filename}")
            emitted_rows[filename] = actual_rows
    summary_columns = rows["factor_outcome_summary.csv"][0]
    if "included_daily_identities" in summary_columns:
        raise ValueError("summary artifact must not contain the full daily identity collection")
    if not set(_SUMMARY_COMPACT_IDENTITY_COLUMNS).issubset(summary_columns):
        raise ValueError("summary artifact is missing compact daily identity audit fields")
    summary_rows = emitted_rows["factor_outcome_summary.csv"]
    if len(summary_rows) != len(evaluation.summaries):
        raise ValueError("emitted summary rows do not reconcile with immutable summaries")
    for row, summary in zip(summary_rows, evaluation.summaries, strict=True):
        if int(row["included_daily_identity_count"]) != len(summary.included_daily_identities):
            raise ValueError("summary daily identity count does not reconcile")
        if row["included_daily_identities_sha256"] != _included_daily_identities_sha256(
            summary.included_daily_identities,
        ):
            raise ValueError("summary daily identity hash does not reconcile")
        if row["identity"] != summary.identity:
            raise ValueError("summary identity changed during artifact projection")
        for column in _SUMMARY_DIRECT_COLUMNS:
            expected = str(_csv_value(getattr(summary, column)))
            if row[column] != expected:
                raise ValueError(f"summary artifact value does not reconcile: {column}")
    actual = tuple(sorted(path.name for path in directory.iterdir()))
    if actual != tuple(sorted(_REQUIRED_FILENAMES)):
        raise ValueError("experiment artifact set is incomplete")


def _publish_atomic(temporary: Path, output: Path) -> None:
    if not output.exists():
        temporary.replace(output)
        return
    backup = output.with_name(f".{output.name}.backup-{uuid4().hex}")
    output.replace(backup)
    try:
        temporary.replace(output)
    except Exception:
        if output.exists():
            shutil.rmtree(output)
        backup.replace(output)
        raise
    try:
        shutil.rmtree(backup)
    except Exception:
        if output.exists():
            shutil.rmtree(output)
        backup.replace(output)
        raise


_ASSUMPTIONS = """# Assumptions and limitations

- Forward labels are future-looking research outcomes; no future values enter candidate construction.
- Close-to-close forward returns are not tradable execution returns.
- No costs, slippage, fills, sizing, portfolio simulation, Trade objects, or execution are included.
- No p-values, t-statistics, confidence intervals, Sharpe ratios, or statistical-significance claims are made.
- Forward horizons overlap, so daily observations are dependent.
- Diagnostics are selection-conditioned on accepted Frozen-Q70 candidates.
- The accepted-candidate cross-section is historically small; coverage is reported explicitly.
- Breadth is shared market context and is expected to be constant within a signal date.
- `quality_score` is mechanically derived from score, relative-strength-20D, and ADX percentiles.
- Database coverage is local data availability, not historical VN100 membership.
- Positive rank IC or bucket spread is descriptive and is not proof of persistent alpha.
- No factor weights were selected or optimized.
- Candidates near the immutable snapshot end may have censored forward outcomes; censoring remains in status counts.
"""


def run_factor_outcome_evaluation(
    *,
    database_path: str | Path | None,
    start_date: str,
    end_date: str,
    minimum_history_sessions: int = 50,
    maximum_staleness_sessions: int = 5,
    benchmark_symbol: str = "VNINDEX",
    output_root: str | Path | None = None,
    overwrite: bool = False,
    cache_root: str | Path | None = None,
    cache_codec: str | None = None,
) -> dict[str, Any]:
    start = _date_text(start_date, name="start_date")
    end = _date_text(end_date, name="end_date")
    if start > end:
        raise ValueError("start_date must be on or before end_date")
    if minimum_history_sessions < 0 or maximum_staleness_sessions < 0:
        raise ValueError("coverage thresholds must be non-negative")
    benchmark = str(benchmark_symbol).strip().upper()
    if not benchmark:
        raise ValueError("benchmark_symbol is required")
    database = resolve_market_database_path(database_path)
    if not database.is_file():
        raise FileNotFoundError(f"market database not found: {database}")
    output = _output_path(output_root, start_date=start, end_date=end)
    _validate_output_target(output, database_path=database, overwrite=overwrite)
    if cache_codec is not None and cache_root is None:
        raise ValueError("cache_codec requires an explicit cache_root")
    if cache_root is not None:
        resolved_cache = Path(cache_root).expanduser()
        if not resolved_cache.is_absolute():
            resolved_cache = PROJECT_ROOT / resolved_cache
        resolved_cache = resolved_cache.resolve()
        if (
            resolved_cache == database
            or _is_within(resolved_cache, output)
            or _is_within(output, resolved_cache)
        ):
            raise ValueError("cache_root must be separate from the database and experiment output")
    else:
        resolved_cache = None

    database_digest = _file_sha256(database)
    base_snapshot = build_market_data_snapshot(database)
    if base_snapshot.first_session_date is None or base_snapshot.last_session_date is None:
        raise ValueError("market snapshot contains no sessions")
    snapshot = _ObservedMarketDataSnapshot(base_snapshot)
    coverage = build_database_coverage_index(
        base_snapshot.first_session_date,
        end,
        minimum_history_sessions=minimum_history_sessions,
        maximum_staleness_sessions=maximum_staleness_sessions,
        database_path=database,
    )
    if not coverage.candidate_symbols:
        raise ValueError("database coverage produced no candidate symbols for the requested range")
    universe_context = PointInTimeUniverseContext.from_coverage_index(coverage)
    cache = None if resolved_cache is None else _ObservedPreparedFeatureCache(
        resolved_cache, codec=cache_codec,
    )
    candidate_batch = build_frozen_q70_candidate_records(
        snapshot,
        coverage.candidate_symbols,
        benchmark_symbol=benchmark,
        universe_context=universe_context,
        start_date=start,
        through_date=end,
        entry_model=_entry_model(),
        entry_policy_identity=FROZEN_ENTRY_POLICY_IDENTITY,
        market_context_identity=universe_context.membership_identity,
        feature_cache=cache,
    )
    candidate_feature_load_count = snapshot.load_ohlcv_count
    outcome_set = label_candidate_forward_outcomes(
        snapshot, candidate_batch, FORWARD_CLOSE_RETURNS_5_10_20_V1,
    )
    outcome_label_load_count = snapshot.load_ohlcv_count - candidate_feature_load_count
    evaluation = evaluate_candidate_factor_outcomes(
        candidate_batch, outcome_set, FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1,
    )

    frozen_q70_fingerprint = FrozenQ70Policy().fingerprint
    expected_provenance = (
        base_snapshot.snapshot_id,
        FROZEN_ENTRY_POLICY_IDENTITY,
        frozen_q70_fingerprint,
        universe_context.membership_identity,
    )
    actual_provenance = (
        candidate_batch.snapshot_id,
        candidate_batch.entry_policy_identity,
        candidate_batch.q70_policy_fingerprint,
        candidate_batch.universe_membership_identity,
    )
    if actual_provenance != expected_provenance:
        raise ValueError("candidate batch provenance does not match the validated runner configuration")
    if candidate_batch.start_date != start or candidate_batch.through_date != end:
        raise ValueError("candidate batch date bounds do not match the requested signal interval")
    if outcome_set.outcome_spec_fingerprint != FORWARD_CLOSE_RETURNS_5_10_20_V1.fingerprint:
        raise ValueError("outcome spec fingerprint does not reconcile")

    rows = _artifact_rows(evaluation, outcome_set, coverage, candidate_batch)
    _validate_reconciliation(candidate_batch, outcome_set, evaluation, rows)
    if _file_sha256(database) != database_digest:
        raise RuntimeError("market database changed during read-only evaluation")

    cache_manifest = {
        "enabled": cache is not None,
        "root": None if resolved_cache is None else str(resolved_cache),
        "codec": None if cache is None else (cache_codec or "sqlite-json-v1"),
        "lookup_count": 0 if cache is None else cache.lookup_count,
        "hit": None if cache is None else cache.last_hit,
    }
    status_manifest = {
        str(horizon): {
            status.value: evaluation.status_counts_by_horizon[horizon][status]
            for status in ForwardOutcomeStatus
        }
        for horizon in FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1.horizons
    }
    signal_dates = candidate_batch.signal_dates
    evaluation_spec = FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1
    manifest = {
        "runner_contract": RUNNER_CONTRACT,
        "runner_version": RUNNER_VERSION,
        "completion_status": "complete",
        "database_path": str(database),
        "database_sha256": database_digest,
        "snapshot_id": base_snapshot.snapshot_id,
        "logical_content_fingerprint": base_snapshot.logical_content_fingerprint,
        "requested_signal_start_date": start,
        "requested_signal_end_date": end,
        "effective_coverage_start_date": coverage.effective_start_date,
        "effective_coverage_end_date": coverage.effective_end_date,
        "first_candidate_signal_date": signal_dates[0] if signal_dates else None,
        "last_candidate_signal_date": signal_dates[-1] if signal_dates else None,
        "snapshot_last_session_date": base_snapshot.last_session_date,
        "label_data_availability_end_date": base_snapshot.last_session_date,
        "universe_mode": "database_coverage",
        "minimum_history_sessions": minimum_history_sessions,
        "maximum_staleness_sessions": maximum_staleness_sessions,
        "universe_membership_identity": universe_context.membership_identity,
        "benchmark_symbol": benchmark,
        "entry_policy_identity": FROZEN_ENTRY_POLICY_IDENTITY,
        "frozen_q70_policy_fingerprint": frozen_q70_fingerprint,
        "candidate_batch_identity": candidate_batch.batch_identity,
        "candidate_count": candidate_batch.candidate_count,
        "candidate_signal_date_count": len(signal_dates),
        "outcome_spec_fingerprint": FORWARD_CLOSE_RETURNS_5_10_20_V1.fingerprint,
        "outcome_set_identity": outcome_set.set_identity,
        "outcome_count": len(outcome_set.outcomes),
        "outcome_status_counts_by_horizon": status_manifest,
        "evaluation_spec_fingerprint": evaluation_spec.fingerprint,
        "evaluation_result_identity": evaluation.result_identity,
        "evaluation_summary_count": len(evaluation.summaries),
        "evaluation_daily_count": len(evaluation.daily_evaluations),
        "evaluation_semantics": {
            "minimum_cross_section_size": evaluation_spec.minimum_cross_section_size,
            "low_bucket_max_percentile": evaluation_spec.low_bucket_max_percentile,
            "high_bucket_min_percentile": evaluation_spec.high_bucket_min_percentile,
            "rank_method": evaluation_spec.rank_method,
            "rank_ic_method": evaluation_spec.rank_ic_method,
            "percentile_method": evaluation_spec.percentile_method,
            "bucket_spread_method": evaluation_spec.bucket_spread_method,
            "descriptive_coverage_minimum_defined_dates": INTERPRETATION_MINIMUM_DEFINED_DATES,
        },
        "market_data_load_counts": {
            "candidate_feature_loads": candidate_feature_load_count,
            "outcome_label_loads": outcome_label_load_count,
            "total_snapshot_ohlcv_loads": snapshot.load_ohlcv_count,
        },
        "cache": cache_manifest,
        "warnings": [
            "Forward labels intentionally use sessions after each candidate signal date.",
            "Candidates near the immutable snapshot end may be censored; censored outcomes remain counted.",
            "Forward horizons overlap; summaries are descriptive and make no significance claim.",
        ],
        "limitations": [
            "Database coverage is local data availability, not historical VN100 membership.",
            "Evaluations are conditioned on candidates accepted by the frozen Q70 policy.",
            "Close-to-close labels are not execution returns and contain no costs, fills, sizing, or portfolio simulation.",
            "No factor weights were selected or optimized.",
        ],
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent)).resolve()
    try:
        for filename, (columns, artifact_rows) in rows.items():
            _write_csv(temporary / filename, columns, artifact_rows)
        (temporary / "assumptions.md").write_text(_ASSUMPTIONS, encoding="utf-8", newline="\n")
        _write_json(temporary / "experiment_manifest.json", manifest)
        _validate_emitted(temporary, rows, evaluation)
        if _file_sha256(database) != database_digest:
            raise RuntimeError("market database changed before experiment publication")
        _publish_atomic(temporary, output)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return {
        "output_root": output,
        "manifest": manifest,
        "candidate_batch": candidate_batch,
        "outcome_set": outcome_set,
        "evaluation": evaluation,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-path", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--minimum-history-sessions", type=int, default=50)
    parser.add_argument("--maximum-staleness-sessions", type=int, default=5)
    parser.add_argument("--benchmark-symbol", default="VNINDEX")
    parser.add_argument("--output-root")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--cache-root")
    parser.add_argument("--cache-codec", choices=("sqlite-json-v1", "npz_numeric_v1"))
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    result = run_factor_outcome_evaluation(
        database_path=arguments.database_path,
        start_date=arguments.start_date,
        end_date=arguments.end_date,
        minimum_history_sessions=arguments.minimum_history_sessions,
        maximum_staleness_sessions=arguments.maximum_staleness_sessions,
        benchmark_symbol=arguments.benchmark_symbol,
        output_root=arguments.output_root,
        overwrite=arguments.overwrite,
        cache_root=arguments.cache_root,
        cache_codec=arguments.cache_codec,
    )
    print(result["output_root"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
