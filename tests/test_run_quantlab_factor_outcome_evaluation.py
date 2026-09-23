from __future__ import annotations

import csv
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType, SimpleNamespace

import pytest

import research.run_quantlab_factor_outcome_evaluation as runner
from core.database_coverage import CoverageUniverseIndex
from quantlab.alpha import FrozenQ70Policy
from quantlab.candidates import FrozenQ70CandidateBatch, FrozenQ70CandidateRecord
from quantlab.evaluation import evaluate_candidate_factor_outcomes as real_evaluator
from quantlab.features import PointInTimeUniverseContext
from quantlab.outcomes import (
    FORWARD_CLOSE_RETURNS_5_10_20_V1,
    CandidateForwardOutcome,
    CandidateForwardOutcomeSet,
    ForwardOutcomeStatus,
)


DAY_1 = "2024-01-02"
DAY_2 = "2024-01-03"
SNAPSHOT_END = "2024-02-01"
SYMBOLS = ("AAA", "BBB", "CCC", "DDD", "EEE")


def _coverage() -> CoverageUniverseIndex:
    members = frozenset(SYMBOLS)
    return CoverageUniverseIndex(
        start_date="2023-01-02", end_date=DAY_2,
        effective_start_date="2023-01-02", effective_end_date=DAY_2,
        minimum_history_sessions=50, maximum_staleness_sessions=5,
        session_dates=(DAY_1, DAY_2), candidate_symbols=SYMBOLS,
        eligible_count_by_session=MappingProxyType({DAY_1: 5, DAY_2: 5}),
        _members_by_session=MappingProxyType({DAY_1: members, DAY_2: members}),
    )


def _candidate_batch() -> FrozenQ70CandidateBatch:
    context = PointInTimeUniverseContext.from_coverage_index(_coverage())
    policy = FrozenQ70Policy().fingerprint
    records = []
    for day_index, day in enumerate((DAY_1, DAY_2)):
        for symbol_index, symbol in enumerate(SYMBOLS):
            value = float(symbol_index + 1 + day_index)
            key = f"{symbol}:{day}"
            records.append(FrozenQ70CandidateRecord(
                candidate_key=key, evaluation_key=key, symbol=symbol,
                signal_date=day, snapshot_id="snapshot",
                prepared_v6_identity="prepared", phase_3_7_run_identity="phase-3.7",
                entry_policy_identity=runner.FROZEN_ENTRY_POLICY_IDENTITY,
                q70_policy_fingerprint=policy,
                universe_membership_identity=context.membership_identity,
                score=value, relative_strength_20d=value + 1.0, adx=value + 20.0,
                component_percentiles=MappingProxyType({
                    "score": value / 5.0,
                    "relative_strength_20d": value / 6.0,
                    "adx": value / 7.0,
                }),
                quality_score=.75 + value / 100.0, q70_threshold=.70,
                acceptance_reason="Q0.70_PASS", paper_v2_state="HEALTHY_BULL",
                signal_close=10.0, atr14=1.0, atr_percent=value,
                rsi14=50.0 + value, volume_ratio=1.0 + value / 10.0,
                ema10=9.8, ema20=9.6, ema50=9.0,
                previous_20d_high=9.9, donchian_breakout=True,
                market_regime="BULL", breadth_ema50_pct=70.0,
                breadth_ema50_change_10d=2.0, breadth_universe_count=5,
            ))
    return FrozenQ70CandidateBatch(
        candidates=tuple(records), requested_symbols=SYMBOLS,
        available_symbols=SYMBOLS, start_date=DAY_1, through_date=DAY_2,
        snapshot_id="snapshot", prepared_v6_identity="prepared",
        phase_3_7_run_identity="phase-3.7",
        entry_policy_identity=runner.FROZEN_ENTRY_POLICY_IDENTITY,
        q70_policy_fingerprint=policy,
        universe_membership_identity=context.membership_identity,
    )


