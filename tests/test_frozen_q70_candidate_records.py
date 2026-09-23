from __future__ import annotations

from dataclasses import fields, replace
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import MappingProxyType

import numpy as np
import pandas as pd
import pytest

from quantlab.adapters.frozen_q70_candidate_decisions import HistoricalCandidateDecisionResult
from quantlab.adapters.frozen_q70_candidate_records import build_frozen_q70_candidate_records
from quantlab.alpha import (
    FrozenQ70BatchResult,
    FrozenQ70Decision,
    FrozenQ70EvaluationRow,
    FrozenQ70Policy,
)
from quantlab.candidates import FrozenQ70CandidateBatch, candidate_batch_from_decisions
from quantlab.candidates.contracts import FrozenQ70CandidateRecord
from quantlab.catalog.market_data_snapshot import build_market_data_snapshot
from quantlab.features import PointInTimeUniverseContext, PreparedFeatureCache


DAY = "2024-01-02"


def _context_values(*, close: object = 10.0, state: str = "HEALTHY_BULL") -> MappingProxyType:
    return MappingProxyType({
        "close": close,
        "ATR14": 1.5,
        "ATR_Percent": 2.5,
        "RSI": 61.0,
        "Vol_Ratio": 1.2,
        "EMA10": 9.8,
        "EMA20": 9.5,
        "EMA50": 9.0,
        "Previous_20D_High": 9.9,
        "Breakout_20D": True,
        "Market_Regime": "BULL",
        "breadth_ema50_pct": 70.0,
        "breadth_ema50_change_10d": 3.0,
        "breadth_universe_count": 100,
        "paper_v2_state": state,
    })


def _decision_result(
    *,
    accepted: bool = True,
    base_passed: bool = True,
    score: object = 90.0,
    contexts: dict[str, MappingProxyType] | None = None,
    evaluations: tuple[FrozenQ70EvaluationRow, ...] | None = None,
    accepted_keys: tuple[str, ...] | None = None,
) -> HistoricalCandidateDecisionResult:
    key = f"AAA:{DAY}"
    row = FrozenQ70EvaluationRow("AAA", DAY, score, 12.0, 25.0, base_passed, key, None)
    decision = FrozenQ70Decision(
        "AAA", key, base_passed, True,
        MappingProxyType({"score": 1.0, "relative_strength_20d": .8, "adx": .7}),
        5.0 / 6.0, "HEALTHY_BULL", accepted,
        "Q0.70_PASS" if accepted else "quality<0.70",
    )
    policy = FrozenQ70Policy()
    batch = FrozenQ70BatchResult(
        policy, "daily-batch", DAY, "HEALTHY_BULL", "universe",
        (decision,), (decision,), MappingProxyType({}), MappingProxyType({}),
    )
    metadata = MappingProxyType({
        "snapshot_id": "snapshot",
        "universe_membership_identity": "universe",
        "entry_policy_identity": "entry-policy",
        "q70_policy_fingerprint": policy.fingerprint,
        "requested_symbols": ("AAA",),
        "available_symbols": ("AAA",),
        "missing_symbols": (),
        "cache": None,
    })
    return HistoricalCandidateDecisionResult(
        "phase-3-7", "prepared-v6",
        evaluations if evaluations is not None else (row,),
        MappingProxyType({DAY: batch}),
        accepted_keys if accepted_keys is not None else ((key,) if accepted else ()),
        metadata,
        MappingProxyType(contexts if contexts is not None else {key: _context_values()}),
    )


def _batch(result: HistoricalCandidateDecisionResult) -> FrozenQ70CandidateBatch:
    return candidate_batch_from_decisions(
        result,
        requested_symbols=("aaa", "VNINDEX"),
        available_symbols=("AAA",),
        start_date=DAY,
        through_date=DAY,
    )


