from __future__ import annotations

"""Publish neutral whole-universe point-in-time factor diagnostics from local data."""

import argparse
import csv
from datetime import date
from enum import Enum
from hashlib import sha256
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Iterable, Mapping
from uuid import uuid4

# Support direct invocation from the repository root without installation.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database_coverage import build_database_coverage_index
from core.paths import PROJECT_ROOT, resolve_market_database_path
from quantlab.catalog.market_data_snapshot import build_market_data_snapshot
from quantlab.evaluation import (
    NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1,
    evaluate_point_in_time_panel_factors,
)
from quantlab.features import PointInTimeUniverseContext, PreparedFeatureCache
from quantlab.identity import canonical_json
from quantlab.panels import (
    NEUTRAL_RESEARCH_FEATURE_PANEL_V1,
    attach_features_to_observation_index,
    build_point_in_time_observation_index,
    build_point_in_time_outcome_panel,
    build_point_in_time_research_dataset,
    prepare_neutral_research_feature_source,
)


RUNNER_CONTRACT = "quantlab.neutral_panel_factor_evaluation_runner"
RUNNER_VERSION = "v1"
CACHE_DEFAULT_CODEC = "npz_numeric_v1"
REVIEW_MINIMUM_DEFINED_DATES = 30
REVIEW_MINIMUM_AVERAGE_CROSS_SECTION = 20.0

_SUMMARY_COLUMNS = (
    "factor", "factor_direction", "horizon_sessions", "outcome_field",
    "total_signal_dates", "dates_with_any_pairwise_finite_observations",
    "ic_defined_date_count", "ic_coverage_pct", "mean_daily_rank_ic",
    "median_daily_rank_ic", "population_std_daily_ic", "positive_ic_date_count",
    "zero_ic_date_count", "negative_ic_date_count", "positive_ic_rate",
    "bucket_defined_date_count", "bucket_coverage_pct",
    "mean_daily_high_minus_low_mean_spread",
    "median_daily_high_minus_low_mean_spread",
    "mean_daily_high_minus_low_median_spread",
    "median_daily_high_minus_low_median_spread",
    "positive_mean_spread_date_count", "zero_mean_spread_date_count",
    "negative_mean_spread_date_count", "positive_mean_spread_rate",
    "average_low_bucket_size", "average_high_bucket_size",
    "total_eligible_observations", "average_eligible_cross_section_size",
    "factor_availability_coverage_pct", "outcome_availability_coverage_pct",
    "warnings", "included_daily_identity_count",
    "included_daily_identities_sha256", "identity",
)
_DAILY_COLUMNS = (
    "factor", "factor_direction", "horizon_sessions", "outcome_field",
    "signal_date", "total_observation_count", "factor_available_count",
    "outcome_available_count", "factor_usable_count", "outcome_usable_count",
    "pairwise_finite_eligible_count", "excluded_for_factor_count",
    "excluded_for_outcome_count", "excluded_for_both_count",
    "factor_nonfinite_available_count", "outcome_nonfinite_available_count",
    "rank_ic", "ic_undefined_reason", "low_bucket_count", "high_bucket_count",
    "high_minus_low_mean_spread", "high_minus_low_median_spread",
    "bucket_undefined_reason", "identity",
)
_COVERAGE_COLUMNS = (
    "factor", "horizon_sessions", "outcome_field", "total_signal_dates",
    "ic_defined_date_count", "ic_coverage_pct", "spread_defined_date_count",
    "spread_coverage_pct", "average_eligible_cross_section_size",
    "factor_availability_coverage_pct", "outcome_availability_coverage_pct",
    "sparse_or_undefined_warning", "descriptive_review_eligible",
)
_OBSERVATION_COLUMNS = (
    "signal_date", "universe_membership_count", "observation_row_count",
    "available_exact_market_row_count", "missing_exact_market_row_count",
    "complete_feature_row_count", "rows_with_any_available_outcome",
    "fully_labeled_outcome_row_count",
)
_REQUIRED_FILENAMES = (
    "experiment_manifest.json",
    "factor_summary.csv",
    "factor_by_date.csv",
    "factor_coverage.csv",
    "observation_counts_by_date.csv",
    "assumptions.md",
)

