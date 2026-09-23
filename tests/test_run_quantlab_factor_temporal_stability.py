from __future__ import annotations

import csv
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

import research.run_quantlab_factor_temporal_stability as runner
from quantlab.alpha import FrozenQ70Policy
from quantlab.evaluation import (
    FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1,
    FROZEN_Q70_VOLUME_RSI_TEMPORAL_STABILITY_V1,
    evaluate_candidate_factor_temporal_stability as real_temporal_evaluator,
)
from quantlab.identity import canonical_json
from quantlab.outcomes import FORWARD_CLOSE_RETURNS_5_10_20_V1, ForwardOutcomeStatus


START = "2018-08-07"
END = "2026-09-17"
DATES = ("2019-01-02", "2021-06-01", "2023-06-01", "2025-06-02")
SYMBOLS = ("AAA", "BBB")


def _fake_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, fail_temporal=False):
    database = tmp_path / "market.db"
    database.write_bytes(b"immutable-market-database")
    loads: list[tuple[object, ...]] = []
    base_snapshot = SimpleNamespace(
        snapshot_id="snapshot-id", logical_content_fingerprint="logical-fingerprint",
        first_session_date="2018-01-02", last_session_date="2026-10-30",
        load_ohlcv=lambda *args, **kwargs: loads.append((args, kwargs)),
    )
    coverage = SimpleNamespace(candidate_symbols=SYMBOLS)
    context = SimpleNamespace(membership_identity="coverage-identity")
    policy_fingerprint = FrozenQ70Policy().fingerprint
    candidates = tuple(
        SimpleNamespace(symbol=symbol, signal_date=day)
        for day in DATES for symbol in SYMBOLS
    )
    batch = SimpleNamespace(
        candidates=candidates, signal_dates=DATES, candidate_count=len(candidates),
        batch_identity="candidate-batch", snapshot_id="snapshot-id",
        entry_policy_identity=runner.FROZEN_ENTRY_POLICY_IDENTITY,
        q70_policy_fingerprint=policy_fingerprint,
        universe_membership_identity="coverage-identity", start_date=START,
        through_date=END,
    )
    outcomes = tuple(
        SimpleNamespace(
            candidate_key=f"{candidate.symbol}:{candidate.signal_date}",
            signal_date=candidate.signal_date, horizon_sessions=horizon,
            status=ForwardOutcomeStatus.AVAILABLE,
        )
        for candidate in candidates
        for horizon in FORWARD_CLOSE_RETURNS_5_10_20_V1.horizons
    )
    outcome_set = SimpleNamespace(
        outcomes=outcomes, source_candidate_batch_identity=batch.batch_identity,
        requested_horizons=FORWARD_CLOSE_RETURNS_5_10_20_V1.horizons,
        outcome_spec_fingerprint=FORWARD_CLOSE_RETURNS_5_10_20_V1.fingerprint,
        set_identity="outcome-set",
        status_counts_by_horizon={
            horizon: {
                status: (len(candidates) if status is ForwardOutcomeStatus.AVAILABLE else 0)
                for status in ForwardOutcomeStatus
            }
            for horizon in FORWARD_CLOSE_RETURNS_5_10_20_V1.horizons
        },
    )
    daily = []
    for date_index, day in enumerate(DATES):
        for factor in FROZEN_Q70_VOLUME_RSI_TEMPORAL_STABILITY_V1.factors:
            for horizon in FROZEN_Q70_VOLUME_RSI_TEMPORAL_STABILITY_V1.horizons:
                for outcome in FROZEN_Q70_VOLUME_RSI_TEMPORAL_STABILITY_V1.outcome_fields:
                    undefined = date_index == 0 and factor == "rsi14" and horizon == 20
                    daily.append(SimpleNamespace(
                        signal_date=day, factor=factor, horizon_sessions=horizon,
                        outcome_field=outcome, available_labeled_candidates=2,
                        rank_ic=None if undefined else 0.1 * (date_index + 1),
                        bucket_undefined_reason="insufficient" if undefined else None,
                        high_minus_low_mean_spread=None if undefined else 0.2,
                        high_minus_low_median_spread=None if undefined else 0.15,
                        low_bucket_count=0 if undefined else 1,
                        high_bucket_count=0 if undefined else 1,
                        identity=f"daily:{day}:{factor}:{horizon}:{outcome}",
                    ))
    evaluation = SimpleNamespace(
        source_candidate_batch_identity=batch.batch_identity,
        source_outcome_set_identity=outcome_set.set_identity,
        evaluation_spec_fingerprint=FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1.fingerprint,
        daily_evaluations=tuple(daily), result_identity="phase-4.5-result",
    )
    calls = {
        "snapshot": 0, "coverage": 0, "context": 0, "candidate": 0,
        "outcome": 0, "phase_4_5": 0, "phase_4_7a": 0,
    }

    monkeypatch.setattr(runner, "resolve_market_database_path", lambda value: database)
    def build_snapshot(path):
        calls["snapshot"] += 1
        return base_snapshot
    monkeypatch.setattr(runner, "build_market_data_snapshot", build_snapshot)
    def build_coverage(*args, **kwargs):
        calls["coverage"] += 1
        return coverage
    monkeypatch.setattr(runner, "build_database_coverage_index", build_coverage)
    def build_context(value):
        calls["context"] += 1
        assert value is coverage
        return context
    monkeypatch.setattr(runner.PointInTimeUniverseContext, "from_coverage_index", build_context)
    def build_candidates(snapshot, symbols, **kwargs):
        calls["candidate"] += 1
        assert tuple(symbols) == SYMBOLS
        assert kwargs["entry_policy_identity"] == runner.FROZEN_ENTRY_POLICY_IDENTITY
        snapshot.load_ohlcv(SYMBOLS)
        return batch
    monkeypatch.setattr(runner, "build_frozen_q70_candidate_records", build_candidates)
    def label(snapshot, observed_batch, spec):
        calls["outcome"] += 1
        assert observed_batch is batch and spec is FORWARD_CLOSE_RETURNS_5_10_20_V1
        snapshot.load_ohlcv((*SYMBOLS, "VNINDEX"))
        return outcome_set
    monkeypatch.setattr(runner, "label_candidate_forward_outcomes", label)
    def phase_4_5(observed_batch, observed_outcomes, spec):
        calls["phase_4_5"] += 1
        assert observed_batch is batch and observed_outcomes is outcome_set
        assert spec is FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1
        return evaluation
    monkeypatch.setattr(runner, "evaluate_candidate_factor_outcomes", phase_4_5)
    def phase_4_7a(observed_evaluation, spec):
        calls["phase_4_7a"] += 1
        assert observed_evaluation is evaluation
        assert spec is FROZEN_Q70_VOLUME_RSI_TEMPORAL_STABILITY_V1
        if fail_temporal:
            raise RuntimeError("controlled temporal failure")
        return real_temporal_evaluator(observed_evaluation, spec)
    monkeypatch.setattr(runner, "evaluate_candidate_factor_temporal_stability", phase_4_7a)
    return database, calls, loads


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, name="result", **kwargs):
    database, calls, loads = _fake_pipeline(
        tmp_path, monkeypatch, fail_temporal=kwargs.pop("fail_temporal", False),
    )
    output = tmp_path / name
    result = runner.run_factor_temporal_stability(
        database_path=database, start_date=START, end_date=END,
        output_root=output, **kwargs,
    )
    return database, output, result, calls, loads