def test_one_to_one_decision_and_signal_context_parity() -> None:
    result = _decision_result()
    batch = _batch(result)
    record = batch.candidates[0]
    decision = result.batches[DAY].decisions[0]
    evaluation = result.evaluations[0]
    context = result.signal_context_for(record.evaluation_key)

    assert batch.accepted_candidate_keys == result.accepted_candidate_keys == (f"AAA:{DAY}",)
    assert batch.candidate_count == 1
    assert (record.score, record.relative_strength_20d, record.adx) == (
        evaluation.score, evaluation.relative_strength_20d, evaluation.adx,
    )
    assert record.component_percentiles == decision.component_percentiles
    assert record.quality_score == decision.quality_score
    assert record.q70_threshold == result.batches[DAY].policy.threshold
    assert (record.acceptance_reason, record.paper_v2_state) == (
        decision.reason, decision.market_state,
    )
    assert (
        record.signal_close, record.atr14, record.atr_percent, record.rsi14,
        record.volume_ratio, record.ema10, record.ema20, record.ema50,
        record.previous_20d_high, record.donchian_breakout, record.market_regime,
        record.breadth_ema50_pct, record.breadth_ema50_change_10d,
        record.breadth_universe_count,
    ) == (
        context["close"], context["ATR14"], context["ATR_Percent"], context["RSI"],
        context["Vol_Ratio"], context["EMA10"], context["EMA20"], context["EMA50"],
        context["Previous_20D_High"], context["Breakout_20D"], context["Market_Regime"],
        context["breadth_ema50_pct"], context["breadth_ema50_change_10d"],
        context["breadth_universe_count"],
    )


def test_ordering_grouping_empty_and_rejected_rows() -> None:
    empty = replace(_decision_result(accepted=False), batches=MappingProxyType({}), accepted_candidate_keys=())
    empty_batch = _batch(empty)
    assert empty_batch.candidates == () and empty_batch.signal_dates == ()
    assert empty_batch.candidates_for_signal_date(DAY) == ()
    assert empty_batch.batch_identity == _batch(empty).batch_identity

    # Failed/reference, state-rejected, quality-rejected, and PIT-ineligible
    # evaluations can exist in Phase 3.7 while no accepted record exists.
    rows = (
        FrozenQ70EvaluationRow("AAA", DAY, 1, 1, 1, False, f"AAA:{DAY}", "base"),
        FrozenQ70EvaluationRow("PIT", DAY, 99, 99, 99, True, f"PIT:{DAY}"),
    )
    rejected = replace(empty, evaluations=rows)
    assert _batch(rejected).candidate_count == 0


def test_duplicate_missing_and_inconsistent_accepted_keys_fail_clearly() -> None:
    valid = _decision_result()
    row = valid.evaluations[0]
    with pytest.raises(ValueError, match="duplicate evaluation key"):
        _batch(replace(valid, evaluations=(row, row)))
    with pytest.raises(ValueError, match="missing accepted evaluation"):
        _batch(replace(valid, evaluations=()))
    with pytest.raises(ValueError, match="duplicate accepted candidate key"):
        _batch(replace(valid, accepted_candidate_keys=(row.evaluation_key, row.evaluation_key)))
    with pytest.raises(ValueError, match="missing accepted signal context"):
        _batch(replace(valid, _signal_contexts=MappingProxyType({})))


def test_nonfinite_identity_immutability_and_sensitivity() -> None:
    batch = _batch(_decision_result(score=float("nan"), contexts={f"AAA:{DAY}": _context_values(close=None)}))
    same = _batch(_decision_result(score=float("nan"), contexts={f"AAA:{DAY}": _context_values(close=None)}))
    assert batch.batch_identity == same.batch_identity
    record = batch.candidates[0]
    assert np.isnan(record.score) and record.signal_close is None
    with pytest.raises(TypeError):
        record.component_percentiles["score"] = 0.0  # type: ignore[index]
    with pytest.raises(TypeError):
        batch.candidates_by_signal_date[DAY] = ()  # type: ignore[index]
    with pytest.raises(Exception):
        record.symbol = "BBB"  # type: ignore[misc]

    changed_record = replace(record, signal_close=11.0)
    changed = replace(batch, candidates=(changed_record,))
    assert changed.batch_identity != batch.batch_identity
    assert replace(batch, through_date="2024-01-03").batch_identity != batch.batch_identity
    changed_policy_record = replace(record, q70_policy_fingerprint="changed-policy")
    assert replace(batch, candidates=(changed_policy_record,), q70_policy_fingerprint="changed-policy").batch_identity != batch.batch_identity


