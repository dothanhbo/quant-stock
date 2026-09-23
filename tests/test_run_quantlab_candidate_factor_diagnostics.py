from __future__ import annotations

import csv
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType, SimpleNamespace

import pytest

import research.run_quantlab_candidate_factor_diagnostics as runner
from core.database_coverage import CoverageUniverseIndex
from quantlab.candidates import FrozenQ70CandidateBatch, FrozenQ70CandidateRecord
from quantlab.alpha import FrozenQ70Policy
from quantlab.diagnostics import diagnose_candidate_factors as real_diagnostics
from quantlab.features import PointInTimeUniverseContext


DAY_1 = "2024-01-02"
DAY_2 = "2024-01-03"


def _record(symbol: str, day: str, *, score: object, breadth: object = 70.0) -> FrozenQ70CandidateRecord:
    key = f"{symbol}:{day}"
    return FrozenQ70CandidateRecord(
        candidate_key=key, evaluation_key=key, symbol=symbol, signal_date=day,
        snapshot_id="snapshot", prepared_v6_identity="prepared",
        phase_3_7_run_identity="run", entry_policy_identity=runner.FROZEN_ENTRY_POLICY_IDENTITY,
        q70_policy_fingerprint=FrozenQ70Policy().fingerprint,
        universe_membership_identity=PointInTimeUniverseContext.from_coverage_index(_coverage()).membership_identity,
        score=score, relative_strength_20d=5.0, adx=20.0,
        component_percentiles=MappingProxyType({"score": 1.0, "relative_strength_20d": .8, "adx": .7}),
        quality_score=.8, q70_threshold=.7, acceptance_reason="Q0.70_PASS",
        paper_v2_state="HEALTHY_BULL", signal_close=10.0, atr14=1.0,
        atr_percent=2.0, rsi14=60.0, volume_ratio=1.2, ema10=9.8,
        ema20=9.5, ema50=9.0, previous_20d_high=9.9,
        donchian_breakout=True, market_regime="BULL",
        breadth_ema50_pct=breadth, breadth_ema50_change_10d=2.0,
        breadth_universe_count=2,
    )


def _candidate_batch(*, nonfinite: bool = False) -> FrozenQ70CandidateBatch:
    records = (
        _record("AAA", DAY_1, score=float("nan") if nonfinite else 10.0),
        _record("BBB", DAY_1, score=20.0),
        _record("AAA", DAY_2, score=30.0),
    )
    return FrozenQ70CandidateBatch(
        candidates=records, requested_symbols=("AAA", "BBB"), available_symbols=("AAA", "BBB"),
        start_date=DAY_1, through_date=DAY_2, snapshot_id="snapshot",
        prepared_v6_identity="prepared", phase_3_7_run_identity="run",
        entry_policy_identity=runner.FROZEN_ENTRY_POLICY_IDENTITY,
        q70_policy_fingerprint=FrozenQ70Policy().fingerprint,
        universe_membership_identity=PointInTimeUniverseContext.from_coverage_index(_coverage()).membership_identity,
    )


def _coverage() -> CoverageUniverseIndex:
    return CoverageUniverseIndex(
        start_date="2019-01-01", end_date=DAY_2,
        effective_start_date="2019-01-01", effective_end_date=DAY_2,
        minimum_history_sessions=50, maximum_staleness_sessions=5,
        session_dates=(DAY_1, DAY_2), candidate_symbols=("AAA", "BBB"),
        eligible_count_by_session={DAY_1: 2, DAY_2: 1},
        _members_by_session={DAY_1: frozenset({"AAA", "BBB"}), DAY_2: frozenset({"AAA"})},
    )