def _read_csv(path: Path):
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        return tuple(reader.fieldnames or ()), list(reader)


def test_parser_and_default_output_path_are_exact() -> None:
    parser = runner._parser()
    option_strings = {
        item for action in parser._actions for item in action.option_strings
        if item not in {"-h", "--help"}
    }
    assert option_strings == {
        "--database-path", "--start-date", "--end-date",
        "--minimum-history-sessions", "--maximum-staleness-sessions",
        "--benchmark-symbol", "--output-root", "--overwrite",
        "--cache-root", "--cache-codec",
    }
    expected = runner.PROJECT_ROOT / "research_results" / f"quantlab_factor_temporal_stability_{START}_{END}"
    assert runner._output_path(None, start_date=START, end_date=END) == expected.absolute()


def test_exact_temporal_boundary_is_rejected_before_expensive_work(monkeypatch) -> None:
    monkeypatch.setattr(
        runner, "resolve_market_database_path",
        lambda value: (_ for _ in ()).throw(AssertionError("must not resolve DB")),
    )
    with pytest.raises(ValueError, match="exact built-in temporal boundary"):
        runner.run_factor_temporal_stability(
            database_path="unused", start_date="2018-08-08", end_date=END,
        )


def test_pipeline_runs_once_and_artifacts_reconcile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.universe as universe
    monkeypatch.setattr(
        universe, "get_vn100_symbols",
        lambda: (_ for _ in ()).throw(AssertionError("network/VN100 forbidden")),
    )
    database, output, result, calls, loads = _run(tmp_path, monkeypatch)
    assert calls == {
        "snapshot": 1, "coverage": 1, "context": 1, "candidate": 1,
        "outcome": 1, "phase_4_5": 1, "phase_4_7a": 1,
    }
    assert len(loads) == 2
    assert {item.name for item in output.iterdir()} == set(runner._REQUIRED_FILENAMES)
    summary_columns, summaries = _read_csv(output / "temporal_stability_summary.csv")
    block_columns, blocks = _read_csv(output / "temporal_stability_by_block.csv")
    coverage_columns, coverage = _read_csv(output / "temporal_coverage.csv")
    assert summary_columns == runner._SUMMARY_COLUMNS and len(summaries) == 12
    assert block_columns == runner._BLOCK_COLUMNS and len(blocks) == 48
    assert coverage_columns == runner._COVERAGE_COLUMNS and len(coverage) == 4
    spec = FROZEN_Q70_VOLUME_RSI_TEMPORAL_STABILITY_V1
    assert [(row["factor"], row["horizon_sessions"], row["outcome_field"]) for row in summaries] == [
        (factor, str(horizon), outcome)
        for factor in spec.factors for horizon in spec.horizons for outcome in spec.outcome_fields
    ]
    assert [row["block_name"] for row in coverage] == [item.name for item in spec.blocks]
    assert [(row["factor"], row["horizon_sessions"], row["outcome_field"], row["block_name"]) for row in blocks] == [
        (factor, str(horizon), outcome, block.name)
        for factor in spec.factors for horizon in spec.horizons
        for outcome in spec.outcome_fields for block in spec.blocks
    ]
    for row, immutable in zip(summaries, result["temporal_result"].summaries, strict=True):
        identities = tuple(item.identity for item in immutable.block_results)
        assert row["identity"] == immutable.identity
        assert int(row["ordered_block_identity_count"]) == 4
        assert row["ordered_block_identities_sha256"] == sha256(
            canonical_json(list(identities))
        ).hexdigest()
    assert "ordered_block_identities" not in summary_columns
    assert "included_daily_identities" not in block_columns
    assert sha256(database.read_bytes()).hexdigest() == result["manifest"]["database_sha256"]
    assert result["manifest"]["market_data_load_counts"] == {
        "candidate_feature_loads": 1, "outcome_label_loads": 1,
        "total_snapshot_ohlcv_loads": 2,
    }


