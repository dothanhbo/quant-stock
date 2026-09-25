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
    NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1,
    NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2,
    NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1,
    PanelTemporalDirection,
    evaluate_panel_factor_redundancy,
    evaluate_panel_factor_temporal_stability,
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
RUNNER_VERSION = "v3"
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
_TEMPORAL_SUMMARY_COLUMNS = (
    "factor", "horizon_sessions", "outcome_field", "total_block_count",
    "ic_review_eligible_block_count", "spread_review_eligible_block_count",
    "positive_mean_ic_block_count", "zero_mean_ic_block_count",
    "negative_mean_ic_block_count", "positive_mean_spread_block_count",
    "zero_mean_spread_block_count", "negative_mean_spread_block_count",
    "mean_ic_across_block_means", "median_ic_across_block_means",
    "minimum_block_mean_ic", "maximum_block_mean_ic", "range_block_mean_ic",
    "mean_spread_across_block_means", "median_spread_across_block_means",
    "minimum_block_mean_spread", "maximum_block_mean_spread",
    "range_block_mean_spread", "largest_absolute_mean_ic_block_concentration",
    "largest_absolute_mean_spread_block_concentration", "ic_consistent_direction",
    "spread_consistent_direction", "all_blocks_positive_ic",
    "all_blocks_positive_spread", "all_blocks_negative_ic",
    "all_blocks_negative_spread", "ic_sign_flip_count", "spread_sign_flip_count",
    "coverage_sufficient_for_temporal_review", "directionally_consistent_ic",
    "directionally_consistent_spread", "descriptive_temporal_support",
    "block_identity_count", "block_identities_sha256",
    "warnings", "identity",
)
_TEMPORAL_BLOCK_COLUMNS = (
    "factor", "horizon_sessions", "outcome_field", "block_name",
    "block_start_date", "block_end_date", "total_source_signal_dates",
    "dates_with_any_eligible_observations", "ic_defined_date_count",
    "ic_coverage_pct", "mean_daily_rank_ic", "median_daily_rank_ic",
    "population_std_daily_ic", "minimum_daily_ic", "maximum_daily_ic",
    "positive_ic_date_count", "zero_ic_date_count", "negative_ic_date_count",
    "positive_ic_rate", "spread_defined_date_count", "spread_coverage_pct",
    "mean_daily_mean_spread", "median_daily_mean_spread",
    "population_std_daily_mean_spread", "minimum_daily_mean_spread",
    "maximum_daily_mean_spread", "positive_spread_date_count",
    "zero_spread_date_count", "negative_spread_date_count", "positive_spread_rate",
    "mean_daily_median_spread", "median_daily_median_spread",
    "average_low_bucket_size", "average_high_bucket_size",
    "average_eligible_observation_count", "minimum_eligible_observation_count",
    "maximum_eligible_observation_count", "average_factor_availability_coverage_pct",
    "average_outcome_availability_coverage_pct", "ic_review_eligible",
    "spread_review_eligible", "included_daily_identity_count",
    "included_daily_identities_sha256", "warnings", "ic_undefined_reason",
    "spread_undefined_reason", "identity",
)
_TEMPORAL_COVERAGE_COLUMNS = (
    "factor", "horizon_sessions", "outcome_field",
    "ic_review_eligible_block_count", "spread_review_eligible_block_count",
    "coverage_sufficient_for_temporal_review", "directionally_consistent_ic",
    "directionally_consistent_spread", "descriptive_temporal_support",
    "all_blocks_positive_ic", "all_blocks_positive_spread", "all_blocks_negative_ic",
    "all_blocks_negative_spread", "ic_consistent_direction",
    "spread_consistent_direction", "ic_sign_flip_count", "spread_sign_flip_count",
    "largest_absolute_mean_ic_block_concentration",
    "largest_absolute_mean_spread_block_concentration", "review_status",
    "summary_identity",
)
_REDUNDANCY_DAILY_COLUMNS = (
    "signal_date", "first_factor", "second_factor", "observation_count",
    "pairwise_finite_count", "pairwise_coverage_pct", "spearman_correlation",
    "absolute_spearman_correlation", "undefined_reason",
    "pairwise_sample_evidence_sha256", "identity",
)
_REDUNDANCY_BLOCK_COLUMNS = (
    "first_factor", "second_factor", "block_name", "block_start_date",
    "block_end_date", "total_signal_date_count", "minimum_sample_date_count",
    "defined_correlation_date_count", "correlation_coverage_pct",
    "mean_daily_correlation", "median_daily_correlation",
    "population_std_daily_correlation", "minimum_daily_correlation",
    "maximum_daily_correlation", "mean_daily_absolute_correlation",
    "median_daily_absolute_correlation", "positive_correlation_date_count",
    "zero_correlation_date_count", "negative_correlation_date_count",
    "positive_correlation_rate", "zero_correlation_rate",
    "negative_correlation_rate", "average_pairwise_finite_count",
    "median_pairwise_finite_count", "included_daily_identity_count",
    "included_daily_identities_sha256", "warnings", "identity",
)
_REDUNDANCY_SUMMARY_COLUMNS = (
    "first_factor", "second_factor", "total_signal_date_count",
    "minimum_sample_date_count", "defined_correlation_date_count",
    "correlation_coverage_pct", "mean_daily_correlation",
    "median_daily_correlation", "population_std_daily_correlation",
    "minimum_daily_correlation", "maximum_daily_correlation",
    "mean_daily_absolute_correlation", "median_daily_absolute_correlation",
    "positive_correlation_date_count", "zero_correlation_date_count",
    "negative_correlation_date_count", "positive_correlation_rate",
    "zero_correlation_rate", "negative_correlation_rate",
    "average_pairwise_finite_count", "median_pairwise_finite_count",
    "blocks_meeting_temporal_review_count", "chronological_sign_flip_count",
    "largest_absolute_block_mean_concentration",
    "minimum_block_mean_correlation", "maximum_block_mean_correlation",
    "range_block_mean_correlation",
    "minimum_block_mean_absolute_daily_correlation",
    "maximum_block_mean_absolute_daily_correlation",
    "range_block_mean_absolute_daily_correlation", "all_blocks_positive",
    "all_blocks_negative", "included_daily_identity_count",
    "included_daily_identities_sha256", "block_identity_count",
    "block_identities_sha256", "warnings", "identity",
)
_REQUIRED_FILENAMES = (
    "experiment_manifest.json",
    "factor_summary.csv",
    "factor_by_date.csv",
    "factor_coverage.csv",
    "observation_counts_by_date.csv",
    "assumptions.md",
    "temporal_stability_summary.csv",
    "temporal_stability_by_block.csv",
    "temporal_coverage.csv",
    "factor_redundancy_summary.csv",
    "factor_redundancy_by_block.csv",
    "factor_redundancy_by_date.csv",
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

_TEMPORAL_LIMITATIONS = (
    "Temporal review uses the same historical dataset as full-sample discovery and is not independent OOS confirmation.",
    "Results remain descriptive and do not select factors or authorize production use.",
    "Stock and excess outcomes are not independent evidence.",
    "Database coverage is not necessarily historical VN100 membership.",
    "No multiple-testing correction, costs, turnover, liquidity, portfolio utility, or tradability conclusion is included.",
    "A negative direction means higher factor values are empirically associated with lower future outcomes; it is not a short signal or authorization to invert the factor.",
)

_REDUNDANCY_LIMITATIONS = (
    "Factor correlation is descriptive, not causal.",
    "High correlation does not authorize dropping a factor.",
    "Low correlation does not prove incremental alpha.",
    "The redundancy evaluator uses no future outcomes.",
    "Database-coverage membership is not historical VN100 membership.",
    "This is the same historical dataset and not independent OOS confirmation.",
    "No factor selection, weighting, composite, strategy, or portfolio authority is granted.",
)

_REDUNDANCY_ASSUMPTIONS = (
    "Redundancy uses same-date pairwise-finite Spearman correlation.",
    "Factor values use ascending average ranks for ties.",
    "Each daily pair requires at least 20 pairwise-finite observations.",
    "Defined daily correlations receive equal signal-date weight; symbols are never pooled across dates.",
    "Temporal evidence uses the four fixed inclusive Phase 5.5 calendar blocks.",
    "Redundancy reads only the point-in-time predictor projection and no future outcomes.",
    "No redundancy threshold or factor-selection decision is defined.",
)

_ASSUMPTIONS += "\n## Factor-redundancy methodology\n\n" + "\n".join(
    f"- {item}" for item in _REDUNDANCY_ASSUMPTIONS
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


def _temporal_review_status(summary: Any) -> str:
    if summary.descriptive_temporal_support:
        if (
            summary.ic_consistent_direction is PanelTemporalDirection.POSITIVE
            and summary.spread_consistent_direction is PanelTemporalDirection.POSITIVE
        ):
            return "TEMPORAL_SUPPORT_POSITIVE"
        if (
            summary.ic_consistent_direction is PanelTemporalDirection.NEGATIVE
            and summary.spread_consistent_direction is PanelTemporalDirection.NEGATIVE
        ):
            return "TEMPORAL_SUPPORT_NEGATIVE"
        raise ValueError("temporal support has inconsistent direction evidence")
    if (
        summary.directionally_consistent_ic
        and summary.directionally_consistent_spread
        and summary.ic_consistent_direction is not summary.spread_consistent_direction
    ):
        return "DIRECTION_MISMATCH"
    if summary.coverage_sufficient_for_temporal_review:
        return "COVERAGE_ONLY"
    return "INSUFFICIENT_COVERAGE"


def _temporal_artifact_rows(
    temporal: Any,
) -> dict[str, tuple[tuple[str, ...], list[dict[str, Any]]]]:
    summary_rows = [
        {
            **{
                column: (
                    " | ".join(item.warnings)
                    if column == "warnings"
                    else getattr(item, column)
                )
                for column in _TEMPORAL_SUMMARY_COLUMNS
                if column not in {
                    "block_identity_count",
                    "block_identities_sha256",
                }
            },
            "block_identity_count": len(item.included_block_identities),
            "block_identities_sha256": _identity_collection_sha256(
                item.included_block_identities,
            ),
        }
        for item in temporal.summaries
    ]
    block_rows = [
        {
            **{
                column: (
                    " | ".join(item.warnings)
                    if column == "warnings"
                    else getattr(item, column)
                )
                for column in _TEMPORAL_BLOCK_COLUMNS
                if column not in {
                    "included_daily_identity_count",
                    "included_daily_identities_sha256",
                }
            },
            "included_daily_identity_count": len(item.included_daily_identities),
            "included_daily_identities_sha256": _identity_collection_sha256(
                item.included_daily_identities,
            ),
        }
        for item in temporal.block_results
    ]
    coverage_rows = [
        {
            **{
                column: getattr(item, column)
                for column in _TEMPORAL_COVERAGE_COLUMNS
                if column not in {"review_status", "summary_identity"}
            },
            "review_status": _temporal_review_status(item),
            "summary_identity": item.identity,
        }
        for item in temporal.summaries
    ]
    return {
        "temporal_stability_summary.csv": (_TEMPORAL_SUMMARY_COLUMNS, summary_rows),
        "temporal_stability_by_block.csv": (_TEMPORAL_BLOCK_COLUMNS, block_rows),
        "temporal_coverage.csv": (_TEMPORAL_COVERAGE_COLUMNS, coverage_rows),
    }


def _redundancy_artifact_rows(
    redundancy: Any,
) -> dict[str, tuple[tuple[str, ...], list[dict[str, Any]]]]:
    spec = NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1
    factor_order = {factor: index for index, factor in enumerate(spec.factors)}
    daily = sorted(
        redundancy.daily_correlations,
        key=lambda item: (
            item.signal_date,
            factor_order[item.first_factor],
            factor_order[item.second_factor],
        ),
    )
    daily_rows = [
        {column: getattr(item, column) for column in _REDUNDANCY_DAILY_COLUMNS}
        for item in daily
    ]
    block_rows = [
        {
            **{
                column: (
                    " | ".join(item.warnings)
                    if column == "warnings"
                    else getattr(item, column)
                )
                for column in _REDUNDANCY_BLOCK_COLUMNS
                if column not in {
                    "included_daily_identity_count",
                    "included_daily_identities_sha256",
                }
            },
            "included_daily_identity_count": item.included_daily_identity_count,
            "included_daily_identities_sha256": item.included_daily_identities_sha256,
        }
        for item in redundancy.block_correlations
    ]
    summary_rows = [
        {
            **{
                column: (
                    " | ".join(item.warnings)
                    if column == "warnings"
                    else getattr(item, column)
                )
                for column in _REDUNDANCY_SUMMARY_COLUMNS
                if column not in {
                    "included_daily_identity_count",
                    "included_daily_identities_sha256",
                    "block_identity_count",
                    "block_identities_sha256",
                }
            },
            "included_daily_identity_count": item.included_daily_identity_count,
            "included_daily_identities_sha256": item.included_daily_identities_sha256,
            "block_identity_count": item.ordered_block_identity_count,
            "block_identities_sha256": item.ordered_block_identities_sha256,
        }
        for item in redundancy.summaries
    ]
    return {
        "factor_redundancy_summary.csv": (_REDUNDANCY_SUMMARY_COLUMNS, summary_rows),
        "factor_redundancy_by_block.csv": (_REDUNDANCY_BLOCK_COLUMNS, block_rows),
        "factor_redundancy_by_date.csv": (_REDUNDANCY_DAILY_COLUMNS, daily_rows),
    }


def _keys(frame: Any) -> tuple[tuple[str, str], ...]:
    return tuple(zip(frame["session_date"].astype(str), frame["symbol"].astype(str)))


def _validate_temporal_reconciliation(
    evaluation: Any,
    temporal: Any,
    rows: Mapping[str, tuple[tuple[str, ...], list[dict[str, Any]]]],
) -> None:
    spec = NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2
    if temporal.source_result_identity != evaluation.identity:
        raise ValueError("temporal source result identity does not match Phase 5.4A")
    if temporal.source_specification_fingerprint != evaluation.specification_fingerprint:
        raise ValueError("temporal source specification fingerprint does not reconcile")
    if temporal.temporal_specification_fingerprint != spec.fingerprint:
        raise ValueError("temporal specification fingerprint does not match the built-in spec")
    blocks = tuple(temporal.block_results)
    summaries = tuple(temporal.summaries)
    if len(blocks) != 192 or len(summaries) != 48:
        raise ValueError("temporal result dimensions must be 192 blocks and 48 summaries")
    block_map = {
        (item.factor, item.horizon_sessions, item.outcome_field, item.block_name): item
        for item in blocks
    }
    summary_map = {
        (item.factor, item.horizon_sessions, item.outcome_field): item
        for item in summaries
    }
    if len(block_map) != 192 or len(summary_map) != 48:
        raise ValueError("temporal result contains duplicate block or summary keys")
    source_keys = {
        (item.factor, item.horizon_sessions, item.outcome_field)
        for item in evaluation.summaries
    }
    expected_ordered_keys = tuple(
        (factor, horizon, outcome)
        for factor in spec.factors
        for horizon in spec.horizons
        for outcome in spec.outcome_fields
    )
    expected_keys = set(expected_ordered_keys)
    actual_summary_keys = tuple(
        (item.factor, item.horizon_sessions, item.outcome_field) for item in summaries
    )
    expected_block_keys = tuple(
        (*key, block.name) for key in expected_ordered_keys for block in spec.blocks
    )
    actual_block_keys = tuple(
        (item.factor, item.horizon_sessions, item.outcome_field, item.block_name)
        for item in blocks
    )
    if (
        source_keys != expected_keys
        or actual_summary_keys != expected_ordered_keys
        or actual_block_keys != expected_block_keys
    ):
        raise ValueError("temporal factor, horizon, or outcome scope does not reconcile")
    daily_by_identity = {item.identity: item for item in evaluation.daily_evaluations}
    if len(daily_by_identity) != len(evaluation.daily_evaluations):
        raise ValueError("Phase 5.4A daily identities are not unique")
    for key in expected_ordered_keys:
        ordered_blocks = tuple(
            block_map[(*key, temporal_block.name)] for temporal_block in spec.blocks
        )
        if summary_map[key].included_block_identities != tuple(
            item.identity for item in ordered_blocks
        ):
            raise ValueError("temporal summary block identities do not reconcile")
        for temporal_block, block in zip(spec.blocks, ordered_blocks, strict=True):
            expected_daily = tuple(
                item.identity
                for item in evaluation.daily_evaluations
                if (item.factor, item.horizon_sessions, item.outcome_field) == key
                and temporal_block.start_date <= item.signal_date <= temporal_block.end_date
            )
            if block.included_daily_identities != expected_daily:
                raise ValueError("temporal block daily identities or boundaries do not reconcile")
            if any(identity not in daily_by_identity for identity in block.included_daily_identities):
                raise ValueError("temporal block references an unknown daily identity")
    if (
        len(rows["temporal_stability_by_block.csv"][1]) != 192
        or len(rows["temporal_stability_summary.csv"][1]) != 48
        or len(rows["temporal_coverage.csv"][1]) != 48
    ):
        raise ValueError("temporal artifact dimensions do not reconcile")
    for row, block in zip(
        rows["temporal_stability_by_block.csv"][1], blocks, strict=True,
    ):
        if row["included_daily_identity_count"] != len(block.included_daily_identities):
            raise ValueError("temporal block daily identity count does not reconcile")
        if row["included_daily_identities_sha256"] != _identity_collection_sha256(
            block.included_daily_identities,
        ):
            raise ValueError("temporal block daily identity hash does not reconcile")
    for row, summary in zip(
        rows["temporal_stability_summary.csv"][1], summaries, strict=True,
    ):
        if row["block_identity_count"] != len(summary.included_block_identities):
            raise ValueError("temporal summary block identity count does not reconcile")
        if row["block_identities_sha256"] != _identity_collection_sha256(
            summary.included_block_identities,
        ):
            raise ValueError("temporal summary block identity hash does not reconcile")


def _validate_redundancy_reconciliation(
    dataset: Any,
    redundancy: Any,
    rows: Mapping[str, tuple[tuple[str, ...], list[dict[str, Any]]]],
    *,
    start_date: str,
    end_date: str,
) -> None:
    spec = NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1
    pairs = spec.pairs
    blocks = spec.blocks
    if (
        redundancy.source_dataset_identity != dataset.identity
        or redundancy.specification_fingerprint != spec.fingerprint
        or not str(redundancy.source_bounded_content_identity).strip()
    ):
        raise ValueError("redundancy source identity or specification does not reconcile")
    if len(spec.factors) != 8 or len(pairs) != 28 or len(blocks) != 4:
        raise ValueError("built-in redundancy factor, pair, or block scope changed")
    if len(set(pairs)) != 28 or any(
        spec.factors.index(first) >= spec.factors.index(second)
        for first, second in pairs
    ):
        raise ValueError("redundancy pairs are not canonical unordered pairs")

    signal_dates = tuple(audit.session_date for audit in dataset.session_audit)
    if (
        not signal_dates
        or signal_dates != tuple(sorted(signal_dates))
        or len(set(signal_dates)) != len(signal_dates)
        or signal_dates[0] < start_date
        or signal_dates[-1] > end_date
    ):
        raise ValueError("redundancy signal dates do not match the experiment boundary")
    expected_daily_keys = tuple(
        (signal_date, first, second)
        for signal_date in signal_dates
        for first, second in pairs
    )
    result_daily_keys = tuple(
        (item.signal_date, item.first_factor, item.second_factor)
        for item in redundancy.daily_correlations
    )
    if any(
        item.source_dataset_identity != dataset.identity
        or item.source_bounded_content_identity
        != redundancy.source_bounded_content_identity
        or item.specification_fingerprint != spec.fingerprint
        for item in redundancy.daily_correlations
    ):
        raise ValueError("redundancy daily provenance does not reconcile")
    if (
        len(result_daily_keys) != len(signal_dates) * 28
        or len(set(result_daily_keys)) != len(result_daily_keys)
        or set(result_daily_keys) != set(expected_daily_keys)
    ):
        raise ValueError("redundancy daily pair/date dimensions do not reconcile")
    emitted_daily_keys = tuple(
        (row["signal_date"], row["first_factor"], row["second_factor"])
        for row in rows["factor_redundancy_by_date.csv"][1]
    )
    if emitted_daily_keys != expected_daily_keys:
        raise ValueError("redundancy daily artifact ordering or coverage does not reconcile")
    daily_map = {
        (item.signal_date, item.first_factor, item.second_factor): item
        for item in redundancy.daily_correlations
    }
    for row in rows["factor_redundancy_by_date.csv"][1]:
        item = daily_map[(row["signal_date"], row["first_factor"], row["second_factor"])]
        if any(row[column] != getattr(item, column) for column in _REDUNDANCY_DAILY_COLUMNS):
            raise ValueError("redundancy daily artifact is not a direct public-field projection")

    expected_block_keys = tuple(
        (first, second, block.name)
        for first, second in pairs
        for block in blocks
    )
    result_block_keys = tuple(
        (item.first_factor, item.second_factor, item.block_name)
        for item in redundancy.block_correlations
    )
    if any(
        item.source_dataset_identity != dataset.identity
        or item.source_bounded_content_identity
        != redundancy.source_bounded_content_identity
        or item.source_daily_result_identity != redundancy.source_daily_result_identity
        or item.specification_fingerprint != spec.fingerprint
        for item in redundancy.block_correlations
    ):
        raise ValueError("redundancy block provenance does not reconcile")
    if result_block_keys != expected_block_keys or len(set(result_block_keys)) != 112:
        raise ValueError("redundancy block dimensions or ordering do not reconcile")
    emitted_block_keys = tuple(
        (row["first_factor"], row["second_factor"], row["block_name"])
        for row in rows["factor_redundancy_by_block.csv"][1]
    )
    if emitted_block_keys != expected_block_keys:
        raise ValueError("redundancy block artifact ordering does not reconcile")

    result_summary_keys = tuple(
        (item.first_factor, item.second_factor) for item in redundancy.summaries
    )
    if any(
        item.source_dataset_identity != dataset.identity
        or item.source_bounded_content_identity
        != redundancy.source_bounded_content_identity
        or item.source_daily_result_identity != redundancy.source_daily_result_identity
        or item.specification_fingerprint != spec.fingerprint
        for item in redundancy.summaries
    ):
        raise ValueError("redundancy summary provenance does not reconcile")
    emitted_summary_keys = tuple(
        (row["first_factor"], row["second_factor"])
        for row in rows["factor_redundancy_summary.csv"][1]
    )
    if (
        result_summary_keys != pairs
        or emitted_summary_keys != pairs
        or len(set(result_summary_keys)) != 28
    ):
        raise ValueError("redundancy summary dimensions or ordering do not reconcile")

    block_map = {
        (item.first_factor, item.second_factor, item.block_name): item
        for item in redundancy.block_correlations
    }
    summary_map = {
        (item.first_factor, item.second_factor): item for item in redundancy.summaries
    }
    for first, second in pairs:
        summary = summary_map[(first, second)]
        ordered_blocks = tuple(block_map[(first, second, block.name)] for block in blocks)
        if summary.ordered_block_identities != tuple(item.identity for item in ordered_blocks):
            raise ValueError("redundancy summary block identities do not reconcile")
        expected_summary_daily = tuple(
            daily_map[(signal_date, first, second)].identity
            for signal_date in signal_dates
        )
        if summary.included_daily_identities != expected_summary_daily:
            raise ValueError("redundancy summary daily membership does not reconcile")
        for definition, item in zip(blocks, ordered_blocks, strict=True):
            if (
                item.block_start_date != definition.start_date
                or item.block_end_date != definition.end_date
            ):
                raise ValueError("redundancy block boundaries do not reconcile")
            expected_block_daily = tuple(
                daily_map[(signal_date, first, second)].identity
                for signal_date in signal_dates
                if definition.start_date <= signal_date <= definition.end_date
            )
            if item.included_daily_identities != expected_block_daily:
                raise ValueError("redundancy block daily membership does not reconcile")

    for row, item in zip(
        rows["factor_redundancy_by_block.csv"][1],
        redundancy.block_correlations,
        strict=True,
    ):
        expected_count = len(item.included_daily_identities)
        expected_hash = _identity_collection_sha256(item.included_daily_identities)
        projected = {
            column: (
                " | ".join(item.warnings)
                if column == "warnings"
                else getattr(item, column)
            )
            for column in _REDUNDANCY_BLOCK_COLUMNS
            if column not in {
                "included_daily_identity_count", "included_daily_identities_sha256",
            }
        }
        projected.update({
            "included_daily_identity_count": expected_count,
            "included_daily_identities_sha256": expected_hash,
        })
        if (
            row != projected
            or item.included_daily_identity_count != expected_count
            or row["included_daily_identity_count"] != expected_count
            or item.included_daily_identities_sha256 != expected_hash
            or row["included_daily_identities_sha256"] != expected_hash
        ):
            raise ValueError("redundancy block daily identity projection does not reconcile")
    for row, item in zip(
        rows["factor_redundancy_summary.csv"][1],
        redundancy.summaries,
        strict=True,
    ):
        daily_count = len(item.included_daily_identities)
        daily_hash = _identity_collection_sha256(item.included_daily_identities)
        block_count = len(item.ordered_block_identities)
        block_hash = _identity_collection_sha256(item.ordered_block_identities)
        projected = {
            column: (
                " | ".join(item.warnings)
                if column == "warnings"
                else getattr(item, column)
            )
            for column in _REDUNDANCY_SUMMARY_COLUMNS
            if column not in {
                "included_daily_identity_count", "included_daily_identities_sha256",
                "block_identity_count", "block_identities_sha256",
            }
        }
        projected.update({
            "included_daily_identity_count": daily_count,
            "included_daily_identities_sha256": daily_hash,
            "block_identity_count": block_count,
            "block_identities_sha256": block_hash,
        })
        if (
            row != projected
            or item.included_daily_identity_count != daily_count
            or row["included_daily_identity_count"] != daily_count
            or item.included_daily_identities_sha256 != daily_hash
            or row["included_daily_identities_sha256"] != daily_hash
            or item.ordered_block_identity_count != block_count
            or row["block_identity_count"] != block_count
            or item.ordered_block_identities_sha256 != block_hash
            or row["block_identities_sha256"] != block_hash
        ):
            raise ValueError("redundancy summary identity projection does not reconcile")

    forbidden = set(dataset.forbidden_predictor_columns)
    redundancy_columns = set().union(
        _REDUNDANCY_DAILY_COLUMNS,
        _REDUNDANCY_BLOCK_COLUMNS,
        _REDUNDANCY_SUMMARY_COLUMNS,
    )
    if forbidden.intersection(redundancy_columns):
        raise ValueError("future-looking outcome field entered redundancy artifacts")
    if {
        "rank_ic", "high_minus_low_mean_spread", "redundant", "independent",
        "selected", "rejected", "drop", "keep", "composite_score",
    }.intersection(redundancy_columns):
        raise ValueError("selection, outcome, or strategy field entered redundancy artifacts")


def _validate_reconciliation(
    observation_index: Any,
    feature_panel: Any,
    outcome_panel: Any,
    dataset: Any,
    evaluation: Any,
    temporal: Any,
    redundancy: Any,
    rows: Mapping[str, tuple[tuple[str, ...], list[dict[str, Any]]]],
    *,
    start_date: str,
    end_date: str,
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
    _validate_temporal_reconciliation(evaluation, temporal, rows)
    _validate_redundancy_reconciliation(
        dataset,
        redundancy,
        rows,
        start_date=start_date,
        end_date=end_date,
    )


def _validate_emitted(
    directory: Path,
    rows: Mapping[str, tuple[tuple[str, ...], list[dict[str, Any]]]],
    evaluation: Any,
    temporal: Any,
    redundancy: Any,
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
            if any(
                value.strip().lower()
                in {"<na>", "nan", "none", "inf", "-inf", "infinity", "-infinity"}
                for value in row.values()
            ):
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
    for filename in (
        "temporal_stability_summary.csv",
        "temporal_stability_by_block.csv",
        "temporal_coverage.csv",
    ):
        columns, expected_rows = rows[filename]
        for actual, expected in zip(emitted[filename], expected_rows, strict=True):
            serialized = {
                column: str(_csv_value(expected.get(column))) for column in columns
            }
            if actual != serialized:
                raise ValueError(f"temporal CSV projection mismatch: {filename}")
    temporal_summaries = emitted["temporal_stability_summary.csv"]
    temporal_blocks = emitted["temporal_stability_by_block.csv"]
    if "included_block_identities" in _TEMPORAL_SUMMARY_COLUMNS:
        raise ValueError("temporal summary must not contain full block identities")
    if "included_daily_identities" in _TEMPORAL_BLOCK_COLUMNS:
        raise ValueError("temporal block artifact must not contain full daily identities")
    for row, summary in zip(temporal_summaries, temporal.summaries, strict=True):
        if int(row["block_identity_count"]) != len(summary.included_block_identities):
            raise ValueError("emitted temporal block identity count does not reconcile")
        if row["block_identities_sha256"] != _identity_collection_sha256(
            summary.included_block_identities,
        ):
            raise ValueError("emitted temporal block identity hash does not reconcile")
        if row["identity"] != summary.identity:
            raise ValueError("temporal summary identity changed during projection")
    for row, block in zip(temporal_blocks, temporal.block_results, strict=True):
        if int(row["included_daily_identity_count"]) != len(block.included_daily_identities):
            raise ValueError("emitted temporal daily identity count does not reconcile")
        if row["included_daily_identities_sha256"] != _identity_collection_sha256(
            block.included_daily_identities,
        ):
            raise ValueError("emitted temporal daily identity hash does not reconcile")
        if row["identity"] != block.identity:
            raise ValueError("temporal block identity changed during projection")
        for boundary in ("block_start_date", "block_end_date"):
            if date.fromisoformat(row[boundary]).isoformat() != row[boundary]:
                raise ValueError("temporal block artifact contains a non-canonical date")
    temporal_manifest = manifest.get("temporal_stability", {})
    review_counts = {
        status: sum(
            row["review_status"] == status for row in emitted["temporal_coverage.csv"]
        )
        for status in (
            "TEMPORAL_SUPPORT_POSITIVE",
            "TEMPORAL_SUPPORT_NEGATIVE",
            "DIRECTION_MISMATCH",
            "COVERAGE_ONLY",
            "INSUFFICIENT_COVERAGE",
        )
    }
    if temporal_manifest.get("review_status_counts") != review_counts:
        raise ValueError("temporal review-status counts do not reconcile")
    if temporal_manifest.get("temporal_result_identity") != temporal.identity:
        raise ValueError("temporal manifest result identity does not reconcile")
    for filename in (
        "factor_redundancy_summary.csv",
        "factor_redundancy_by_block.csv",
        "factor_redundancy_by_date.csv",
    ):
        columns, expected_rows = rows[filename]
        for actual, expected in zip(emitted[filename], expected_rows, strict=True):
            serialized = {
                column: str(_csv_value(expected.get(column))) for column in columns
            }
            if actual != serialized:
                raise ValueError(f"redundancy CSV projection mismatch: {filename}")
    for row in emitted["factor_redundancy_by_date.csv"]:
        if date.fromisoformat(row["signal_date"]).isoformat() != row["signal_date"]:
            raise ValueError("redundancy daily artifact contains a non-canonical date")
    for row in emitted["factor_redundancy_by_block.csv"]:
        for boundary in ("block_start_date", "block_end_date"):
            if date.fromisoformat(row[boundary]).isoformat() != row[boundary]:
                raise ValueError("redundancy block artifact contains a non-canonical date")
    if "included_daily_identities" in _REDUNDANCY_BLOCK_COLUMNS:
        raise ValueError("redundancy block artifact contains full daily identities")
    if (
        "included_daily_identities" in _REDUNDANCY_SUMMARY_COLUMNS
        or "ordered_block_identities" in _REDUNDANCY_SUMMARY_COLUMNS
    ):
        raise ValueError("redundancy summary artifact contains full identity tuples")
    redundancy_manifest = manifest.get("factor_redundancy", {})
    redundancy_dates = sorted({
        item.signal_date for item in redundancy.daily_correlations
    })
    defined = sum(
        item.spearman_correlation is not None
        for item in redundancy.daily_correlations
    )
    if (
        redundancy_manifest.get("result_identity") != redundancy.identity
        or redundancy_manifest.get("contract_name") != redundancy.contract_name
        or redundancy_manifest.get("contract_version") != redundancy.contract_version
        or redundancy_manifest.get("specification_fingerprint")
        != redundancy.specification_fingerprint
        or redundancy_manifest.get("source_dataset_identity")
        != redundancy.source_dataset_identity
        or redundancy_manifest.get("source_bounded_content_identity")
        != redundancy.source_bounded_content_identity
        or redundancy_manifest.get("factor_count") != 8
        or redundancy_manifest.get("canonical_pair_count") != 28
        or redundancy_manifest.get("temporal_block_count") != 4
        or redundancy_manifest.get("block_result_count") != 112
        or redundancy_manifest.get("summary_count") != 28
        or redundancy_manifest.get("daily_record_count")
        != len(redundancy.daily_correlations)
        or redundancy_manifest.get("signal_date_count") != len(redundancy_dates)
        or redundancy_manifest.get("dates_represented") != redundancy_dates
        or redundancy_manifest.get("defined_daily_correlation_count") != defined
        or redundancy_manifest.get("undefined_daily_correlation_count")
        != len(redundancy.daily_correlations) - defined
        or redundancy_manifest.get("artifacts") != [
            "factor_redundancy_summary.csv",
            "factor_redundancy_by_block.csv",
            "factor_redundancy_by_date.csv",
        ]
        or redundancy_manifest.get("limitations") != list(_REDUNDANCY_LIMITATIONS)
        or redundancy_manifest.get("completed") is not True
    ):
        raise ValueError("redundancy manifest does not reconcile")
    assumptions = (directory / "assumptions.md").read_text(encoding="utf-8")
    if any(item not in assumptions for item in _REDUNDANCY_ASSUMPTIONS):
        raise ValueError("redundancy assumptions documentation is incomplete")


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
    redundancy = evaluate_panel_factor_redundancy(
        dataset,
        NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1,
    )
    evaluation = evaluate_point_in_time_panel_factors(
        dataset,
        NEUTRAL_TECHNICAL_FACTOR_EVALUATION_5_10_20_V1,
    )
    temporal = evaluate_panel_factor_temporal_stability(
        evaluation,
        NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2,
    )
    rows = _artifact_rows(evaluation, observation_index, feature_panel, outcome_panel)
    rows.update(_temporal_artifact_rows(temporal))
    rows.update(_redundancy_artifact_rows(redundancy))
    _validate_reconciliation(
        observation_index,
        feature_panel,
        outcome_panel,
        dataset,
        evaluation,
        temporal,
        redundancy,
        rows,
        start_date=start,
        end_date=end,
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
    review_status_counts = {
        status: sum(_temporal_review_status(item) == status for item in temporal.summaries)
        for status in (
            "TEMPORAL_SUPPORT_POSITIVE",
            "TEMPORAL_SUPPORT_NEGATIVE",
            "DIRECTION_MISMATCH",
            "COVERAGE_ONLY",
            "INSUFFICIENT_COVERAGE",
        )
    }
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
        "temporal_stability": {
            "contract_name": temporal.contract_name,
            "contract_version": temporal.contract_version,
            "temporal_specification_fingerprint": temporal.temporal_specification_fingerprint,
            "temporal_result_identity": temporal.identity,
            "source_evaluation_result_identity": temporal.source_result_identity,
            "blocks": [
                {
                    "name": block.name,
                    "start_date": block.start_date,
                    "end_date": block.end_date,
                }
                for block in NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2.blocks
            ],
            "minimum_defined_dates_per_block": (
                NEUTRAL_PANEL_FACTOR_TEMPORAL_STABILITY_V2.minimum_defined_dates_per_block
            ),
            "block_result_count": len(temporal.block_results),
            "summary_count": len(temporal.summaries),
            "review_status_counts": review_status_counts,
            "descriptive_flag_counts": {
                "coverage_sufficient_for_temporal_review": sum(
                    item.coverage_sufficient_for_temporal_review
                    for item in temporal.summaries
                ),
                "directionally_consistent_ic": sum(
                    item.directionally_consistent_ic for item in temporal.summaries
                ),
                "directionally_consistent_spread": sum(
                    item.directionally_consistent_spread for item in temporal.summaries
                ),
                "descriptive_temporal_support": sum(
                    item.descriptive_temporal_support for item in temporal.summaries
                ),
                "all_blocks_positive_ic": sum(
                    item.all_blocks_positive_ic for item in temporal.summaries
                ),
                "all_blocks_positive_spread": sum(
                    item.all_blocks_positive_spread for item in temporal.summaries
                ),
                "all_blocks_negative_ic": sum(
                    item.all_blocks_negative_ic for item in temporal.summaries
                ),
                "all_blocks_negative_spread": sum(
                    item.all_blocks_negative_spread for item in temporal.summaries
                ),
            },
            "limitations": list(_TEMPORAL_LIMITATIONS),
        },
        "factor_redundancy": {
            "contract_name": redundancy.contract_name,
            "contract_version": redundancy.contract_version,
            "specification_fingerprint": redundancy.specification_fingerprint,
            "result_identity": redundancy.identity,
            "source_dataset_identity": redundancy.source_dataset_identity,
            "source_bounded_content_identity": redundancy.source_bounded_content_identity,
            "factor_count": len(NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1.factors),
            "canonical_pair_count": len(NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1.pairs),
            "temporal_block_count": len(NEUTRAL_PANEL_FACTOR_REDUNDANCY_V1.blocks),
            "block_result_count": len(redundancy.block_correlations),
            "summary_count": len(redundancy.summaries),
            "daily_record_count": len(redundancy.daily_correlations),
            "signal_date_count": len({
                item.signal_date for item in redundancy.daily_correlations
            }),
            "dates_represented": sorted({
                item.signal_date for item in redundancy.daily_correlations
            }),
            "defined_daily_correlation_count": sum(
                item.spearman_correlation is not None
                for item in redundancy.daily_correlations
            ),
            "undefined_daily_correlation_count": sum(
                item.spearman_correlation is None
                for item in redundancy.daily_correlations
            ),
            "artifacts": [
                "factor_redundancy_summary.csv",
                "factor_redundancy_by_block.csv",
                "factor_redundancy_by_date.csv",
            ],
            "completed": True,
            "limitations": list(_REDUNDANCY_LIMITATIONS),
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
        _validate_emitted(temporary, rows, evaluation, temporal, redundancy)
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
        "temporal_stability": temporal,
        "factor_redundancy": redundancy,
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
