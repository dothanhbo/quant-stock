from __future__ import annotations

from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import MappingProxyType, SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from quantlab.candidates import FrozenQ70CandidateBatch, FrozenQ70CandidateRecord
from quantlab.catalog.market_data_snapshot import MarketDataBundle, build_market_data_snapshot
from quantlab.outcomes import (
    CandidateForwardOutcomeSet,
    FORWARD_CLOSE_RETURNS_5_10_20_V1,
    ForwardOutcomeSpec,
    ForwardOutcomeStatus,
    label_candidate_forward_outcomes,
)


def _candidate(
    snapshot_id: str,
    *,
    key: str,
    symbol: str,
    signal_date: str,
    signal_close: object,
) -> FrozenQ70CandidateRecord:
    return FrozenQ70CandidateRecord(
        candidate_key=key,
        evaluation_key=key,
        symbol=symbol,
        signal_date=signal_date,
        snapshot_id=snapshot_id,
        prepared_v6_identity="prepared",
        phase_3_7_run_identity="phase-3-7",
        entry_policy_identity="entry",
        q70_policy_fingerprint="q70",
        universe_membership_identity="universe",
        score=90.0,
        relative_strength_20d=5.0,
        adx=25.0,
        component_percentiles=MappingProxyType({"score": .9, "relative_strength_20d": .8, "adx": .7}),
        quality_score=.8,
        q70_threshold=.7,
        acceptance_reason="Q0.70_PASS",
        paper_v2_state="HEALTHY_BULL",
        signal_close=signal_close,
        atr14=1.0,
        atr_percent=2.0,
        rsi14=60.0,
        volume_ratio=1.2,
        ema10=9.8,
        ema20=9.5,
        ema50=9.0,
        previous_20d_high=9.9,
        donchian_breakout=True,
        market_regime="BULL",
        breadth_ema50_pct=70.0,
        breadth_ema50_change_10d=2.0,
        breadth_universe_count=50,
    )


def _batch(
    snapshot_id: str,
    candidates: tuple[FrozenQ70CandidateRecord, ...],
) -> FrozenQ70CandidateBatch:
    dates = tuple(item.signal_date for item in candidates)
    start = min(dates) if dates else "2024-01-01"
    through = max(dates) if dates else "2024-01-01"
    symbols = tuple(item.symbol for item in candidates)
    return FrozenQ70CandidateBatch(
        candidates=candidates,
        requested_symbols=symbols,
        available_symbols=symbols,
        start_date=start,
        through_date=through,
        snapshot_id=snapshot_id,
        prepared_v6_identity="prepared",
        phase_3_7_run_identity="phase-3-7",
        entry_policy_identity="entry",
        q70_policy_fingerprint="q70",
        universe_membership_identity="universe",
    )


