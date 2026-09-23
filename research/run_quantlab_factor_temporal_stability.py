from __future__ import annotations

"""Run the fixed, descriptive Quant Lab factor temporal-stability study."""

import argparse
import csv
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

# Support the documented direct-script invocation without installation.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database_coverage import build_database_coverage_index
from core.paths import PROJECT_ROOT, resolve_market_database_path
from quantlab.adapters.frozen_q70_candidate_records import build_frozen_q70_candidate_records
from quantlab.alpha import FrozenQ70Policy
from quantlab.catalog.market_data_snapshot import build_market_data_snapshot
from quantlab.evaluation import (
    FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1,
    FROZEN_Q70_VOLUME_RSI_TEMPORAL_STABILITY_V1,
    evaluate_candidate_factor_outcomes,
    evaluate_candidate_factor_temporal_stability,
)
from quantlab.features import PointInTimeUniverseContext
from quantlab.identity import canonical_json
from quantlab.outcomes import (
    FORWARD_CLOSE_RETURNS_5_10_20_V1,
    ForwardOutcomeStatus,
    label_candidate_forward_outcomes,
)
from research.run_quantlab_candidate_factor_diagnostics import (
    FROZEN_ENTRY_POLICY_IDENTITY,
    _ObservedPreparedFeatureCache,
    _entry_model,
)
from research.run_quantlab_factor_outcome_evaluation import (
    _ObservedMarketDataSnapshot,
    _csv_value,
    _date_text,
    _file_sha256,
    _is_within,
    _publish_atomic,
    _safe_json,
    _validate_output_target,
    _write_csv,
    _write_json,
)


RUNNER_CONTRACT = "quantlab.factor_temporal_stability_runner"
RUNNER_VERSION = "v1"

_SUMMARY_COLUMNS = (
    "factor", "horizon_sessions", "outcome_field",
    "blocks_with_defined_ic", "blocks_with_defined_spread",
    "blocks_with_at_least_20_defined_ic_dates",
    "blocks_with_at_least_20_defined_spread_dates",
    "positive_mean_ic_block_count", "zero_mean_ic_block_count",
    "negative_mean_ic_block_count", "positive_mean_spread_block_count",
    "zero_mean_spread_block_count", "negative_mean_spread_block_count",
    "minimum_block_mean_ic", "maximum_block_mean_ic", "range_block_mean_ic",
    "minimum_block_mean_spread", "maximum_block_mean_spread",
    "range_block_mean_spread", "largest_absolute_mean_ic_block",
    "largest_absolute_mean_spread_block", "largest_absolute_mean_ic_concentration",
    "largest_absolute_mean_spread_concentration",
    "coverage_sufficient_for_stability_review", "directionally_consistent_ic",
    "directionally_consistent_spread", "descriptive_temporal_support",
    "ordered_block_identity_count", "ordered_block_identities_sha256",
    "warning", "identity",
)
_BLOCK_COLUMNS = (
    "factor", "horizon_sessions", "outcome_field", "block_name",
    "block_start_date", "block_end_date", "total_signal_date_rows",
    "dates_with_available_labels", "ic_defined_date_count",
    "bucket_defined_date_count", "mean_daily_rank_ic", "median_daily_rank_ic",
    "population_std_daily_ic", "positive_ic_date_count", "zero_ic_date_count",
    "negative_ic_date_count", "positive_ic_rate",
    "mean_daily_high_minus_low_mean_spread",
    "median_daily_high_minus_low_mean_spread",
    "mean_daily_high_minus_low_median_spread",
    "median_daily_high_minus_low_median_spread",
    "positive_mean_spread_date_count", "zero_mean_spread_date_count",
    "negative_mean_spread_date_count", "positive_mean_spread_rate",
    "average_low_bucket_size", "average_high_bucket_size",
    "included_daily_identity_count", "included_daily_identities_sha256",
    "undefined_reason", "warning", "identity",
)
_COVERAGE_COLUMNS = (
    "block_name", "block_start_date", "block_end_date",
    "candidate_signal_date_count", "candidate_count", "phase_4_5_daily_row_count",
    "requested_summary_count", "summaries_with_defined_ic",
    "summaries_with_defined_spread", "summaries_ic_eligible_at_20_dates",
    "summaries_spread_eligible_at_20_dates", "block_identity_count",
    "block_identities_sha256",
)
_REQUIRED_FILENAMES = (
    "experiment_manifest.json", "temporal_stability_summary.csv",
    "temporal_stability_by_block.csv", "temporal_coverage.csv", "assumptions.md",
)


