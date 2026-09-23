from __future__ import annotations

"""Local, outcome-free diagnostics for accepted Quant Lab Frozen-Q70 candidates."""

import argparse
import csv
from datetime import date
from hashlib import sha256
from itertools import combinations
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Iterable

# Support the documented direct-script invocation without installation.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database_coverage import CoverageUniverseIndex, build_database_coverage_index
from core.paths import PROJECT_ROOT, resolve_market_database_path
from quantlab.adapters.frozen_q70_candidate_records import build_frozen_q70_candidate_records
from quantlab.alpha import FrozenQ70Policy
from quantlab.catalog.market_data_snapshot import MarketDataSnapshot, build_market_data_snapshot
from quantlab.diagnostics import (
    CandidateFactorDiagnosticsResult,
    FROZEN_Q70_CANDIDATE_DIAGNOSTICS_V1,
    diagnose_candidate_factors,
)
from quantlab.features import PointInTimeUniverseContext, PreparedFeatureCache
from strategy.hybrid_trend_donchian_entry import HybridTrendDonchianEntryModel


RUNNER_CONTRACT = "quantlab.candidate_factor_diagnostics_runner"
RUNNER_VERSION = "v1"
FROZEN_ENTRY_POLICY_IDENTITY = (
    "hybrid_trend_donchian_v1|mode=trend_context|trend_weight=0.4|"
    "donchian_weight=0.6|min_hybrid_score=60|use_regime_thresholds=true|"
    "require_hybrid_score=true"
)

_DESCRIPTIVE_COLUMNS = (
    "total_candidate_count", "finite_count", "missing_nonfinite_count",
    "finite_coverage_pct", "minimum", "maximum", "mean", "population_std",
    "median", "percentile_25", "percentile_75", "interquartile_range",
    "unique_finite_count", "tie_count", "tie_rate", "constant",
)
_STABILITY_COLUMNS = (
    "dates_with_finite_observations", "constant_date_count",
    "mean_date_finite_coverage_pct", "mean_date_median",
    "population_std_date_median", "mean_date_iqr",
)
_ASSOCIATION_COLUMNS = (
    "first_factor", "second_factor", "pairwise_finite_count",
    "pearson_correlation", "spearman_correlation", "undefined_reason",
)
_SUMMARY_COLUMNS = ("factor", *_DESCRIPTIVE_COLUMNS, *_STABILITY_COLUMNS)
_BY_DATE_COLUMNS = ("signal_date", "factor", *_DESCRIPTIVE_COLUMNS)
_AGGREGATE_ASSOCIATION_COLUMNS = (*_ASSOCIATION_COLUMNS, "scope")
_BY_DATE_ASSOCIATION_COLUMNS = ("signal_date", *_ASSOCIATION_COLUMNS)
_COUNT_COLUMNS = (
    "signal_date", "accepted_candidate_count", "coverage_universe_member_count",
    "diagnostic_candidate_count",
)
_REQUIRED_FILENAMES = (
    "experiment_manifest.json",
    "factor_summary.csv",
    "factor_by_date.csv",
    "factor_associations_aggregate.csv",
    "factor_associations_by_date.csv",
    "candidate_counts_by_date.csv",
    "assumptions.md",
)


class _ObservedPreparedFeatureCache(PreparedFeatureCache):
    """Runner-local observation seam; cache keys and persistence stay unchanged."""

    def __init__(self, cache_root: str | Path, *, codec: str | None = None) -> None:
        if codec is None:
            super().__init__(cache_root)
        else:
            super().__init__(cache_root, codec=codec)
        self.last_hit: bool | None = None
        self.lookup_count = 0

    def get(self, identity):
        self.lookup_count += 1
        result = super().get(identity)
        self.last_hit = result is not None
        return result