def test_nulls_warnings_manifest_and_projection_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database, output, result, _calls, _loads = _run(tmp_path, monkeypatch)
    text = (output / "experiment_manifest.json").read_text(encoding="utf-8")
    assert "NaN" not in text and "Infinity" not in text
    manifest = json.loads(text)
    assert manifest["scope"]["factors"] == ["volume_ratio", "rsi14"]
    assert manifest["scope"]["horizons"] == [5, 10, 20]
    assert manifest["artifact_row_counts"] == {
        "temporal_stability_summary.csv": 12,
        "temporal_stability_by_block.csv": 48,
        "temporal_coverage.csv": 4,
    }
    assert "not independent evidence" in " ".join(manifest["warnings"])
    assumptions = (output / "assumptions.md").read_text(encoding="utf-8")
    assert "not historical VN100" in assumptions
    assert "not statistically independent" in assumptions
    assert "not statistical-significance claims" in assumptions
    _, blocks = _read_csv(output / "temporal_stability_by_block.csv")
    projected = next(
        row for row in blocks
        if row["factor"] == "rsi14" and row["horizon_sessions"] == "20"
        and row["outcome_field"] == "stock_forward_return_pct"
        and row["block_name"] == "early_2018_2020"
    )
    immutable = result["temporal_result"].summary_for(
        "rsi14", 20, "stock_forward_return_pct",
    ).block_results[0]
    assert projected["mean_daily_rank_ic"] == ""
    assert projected["identity"] == immutable.identity
    assert projected["included_daily_identities_sha256"] == immutable.included_daily_identities_sha256