_LIMITATIONS = (
    "Database coverage represents local data availability, not historical VN100 membership.",
    "Results use the complete PIT observation population without strategy or candidate filtering.",
    "Outcomes are future-looking and for offline research only.",
    "Daily cross-sections receive equal weight.",
    "Results are descriptive and do not prove causality, tradability, or portfolio utility.",
    "Stock-return and excess-return daily rank IC are not independent evidence.",
    "No transaction costs, liquidity constraints, or portfolio construction are modeled.",
)
_ASSUMPTIONS = "# Assumptions and limitations\n\n" + "\n".join(
    f"- {item}" for item in _LIMITATIONS
) + "\n"


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


def _identity_collection_sha256(values: tuple[str, ...]) -> str:
    return sha256(canonical_json(list(values))).hexdigest()


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _output_path(
    output_root: str | Path | None,
    *,
    start_date: str,
    end_date: str,
) -> Path:
    if output_root is None:
        return (
            PROJECT_ROOT / "research_results" /
            f"quantlab_neutral_panel_factor_evaluation_{start_date}_{end_date}"
        ).resolve()
    candidate = Path(output_root).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()


def _validate_output_target(path: Path, *, database_path: Path, overwrite: bool) -> None:
    if path.is_symlink():
        raise ValueError("output_root must not be a symlink")
    resolved = path.resolve()
    repository = PROJECT_ROOT.resolve()
    forbidden_exact = {
        Path(resolved.anchor),
        repository,
        repository / ".git",
        repository / "data",
        repository / "research",
        repository / "research_results",
        database_path.resolve(),
    }
    forbidden_trees = (repository / ".git", repository / "data", repository / "research")
    if resolved in forbidden_exact or any(
        _is_within(resolved, tree.resolve()) for tree in forbidden_trees
    ):
        raise ValueError("output_root must be a dedicated experiment directory")
    if resolved.exists():
        if not resolved.is_dir():
            raise ValueError("output_root exists but is not a directory")
        if not overwrite:
            raise FileExistsError(f"output directory already exists: {resolved}")


def _resolved_cache_root(
    cache_root: str | Path | None,
    *,
    output: Path,
    database: Path,
) -> Path | None:
    if cache_root is None:
        return None
    candidate = Path(cache_root).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    resolved = candidate.resolve()
    if (
        resolved == database.resolve()
        or _is_within(resolved, output)
        or _is_within(output, resolved)
    ):
        raise ValueError("cache_root and output_root must not contain one another")
    return resolved


def _safe_json(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _safe_json(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_safe_json(item) for item in value]
    if hasattr(value, "item"):
        return _safe_json(value.item())
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(
            _safe_json(payload),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ) + "\n",
        encoding="utf-8",
    )


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _write_csv(
    path: Path,
    columns: tuple[str, ...],
    rows: Iterable[Mapping[str, Any]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=list(columns),
            extrasaction="raise",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _csv_value(row.get(column)) for column in columns})