def _date_text(value: str, *, name: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
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
        return (PROJECT_ROOT / "research_results" / f"quantlab_candidate_factor_diagnostics_{start_date}_{end_date}").resolve()
    candidate = Path(output_root).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()


def _validate_output_target(path: Path, *, database_path: Path, overwrite: bool) -> None:
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
        if resolved.is_symlink():
            raise ValueError("output_root must not be a symlink")
        if not resolved.is_dir():
            raise ValueError("output_root exists but is not a directory")
        if not overwrite:
            raise FileExistsError(f"output directory already exists: {resolved}")


def _entry_model() -> HybridTrendDonchianEntryModel:
    model = HybridTrendDonchianEntryModel(
        mode="trend_context",
        trend_weight=.4,
        donchian_weight=.6,
        min_hybrid_score=60,
        use_regime_thresholds=True,
        require_hybrid_score=True,
    )
    if (
        model.name != "hybrid_trend_donchian_v1__trend_context"
        or model.trend_weight != .4
        or model.donchian_weight != .6
        or model.min_hybrid_score != 60
        or not model.use_regime_thresholds
        or not model.require_hybrid_score
    ):
        raise ValueError("Hybrid Trend/Donchian authority no longer matches the runner contract")
    return model


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
        json.dumps(_safe_json(payload), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def _write_csv(path: Path, columns: tuple[str, ...], rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(columns), extrasaction="raise", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _csv_value(row.get(column)) for column in columns})


def _descriptive_dict(value) -> dict[str, Any]:
    return {column: getattr(value, column) for column in _DESCRIPTIVE_COLUMNS}


def _artifact_rows(
    diagnostics: CandidateFactorDiagnosticsResult,
    coverage: CoverageUniverseIndex,
    candidate_batch,
) -> dict[str, tuple[tuple[str, ...], list[dict[str, Any]]]]:
    factors = FROZEN_Q70_CANDIDATE_DIAGNOSTICS_V1.fields
    summary = []
    for factor in factors:
        descriptive = diagnostics.aggregate.per_factor[factor]
        stability = diagnostics.aggregate.date_level_stability[factor]
        summary.append({
            "factor": factor,
            **_descriptive_dict(descriptive),
            **{column: getattr(stability, column) for column in _STABILITY_COLUMNS},
        })
    by_date = [
        {"signal_date": date_diagnostic.signal_date, "factor": factor, **_descriptive_dict(date_diagnostic.per_factor[factor])}
        for date_diagnostic in diagnostics.per_date
        for factor in factors
    ]
    aggregate_associations = [
        {**{column: getattr(item, column) for column in _ASSOCIATION_COLUMNS}, "scope": diagnostics.aggregate.scope}
        for item in diagnostics.aggregate.pairwise_associations
    ]
    date_associations = [
        {"signal_date": date_diagnostic.signal_date, **{column: getattr(item, column) for column in _ASSOCIATION_COLUMNS}}
        for date_diagnostic in diagnostics.per_date
        for item in date_diagnostic.pairwise_associations
    ]
    candidate_counts = {date_text: len(candidate_batch.candidates_for_signal_date(date_text)) for date_text in candidate_batch.signal_dates}
    counts = [
        {
            "signal_date": date_diagnostic.signal_date,
            "accepted_candidate_count": candidate_counts[date_diagnostic.signal_date],
            "coverage_universe_member_count": len(coverage.members_as_of(date_diagnostic.signal_date)),
            "diagnostic_candidate_count": date_diagnostic.candidate_count,
        }
        for date_diagnostic in diagnostics.per_date
    ]
    return {
        "factor_summary.csv": (_SUMMARY_COLUMNS, summary),
        "factor_by_date.csv": (_BY_DATE_COLUMNS, by_date),
        "factor_associations_aggregate.csv": (_AGGREGATE_ASSOCIATION_COLUMNS, aggregate_associations),
        "factor_associations_by_date.csv": (_BY_DATE_ASSOCIATION_COLUMNS, date_associations),
        "candidate_counts_by_date.csv": (_COUNT_COLUMNS, counts),
    }


def _validate_reconciliation(diagnostics, candidate_batch, rows) -> None:
    factors = FROZEN_Q70_CANDIDATE_DIAGNOSTICS_V1.fields
    if diagnostics.aggregate.total_candidate_observations != candidate_batch.candidate_count:
        raise ValueError("candidate count does not reconcile with aggregate diagnostics")
    for date_diagnostic in diagnostics.per_date:
        expected = len(candidate_batch.candidates_for_signal_date(date_diagnostic.signal_date))
        if date_diagnostic.candidate_count != expected:
            raise ValueError(f"candidate count does not reconcile for {date_diagnostic.signal_date}")
    if tuple(row["factor"] for row in rows["factor_summary.csv"][1]) != factors:
        raise ValueError("factor summary does not contain each built-in factor exactly once")
    expected_pairs = tuple(combinations(factors, 2))
    aggregate_pairs = tuple((row["first_factor"], row["second_factor"]) for row in rows["factor_associations_aggregate.csv"][1])
    if aggregate_pairs != expected_pairs:
        raise ValueError("aggregate associations do not contain each canonical pair exactly once")
    for date_diagnostic in diagnostics.per_date:
        date_rows = [row for row in rows["factor_associations_by_date.csv"][1] if row["signal_date"] == date_diagnostic.signal_date]
        if tuple((row["first_factor"], row["second_factor"]) for row in date_rows) != expected_pairs:
            raise ValueError(f"per-date associations do not reconcile for {date_diagnostic.signal_date}")
    count_rows = rows["candidate_counts_by_date.csv"][1]
    if any(row["accepted_candidate_count"] != row["diagnostic_candidate_count"] for row in count_rows):
        raise ValueError("accepted and diagnostic candidate counts do not reconcile")


def _validate_emitted(directory: Path, rows: dict[str, tuple[tuple[str, ...], list[dict[str, Any]]]]) -> None:
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
    actual = tuple(sorted(path.name for path in directory.iterdir()))
    if actual != tuple(sorted(_REQUIRED_FILENAMES)):
        raise ValueError("experiment artifact set is incomplete")


_ASSUMPTIONS = """# Assumptions and limitations

- No outcomes, future returns, realized trades, PnL, exits, costs, labels, or benchmark performance are used.
- Diagnostics are conditional on candidates that passed the base entry model, Frozen-Q70 threshold, PaperV2 state policy, and point-in-time database-coverage eligibility.
- This is selection-conditioned diagnostics, not whole-market factor diagnostics.
- `quality_score` is mechanically derived from score, relative-strength-20D, and ADX percentiles; association with those components is structurally expected.
- Breadth fields are market-date context shared across candidates on a date and can be constant within a daily cross-section.
- Pooled correlations are descriptive and are not cross-sectional rank information coefficients.
- Database coverage measures local data availability and is not reconstructed historical VN100 membership.
- No alpha improvement, factor-weight recommendation, or portfolio-performance claim is made.
"""


def run_candidate_factor_diagnostics(
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
        resolved_cache = Path(cache_root).expanduser().resolve()
        if (
            resolved_cache == database
            or _is_within(resolved_cache, output)
            or _is_within(output, resolved_cache)
        ):
            raise ValueError("cache_root must be separate from the database and experiment output")
    else:
        resolved_cache = None

    database_digest = _file_sha256(database)
    snapshot: MarketDataSnapshot = build_market_data_snapshot(database)
    if snapshot.first_session_date is None:
        raise ValueError("market snapshot contains no sessions")
    # Membership context starts at the first stored session so the v6 graph can
    # produce causal pre-start breadth/state warmup.  Candidate evaluation is
    # still trimmed to the explicit requested start below.
    coverage = build_database_coverage_index(
        snapshot.first_session_date, end,
        minimum_history_sessions=minimum_history_sessions,
        maximum_staleness_sessions=maximum_staleness_sessions,
        database_path=database,
    )
    if not coverage.candidate_symbols:
        raise ValueError("database coverage produced no candidate symbols for the requested range")
    universe_context = PointInTimeUniverseContext.from_coverage_index(coverage)
    model = _entry_model()
    cache = None if resolved_cache is None else _ObservedPreparedFeatureCache(resolved_cache, codec=cache_codec)
    candidate_batch = build_frozen_q70_candidate_records(
        snapshot,
        coverage.candidate_symbols,
        benchmark_symbol=benchmark,
        universe_context=universe_context,
        start_date=start,
        through_date=end,
        entry_model=model,
        entry_policy_identity=FROZEN_ENTRY_POLICY_IDENTITY,
        market_context_identity=universe_context.membership_identity,
        feature_cache=cache,
    )
    frozen_q70_fingerprint = FrozenQ70Policy().fingerprint
    expected_provenance = (
        snapshot.snapshot_id,
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
        raise ValueError("candidate batch date bounds do not match the requested interval")
    diagnostics = diagnose_candidate_factors(candidate_batch, FROZEN_Q70_CANDIDATE_DIAGNOSTICS_V1)
    rows = _artifact_rows(diagnostics, coverage, candidate_batch)
    _validate_reconciliation(diagnostics, candidate_batch, rows)
    if _file_sha256(database) != database_digest:
        raise RuntimeError("market database changed during read-only diagnostics")

    cache_manifest = {
        "enabled": cache is not None,
        "root": None if resolved_cache is None else str(resolved_cache),
        "codec": None if cache is None else (cache_codec or "sqlite-json-v1"),
        "lookup_count": 0 if cache is None else cache.lookup_count,
        "hit": None if cache is None else cache.last_hit,
    }
    signal_dates = candidate_batch.signal_dates
    manifest = {
        "runner_contract": RUNNER_CONTRACT,
        "runner_version": RUNNER_VERSION,
        "completion_status": "complete",
        "database_path": str(database),
        "database_sha256": database_digest,
        "snapshot_id": snapshot.snapshot_id,
        "logical_content_fingerprint": snapshot.logical_content_fingerprint,
        "requested_start_date": start,
        "requested_end_date": end,
        "effective_coverage_start_date": coverage.effective_start_date,
        "effective_coverage_end_date": coverage.effective_end_date,
        "first_candidate_signal_date": signal_dates[0] if signal_dates else None,
        "last_candidate_signal_date": signal_dates[-1] if signal_dates else None,
        "benchmark_symbol": benchmark,
        "universe_mode": "database_coverage",
        "minimum_history_sessions": minimum_history_sessions,
        "maximum_staleness_sessions": maximum_staleness_sessions,
        "universe_membership_identity": universe_context.membership_identity,
        "entry_policy_identity": FROZEN_ENTRY_POLICY_IDENTITY,
        "frozen_q70_policy_fingerprint": frozen_q70_fingerprint,
        "candidate_batch_identity": candidate_batch.batch_identity,
        "diagnostic_factor_set_fingerprint": FROZEN_Q70_CANDIDATE_DIAGNOSTICS_V1.fingerprint,
        "diagnostics_result_identity": diagnostics.result_identity,
        "cache": cache_manifest,
        "candidate_count": candidate_batch.candidate_count,
        "signal_date_count": len(signal_dates),
        "limitations": [
            "Database coverage is local data availability, not historical VN100 membership.",
            "Diagnostics are selection-conditioned on base-entry, Q70, state, and database-coverage acceptance.",
            "Pooled associations are descriptive and are not cross-sectional rank IC.",
            "No outcomes, future returns, or alpha-improvement claims are included.",
            "Snapshot/provenance identities cover the full local database; bounded diagnostic values use no rows after requested_end_date.",
        ],
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent)).resolve()
    try:
        for filename, (columns, artifact_rows) in rows.items():
            _write_csv(temporary / filename, columns, artifact_rows)
        (temporary / "assumptions.md").write_text(_ASSUMPTIONS, encoding="utf-8", newline="\n")
        _write_json(temporary / "experiment_manifest.json", manifest)
        _validate_emitted(temporary, rows)
        if _file_sha256(database) != database_digest:
            raise RuntimeError("market database changed before experiment publication")
        if output.exists():
            shutil.rmtree(output)
        temporary.replace(output)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return {"output_root": output, "manifest": manifest, "candidate_batch": candidate_batch, "diagnostics": diagnostics}


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
    result = run_candidate_factor_diagnostics(
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