def test_existing_output_rejected_and_overwrite_is_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, calls, _loads = _fake_pipeline(tmp_path, monkeypatch)
    output = tmp_path / "result"
    output.mkdir()
    marker = output / "old.txt"
    marker.write_text("old", encoding="utf-8")
    with pytest.raises(FileExistsError):
        runner.run_factor_temporal_stability(
            database_path=database, start_date=START, end_date=END, output_root=output,
        )
    assert marker.read_text(encoding="utf-8") == "old"
    assert sum(calls.values()) == 0
    runner.run_factor_temporal_stability(
        database_path=database, start_date=START, end_date=END,
        output_root=output, overwrite=True,
    )
    assert not marker.exists()
    assert {item.name for item in output.iterdir()} == set(runner._REQUIRED_FILENAMES)


def test_failure_before_publication_preserves_existing_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, _calls, _loads = _fake_pipeline(tmp_path, monkeypatch, fail_temporal=True)
    output = tmp_path / "result"
    output.mkdir()
    marker = output / "old.txt"
    marker.write_text("old", encoding="utf-8")
    with pytest.raises(RuntimeError, match="controlled temporal failure"):
        runner.run_factor_temporal_stability(
            database_path=database, start_date=START, end_date=END,
            output_root=output, overwrite=True,
        )
    assert tuple(output.iterdir()) == (marker,)
    assert marker.read_text(encoding="utf-8") == "old"

    absent_output = tmp_path / "absent-result"
    with pytest.raises(RuntimeError, match="controlled temporal failure"):
        runner.run_factor_temporal_stability(
            database_path=database, start_date=START, end_date=END,
            output_root=absent_output,
        )
    assert not absent_output.exists()


def test_failed_atomic_swap_restores_previous_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, _calls, _loads = _fake_pipeline(tmp_path, monkeypatch)
    output = tmp_path / "result"
    output.mkdir()
    marker = output / "old.txt"
    marker.write_text("old", encoding="utf-8")
    original_replace = Path.replace
    replacement_attempts = 0

    def fail_new_target_swap(path: Path, target: Path):
        nonlocal replacement_attempts
        if path.name.startswith(f".{output.name}.tmp-") and Path(target) == output:
            replacement_attempts += 1
            raise OSError("controlled swap failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_new_target_swap)
    with pytest.raises(OSError, match="controlled swap failure"):
        runner.run_factor_temporal_stability(
            database_path=database, start_date=START, end_date=END,
            output_root=output, overwrite=True,
        )
    assert replacement_attempts == 1
    assert tuple(output.iterdir()) == (marker,)
    assert marker.read_text(encoding="utf-8") == "old"


def test_unsafe_and_cache_containment_paths_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, _calls, _loads = _fake_pipeline(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="dedicated experiment directory"):
        runner.run_factor_temporal_stability(
            database_path=database, start_date=START, end_date=END,
            output_root=runner.PROJECT_ROOT / "research_results",
        )
    output = tmp_path / "result"
    with pytest.raises(ValueError, match="cache_root must be separate"):
        runner.run_factor_temporal_stability(
            database_path=database, start_date=START, end_date=END,
            output_root=output, cache_root=output / "cache",
        )
    cache_parent = tmp_path / "cache-parent"
    with pytest.raises(ValueError, match="cache_root must be separate"):
        runner.run_factor_temporal_stability(
            database_path=database, start_date=START, end_date=END,
            output_root=cache_parent / "result", cache_root=cache_parent,
        )


def test_equivalent_executions_are_byte_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database, first, _result, _calls, _loads = _run(tmp_path, monkeypatch, name="first")
    first_bytes = {item.name: item.read_bytes() for item in first.iterdir()}
    _database, second, _result, _calls, _loads = _run(tmp_path, monkeypatch, name="second")
    assert {item.name: item.read_bytes() for item in second.iterdir()} == first_bytes


def test_import_has_no_database_cache_network_or_output_side_effect(tmp_path: Path) -> None:
    code = (
        "import pathlib, sys; before=set(pathlib.Path('.').iterdir()); "
        "import research.run_quantlab_factor_temporal_stability; "
        "after=set(pathlib.Path('.').iterdir()); "
        "assert before == after; "
        "assert not any(name.startswith('vnstock') for name in sys.modules)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path,
        env={**__import__("os").environ, "PYTHONPATH": str(runner.PROJECT_ROOT)},
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_runner_has_no_trade_portfolio_or_backtest_dependency() -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")
    for forbidden in ("PortfolioSimulator", "run_backtest", "walk_forward", "Trade("):
        assert forbidden not in source