def _install_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    nonfinite: bool = False,
    fail_diagnostics: bool = False,
):
    database = tmp_path / "market.db"
    database.write_bytes(b"immutable-market-database")
    snapshot = SimpleNamespace(
        canonical_db_path=database,
        snapshot_id="snapshot",
        logical_content_fingerprint="logical",
        first_session_date="2019-01-01",
        last_session_date=DAY_2,
    )
    coverage = _coverage()
    batch = _candidate_batch(nonfinite=nonfinite)
    calls = {"snapshot": 0, "coverage": 0, "candidate": 0, "diagnostics": 0}
    arguments = {}
    monkeypatch.setattr(runner, "resolve_market_database_path", lambda value: database)
    def build_snapshot(path):
        calls["snapshot"] += 1
        assert path == database
        return snapshot
    monkeypatch.setattr(runner, "build_market_data_snapshot", build_snapshot)
    def build_coverage(start, end, **kwargs):
        calls["coverage"] += 1
        arguments["coverage"] = (start, end, kwargs)
        return coverage
    monkeypatch.setattr(runner, "build_database_coverage_index", build_coverage)
    def build_candidates(observed_snapshot, symbols, **kwargs):
        calls["candidate"] += 1
        arguments["candidate"] = (observed_snapshot, tuple(symbols), kwargs)
        assert isinstance(kwargs["entry_model"], runner.HybridTrendDonchianEntryModel)
        assert kwargs["entry_policy_identity"] == runner.FROZEN_ENTRY_POLICY_IDENTITY
        cache = kwargs["feature_cache"]
        if cache is not None:
            cache.lookup_count = 1
            cache.last_hit = True
        return batch
    monkeypatch.setattr(runner, "build_frozen_q70_candidate_records", build_candidates)
    def diagnostics(candidate_batch, factor_set):
        calls["diagnostics"] += 1
        if fail_diagnostics:
            raise RuntimeError("controlled diagnostics failure")
        return real_diagnostics(candidate_batch, factor_set)
    monkeypatch.setattr(runner, "diagnose_candidate_factors", diagnostics)
    return database, calls, arguments


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, output_name: str = "experiment", **kwargs):
    database, calls, arguments = _install_pipeline(tmp_path, monkeypatch, nonfinite=kwargs.pop("nonfinite", False), fail_diagnostics=kwargs.pop("fail_diagnostics", False))
    output = tmp_path / output_name
    result = runner.run_candidate_factor_diagnostics(
        database_path=database, start_date=DAY_1, end_date=DAY_2,
        output_root=output, **kwargs,
    )
    return database, output, result, calls, arguments


def test_database_coverage_pipeline_builds_each_dependency_once_and_reconciles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database, output, result, calls, arguments = _run(tmp_path, monkeypatch)
    assert calls == {"snapshot": 1, "coverage": 1, "candidate": 1, "diagnostics": 1}
    coverage_start, coverage_end, coverage_kwargs = arguments["coverage"]
    assert (coverage_start, coverage_end) == ("2019-01-01", DAY_2)  # causal pre-start context
    assert coverage_kwargs["minimum_history_sessions"] == 50
    assert coverage_kwargs["maximum_staleness_sessions"] == 5
    observed_snapshot, symbols, candidate_kwargs = arguments["candidate"]
    assert observed_snapshot.snapshot_id == "snapshot" and symbols == ("AAA", "BBB")
    assert candidate_kwargs["start_date"] == DAY_1 and candidate_kwargs["through_date"] == DAY_2
    assert candidate_kwargs["benchmark_symbol"] == "VNINDEX"
    assert candidate_kwargs["universe_context"].universe_mode == "database_coverage"
    assert not hasattr(runner, "get_vn100_symbols")
    assert result["manifest"]["universe_mode"] == "database_coverage"
    assert result["manifest"]["candidate_count"] == 3
    assert result["manifest"]["signal_date_count"] == 2
    assert sha256(database.read_bytes()).hexdigest() == result["manifest"]["database_sha256"]

    with (output / "candidate_counts_by_date.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows == [
        {"signal_date": DAY_1, "accepted_candidate_count": "2", "coverage_universe_member_count": "2", "diagnostic_candidate_count": "2"},
        {"signal_date": DAY_2, "accepted_candidate_count": "1", "coverage_universe_member_count": "1", "diagnostic_candidate_count": "1"},
    ]


def test_artifact_set_schema_order_warnings_and_json_are_deterministic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _database, output, _result, _calls, _arguments = _run(tmp_path, monkeypatch, nonfinite=True)
    assert {path.name for path in output.iterdir()} == set(runner._REQUIRED_FILENAMES)
    manifest_text = (output / "experiment_manifest.json").read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert manifest["completion_status"] == "complete"
    assert "NaN" not in manifest_text and "Infinity" not in manifest_text
    assert manifest["limitations"] == [
        "Database coverage is local data availability, not historical VN100 membership.",
        "Diagnostics are selection-conditioned on base-entry, Q70, state, and database-coverage acceptance.",
        "Pooled associations are descriptive and are not cross-sectional rank IC.",
        "No outcomes, future returns, or alpha-improvement claims are included.",
        "Snapshot/provenance identities cover the full local database; bounded diagnostic values use no rows after requested_end_date.",
    ]
    assumptions = (output / "assumptions.md").read_text(encoding="utf-8")
    assert "mechanically derived from score, relative-strength-20D, and ADX" in assumptions
    assert "Breadth fields are market-date context shared" in assumptions
    assert "not reconstructed historical VN100" in assumptions
    with (output / "factor_summary.csv").open(encoding="utf-8", newline="") as stream:
        summary = list(csv.DictReader(stream))
    assert [row["factor"] for row in summary] == list(runner.FROZEN_Q70_CANDIDATE_DIAGNOSTICS_V1.fields)
    with (output / "factor_by_date.csv").open(encoding="utf-8", newline="") as stream:
        by_date = list(csv.DictReader(stream))
    assert [(row["signal_date"], row["factor"]) for row in by_date] == [
        (day, factor)
        for day in (DAY_1, DAY_2)
        for factor in runner.FROZEN_Q70_CANDIDATE_DIAGNOSTICS_V1.fields
    ]
    score_day = next(row for row in by_date if row["signal_date"] == DAY_1 and row["factor"] == "score")
    assert score_day["missing_nonfinite_count"] == "1"
    assert "nan" not in (output / "factor_by_date.csv").read_text(encoding="utf-8").lower()

    # A second logical run to another destination produces byte-identical artifacts.
    monkeypatch.undo()
    _database2, output2, _result2, _calls2, _arguments2 = _run(tmp_path, monkeypatch, output_name="experiment-two", nonfinite=True)
    for filename in runner._REQUIRED_FILENAMES:
        assert (output / filename).read_bytes() == (output2 / filename).read_bytes()