def _artifact_rows(
    evaluation: Any,
    observation_index: Any,
    feature_panel: Any,
    outcome_panel: Any,
) -> dict[str, tuple[tuple[str, ...], list[dict[str, Any]]]]:
    summary_rows = []
    for item in evaluation.summaries:
        summary_rows.append({
            **{
                column: (
                    " | ".join(item.warnings)
                    if column == "warnings"
                    else getattr(item, column)
                )
                for column in _SUMMARY_COLUMNS
                if column not in {
                    "included_daily_identity_count",
                    "included_daily_identities_sha256",
                }
            },
            "included_daily_identity_count": len(item.included_daily_identities),
            "included_daily_identities_sha256": _identity_collection_sha256(
                item.included_daily_identities,
            ),
        })
    daily_rows = [
        {column: getattr(item, column) for column in _DAILY_COLUMNS}
        for item in evaluation.daily_evaluations
    ]
    coverage_rows = [
        {
            "factor": item.factor,
            "horizon_sessions": item.horizon_sessions,
            "outcome_field": item.outcome_field,
            "total_signal_dates": item.total_signal_dates,
            "ic_defined_date_count": item.ic_defined_date_count,
            "ic_coverage_pct": item.ic_coverage_pct,
            "spread_defined_date_count": item.bucket_defined_date_count,
            "spread_coverage_pct": item.bucket_coverage_pct,
            "average_eligible_cross_section_size": item.average_eligible_cross_section_size,
            "factor_availability_coverage_pct": item.factor_availability_coverage_pct,
            "outcome_availability_coverage_pct": item.outcome_availability_coverage_pct,
            "sparse_or_undefined_warning": " | ".join(item.warnings),
            "descriptive_review_eligible": (
                item.ic_defined_date_count >= REVIEW_MINIMUM_DEFINED_DATES
                and item.bucket_defined_date_count >= REVIEW_MINIMUM_DEFINED_DATES
                and item.average_eligible_cross_section_size is not None
                and item.average_eligible_cross_section_size
                >= REVIEW_MINIMUM_AVERAGE_CROSS_SECTION
            ),
        }
        for item in evaluation.summaries
    ]
    feature_frame = feature_panel.frame
    outcome_frame = outcome_panel.frame
    feature_complete = {
        str(signal_date): int(group["complete_feature_row"].astype(bool).sum())
        for signal_date, group in feature_frame.groupby("session_date", sort=False)
    }
    outcome_any = {
        str(signal_date): int(group["available_horizon_count"].astype(int).gt(0).sum())
        for signal_date, group in outcome_frame.groupby("session_date", sort=False)
    }
    outcome_full = {
        str(signal_date): int(group["fully_labeled_outcome_row"].astype(bool).sum())
        for signal_date, group in outcome_frame.groupby("session_date", sort=False)
    }
    observation_rows = [
        {
            "signal_date": audit.session_date,
            "universe_membership_count": audit.membership_count,
            "observation_row_count": audit.emitted_observation_row_count,
            "available_exact_market_row_count": audit.available_row_count,
            "missing_exact_market_row_count": audit.missing_row_count,
            "complete_feature_row_count": feature_complete.get(audit.session_date, 0),
            "rows_with_any_available_outcome": outcome_any.get(audit.session_date, 0),
            "fully_labeled_outcome_row_count": outcome_full.get(audit.session_date, 0),
        }
        for audit in observation_index.session_audit
    ]
    return {
        "factor_summary.csv": (_SUMMARY_COLUMNS, summary_rows),
        "factor_by_date.csv": (_DAILY_COLUMNS, daily_rows),
        "factor_coverage.csv": (_COVERAGE_COLUMNS, coverage_rows),
        "observation_counts_by_date.csv": (_OBSERVATION_COLUMNS, observation_rows),
    }


def _keys(frame: Any) -> tuple[tuple[str, str], ...]:
    return tuple(zip(frame["session_date"].astype(str), frame["symbol"].astype(str)))