def _database(path: Path, rows: list[tuple[object, ...]]) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE prices (symbol TEXT, time TEXT, open REAL, high REAL, "
            "low REAL, close REAL, volume INTEGER)"
        )
        connection.executemany("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def _canonical_fixture(tmp_path: Path):
    path = tmp_path / "market.db"
    dates = [item.date().isoformat() for item in pd.bdate_range("2024-01-02", periods=24)]
    # Explicit market-calendar holes exercise session counting across a weekday
    # holiday as well as ordinary weekends.
    dates.pop(7)
    dates.pop(3)
    rows: list[tuple[object, ...]] = []
    rows.append((" aaa ", dates[0], 998.0, 1000.0, 997.0, 999.0, 1))
    for index, day in enumerate(dates):
        stock = 100.0 + index
        benchmark = 1000.0 + 2.0 * index
        rows.append(("AAA", day, stock, stock, stock, stock, 100 + index))
        rows.append(("VNINDEX", day, benchmark, benchmark, benchmark, benchmark, 1000 + index))
    _database(path, rows)
    snapshot = build_market_data_snapshot(path)
    candidate = _candidate(
        snapshot.snapshot_id,
        key=f"AAA:{dates[0]}",
        symbol="aaa",
        signal_date=dates[0],
        signal_close=100.0,
    )
    return snapshot, dates, _batch(snapshot.snapshot_id, (candidate,))


class _FakeSnapshot:
    def __init__(self, snapshot_id: str, frames: dict[str, pd.DataFrame]) -> None:
        self.snapshot_id = snapshot_id
        self.last_session_date = max(
            pd.Timestamp(value).date().isoformat()
            for frame in frames.values()
            for value in frame["time"]
        )
        self.frames = {key: value.copy(deep=True) for key, value in frames.items()}
        self.loads: list[tuple[tuple[str, ...], object]] = []

    def load_ohlcv(self, symbols, *, through_date=None, **_kwargs) -> MarketDataBundle:
        requested = tuple(sorted({str(item).strip().upper() for item in symbols}))
        self.loads.append((requested, through_date))
        selected = {symbol: self.frames[symbol].copy(deep=True) for symbol in requested if symbol in self.frames}
        return MarketDataBundle(
            requested,
            tuple(sorted(selected)),
            tuple(symbol for symbol in requested if symbol not in selected),
            None,
            through_date,
            sum(len(frame) for frame in selected.values()),
            MappingProxyType(selected),
            "test-double",
            sum(len(frame) for frame in selected.values()),
        )


def _frame(rows: list[tuple[str, object]]) -> pd.DataFrame:
    return pd.DataFrame({"time": pd.to_datetime([item[0] for item in rows]), "close": [item[1] for item in rows]})


def test_exact_market_session_targets_returns_and_one_union_load(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, dates, batch = _canonical_fixture(tmp_path)
    calls: list[tuple[object, ...]] = []
    original = type(snapshot).load_ohlcv

    def counted(self, symbols, **kwargs):
        calls.append((tuple(symbols), kwargs))
        return original(self, symbols, **kwargs)

    monkeypatch.setattr(type(snapshot), "load_ohlcv", counted)
    result = label_candidate_forward_outcomes(snapshot, batch, FORWARD_CLOSE_RETURNS_5_10_20_V1)

    assert len(calls) == 1
    assert calls[0][0] == ("AAA", "VNINDEX")
    assert calls[0][1] == {"through_date": snapshot.last_session_date}
    assert tuple(item.target_market_session_date for item in result.outcomes) == (
        dates[5], dates[10], dates[20],
    )
    for item, horizon in zip(result.outcomes, (5, 10, 20), strict=True):
        assert item.status is ForwardOutcomeStatus.AVAILABLE
        assert item.stock_forward_return_pct == pytest.approx(horizon)
        expected_benchmark = ((1000.0 + 2.0 * horizon) / 1000.0 - 1.0) * 100.0
        assert item.benchmark_forward_return_pct == pytest.approx(expected_benchmark)
        assert item.excess_forward_return_percentage_points == pytest.approx(horizon - expected_benchmark)
    # The duplicate normalized AAA/signal row keeps the later rowid (100), not 999.
    assert result.outcomes[0].signal_close == 100.0


def test_explicit_missing_and_censored_statuses_with_no_fill() -> None:
    snapshot_id = "snapshot"
    frames = {
        "VNINDEX": _frame([
            ("2024-01-02", 100.0),
            ("2024-01-03", 101.0),
            ("2024-01-04", np.nan),
            ("2024-01-05", 103.0),
        ]),
        "NO_TARGET": _frame([("2024-01-02", 10.0)]),
        "NO_SIGNAL": _frame([("2024-01-03", 21.0)]),
        "NO_BENCH_SIGNAL": _frame([("2024-01-01", 30.0), ("2024-01-02", 31.0)]),
        "NO_BENCH_TARGET": _frame([("2024-01-03", 40.0), ("2024-01-04", 41.0)]),
        "CENSORED": _frame([("2024-01-05", 50.0)]),
    }
    snapshot = _FakeSnapshot(snapshot_id, frames)
    candidates = (
        _candidate(snapshot_id, key="target", symbol="NO_TARGET", signal_date="2024-01-02", signal_close=10.0),
        _candidate(snapshot_id, key="signal", symbol="NO_SIGNAL", signal_date="2024-01-02", signal_close=20.0),
        _candidate(snapshot_id, key="bench-signal", symbol="NO_BENCH_SIGNAL", signal_date="2024-01-01", signal_close=30.0),
        _candidate(snapshot_id, key="bench-target", symbol="NO_BENCH_TARGET", signal_date="2024-01-03", signal_close=40.0),
        _candidate(snapshot_id, key="censored", symbol="CENSORED", signal_date="2024-01-05", signal_close=50.0),
    )
    spec = ForwardOutcomeSpec("one-session", "v1", (1,))
    result = label_candidate_forward_outcomes(snapshot, _batch(snapshot_id, candidates), spec)

    expected = {
        "target": ForwardOutcomeStatus.MISSING_TARGET_CLOSE,
        "signal": ForwardOutcomeStatus.MISSING_SIGNAL_CLOSE,
        "bench-signal": ForwardOutcomeStatus.MISSING_BENCHMARK_SIGNAL_CLOSE,
        "bench-target": ForwardOutcomeStatus.MISSING_BENCHMARK_TARGET_CLOSE,
        "censored": ForwardOutcomeStatus.CENSORED_AFTER_DATA_END,
    }
    assert {item.candidate_key: item.status for item in result.outcomes} == expected
    assert result.outcome_for("target", 1).target_close is None
    assert result.outcome_for("signal", 1).signal_close is None
    assert result.outcome_for("bench-signal", 1).benchmark_signal_close is None
    assert result.outcome_for("bench-target", 1).benchmark_target_close is None
    assert result.outcome_for("censored", 1).target_market_session_date is None
    assert result.available_count == 0
    assert result.censored_count == 1
    assert result.missing_count == 4
    for status, count in result.status_counts.items():
        assert count == (1 if status in expected.values() else 0)
        assert result.status_counts_by_horizon[1][status] == count
    assert len(snapshot.loads) == 1


def test_signal_close_parity_and_nonpositive_market_close_fail_clearly() -> None:
    frames = {
        "VNINDEX": _frame([("2024-01-02", 100.0), ("2024-01-03", 101.0)]),
        "AAA": _frame([("2024-01-02", 10.0), ("2024-01-03", 11.0)]),
    }
    snapshot = _FakeSnapshot("snapshot", frames)
    mismatch = _batch("snapshot", (_candidate("snapshot", key="AAA", symbol="AAA", signal_date="2024-01-02", signal_close=10.1),))
    with pytest.raises(ValueError, match="does not match snapshot close"):
        label_candidate_forward_outcomes(snapshot, mismatch, ForwardOutcomeSpec("one", "v1", (1,)))

    bad = _FakeSnapshot("snapshot", {**frames, "AAA": _frame([("2024-01-02", 10.0), ("2024-01-03", 0.0)])})
    valid = _batch("snapshot", (_candidate("snapshot", key="AAA", symbol="AAA", signal_date="2024-01-02", signal_close=10.0),))
    with pytest.raises(ValueError, match="close must be positive"):
        label_candidate_forward_outcomes(bad, valid, ForwardOutcomeSpec("one", "v1", (1,)))

    invalid_candidate = _batch("snapshot", (_candidate("snapshot", key="AAA", symbol="AAA", signal_date="2024-01-02", signal_close=0.0),))
    with pytest.raises(ValueError, match="candidate signal_close must be a positive finite number"):
        label_candidate_forward_outcomes(snapshot, invalid_candidate, ForwardOutcomeSpec("one", "v1", (1,)))


def test_empty_batch_performs_no_load_and_retains_requested_horizons() -> None:
    snapshot = _FakeSnapshot("snapshot", {"VNINDEX": _frame([("2024-01-02", 100.0)])})
    result = label_candidate_forward_outcomes(
        snapshot,
        _batch("snapshot", ()),
        FORWARD_CLOSE_RETURNS_5_10_20_V1,
    )
    assert isinstance(result, CandidateForwardOutcomeSet)
    assert result.outcomes == ()
    assert result.requested_horizons == (5, 10, 20)
    assert result.candidate_count == result.available_count == result.censored_count == result.missing_count == 0
    assert snapshot.loads == []


def test_order_cardinality_identity_and_immutable_grouping() -> None:
    frames = {
        "VNINDEX": _frame([("2024-01-02", 100.0), ("2024-01-03", 101.0), ("2024-01-04", 102.0)]),
        "AAA": _frame([("2024-01-02", 10.0), ("2024-01-03", 11.0), ("2024-01-04", 12.0)]),
        "BBB": _frame([("2024-01-02", 20.0), ("2024-01-03", 21.0), ("2024-01-04", 22.0)]),
    }
    spec = ForwardOutcomeSpec("two", "v1", (2, 1))
    first_snapshot = _FakeSnapshot("snapshot", frames)
    aaa = _candidate("snapshot", key="z-key", symbol="AAA", signal_date="2024-01-02", signal_close=10.0)
    bbb = _candidate("snapshot", key="a-key", symbol="BBB", signal_date="2024-01-02", signal_close=20.0)
    first = label_candidate_forward_outcomes(first_snapshot, _batch("snapshot", (bbb, aaa)), spec)
    repeated = label_candidate_forward_outcomes(_FakeSnapshot("snapshot", frames), _batch("snapshot", (aaa, bbb)), ForwardOutcomeSpec("two", "v1", (1, 2)))

    assert len(first.outcomes) == first.candidate_count * len(first.requested_horizons) == 4
    assert tuple((item.symbol, item.candidate_key, item.horizon_sessions) for item in first.outcomes) == (
        ("AAA", "z-key", 1), ("AAA", "z-key", 2),
        ("BBB", "a-key", 1), ("BBB", "a-key", 2),
    )
    assert first.set_identity == repeated.set_identity
    assert tuple(item.outcome_identity for item in first.outcomes) == tuple(item.outcome_identity for item in repeated.outcomes)
    with pytest.raises(TypeError):
        first.outcomes_by_candidate["new"] = ()  # type: ignore[index]
    with pytest.raises(TypeError):
        first.status_counts_by_horizon[1][ForwardOutcomeStatus.AVAILABLE] = 0  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        first.outcomes[0].symbol = "CHANGED"  # type: ignore[misc]

    changed_frames = {**frames, "AAA": _frame([("2024-01-02", 10.0), ("2024-01-03", 11.5), ("2024-01-04", 12.0)])}
    changed = label_candidate_forward_outcomes(_FakeSnapshot("snapshot", changed_frames), _batch("snapshot", (aaa, bbb)), spec)
    assert changed.set_identity != first.set_identity
    assert changed.outcome_for("z-key", 1).outcome_identity != first.outcome_for("z-key", 1).outcome_identity

    changed_calendar_frames = {
        **frames,
        "VNINDEX": _frame([("2024-01-02", 100.0), ("2024-01-04", 102.0)]),
    }
    changed_calendar = label_candidate_forward_outcomes(
        _FakeSnapshot("snapshot", changed_calendar_frames), _batch("snapshot", (aaa, bbb)), spec,
    )
    assert changed_calendar.set_identity != first.set_identity
    assert changed_calendar.outcome_for("z-key", 1).target_market_session_date == "2024-01-04"

    one_horizon = label_candidate_forward_outcomes(
        _FakeSnapshot("snapshot", frames), _batch("snapshot", (aaa, bbb)),
        ForwardOutcomeSpec("one", "v1", (1,)),
    )
    assert one_horizon.set_identity != first.set_identity
    custom_benchmark_frames = {**frames, "ALTINDEX": frames["VNINDEX"]}
    custom_benchmark = label_candidate_forward_outcomes(
        _FakeSnapshot("snapshot", custom_benchmark_frames), _batch("snapshot", (aaa, bbb)),
        ForwardOutcomeSpec("two", "v1", (1, 2), benchmark_symbol="ALTINDEX"),
    )
    assert custom_benchmark.set_identity != first.set_identity

    changed_key_candidate = _candidate(
        "snapshot", key="changed-key", symbol="AAA",
        signal_date="2024-01-02", signal_close=10.0,
    )
    changed_batch = label_candidate_forward_outcomes(
        _FakeSnapshot("snapshot", frames), _batch("snapshot", (changed_key_candidate, bbb)), spec,
    )
    assert changed_batch.source_candidate_batch_identity != first.source_candidate_batch_identity
    assert changed_batch.set_identity != first.set_identity

    other_snapshot_aaa = _candidate(
        "other-snapshot", key="z-key", symbol="AAA",
        signal_date="2024-01-02", signal_close=10.0,
    )
    other_snapshot_bbb = _candidate(
        "other-snapshot", key="a-key", symbol="BBB",
        signal_date="2024-01-02", signal_close=20.0,
    )
    other_snapshot = label_candidate_forward_outcomes(
        _FakeSnapshot("other-snapshot", frames),
        _batch("other-snapshot", (other_snapshot_aaa, other_snapshot_bbb)), spec,
    )
    assert other_snapshot.snapshot_id != first.snapshot_id
    assert other_snapshot.set_identity != first.set_identity


def test_spec_validation_snapshot_provenance_and_benchmark_candidate_rejection() -> None:
    assert FORWARD_CLOSE_RETURNS_5_10_20_V1.horizons == (5, 10, 20)
    assert ForwardOutcomeSpec("same", "v1", (20, 5, 10)).fingerprint == ForwardOutcomeSpec("same", "v1", (5, 10, 20)).fingerprint
    with pytest.raises(ValueError, match="positive integers"):
        ForwardOutcomeSpec("bad", "v1", (0,))
    with pytest.raises(ValueError, match="unique"):
        ForwardOutcomeSpec("bad", "v1", (5, 5))
    with pytest.raises(ValueError, match="benchmark_symbol"):
        ForwardOutcomeSpec("bad", "v1", (5,), benchmark_symbol=" ")

    snapshot = _FakeSnapshot("other", {"VNINDEX": _frame([("2024-01-02", 100.0)])})
    with pytest.raises(ValueError, match="snapshot_id"):
        label_candidate_forward_outcomes(snapshot, _batch("snapshot", ()), FORWARD_CLOSE_RETURNS_5_10_20_V1)
    with pytest.raises(TypeError, match="missing required fields"):
        label_candidate_forward_outcomes(snapshot, object(), FORWARD_CLOSE_RETURNS_5_10_20_V1)
    malformed = SimpleNamespace(candidates=[], batch_identity="batch", snapshot_id="other")
    with pytest.raises(TypeError, match="immutable tuple"):
        label_candidate_forward_outcomes(snapshot, malformed, FORWARD_CLOSE_RETURNS_5_10_20_V1)


def test_labeling_does_not_invoke_feature_decision_ranking_diagnostics_or_simulation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frames = {
        "VNINDEX": _frame([("2024-01-02", 100.0), ("2024-01-03", 101.0)]),
        "AAA": _frame([("2024-01-02", 10.0), ("2024-01-03", 11.0)]),
    }
    snapshot = _FakeSnapshot("snapshot", frames)
    batch = _batch("snapshot", (_candidate("snapshot", key="AAA", symbol="AAA", signal_date="2024-01-02", signal_close=10.0),))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("outcome labeling crossed a forbidden research/production boundary")

    import quantlab.adapters.frozen_q70_candidate_decisions as decisions
    import quantlab.diagnostics as diagnostics
    import quantlab.ranking as ranking
    import strategy.scanner as scanner
    import backtesting.portfolio_simulator as simulator
    monkeypatch.setattr(decisions, "evaluate_frozen_q70_candidates", forbidden)
    monkeypatch.setattr(diagnostics, "diagnose_candidate_factors", forbidden)
    monkeypatch.setattr(ranking, "rank_candidate_batch", forbidden)
    monkeypatch.setattr(scanner, "evaluate_prepared_row", forbidden)
    monkeypatch.setattr(simulator, "PortfolioSimulator", forbidden)

    result = label_candidate_forward_outcomes(snapshot, batch, ForwardOutcomeSpec("one", "v1", (1,)))
    assert result.available_count == 1


def test_fresh_import_has_no_database_cache_or_network_side_effect(tmp_path: Path) -> None:
    missing = tmp_path / "must-not-exist.db"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import quantlab.outcomes; "
            "forbidden={'quantlab.features.builtins','quantlab.alpha.frozen_q70','quantlab.candidates.frozen_q70'}; "
            "loaded=forbidden.intersection(sys.modules); "
            "assert not loaded, f'forbidden outcome import dependencies: {sorted(loaded)}'",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, MARKET_DATABASE_PATH=str(missing)),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert not missing.exists()