def _outcomes(batch: FrozenQ70CandidateBatch) -> CandidateForwardOutcomeSet:
    records = []
    targets = {5: "2024-01-10", 10: "2024-01-17", 20: SNAPSHOT_END}
    for candidate in batch.candidates:
        for horizon in FORWARD_CLOSE_RETURNS_5_10_20_V1.horizons:
            censored = candidate.signal_date == DAY_2 and horizon == 20
            stock_return = None if censored else float(ord(candidate.symbol[0]) - 64 + horizon / 10.0)
            benchmark_return = None if censored else float(horizon / 20.0)
            records.append(CandidateForwardOutcome(
                candidate_key=candidate.candidate_key, symbol=candidate.symbol,
                signal_date=candidate.signal_date, horizon_sessions=horizon,
                target_market_session_date=None if censored else targets[horizon],
                status=(
                    ForwardOutcomeStatus.CENSORED_AFTER_DATA_END
                    if censored else ForwardOutcomeStatus.AVAILABLE
                ),
                signal_close=None if censored else 10.0,
                target_close=None if censored else 10.0 * (1.0 + stock_return / 100.0),
                stock_forward_return_pct=stock_return,
                benchmark_signal_close=None if censored else 1000.0,
                benchmark_target_close=(
                    None if censored else 1000.0 * (1.0 + benchmark_return / 100.0)
                ),
                benchmark_forward_return_pct=benchmark_return,
                excess_forward_return_percentage_points=(
                    None if censored else stock_return - benchmark_return
                ),
                source_candidate_batch_identity=batch.batch_identity,
                snapshot_id="snapshot",
                outcome_spec_fingerprint=FORWARD_CLOSE_RETURNS_5_10_20_V1.fingerprint,
            ))
    return CandidateForwardOutcomeSet(
        tuple(records), batch.batch_identity, "snapshot",
        FORWARD_CLOSE_RETURNS_5_10_20_V1.fingerprint,
        FORWARD_CLOSE_RETURNS_5_10_20_V1.horizons,
    )


def _install_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    warm_cache: bool = False,
    fail_evaluation: bool = False,
):
    database = tmp_path / "market.db"
    database.write_bytes(b"immutable-market-database")
    load_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    base_snapshot = SimpleNamespace(
        canonical_db_path=database,
        snapshot_id="snapshot",
        logical_content_fingerprint="logical",
        first_session_date="2023-01-02",
        last_session_date=SNAPSHOT_END,
        load_ohlcv=lambda *args, **kwargs: load_calls.append((args, kwargs)),
    )
    coverage = _coverage()
    batch = _candidate_batch()
    outcome_set = _outcomes(batch)
    calls = {"snapshot": 0, "coverage": 0, "context": 0, "candidate": 0, "label": 0, "evaluate": 0}
    arguments: dict[str, object] = {}

    monkeypatch.setattr(runner, "resolve_market_database_path", lambda value: database)
    monkeypatch.setattr(runner, "build_market_data_snapshot", lambda path: calls.__setitem__("snapshot", calls["snapshot"] + 1) or base_snapshot)
    monkeypatch.setattr(runner, "build_database_coverage_index", lambda *args, **kwargs: calls.__setitem__("coverage", calls["coverage"] + 1) or arguments.setdefault("coverage", (args, kwargs)) and coverage)
    original_context = PointInTimeUniverseContext.from_coverage_index
    def build_context(index):
        calls["context"] += 1
        return original_context(index)
    monkeypatch.setattr(runner.PointInTimeUniverseContext, "from_coverage_index", build_context)

    def build_candidates(snapshot, symbols, **kwargs):
        calls["candidate"] += 1
        arguments["candidate"] = (snapshot, tuple(symbols), kwargs)
        assert kwargs["entry_model"].name == "hybrid_trend_donchian_v1__trend_context"
        assert kwargs["entry_policy_identity"] == runner.FROZEN_ENTRY_POLICY_IDENTITY
        cache = kwargs["feature_cache"]
        if cache is not None:
            cache.lookup_count = 1
            cache.last_hit = warm_cache
        if not warm_cache:
            snapshot.load_ohlcv(symbols, through_date=DAY_2)
        return batch
    monkeypatch.setattr(runner, "build_frozen_q70_candidate_records", build_candidates)

    def label(snapshot, observed_batch, spec):
        calls["label"] += 1
        assert observed_batch is batch and spec is FORWARD_CLOSE_RETURNS_5_10_20_V1
        snapshot.load_ohlcv((*SYMBOLS, "VNINDEX"), through_date=SNAPSHOT_END)
        return outcome_set
    monkeypatch.setattr(runner, "label_candidate_forward_outcomes", label)

    def evaluate(observed_batch, observed_outcomes, spec):
        calls["evaluate"] += 1
        if fail_evaluation:
            raise RuntimeError("controlled evaluation failure")
        return real_evaluator(observed_batch, observed_outcomes, spec)
    monkeypatch.setattr(runner, "evaluate_candidate_factor_outcomes", evaluate)
    return database, calls, arguments, load_calls


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, output_name: str = "experiment", **kwargs):
    database, calls, arguments, load_calls = _install_pipeline(
        tmp_path, monkeypatch,
        warm_cache=kwargs.pop("warm_cache", False),
        fail_evaluation=kwargs.pop("fail_evaluation", False),
    )
    output = tmp_path / output_name
    result = runner.run_factor_outcome_evaluation(
        database_path=database, start_date=DAY_1, end_date=DAY_2,
        output_root=output, **kwargs,
    )
    return database, output, result, calls, arguments, load_calls