def _validate_reconciliation(
    observation_index: Any,
    feature_panel: Any,
    outcome_panel: Any,
    dataset: Any,
    evaluation: Any,
    rows: Mapping[str, tuple[tuple[str, ...], list[dict[str, Any]]]],
) -> None:
    observation_frame = observation_index.frame
    feature_frame = feature_panel.frame
    outcome_frame = outcome_panel.frame
    dataset_frame = dataset.evaluation_frame()
    expected_count = observation_index.total_membership_row_count
    if not (
        expected_count
        == feature_panel.observation_row_count
        == outcome_panel.observation_row_count
        == dataset.observation_row_count
        == len(dataset_frame)
    ):
        raise ValueError("observation, feature, outcome, and research-dataset counts differ")
    expected_keys = _keys(observation_frame)
    if _keys(feature_frame) != expected_keys or _keys(outcome_frame) != expected_keys or _keys(dataset_frame) != expected_keys:
        raise ValueError("feature, outcome, and research-dataset keys do not match observations")
    if (
        feature_panel.observation_index_identity != observation_index.identity
        or outcome_panel.observation_index_identity != observation_index.identity
        or dataset.observation_index_identity != observation_index.identity
        or dataset.feature_panel_identity != feature_panel.identity
        or dataset.outcome_panel_identity != outcome_panel.identity
    ):
        raise ValueError("research-dataset source identity reconciliation failed")
    spec = NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1
    if (
        evaluation.source_dataset_identity != dataset.identity
        or evaluation.source_dataset_content_identity != dataset.content_identity
        or evaluation.specification_fingerprint != spec.fingerprint
    ):
        raise ValueError("evaluator source identity reconciliation failed")
    expected_factors = (
        "atr_percent_14", "rsi_14", "adx_14", "volume_ratio_20",
        "stock_return_20d_pct", "relative_strength_20d_pct_points",
        "ema20_distance_pct", "return_3d_pct",
    )
    if spec.factors != expected_factors or spec.horizons != (5, 10, 20) or spec.outcome_fields != (
        "stock_forward_return_pct", "excess_forward_return_pct_points",
    ):
        raise ValueError("built-in neutral evaluator scope changed")
    if set(spec.factors).intersection(dataset.forbidden_predictor_columns):
        raise ValueError("forbidden predictor column entered the evaluator")
    if len(evaluation.summaries) != 48 or len(rows["factor_summary.csv"][1]) != 48:
        raise ValueError("factor summary must contain exactly 48 rows")
    evaluation_signal_dates = tuple(sorted(set(dataset_frame["session_date"].astype(str))))
    signal_date_count = len(evaluation_signal_dates)
    if len(evaluation.daily_evaluations) != signal_date_count * 48:
        raise ValueError("daily evaluation dimensions do not reconcile")
    summary_keys = [
        (item.factor, item.horizon_sessions, item.outcome_field)
        for item in evaluation.summaries
    ]
    daily_keys = [
        (item.factor, item.horizon_sessions, item.outcome_field, item.signal_date)
        for item in evaluation.daily_evaluations
    ]
    if len(set(summary_keys)) != len(summary_keys) or len(set(daily_keys)) != len(daily_keys):
        raise ValueError("evaluation contains duplicate summary or daily keys")
    observations_by_date = {
        audit.session_date: audit.emitted_observation_row_count
        for audit in observation_index.session_audit
    }
    for item in evaluation.daily_evaluations:
        if item.total_observation_count != observations_by_date[item.signal_date]:
            raise ValueError("daily evaluation population differs from observations")
        if (
            item.pairwise_finite_eligible_count
            + item.excluded_for_factor_count
            + item.excluded_for_outcome_count
            + item.excluded_for_both_count
            != item.total_observation_count
        ):
            raise ValueError("daily eligibility counts do not reconcile")
    if len(rows["factor_by_date.csv"][1]) != signal_date_count * 48:
        raise ValueError("daily artifact dimensions do not reconcile")
    if len(rows["factor_coverage.csv"][1]) != 48:
        raise ValueError("coverage artifact dimensions do not reconcile")
    if len(rows["observation_counts_by_date.csv"][1]) != len(observation_index.session_audit):
        raise ValueError("observation-count artifact dimensions do not reconcile")