def test_public_contract_has_no_future_execution_or_trade_fields() -> None:
    names = {item.name for item in fields(FrozenQ70CandidateRecord)}
    forbidden = {
        "entry_date", "entry_price", "future_open", "future_high", "future_low",
        "quantity", "cash_allocation", "position_weight", "stop_price", "target_price",
        "exit_date", "exit_reason", "pnl", "commission", "tax", "slippage",
    }
    assert not names.intersection(forbidden)
    assert "backtesting.trade" not in sys.modules or all(
        not isinstance(item, sys.modules["backtesting.trade"].Trade)
        for item in _batch(_decision_result()).candidates
    )


class _EntryModel:
    name = "phase-3-8-test-entry"

    def evaluate(self, *, latest: pd.Series, **_: object) -> dict[str, object]:
        close = float(latest["close"])
        if 200 <= close < 300:
            return {"status": "FAILED", "reason": "controlled_base_failure", "score": 1.0}
        return {"status": "PASSED", "score": 100.0 if close < 200 else 90.0}


def _market_fixture(tmp_path: Path):
    path = tmp_path / "market.db"
    dates = pd.date_range("2020-01-01", periods=340, freq="B")
    rows = []
    for symbol, base, slope in (
        ("AAA", 100.0, .50), ("FAIL", 200.0, .05),
        ("FUT", 300.0, .40), ("VNINDEX", 1000.0, 1.0),
    ):
        for position, day in enumerate(dates):
            close = base + position * slope
            rows.append((symbol, day.date().isoformat(), close - .2, close + .5, close - .6, close, position + 100))
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE prices (symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER)")
        connection.executemany("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    sessions = tuple(day.date().isoformat() for day in dates)
    context = PointInTimeUniverseContext.from_memberships(
        universe_mode="controlled_point_in_time",
        memberships={day: (("AAA", "FAIL") if index < 300 else ("AAA", "FAIL", "FUT")) for index, day in enumerate(sessions)},
    )
    return path, build_market_data_snapshot(path), sessions, context


def test_real_adapter_reuses_v6_once_and_maps_only_phase_3_7_accepts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _path, snapshot, sessions, context = _market_fixture(tmp_path)
    import backtesting.portfolio_simulator as portfolio_module
    import backtesting.trade as trade_module
    import quantlab.adapters.frozen_q70_candidate_records as adapter_module
    def forbidden(*args, **kwargs):
        raise AssertionError("Phase 3.8 must not construct Trade or invoke PortfolioSimulator")
    monkeypatch.setattr(trade_module, "Trade", forbidden)
    monkeypatch.setattr(portfolio_module, "PortfolioSimulator", forbidden)
    captured = {}
    real_evaluate = adapter_module.evaluate_frozen_q70_candidates
    def capture(*args, **kwargs):
        result = real_evaluate(*args, **kwargs); captured["result"] = result; return result
    monkeypatch.setattr(adapter_module, "evaluate_frozen_q70_candidates", capture)
    loads = 0
    real_load = type(snapshot).load_ohlcv
    def counted_load(self, *args, **kwargs):
        nonlocal loads
        loads += 1
        return real_load(self, *args, **kwargs)
    monkeypatch.setattr(type(snapshot), "load_ohlcv", counted_load)
    start, through = sessions[280], sessions[338]
    batch = build_frozen_q70_candidate_records(
        snapshot, ("AAA", "FAIL", "FUT"), benchmark_symbol="VNINDEX",
        universe_context=context, start_date=start, through_date=through,
        entry_model=_EntryModel(), entry_policy_identity="phase-3-8-entry-v1",
    )
    decisions = captured["result"]
    assert loads == 1
    assert batch.accepted_candidate_keys == decisions.accepted_candidate_keys
    assert batch.candidates == tuple(sorted(batch.candidates, key=lambda item: (item.signal_date, item.symbol, item.candidate_key)))
    assert any(not row.base_entry_passed for row in decisions.evaluations)
    assert any(row.symbol == "FUT" and row.signal_date < sessions[300] for row in decisions.evaluations)
    assert all(not item.candidate_key.startswith("FAIL:") for item in batch.candidates)
    assert all(not item.candidate_key.startswith("FUT:") or item.signal_date >= sessions[300] for item in batch.candidates)
    for record in batch.candidates:
        signal = decisions.signal_context_for(record.evaluation_key)
        assert record.signal_close == signal["close"]
        assert record.paper_v2_state == signal["paper_v2_state"]
        assert record.signal_date < through  # Phase 3.7's no-next-bar boundary.


def test_explicit_npz_warm_hit_has_zero_loads_and_feature_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _path, snapshot, sessions, context = _market_fixture(tmp_path)
    import quantlab.adapters.historical_prepared_features as prepared_module
    from quantlab.features.builtins import builtin_definitions as real_definitions
    calls = 0
    def instrumented_definitions():
        definitions = []
        for definition in real_definitions():
            original = definition.compute
            def counted(*args, _original=original, **kwargs):
                nonlocal calls
                calls += 1
                assert _original is not None
                return _original(*args, **kwargs)
            definitions.append(replace(definition, compute=counted))
        return tuple(definitions)
    monkeypatch.setattr(prepared_module, "builtin_definitions", instrumented_definitions)
    loads = 0
    real_load = type(snapshot).load_ohlcv
    def counted_load(self, *args, **kwargs):
        nonlocal loads
        loads += 1
        return real_load(self, *args, **kwargs)
    monkeypatch.setattr(type(snapshot), "load_ohlcv", counted_load)
    cache = PreparedFeatureCache(tmp_path / "cache", codec="npz_numeric_v1")
    kwargs = dict(
        benchmark_symbol="VNINDEX", universe_context=context,
        start_date=sessions[280], through_date=sessions[338],
        entry_model=_EntryModel(), entry_policy_identity="phase-3-8-entry-v1",
        feature_cache=cache,
    )
    cold = build_frozen_q70_candidate_records(snapshot, ("AAA", "FAIL", "FUT"), **kwargs)
    assert loads == 1 and calls > 0
    cold_calls = calls
    loads = 0
    warm = build_frozen_q70_candidate_records(snapshot, ("AAA", "FAIL", "FUT"), **kwargs)
    assert loads == 0 and calls == cold_calls
    assert warm.batch_identity == cold.batch_identity
    assert warm.candidates == cold.candidates


def test_future_rows_do_not_change_causal_candidate_content(tmp_path: Path) -> None:
    path, snapshot, sessions, context = _market_fixture(tmp_path)
    kwargs = dict(
        benchmark_symbol="VNINDEX", universe_context=context,
        start_date=sessions[280], through_date=sessions[330],
        entry_model=_EntryModel(), entry_policy_identity="phase-3-8-entry-v1",
    )
    before = build_frozen_q70_candidate_records(snapshot, ("AAA", "FAIL", "FUT"), **kwargs)
    future = pd.Timestamp(sessions[-1]) + pd.Timedelta(days=7)
    with sqlite3.connect(path) as connection:
        for symbol, close in (("AAA", 999.0), ("FAIL", 999.0), ("FUT", 999.0), ("VNINDEX", 9999.0)):
            connection.execute("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", (symbol, future.date().isoformat(), close, close, close, close, 1))
    after = build_frozen_q70_candidate_records(build_market_data_snapshot(path), ("AAA", "FAIL", "FUT"), **kwargs)
    assert before.accepted_candidate_keys == after.accepted_candidate_keys
    provenance = {"snapshot_id", "prepared_v6_identity", "phase_3_7_run_identity"}
    assert [
        {key: value for key, value in item.canonical_content().items() if key not in provenance}
        for item in before.candidates
    ] == [
        {key: value for key, value in item.canonical_content().items() if key not in provenance}
        for item in after.candidates
    ]
    assert before.snapshot_id != after.snapshot_id and before.batch_identity != after.batch_identity


def test_import_has_no_database_cache_or_network_side_effect(tmp_path: Path) -> None:
    missing = tmp_path / "must-not-exist.db"
    completed = subprocess.run(
        [sys.executable, "-c", "import quantlab.candidates; import quantlab.adapters.frozen_q70_candidate_records"],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, MARKET_DATABASE_PATH=str(missing)),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert not missing.exists()
