from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import backtesting.engine as historical_engine
import backtesting.frozen_q70_evaluator as legacy_evaluator
from backtesting.paper_parity import BacktestPaperParityConfig
from quantlab.adapters.frozen_q70_candidate_decisions import (
    evaluate_frozen_q70_candidates,
)
from quantlab.catalog.market_data_snapshot import build_market_data_snapshot
from quantlab.features import PointInTimeUniverseContext, PreparedFeatureCache


def test_adapter_requires_explicit_model_and_stable_policy_identity() -> None:
    context = PointInTimeUniverseContext.static(["AAA"], ["2024-01-02"])
    with pytest.raises(TypeError, match="entry_model"):
        evaluate_frozen_q70_candidates(None, ["AAA"], benchmark_symbol="VNINDEX", universe_context=context, start_date="2024-01-02", through_date="2024-01-02", entry_model=None, entry_policy_identity="frozen")
    class Model:
        def evaluate(self): pass
    with pytest.raises(ValueError, match="entry_policy_identity"):
        evaluate_frozen_q70_candidates(None, ["AAA"], benchmark_symbol="VNINDEX", universe_context=context, start_date="2024-01-02", through_date="2024-01-02", entry_model=Model(), entry_policy_identity="")


def test_adapter_import_has_no_database_or_cache_side_effect(tmp_path: Path) -> None:
    missing = tmp_path / "not-created.db"
    completed = subprocess.run([sys.executable, "-c", "import quantlab.adapters.frozen_q70_candidate_decisions"], cwd=Path(__file__).resolve().parents[1], env=dict(os.environ, MARKET_DATABASE_PATH=str(missing)), capture_output=True, text=True)
    assert completed.returncode == 0 and not missing.exists()


class _DeterministicEntryModel:
    """Test-only entry model shared by the legacy and Quant Lab paths."""

    name = "controlled_q70_oracle"

    def evaluate(self, *, latest: pd.Series, **_: object) -> dict[str, object]:
        # The symbol-specific starting levels make this independent of either
        # implementation's candidate/Q70 machinery.  BBB is a reference-only
        # base-entry failure; CCC deliberately contributes a non-finite score.
        close = float(latest["close"])
        if close < 200:
            return {"status": "PASSED", "score": 50.0}
        if close < 300:
            return {"status": "FAILED", "reason": "controlled_base_failure", "score": 50.0}
        if close < 400:
            return {"status": "PASSED", "score": float("nan")}
        return {"status": "PASSED", "score": 100.0}