def test_database_coverage_pipeline_builds_each_dependency_once_and_reconciles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.universe as universe
    monkeypatch.setattr(universe, "get_vn100_symbols", lambda: (_ for _ in ()).throw(AssertionError("VN100/network forbidden")))
    database, _output, result, calls, arguments, load_calls = _run(tmp_path, monkeypatch)
    assert calls == {"snapshot": 1, "coverage": 1, "context": 1, "candidate": 1, "label": 1, "evaluate": 1}
    assert len(load_calls) == 2
    coverage_args, coverage_kwargs = arguments["coverage"]
    assert coverage_args == ("2023-01-02", DAY_2)
    assert coverage_kwargs == {
        "minimum_history_sessions": 50,
        "maximum_staleness_sessions": 5,
        "database_path": database,
    }
    _snapshot, symbols, candidate_kwargs = arguments["candidate"]
    assert symbols == SYMBOLS
    assert candidate_kwargs["start_date"] == DAY_1
    assert candidate_kwargs["through_date"] == DAY_2
    assert candidate_kwargs["benchmark_symbol"] == "VNINDEX"
    assert candidate_kwargs["universe_context"].universe_mode == "database_coverage"
    manifest = result["manifest"]
    assert manifest["market_data_load_counts"] == {
        "candidate_feature_loads": 1,
        "outcome_label_loads": 1,
        "total_snapshot_ohlcv_loads": 2,
    }
    assert manifest["candidate_count"] == 10
    assert manifest["outcome_count"] == 30
    assert manifest["evaluation_summary_count"] == 54
    assert manifest["evaluation_daily_count"] == 108
    assert manifest["candidate_batch_identity"] == result["candidate_batch"].batch_identity
    assert manifest["outcome_set_identity"] == result["outcome_set"].set_identity
    assert manifest["evaluation_result_identity"] == result["evaluation"].result_identity
    assert sha256(database.read_bytes()).hexdigest() == manifest["database_sha256"]