def _ordered_hash(values: tuple[str, ...]) -> str:
    return sha256(canonical_json(list(values))).hexdigest()


def _warning(values: tuple[str, ...]) -> str:
    return " | ".join(values)


def _output_path(output_root: str | Path | None, *, start_date: str, end_date: str) -> Path:
    if output_root is None:
        return (
            PROJECT_ROOT / "research_results" /
            f"quantlab_factor_temporal_stability_{start_date}_{end_date}"
        ).absolute()
    candidate = Path(output_root).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.absolute()


def _summary_rows(temporal_result: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in temporal_result.summaries:
        identities = tuple(block.identity for block in item.block_results)
        rows.append({
            "factor": item.factor,
            "horizon_sessions": item.horizon_sessions,
            "outcome_field": item.outcome_field,
            "blocks_with_defined_ic": item.blocks_with_defined_ic,
            "blocks_with_defined_spread": item.blocks_with_defined_spread,
            "blocks_with_at_least_20_defined_ic_dates": item.blocks_with_at_least_minimum_ic_dates,
            "blocks_with_at_least_20_defined_spread_dates": item.blocks_with_at_least_minimum_spread_dates,
            "positive_mean_ic_block_count": item.positive_mean_ic_block_count,
            "zero_mean_ic_block_count": item.zero_mean_ic_block_count,
            "negative_mean_ic_block_count": item.negative_mean_ic_block_count,
            "positive_mean_spread_block_count": item.positive_mean_spread_block_count,
            "zero_mean_spread_block_count": item.zero_mean_spread_block_count,
            "negative_mean_spread_block_count": item.negative_mean_spread_block_count,
            "minimum_block_mean_ic": item.minimum_block_mean_ic,
            "maximum_block_mean_ic": item.maximum_block_mean_ic,
            "range_block_mean_ic": item.range_block_mean_ic,
            "minimum_block_mean_spread": item.minimum_block_mean_spread,
            "maximum_block_mean_spread": item.maximum_block_mean_spread,
            "range_block_mean_spread": item.range_block_mean_spread,
            "largest_absolute_mean_ic_block": item.largest_absolute_mean_ic_block_name,
            "largest_absolute_mean_spread_block": item.largest_absolute_mean_spread_block_name,
            "largest_absolute_mean_ic_concentration": item.absolute_mean_ic_concentration,
            "largest_absolute_mean_spread_concentration": item.absolute_mean_spread_concentration,
            "coverage_sufficient_for_stability_review": item.coverage_sufficient_for_stability_review,
            "directionally_consistent_ic": item.directionally_consistent_ic,
            "directionally_consistent_spread": item.directionally_consistent_spread,
            "descriptive_temporal_support": item.descriptive_temporal_support,
            "ordered_block_identity_count": len(identities),
            "ordered_block_identities_sha256": _ordered_hash(identities),
            "warning": _warning(item.warnings),
            "identity": item.identity,
        })
    return rows


def _block_rows(temporal_result: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for summary in temporal_result.summaries:
        for item in summary.block_results:
            rows.append({
                "factor": item.factor,
                "horizon_sessions": item.horizon_sessions,
                "outcome_field": item.outcome_field,
                "block_name": item.block_name,
                "block_start_date": item.block_start_date,
                "block_end_date": item.block_end_date,
                "total_signal_date_rows": item.total_signal_date_rows,
                "dates_with_available_labels": item.dates_with_available_labels,
                "ic_defined_date_count": item.ic_defined_date_count,
                "bucket_defined_date_count": item.bucket_defined_date_count,
                "mean_daily_rank_ic": item.mean_daily_rank_ic,
                "median_daily_rank_ic": item.median_daily_rank_ic,
                "population_std_daily_ic": item.population_std_daily_rank_ic,
                "positive_ic_date_count": item.positive_ic_date_count,
                "zero_ic_date_count": item.zero_ic_date_count,
                "negative_ic_date_count": item.negative_ic_date_count,
                "positive_ic_rate": item.positive_ic_rate,
                "mean_daily_high_minus_low_mean_spread": item.mean_daily_high_minus_low_mean_spread,
                "median_daily_high_minus_low_mean_spread": item.median_daily_high_minus_low_mean_spread,
                "mean_daily_high_minus_low_median_spread": item.mean_daily_high_minus_low_median_spread,
                "median_daily_high_minus_low_median_spread": item.median_daily_high_minus_low_median_spread,
                "positive_mean_spread_date_count": item.positive_mean_spread_date_count,
                "zero_mean_spread_date_count": item.zero_mean_spread_date_count,
                "negative_mean_spread_date_count": item.negative_mean_spread_date_count,
                "positive_mean_spread_rate": item.positive_mean_spread_rate,
                "average_low_bucket_size": item.average_low_bucket_size,
                "average_high_bucket_size": item.average_high_bucket_size,
                "included_daily_identity_count": item.included_daily_identity_count,
                "included_daily_identities_sha256": item.included_daily_identities_sha256,
                "undefined_reason": item.undefined_reason,
                "warning": _warning(item.warnings),
                "identity": item.identity,
            })
    return rows


def _coverage_rows(candidate_batch: Any, evaluation: Any, temporal_result: Any) -> list[dict[str, Any]]:
    spec = FROZEN_Q70_VOLUME_RSI_TEMPORAL_STABILITY_V1
    rows: list[dict[str, Any]] = []
    for block in spec.blocks:
        signal_dates = tuple(
            item for item in candidate_batch.signal_dates
            if block.start_date <= item <= block.end_date
        )
        candidates = tuple(
            item for item in candidate_batch.candidates
            if block.start_date <= item.signal_date <= block.end_date
        )
        daily = tuple(
            item for item in evaluation.daily_evaluations
            if block.start_date <= item.signal_date <= block.end_date
        )
        block_results = tuple(
            item
            for summary in temporal_result.summaries
            for item in summary.block_results
            if item.block_name == block.name
        )
        identities = tuple(item.identity for item in block_results)
        rows.append({
            "block_name": block.name,
            "block_start_date": block.start_date,
            "block_end_date": block.end_date,
            "candidate_signal_date_count": len(signal_dates),
            "candidate_count": len(candidates),
            "phase_4_5_daily_row_count": len(daily),
            "requested_summary_count": len(block_results),
            "summaries_with_defined_ic": sum(item.mean_daily_rank_ic is not None for item in block_results),
            "summaries_with_defined_spread": sum(
                item.mean_daily_high_minus_low_mean_spread is not None for item in block_results
            ),
            "summaries_ic_eligible_at_20_dates": sum(
                item.ic_defined_date_count >= spec.minimum_defined_dates_per_eligible_block
                for item in block_results
            ),
            "summaries_spread_eligible_at_20_dates": sum(
                item.bucket_defined_date_count >= spec.minimum_defined_dates_per_eligible_block
                for item in block_results
            ),
            "block_identity_count": len(identities),
            "block_identities_sha256": _ordered_hash(identities),
        })
    return rows


def _artifact_rows(candidate_batch: Any, evaluation: Any, temporal_result: Any):
    return {
        "temporal_stability_summary.csv": (_SUMMARY_COLUMNS, _summary_rows(temporal_result)),
        "temporal_stability_by_block.csv": (_BLOCK_COLUMNS, _block_rows(temporal_result)),
        "temporal_coverage.csv": (_COVERAGE_COLUMNS, _coverage_rows(candidate_batch, evaluation, temporal_result)),
    }


def _validate_reconciliation(
    candidate_batch: Any,
    outcomes: Any,
    evaluation: Any,
    temporal_result: Any,
    rows: dict[str, tuple[tuple[str, ...], list[dict[str, Any]]]],
) -> None:
    spec = FROZEN_Q70_VOLUME_RSI_TEMPORAL_STABILITY_V1
    if evaluation.source_candidate_batch_identity != candidate_batch.batch_identity:
        raise ValueError("Phase 4.5 candidate identity does not reconcile")
    if outcomes.source_candidate_batch_identity != candidate_batch.batch_identity:
        raise ValueError("outcome candidate identity does not reconcile")
    if evaluation.source_outcome_set_identity != outcomes.set_identity:
        raise ValueError("Phase 4.5 outcome identity does not reconcile")
    if temporal_result.source_evaluation_identity != evaluation.result_identity:
        raise ValueError("temporal source evaluation identity does not reconcile")
    if temporal_result.source_candidate_batch_identity != candidate_batch.batch_identity:
        raise ValueError("temporal source candidate identity does not reconcile")
    if temporal_result.source_outcome_set_identity != outcomes.set_identity:
        raise ValueError("temporal source outcome identity does not reconcile")
    if temporal_result.temporal_stability_spec_fingerprint != spec.fingerprint:
        raise ValueError("temporal specification fingerprint does not reconcile")
    expected_keys = tuple(
        (factor, horizon, outcome)
        for factor in spec.factors
        for horizon in spec.horizons
        for outcome in spec.outcome_fields
    )
    actual_keys = tuple(
        (item.factor, item.horizon_sessions, item.outcome_field)
        for item in temporal_result.summaries
    )
    if actual_keys != expected_keys or len(actual_keys) != 12:
        raise ValueError("temporal summary combinations do not reconcile")
    block_keys = tuple(
        (item.factor, item.horizon_sessions, item.outcome_field, block.block_name)
        for item in temporal_result.summaries
        for block in item.block_results
    )
    expected_block_keys = tuple(
        (*key, block.name) for key in expected_keys for block in spec.blocks
    )
    if block_keys != expected_block_keys or len(block_keys) != 48:
        raise ValueError("temporal block combinations do not reconcile")
    if any(len(item.block_results) != 4 for item in temporal_result.summaries):
        raise ValueError("every temporal summary must contain four blocks")
    if len(rows["temporal_coverage.csv"][1]) != 4:
        raise ValueError("temporal coverage must contain four rows")
    if len(outcomes.outcomes) != candidate_batch.candidate_count * len(spec.horizons):
        raise ValueError("outcome cardinality does not reconcile")
    if tuple(outcomes.requested_horizons) != spec.horizons:
        raise ValueError("outcome horizons do not reconcile")
    selected_daily = {
        (item.signal_date, item.factor, item.horizon_sessions, item.outcome_field)
        for item in evaluation.daily_evaluations
        if item.factor in spec.factors
        and item.horizon_sessions in spec.horizons
        and item.outcome_field in spec.outcome_fields
    }
    expected_daily = {
        (signal_date, factor, horizon, outcome)
        for signal_date in candidate_batch.signal_dates
        for factor in spec.factors
        for horizon in spec.horizons
        for outcome in spec.outcome_fields
    }
    if selected_daily != expected_daily:
        raise ValueError("Phase 4.5 daily keys do not cover the temporal specification exactly")
    summary_rows = rows["temporal_stability_summary.csv"][1]
    block_rows = rows["temporal_stability_by_block.csv"][1]
    for projected, immutable in zip(summary_rows, temporal_result.summaries, strict=True):
        identities = tuple(item.identity for item in immutable.block_results)
        if projected["identity"] != immutable.identity:
            raise ValueError("summary projection identity does not reconcile")
        if projected["ordered_block_identity_count"] != len(identities):
            raise ValueError("summary block identity count does not reconcile")
        if projected["ordered_block_identities_sha256"] != _ordered_hash(identities):
            raise ValueError("summary block identity hash does not reconcile")
    immutable_blocks = tuple(
        item for summary in temporal_result.summaries for item in summary.block_results
    )
    if tuple(row["identity"] for row in block_rows) != tuple(item.identity for item in immutable_blocks):
        raise ValueError("block projection identities do not reconcile")
    for projected, immutable in zip(block_rows, immutable_blocks, strict=True):
        if projected["included_daily_identity_count"] != immutable.included_daily_identity_count:
            raise ValueError("block daily identity count does not reconcile")
        if projected["included_daily_identities_sha256"] != immutable.included_daily_identities_sha256:
            raise ValueError("block daily identity hash does not reconcile")
    for row, block in zip(rows["temporal_coverage.csv"][1], spec.blocks, strict=True):
        expected_dates = sum(block.start_date <= item <= block.end_date for item in candidate_batch.signal_dates)
        expected_candidates = sum(
            block.start_date <= item.signal_date <= block.end_date
            for item in candidate_batch.candidates
        )
        if row["candidate_signal_date_count"] != expected_dates or row["candidate_count"] != expected_candidates:
            raise ValueError("coverage candidate counts do not reconcile")


def _validate_emitted(
    directory: Path,
    rows: dict[str, tuple[tuple[str, ...], list[dict[str, Any]]]],
) -> None:
    manifest = json.loads((directory / "experiment_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("completion_status") != "complete":
        raise ValueError("experiment manifest is not complete")
    for filename, (columns, expected_rows) in rows.items():
        with (directory / filename).open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            actual_rows = list(reader)
        if tuple(reader.fieldnames or ()) != columns:
            raise ValueError(f"emitted CSV schema mismatch: {filename}")
        if len(actual_rows) != len(expected_rows):
            raise ValueError(f"emitted CSV row count mismatch: {filename}")
        if any("identities" in column and column not in {
            "ordered_block_identities_sha256", "included_daily_identities_sha256",
            "block_identities_sha256",
        } for column in columns):
            raise ValueError(f"full identity collections are forbidden in CSV: {filename}")
    actual = tuple(sorted(path.name for path in directory.iterdir()))
    if actual != tuple(sorted(_REQUIRED_FILENAMES)):
        raise ValueError("experiment artifact set is incomplete")


_ASSUMPTIONS = """# Assumptions and limitations

- Database coverage represents local data availability, not historical VN100 membership.
- Candidates are already conditioned on entry, Paper state, and Frozen-Q70 acceptance.
- Forward horizons overlap, so observations are not statistically independent.
- Raw and excess daily rank IC are not independent evidence.
- The four temporal blocks were fixed before block-level result inspection.
- The 20-date rule is a descriptive coverage threshold.
- Temporal-support flags are descriptive and are not statistical-significance claims.
- No portfolio performance, costs, capacity, turnover, or drawdown is evaluated.
- No factor weight or production strategy change is authorized.
"""


def run_factor_temporal_stability(
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
    spec = FROZEN_Q70_VOLUME_RSI_TEMPORAL_STABILITY_V1
    start = _date_text(start_date, name="start_date")
    end = _date_text(end_date, name="end_date")
    if (start, end) != (spec.overall_start_date, spec.overall_end_date):
        raise ValueError(
            "this runner requires the exact built-in temporal boundary "
            f"{spec.overall_start_date} through {spec.overall_end_date}"
        )
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
        raise ValueError("database coverage produced no candidate symbols")
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
    outcomes = label_candidate_forward_outcomes(
        snapshot, candidate_batch, FORWARD_CLOSE_RETURNS_5_10_20_V1,
    )
    outcome_label_load_count = snapshot.load_ohlcv_count - candidate_feature_load_count
    evaluation = evaluate_candidate_factor_outcomes(
        candidate_batch, outcomes, FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1,
    )
    temporal_result = evaluate_candidate_factor_temporal_stability(evaluation, spec)

    frozen_q70_fingerprint = FrozenQ70Policy().fingerprint
    expected_provenance = (
        base_snapshot.snapshot_id, FROZEN_ENTRY_POLICY_IDENTITY,
        frozen_q70_fingerprint, universe_context.membership_identity,
    )
    actual_provenance = (
        candidate_batch.snapshot_id, candidate_batch.entry_policy_identity,
        candidate_batch.q70_policy_fingerprint, candidate_batch.universe_membership_identity,
    )
    if actual_provenance != expected_provenance:
        raise ValueError("candidate provenance does not match runner configuration")
    if (candidate_batch.start_date, candidate_batch.through_date) != (start, end):
        raise ValueError("candidate date bounds do not reconcile")
    if outcomes.outcome_spec_fingerprint != FORWARD_CLOSE_RETURNS_5_10_20_V1.fingerprint:
        raise ValueError("outcome spec fingerprint does not reconcile")

    rows = _artifact_rows(candidate_batch, evaluation, temporal_result)
    _validate_reconciliation(candidate_batch, outcomes, evaluation, temporal_result, rows)
    if _file_sha256(database) != database_digest:
        raise RuntimeError("market database changed during read-only evaluation")

    status_counts = {
        str(horizon): {
            status.value: outcomes.status_counts_by_horizon[horizon][status]
            for status in ForwardOutcomeStatus
        }
        for horizon in spec.horizons
    }
    cache_manifest = {
        "enabled": cache is not None,
        "root": None if resolved_cache is None else str(resolved_cache),
        "codec": None if cache is None else (cache_codec or "sqlite-json-v1"),
        "lookup_count": 0 if cache is None else cache.lookup_count,
        "hit": None if cache is None else cache.last_hit,
    }
    manifest = {
        "runner_contract": RUNNER_CONTRACT,
        "runner_version": RUNNER_VERSION,
        "completion_status": "complete",
        "completed": True,
        "database_path": str(database),
        "database_sha256": database_digest,
        "snapshot_id": base_snapshot.snapshot_id,
        "logical_content_fingerprint": base_snapshot.logical_content_fingerprint,
        "requested_start_date": start,
        "requested_end_date": end,
        "benchmark_symbol": benchmark,
        "universe_mode": "database_coverage",
        "minimum_history_sessions": minimum_history_sessions,
        "maximum_staleness_sessions": maximum_staleness_sessions,
        "universe_membership_identity": universe_context.membership_identity,
        "entry_policy_identity": FROZEN_ENTRY_POLICY_IDENTITY,
        "frozen_q70_policy_fingerprint": frozen_q70_fingerprint,
        "candidate_batch_identity": candidate_batch.batch_identity,
        "candidate_count": candidate_batch.candidate_count,
        "candidate_signal_date_count": len(candidate_batch.signal_dates),
        "outcome_spec_fingerprint": outcomes.outcome_spec_fingerprint,
        "outcome_set_identity": outcomes.set_identity,
        "outcome_count": len(outcomes.outcomes),
        "outcome_status_counts_by_horizon": status_counts,
        "phase_4_5_evaluation_spec_fingerprint": evaluation.evaluation_spec_fingerprint,
        "phase_4_5_evaluation_identity": evaluation.result_identity,
        "phase_4_7a_specification_fingerprint": spec.fingerprint,
        "phase_4_7a_temporal_result_identity": temporal_result.result_identity,
        "scope": {
            "factors": list(spec.factors),
            "horizons": list(spec.horizons),
            "outcome_fields": list(spec.outcome_fields),
            "temporal_blocks": [
                {"name": item.name, "start_date": item.start_date, "end_date": item.end_date}
                for item in spec.blocks
            ],
        },
        "cache": cache_manifest,
        "market_data_load_counts": {
            "candidate_feature_loads": candidate_feature_load_count,
            "outcome_label_loads": outcome_label_load_count,
            "total_snapshot_ohlcv_loads": snapshot.load_ohlcv_count,
        },
        "artifact_row_counts": {
            "temporal_stability_summary.csv": len(rows["temporal_stability_summary.csv"][1]),
            "temporal_stability_by_block.csv": len(rows["temporal_stability_by_block.csv"][1]),
            "temporal_coverage.csv": len(rows["temporal_coverage.csv"][1]),
        },
        "warnings": [
            "Raw and excess daily rank IC are not independent evidence.",
            "Descriptive temporal support is not statistical significance or production authorization.",
        ],
        "limitations": [
            "Database coverage is local data availability, not historical VN100 membership.",
            "Candidates are conditioned on entry, Paper state, and Frozen-Q70 acceptance.",
            "No portfolio performance, costs, capacity, turnover, or drawdown is evaluated.",
        ],
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent)).resolve()
    try:
        for filename, (columns, artifact_rows) in rows.items():
            _write_csv(temporary / filename, columns, artifact_rows)
        (temporary / "assumptions.md").write_text(
            _ASSUMPTIONS, encoding="utf-8", newline="\n",
        )
        _write_json(temporary / "experiment_manifest.json", manifest)
        _validate_emitted(temporary, rows)
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
        "outcome_set": outcomes,
        "evaluation": evaluation,
        "temporal_result": temporal_result,
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
    result = run_factor_temporal_stability(
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