def _oracle_fixture(tmp_path: Path):
    """A local-only data set with warmup, failures, a future member and tail."""
    path = tmp_path / "oracle_market.db"
    dates = pd.date_range("2020-01-01", periods=340, freq="B")
    rows: list[tuple[object, ...]] = []
    # The deliberately different slopes yield finite RS/ADX values.  FUT has
    # enough rows to be prepared, but is point-in-time ineligible at first.
    for symbol, base, slope in (
        ("AAA", 100.0, 0.70),
        ("BBB", 200.0, 0.55),
        ("CCC", 300.0, 0.45),
        ("FUT", 400.0, 0.65),
        ("VNINDEX", 1000.0, 1.00),
    ):
        for index, day in enumerate(dates):
            close = base + index * slope
            rows.append((symbol, day.date().isoformat(), close - .1, close + .5,
                         close - .6, close, index + 100))
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE prices (symbol TEXT, time TEXT, open REAL, high REAL, "
            "low REAL, close REAL, volume INTEGER)"
        )
        connection.executemany("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    sessions = tuple(day.date().isoformat() for day in dates)
    # FUT is intentionally excluded from early same-day Q70 reference rows.
    memberships = {
        day: (("AAA", "BBB", "CCC") if position < 300 else ("AAA", "BBB", "CCC", "FUT"))
        for position, day in enumerate(sessions)
    }
    return path, build_market_data_snapshot(path), sessions, PointInTimeUniverseContext.from_memberships(
        universe_mode="controlled_point_in_time", memberships=memberships
    )


def _parity() -> BacktestPaperParityConfig:
    return BacktestPaperParityConfig(
        initial_cash=1_000_000.0, position_sizer="fixed_fraction",
        risk_per_trade_pct=1.0, atr_stop_multiplier=2.0, fixed_fraction_pct=10.0,
        lot_size=100, commission_rate=.0015, slippage_bps=5.0,
        maximum_position_pct=20.0, maximum_gross_exposure_pct=80.0,
        maximum_open_positions=10, maximum_daily_loss_pct=3.0,
        minimum_cash_buffer_pct=5.0, sell_tax_rate=.001,
    )


def _legacy_rows_from_real_collection(monkeypatch: pytest.MonkeyPatch, snapshot, symbols, session_dates, *, start: str, end: str, model: _DeterministicEntryModel):
    """Use the real collector loop while sourcing its frame from v6 once.

    This keeps the controlled oracle focused on the final historical
    evaluation/gate boundary, rather than asserting unrelated legacy-vs-v6
    preparation internals in this Phase 3.7 test.
    """
    from quantlab.adapters.historical_prepared_features import prepare_historical_paper_state_subset

    context = PointInTimeUniverseContext.static(symbols, session_dates)
    prepared = prepare_historical_paper_state_subset(
        snapshot, symbols, benchmark_symbol="VNINDEX", universe_context=context,
        # The real collector receives its full historical frame and applies
        # the signal-date cutoff itself, including its 60-row warmup rule.
        start_date=session_dates[0], through_date=end,
    )
    frames = {symbol: prepared.frame_for(symbol).reset_index(drop=True) for symbol in symbols}
    monkeypatch.setattr(
        historical_engine, "prepare_backtest_dataset",
        lambda symbol, **_: frames.get(str(symbol).upper(), pd.DataFrame()).copy(deep=True),
    )
    return frames


def test_controlled_legacy_pre_simulator_oracle_matches_public_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real legacy collection/gate is the oracle immediately before simulate."""
    path, snapshot, sessions, context = _oracle_fixture(tmp_path)
    start, end = sessions[280], sessions[337]  # a later unused row proves the tail boundary.
    symbols = ("AAA", "BBB", "CCC", "FUT")
    model = _DeterministicEntryModel()
    frames = _legacy_rows_from_real_collection(
        monkeypatch, snapshot, symbols, sessions, start=start, end=end, model=model
    )
    # Use the same PIT membership for adapter breadth and the legacy evaluator.
    # Its coverage-facing protocol is deliberately tiny and is only consumed
    # before gate.apply(), exactly as in production.
    class Coverage:
        start_date, end_date = start, end
        minimum_history_sessions, maximum_staleness_sessions = 50, 5
        candidate_symbols = symbols
        session_dates = tuple(sessions[280:338])
        eligible_count_by_session = {day: len(context.members_as_of(day)) for day in session_dates}
        def is_eligible(self, symbol: str, date: object) -> bool:
            return str(symbol).upper() in context.members_as_of(pd.Timestamp(date).date().isoformat())
    coverage = Coverage()

    # The historical breadth builder is independently tested; pin it here to
    # one controlled, finite state input so this oracle isolates gate parity.
    class Breadth:
        start_date, end_date = start, end
        def signal_fields_as_of(self, _: object) -> dict[str, float]:
            return {"breadth_ema50_pct": 70.0, "breadth_ema50_change_10d": 0.0}
    breadth = Breadth()

    evaluate_calls = 0
    original_evaluate = historical_engine.evaluate_prepared_row
    def counted_evaluate(**kwargs):
        nonlocal evaluate_calls
        evaluate_calls += 1
        return original_evaluate(**kwargs)
    monkeypatch.setattr(historical_engine, "evaluate_prepared_row", counted_evaluate)
    legacy_collections = []
    def collect_and_record(**kwargs):
        collection = historical_engine.collect_candidate_generation(**kwargs)
        legacy_collections.append(collection)
        return collection
    monkeypatch.setattr(legacy_evaluator, "collect_candidate_generation", collect_and_record)
    monkeypatch.setattr(legacy_evaluator, "HybridTrendDonchianEntryModel", lambda: model)
    monkeypatch.setattr(legacy_evaluator, "build_historical_breadth_index", lambda *a, **k: breadth)
    # Q70 sector RS is telemetry only.  An explicit inert mapping prevents any
    # provider access while preserving the production gate/scoring path.
    monkeypatch.setattr(legacy_evaluator.PaperV2QualityGate, "_add_sector_rs_telemetry", lambda self, signal: dict(signal))
    captured: dict[str, object] = {"simulations": 0}
    class PreSimulatorCapture:
        def __init__(self, **kwargs: object) -> None:
            captured["settings"] = kwargs
        def simulate(self, candidates):
            captured["simulations"] = int(captured["simulations"]) + 1
            captured["candidates"] = tuple(candidates)
            # Deliberately no trade execution/sizing/PnL: this is the last
            # boundary and makes the legacy ledger expose pending candidates.
            return SimpleNamespace(executed_trades=(), rejected_trades=(), equity_curve=pd.DataFrame({"date": [], "equity": []}), final_equity=1_000_000.0)
    monkeypatch.setattr(legacy_evaluator, "PortfolioSimulator", PreSimulatorCapture)
    monkeypatch.setattr(legacy_evaluator, "calculate_metrics", lambda *a, **k: {})
    monkeypatch.setattr(legacy_evaluator, "calculate_portfolio_metrics", lambda *a, **k: {})

    _, legacy_metrics, _ = legacy_evaluator.run_frozen_q70_backtest(
        symbols=None, db_path=str(path), start_date=start, end_date=end,
        universe_mode="database_coverage", coverage_index=coverage,
        breadth_index=breadth, parity_config=_parity(), collect_decision_ledger=True,
    )
    # Adapter has its own v6 computation and no PortfolioSimulator reference.
    scorer_calls = 0
    import quantlab.adapters.frozen_q70_candidate_decisions as adapter_module
    original_score = adapter_module.score_frozen_q70_batch
    def counted_score(*args, **kwargs):
        nonlocal scorer_calls
        scorer_calls += 1
        return original_score(*args, **kwargs)
    monkeypatch.setattr(adapter_module, "score_frozen_q70_batch", counted_score)
    snapshot_loads = 0
    original_load = type(snapshot).load_ohlcv
    def counted_load(self, *args, **kwargs):
        nonlocal snapshot_loads
        snapshot_loads += 1
        return original_load(self, *args, **kwargs)
    monkeypatch.setattr(type(snapshot), "load_ohlcv", counted_load)
    cache = PreparedFeatureCache(tmp_path / "oracle-cache", codec="npz_numeric_v1")
    adapter = evaluate_frozen_q70_candidates(
        snapshot, symbols, benchmark_symbol="VNINDEX", universe_context=context,
        start_date=start, through_date=end, entry_model=model,
        entry_policy_identity="controlled-q70-oracle-v1", feature_cache=cache,
    )
    assert snapshot_loads == 1 and adapter.metadata["cache"]["hit"] is False
    snapshot_loads = 0
    warm = evaluate_frozen_q70_candidates(
        snapshot, symbols, benchmark_symbol="VNINDEX", universe_context=context,
        start_date=start, through_date=end, entry_model=model,
        entry_policy_identity="controlled-q70-oracle-v1", feature_cache=cache,
    )
    assert snapshot_loads == 0 and warm.metadata["cache"]["hit"] is True

    ledger = tuple(legacy_metrics["decision_ledger"])
    legacy_by_key = {
        f"{item.symbol}:{item.signal_date.date().isoformat()}": item
        for item in ledger
    }
    adapter_by_key = {
        decision.evaluation_key: decision
        for batch in adapter.batches.values() for decision in batch.decisions
        if decision.base_entry_passed
    }
    # The legacy ledger covers base-entry candidates only; its underlying
    # collector still proves the complete same-day evaluation population below.
    assert tuple(sorted(legacy_by_key)) == tuple(sorted(adapter_by_key))
    legacy_rows = {
        f"{row.symbol}:{row.signal_date.date().isoformat()}": row
        for collection in legacy_collections for row in collection.evaluations
        if coverage.is_eligible(row.symbol, row.signal_date)
    }
    adapter_rows = {
        row.evaluation_key: row for row in adapter.evaluations
        if context.members_as_of(row.signal_date) and row.symbol in context.members_as_of(row.signal_date)
    }
    assert tuple(sorted(legacy_rows)) == tuple(sorted(adapter_rows))
    for key, legacy_row in legacy_rows.items():
        adapter_row = adapter_rows[key]
        assert (legacy_row.symbol, legacy_row.signal_date.date().isoformat()) == (
            adapter_row.symbol, adapter_row.signal_date,
        )
        assert legacy_row.base_entry_passed == adapter_row.base_entry_passed
        assert legacy_row.reason == adapter_row.reason
        for legacy_value, adapter_value in (
            (legacy_row.score, adapter_row.score),
            (legacy_row.relative_strength_20d, adapter_row.relative_strength_20d),
            (legacy_row.adx, adapter_row.adx),
        ):
            assert np.isclose(legacy_value, adapter_value, equal_nan=True)
    for key, legacy in legacy_by_key.items():
        decision = adapter_by_key[key]
        assert key == f"{decision.symbol}:{decision.evaluation_key.rsplit(':', 1)[1]}"
        assert legacy.percentile_score == pytest.approx(decision.component_percentiles["score"])
        assert legacy.percentile_relative_strength_20d == pytest.approx(decision.component_percentiles["relative_strength_20d"])
        assert legacy.percentile_adx == pytest.approx(decision.component_percentiles["adx"])
        assert legacy.quality_score == pytest.approx(decision.quality_score)
        assert legacy.quality_threshold == .70
        assert legacy.market_state == decision.market_state
        assert legacy.gate_accepted == decision.accepted and legacy.gate_reason == decision.reason
    legacy_keys = tuple(
        f"{trade.symbol}:{pd.Timestamp(trade.signal_date).date().isoformat()}"
        for trade in captured["candidates"]
    )
    assert legacy_keys == adapter.accepted_candidate_keys
    assert legacy_keys == tuple(sorted(legacy_keys, key=lambda key: (key.split(":", 1)[1], key.split(":", 1)[0])))
    assert captured["simulations"] == 1
    # The legacy collector evaluates each v6-produced non-tail row once per
    # symbol; the excluded FUT rows are evaluated but never reach the gate.
    assert evaluate_calls == sum(
        ((frame["time"] >= pd.Timestamp(start)) & (frame["time"] < pd.Timestamp(end))).sum()
        for frame in frames.values()
    )
    # Cold and warm feature preparation both evaluate and score every causal
    # date batch; only the feature graph/market-data load is cached.
    assert scorer_calls == 2 * len(adapter.batches)
    assert all(
        key.split(":", 1)[0] != "FUT" or key.split(":", 1)[1] >= sessions[300]
        for key in legacy_keys
    )
    assert any(
        row.symbol == "FUT" and row.signal_date < sessions[300]
        for row in adapter.evaluations
    )
    assert any(not item.base_entry_passed for item in adapter.evaluations)
    assert all(
        item.symbol != "FUT" or batch.signal_date >= sessions[300]
        for batch in adapter.batches.values() for item in batch.decisions
    )
    assert adapter.evaluations[-1].signal_date < end  # no next-bar row is excluded.
    # Immutable public result and explicit identity contract.
    with pytest.raises(TypeError):
        adapter.batches["other"] = object()  # type: ignore[index]
    repeated = warm
    changed_identity = evaluate_frozen_q70_candidates(snapshot, symbols, benchmark_symbol="VNINDEX", universe_context=context, start_date=start, through_date=end, entry_model=model, entry_policy_identity="controlled-q70-oracle-v2", feature_cache=cache)
    assert repeated.identity == adapter.identity and repeated.accepted_candidate_keys == adapter.accepted_candidate_keys
    assert changed_identity.identity != adapter.identity