@pytest.mark.parametrize("warm_cache,expected_candidate_loads,expected_total", [(False, 1, 2), (True, 0, 1)])
def test_cold_and_warm_npz_load_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warm_cache: bool,
    expected_candidate_loads: int,
    expected_total: int,
) -> None:
    _database, _output, result, _calls, _arguments, load_calls = _run(
        tmp_path, monkeypatch, warm_cache=warm_cache,
        cache_root=tmp_path / "cache", cache_codec="npz_numeric_v1",
    )
    assert len(load_calls) == expected_total
    assert result["manifest"]["market_data_load_counts"] == {
        "candidate_feature_loads": expected_candidate_loads,
        "outcome_label_loads": 1,
        "total_snapshot_ohlcv_loads": expected_total,
    }
    assert result["manifest"]["cache"]["hit"] is warm_cache


def test_artifacts_order_status_tail_censoring_nulls_and_assumptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _database, output, result, _calls, _arguments, _loads = _run(tmp_path, monkeypatch)
    assert {path.name for path in output.iterdir()} == set(runner._REQUIRED_FILENAMES)
    manifest_text = (output / "experiment_manifest.json").read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert "NaN" not in manifest_text and "Infinity" not in manifest_text
    assert manifest["requested_signal_end_date"] == DAY_2
    assert manifest["snapshot_last_session_date"] == SNAPSHOT_END
    assert manifest["label_data_availability_end_date"] == SNAPSHOT_END
    assert manifest["outcome_status_counts_by_horizon"]["20"]["CENSORED_AFTER_DATA_END"] == 5

    with (output / "factor_outcome_summary.csv").open(encoding="utf-8", newline="") as stream:
        summary = list(csv.DictReader(stream))
    expected_order = [
        (factor, str(horizon), outcome)
        for factor in runner.FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1.factors
        for horizon in runner.FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1.horizons
        for outcome in runner.FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1.outcome_fields
    ]
    assert [(row["factor"], row["horizon_sessions"], row["outcome_field"]) for row in summary] == expected_order
    assert len(summary) == 54
    breadth = next(row for row in summary if row["factor"] == "breadth_ema50_pct")
    assert breadth["mean_daily_rank_ic"] == ""
    assert "nan" not in (output / "factor_outcome_summary.csv").read_text(encoding="utf-8").lower()

    with (output / "factor_outcome_by_date.csv").open(encoding="utf-8", newline="") as stream:
        daily = list(csv.DictReader(stream))
    assert len(daily) == 108
    assert [(row["signal_date"], row["factor"], row["horizon_sessions"], row["outcome_field"]) for row in daily] == [
        (day, factor, str(horizon), outcome)
        for day in (DAY_1, DAY_2)
        for factor in runner.FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1.factors
        for horizon in runner.FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1.horizons
        for outcome in runner.FROZEN_Q70_FACTOR_OUTCOMES_5_10_20_V1.outcome_fields
    ]
    with (output / "outcome_status_counts.csv").open(encoding="utf-8", newline="") as stream:
        status_rows = list(csv.DictReader(stream))
    assert len(status_rows) == 3 * len(tuple(ForwardOutcomeStatus))
    assert sum(int(row["count"]) for row in status_rows) == 30
    assumptions = (output / "assumptions.md").read_text(encoding="utf-8")
    for phrase in (
        "future-looking research outcomes", "not tradable execution returns",
        "Forward horizons overlap", "selection-conditioned", "historically small",
        "Breadth is shared market context", "mechanically derived",
        "not historical VN100", "No factor weights were selected",
    ):
        assert phrase in assumptions
    assert result["evaluation"].source_outcome_set_identity == result["outcome_set"].set_identity