def test_npz_cache_configuration_is_propagated_and_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache_root = tmp_path / "cache"
    _database, _output, result, _calls, arguments = _run(
        tmp_path, monkeypatch, cache_root=cache_root, cache_codec="npz_numeric_v1",
    )
    cache = arguments["candidate"][2]["feature_cache"]
    assert isinstance(cache, runner._ObservedPreparedFeatureCache)
    assert result["manifest"]["cache"] == {
        "enabled": True,
        "root": str(cache_root.resolve()),
        "codec": "npz_numeric_v1",
        "lookup_count": 1,
        "hit": True,
    }


def test_existing_output_refusal_exact_overwrite_and_database_immutability(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database, calls, _arguments = _install_pipeline(tmp_path, monkeypatch)
    original = database.read_bytes()
    output = tmp_path / "experiment"; output.mkdir(); marker = output / "old.txt"; marker.write_text("old", encoding="utf-8")
    sibling = tmp_path / "sibling.txt"; sibling.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        runner.run_candidate_factor_diagnostics(database_path=database, start_date=DAY_1, end_date=DAY_2, output_root=output)
    assert calls == {"snapshot": 0, "coverage": 0, "candidate": 0, "diagnostics": 0}
    runner.run_candidate_factor_diagnostics(database_path=database, start_date=DAY_1, end_date=DAY_2, output_root=output, overwrite=True)
    assert not marker.exists() and (output / "experiment_manifest.json").exists()
    assert sibling.read_text(encoding="utf-8") == "keep"
    assert database.read_bytes() == original
    with pytest.raises(ValueError, match="dedicated experiment"):
        runner._validate_output_target(runner.PROJECT_ROOT, database_path=database, overwrite=True)


def test_failure_before_publication_leaves_no_new_output_and_preserves_old_on_overwrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database, _calls, _arguments = _install_pipeline(tmp_path, monkeypatch, fail_diagnostics=True)
    output = tmp_path / "new-output"
    with pytest.raises(RuntimeError, match="controlled"):
        runner.run_candidate_factor_diagnostics(database_path=database, start_date=DAY_1, end_date=DAY_2, output_root=output)
    assert not output.exists()

    existing = tmp_path / "existing"; existing.mkdir(); marker = existing / "keep.txt"; marker.write_text("keep", encoding="utf-8")
    with pytest.raises(RuntimeError, match="controlled"):
        runner.run_candidate_factor_diagnostics(database_path=database, start_date=DAY_1, end_date=DAY_2, output_root=existing, overwrite=True)
    assert marker.read_text(encoding="utf-8") == "keep"


def test_runner_invokes_no_trade_simulator_pnl_execution_or_outcome_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import backtesting.portfolio_simulator as portfolio_module
    import backtesting.trade as trade_module
    def forbidden(*args, **kwargs):
        raise AssertionError("outcome/execution behavior is outside this runner")
    monkeypatch.setattr(trade_module.Trade, "net_pnl", property(forbidden))
    monkeypatch.setattr(portfolio_module, "PortfolioSimulator", forbidden)
    _run(tmp_path, monkeypatch)


def test_cli_contract_and_fresh_import_have_no_side_effect(tmp_path: Path) -> None:
    parser = runner._parser()
    parsed = parser.parse_args(["--database-path", "db", "--start-date", DAY_1, "--end-date", DAY_2, "--cache-root", "cache", "--cache-codec", "npz_numeric_v1"])
    assert parsed.minimum_history_sessions == 50 and parsed.maximum_staleness_sessions == 5
    assert parsed.benchmark_symbol == "VNINDEX" and parsed.cache_codec == "npz_numeric_v1"
    missing = tmp_path / "must-not-exist.db"
    completed = subprocess.run(
        [sys.executable, "-c", "import research.run_quantlab_candidate_factor_diagnostics"],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, MARKET_DATABASE_PATH=str(missing)),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert not missing.exists()