def _validate_emitted(
    directory: Path,
    rows: Mapping[str, tuple[tuple[str, ...], list[dict[str, Any]]]],
    evaluation: Any,
) -> None:
    if tuple(sorted(path.name for path in directory.iterdir())) != tuple(sorted(_REQUIRED_FILENAMES)):
        raise ValueError("experiment artifact set is incomplete")
    manifest = json.loads((directory / "experiment_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("completed") is not True:
        raise ValueError("experiment manifest is not complete")
    expected_artifacts = {
        filename: len(expected) for filename, (_columns, expected) in rows.items()
    }
    expected_artifacts.update({"experiment_manifest.json": 1, "assumptions.md": 1})
    if manifest.get("artifacts") != expected_artifacts:
        raise ValueError("manifest artifact names or row counts do not reconcile")
    emitted: dict[str, list[dict[str, str]]] = {}
    for filename, (columns, expected) in rows.items():
        with (directory / filename).open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            actual = list(reader)
            if tuple(reader.fieldnames or ()) != columns:
                raise ValueError(f"emitted CSV schema mismatch: {filename}")
            if len(actual) != len(expected):
                raise ValueError(f"emitted CSV row count mismatch: {filename}")
            emitted[filename] = actual
        for row in emitted[filename]:
            if any(value.strip().lower() in {"<na>", "nan", "none", "inf", "-inf"} for value in row.values()):
                raise ValueError(f"invalid missing/non-finite CSV token: {filename}")
    summary_rows = emitted["factor_summary.csv"]
    if "included_daily_identities" in _SUMMARY_COLUMNS:
        raise ValueError("summary artifact must not contain full daily identities")
    for row, summary in zip(summary_rows, evaluation.summaries, strict=True):
        if int(row["included_daily_identity_count"]) != len(summary.included_daily_identities):
            raise ValueError("summary daily identity count does not reconcile")
        if row["included_daily_identities_sha256"] != _identity_collection_sha256(
            summary.included_daily_identities,
        ):
            raise ValueError("summary daily identity hash does not reconcile")
        if row["identity"] != summary.identity:
            raise ValueError("summary identity changed during projection")
    for row in emitted["factor_by_date.csv"]:
        if date.fromisoformat(row["signal_date"]).isoformat() != row["signal_date"]:
            raise ValueError("daily artifact contains a non-canonical date")
    for row in emitted["observation_counts_by_date.csv"]:
        if date.fromisoformat(row["signal_date"]).isoformat() != row["signal_date"]:
            raise ValueError("observation artifact contains a non-canonical date")
    (directory / "assumptions.md").read_text(encoding="utf-8")


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


def run_neutral_panel_factor_evaluation(
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
    cache_codec: str = CACHE_DEFAULT_CODEC,
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
    resolved_cache = _resolved_cache_root(cache_root, output=output, database=database)

    database_digest = _file_sha256(database)
    snapshot = build_market_data_snapshot(database)
    if snapshot.first_session_date is None or snapshot.last_session_date is None:
        raise ValueError("market snapshot contains no sessions")
    coverage = build_database_coverage_index(
        snapshot.first_session_date,
        end,
        minimum_history_sessions=minimum_history_sessions,
        maximum_staleness_sessions=maximum_staleness_sessions,
        database_path=database,
    )
    universe_context = PointInTimeUniverseContext.from_coverage_index(coverage)
    observation_index = build_point_in_time_observation_index(
        snapshot,
        universe_context,
        benchmark_symbol=benchmark,
        start_date=start,
        through_date=end,
    )
    cache = (
        None
        if resolved_cache is None
        else PreparedFeatureCache(resolved_cache, codec=cache_codec)
    )
    feature_source = prepare_neutral_research_feature_source(
        snapshot,
        observation_index.symbols,
        benchmark_symbol=benchmark,
        universe_context=universe_context,
        start_date=start,
        through_date=end,
        cache=cache,
    )
    feature_panel = attach_features_to_observation_index(
        observation_index,
        feature_source,
        spec=NEUTRAL_RESEARCH_FEATURE_PANEL_V1,
    )
    outcome_panel = build_point_in_time_outcome_panel(observation_index, snapshot)
    dataset = build_point_in_time_research_dataset(
        observation_index,
        feature_panel,
        outcome_panel,
    )
    evaluation = evaluate_point_in_time_panel_factors(
        dataset,
        NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1,
    )
    rows = _artifact_rows(evaluation, observation_index, feature_panel, outcome_panel)
    _validate_reconciliation(
        observation_index, feature_panel, outcome_panel, dataset, evaluation, rows,
    )
    if _file_sha256(database) != database_digest:
        raise RuntimeError("market database changed during read-only evaluation")

    source_cache = feature_source.metadata.get("cache")
    source_identity = feature_source.computation_identity
    source_identity_hash = (
        source_identity if isinstance(source_identity, str) else source_identity.sha256
    )
    artifacts = {
        filename: len(artifact_rows)
        for filename, (_columns, artifact_rows) in rows.items()
    }
    artifacts.update({"experiment_manifest.json": 1, "assumptions.md": 1})
    manifest = {
        "runner_contract": RUNNER_CONTRACT,
        "runner_version": RUNNER_VERSION,
        "completed": True,
        "database": {
            "canonical_path": str(database),
            "sha256": database_digest,
        },
        "requested_bounds": {"start_date": start, "end_date": end},
        "effective_bounds": {
            "observation_first_session": observation_index.effective_first_session_date,
            "observation_last_session": observation_index.effective_last_session_date,
            "coverage_first_session": coverage.effective_start_date,
            "coverage_last_session": coverage.effective_end_date,
            "outcome_data_through_session": snapshot.last_session_date,
        },
        "benchmark_symbol": benchmark,
        "coverage": {
            "universe_mode": "database_coverage",
            "minimum_history_sessions": minimum_history_sessions,
            "maximum_staleness_sessions": maximum_staleness_sessions,
            "index_start_date": coverage.start_date,
            "index_end_date": coverage.end_date,
        },
        "snapshot": {
            "snapshot_id": snapshot.snapshot_id,
            "logical_content_fingerprint": snapshot.logical_content_fingerprint,
            "first_session_date": snapshot.first_session_date,
            "last_session_date": snapshot.last_session_date,
        },
        "universe_membership_identity": universe_context.membership_identity,
        "observation_index": {
            "identity": observation_index.identity,
            "content_identity": observation_index.content_identity,
        },
        "features": {
            "computation_identity": source_identity_hash,
            "panel_identity": feature_panel.identity,
            "panel_content_identity": feature_panel.feature_content_identity,
            "cache": {
                "enabled": cache is not None,
                "root": None if resolved_cache is None else str(resolved_cache),
                "codec": None if cache is None else cache_codec,
                "hit": None if source_cache is None else bool(source_cache.get("hit")),
                "cache_key": None if source_cache is None else source_cache.get("cache_key"),
            },
        },
        "outcome_panel": {
            "identity": outcome_panel.identity,
            "content_identity": outcome_panel.outcome_content_identity,
        },
        "research_dataset": {
            "identity": dataset.identity,
            "content_identity": dataset.content_identity,
        },
        "evaluation": {
            "specification_fingerprint": NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1.fingerprint,
            "result_identity": evaluation.identity,
            "factors": list(NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1.factors),
            "horizons": list(NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1.horizons),
            "outcome_fields": list(NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1.outcome_fields),
        },
        "counts": {
            "observation_rows": observation_index.total_membership_row_count,
            "signal_dates": (
                evaluation.summaries[0].total_signal_dates if evaluation.summaries else 0
            ),
            "benchmark_signal_sessions": len(observation_index.session_audit),
            "symbols": observation_index.distinct_member_symbol_count,
            "summary_rows": len(evaluation.summaries),
            "daily_rows": len(evaluation.daily_evaluations),
        },
        "artifacts": artifacts,
        "limitations": list(_LIMITATIONS),
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(
        prefix=f".{output.name}.tmp-", dir=output.parent,
    )).resolve()
    try:
        for filename, (columns, artifact_rows) in rows.items():
            _write_csv(temporary / filename, columns, artifact_rows)
        (temporary / "assumptions.md").write_text(
            _ASSUMPTIONS, encoding="utf-8", newline="\n",
        )
        _write_json(temporary / "experiment_manifest.json", manifest)
        _validate_emitted(temporary, rows, evaluation)
        if _file_sha256(database) != database_digest:
            raise RuntimeError("market database changed before publication")
        _publish_atomic(temporary, output)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return {
        "output_root": output,
        "manifest": manifest,
        "snapshot": snapshot,
        "coverage_index": coverage,
        "universe_context": universe_context,
        "observation_index": observation_index,
        "feature_source": feature_source,
        "feature_panel": feature_panel,
        "outcome_panel": outcome_panel,
        "research_dataset": dataset,
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
    parser.add_argument(
        "--cache-codec",
        choices=("sqlite-json-v1", "npz_numeric_v1"),
        default=CACHE_DEFAULT_CODEC,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    result = run_neutral_panel_factor_evaluation(
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