def test_coverage_flag_uses_counts_only_never_returns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _database, output, _result, _calls, _arguments, _loads = _run(tmp_path, monkeypatch)
    with (output / "evaluation_coverage_summary.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows and all(row["sufficient_for_interpretation"] == "false" for row in rows)
    assert set(rows[0]) == set(runner._COVERAGE_COLUMNS)
    assert not any("return" in column or "spread" in column or "ic_mean" in column for column in runner._COVERAGE_COLUMNS)


def test_repeated_outputs_are_byte_identical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _database, first, _result, _calls, _arguments, _loads = _run(tmp_path, monkeypatch, output_name="first")
    monkeypatch.undo()
    _database, second, _result, _calls, _arguments, _loads = _run(tmp_path, monkeypatch, output_name="second")
    for filename in runner._REQUIRED_FILENAMES:
        assert (first / filename).read_bytes() == (second / filename).read_bytes()


def test_existing_output_refusal_exact_overwrite_and_database_immutability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, calls, _arguments, _loads = _install_pipeline(tmp_path, monkeypatch)
    original = database.read_bytes()
    output = tmp_path / "experiment"
    output.mkdir()
    marker = output / "old.txt"
    marker.write_text("old", encoding="utf-8")
    sibling = tmp_path / "sibling.txt"
    sibling.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        runner.run_factor_outcome_evaluation(
            database_path=database, start_date=DAY_1, end_date=DAY_2, output_root=output,
        )
    assert calls == {"snapshot": 0, "coverage": 0, "context": 0, "candidate": 0, "label": 0, "evaluate": 0}
    runner.run_factor_outcome_evaluation(
        database_path=database, start_date=DAY_1, end_date=DAY_2,
        output_root=output, overwrite=True,
    )
    assert not marker.exists()
    assert (output / "experiment_manifest.json").exists()
    assert sibling.read_text(encoding="utf-8") == "keep"
    assert database.read_bytes() == original
    with pytest.raises(ValueError, match="dedicated experiment"):
        runner._validate_output_target(runner.PROJECT_ROOT, database_path=database, overwrite=True)


def test_failure_before_publication_preserves_existing_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, _calls, _arguments, _loads = _install_pipeline(tmp_path, monkeypatch, fail_evaluation=True)
    fresh = tmp_path / "fresh"
    with pytest.raises(RuntimeError, match="controlled evaluation"):
        runner.run_factor_outcome_evaluation(
            database_path=database, start_date=DAY_1, end_date=DAY_2, output_root=fresh,
        )
    assert not fresh.exists()

    monkeypatch.undo()
    database, _calls, _arguments, _loads = _install_pipeline(tmp_path, monkeypatch)
    existing = tmp_path / "existing"
    existing.mkdir()
    marker = existing / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(
        runner, "_validate_emitted",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("controlled artifact-validation failure")),
    )
    with pytest.raises(RuntimeError, match="controlled artifact-validation"):
        runner.run_factor_outcome_evaluation(
            database_path=database, start_date=DAY_1, end_date=DAY_2,
            output_root=existing, overwrite=True,
        )
    assert marker.read_text(encoding="utf-8") == "keep"


def test_cli_contract_forbidden_runtime_boundary_and_fresh_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = runner._parser()
    parsed = parser.parse_args([
        "--database-path", "db", "--start-date", DAY_1, "--end-date", DAY_2,
        "--cache-root", "cache", "--cache-codec", "npz_numeric_v1",
    ])
    assert parsed.minimum_history_sessions == 50
    assert parsed.maximum_staleness_sessions == 5
    assert parsed.benchmark_symbol == "VNINDEX"
    assert not hasattr(parsed, "universe_mode")

    import backtesting.portfolio_simulator as portfolio_module
    import backtesting.trade as trade_module
    def forbidden(*args, **kwargs):
        raise AssertionError("portfolio/execution behavior is outside this runner")
    monkeypatch.setattr(trade_module.Trade, "net_pnl", property(forbidden))
    monkeypatch.setattr(portfolio_module, "PortfolioSimulator", forbidden)
    _run(tmp_path, monkeypatch)

    missing = tmp_path / "must-not-exist.db"
    completed = subprocess.run(
        [sys.executable, "-c", "import research.run_quantlab_factor_outcome_evaluation"],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, MARKET_DATABASE_PATH=str(missing), PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert not missing.exists()
