from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

import backtesting.frozen_q70_evaluator as evaluator
from backtesting.engine import CandidateGenerationCollection, HistoricalEvaluationRow
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.trade import ExitReason, Trade
from core.database_coverage import CoverageUniverseIndex
from strategy.paper_v2_gate import PaperV2QualityGate


DAY = datetime(2020, 3, 1)


@dataclass
class _Breadth:
    start_date: str = "2020-01-01"
    end_date: str = "2020-12-31"
    fields: dict[str, float] | None = None

    def signal_fields_as_of(self, date: object) -> dict[str, float]:
        return self.fields or {
            "breadth_ema50_pct": 70.0,
            "breadth_ema50_change_10d": 0.0,
        }


def _row(symbol: str, *, passed: bool, score: float, date: datetime = DAY, regime: str = "BULL") -> HistoricalEvaluationRow:
    return HistoricalEvaluationRow(
        symbol=symbol, signal_date=date, score=score,
        relative_strength_20d=score, adx=score, regime=regime,
        base_entry_passed=passed,
    )


def _trade(symbol: str, signal_date: datetime = DAY) -> Trade:
    trade = Trade(symbol=symbol, signal_date=signal_date, entry_date=signal_date + timedelta(days=1), entry_price=100, quantity=1)
    trade.close(signal_date + timedelta(days=2), 105, ExitReason.TAKE_PROFIT)
    return trade


def _parity() -> BacktestPaperParityConfig:
    return BacktestPaperParityConfig(
        initial_cash=100_000_000, position_sizer="fixed_fraction",
        risk_per_trade_pct=1, atr_stop_multiplier=2, fixed_fraction_pct=10,
        lot_size=100, commission_rate=.0015, slippage_bps=5,
        maximum_position_pct=20, maximum_gross_exposure_pct=80,
        maximum_open_positions=10, maximum_daily_loss_pct=3,
        minimum_cash_buffer_pct=5, sell_tax_rate=.001,
    )


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch, rows: list[HistoricalEvaluationRow], trades: list[Trade]) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    collection = CandidateGenerationCollection(tuple(rows), tuple(trades), {})
    monkeypatch.setattr(
        evaluator,
        "collect_candidate_generation",
        lambda **kwargs: (
            collection
            if kwargs["symbol"] == rows[0].symbol
            else CandidateGenerationCollection((), (), {})
        ),
    )
    monkeypatch.setattr(evaluator, "calculate_metrics", lambda *args, **kwargs: {})
    monkeypatch.setattr(evaluator, "calculate_portfolio_metrics", lambda *args, **kwargs: {})

    class Simulator:
        def __init__(self, **kwargs: Any) -> None:
            captured["simulator_kwargs"] = kwargs
        def simulate(self, candidates: list[Trade]) -> Any:
            captured["candidates"] = candidates
            return SimpleNamespace(
                executed_trades=candidates,
                equity_curve=pd.DataFrame({"date": [DAY], "equity": [100.0]}),
                final_equity=100.0,
            )
    monkeypatch.setattr(evaluator, "PortfolioSimulator", Simulator)
    return captured


def _run(monkeypatch: pytest.MonkeyPatch, rows: list[HistoricalEvaluationRow], trades: list[Trade], **kwargs: Any):
    captured = _patch_pipeline(monkeypatch, rows, trades)
    breadth_index = kwargs.pop("breadth_index", _Breadth())
    result = evaluator.run_frozen_q70_backtest(
        symbols=["AAA"], start_date="2020-02-01", end_date="2020-04-01",
        breadth_index=breadth_index, parity_config=_parity(), **kwargs,
    )
    return result, captured


def test_quality_universe_includes_base_entry_failures_and_real_gate_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = _trade("AAA")
    rows = [_row("AAA", passed=True, score=50), _row("FAIL", passed=False, score=100)]
    observed: dict[str, Any] = {}
    original_apply = PaperV2QualityGate.apply
    monkeypatch.setattr(PaperV2QualityGate, "_add_sector_rs_telemetry", lambda self, signal: dict(signal))
    def spy(self: PaperV2QualityGate, signals: list[dict[str, Any]], *, quality_universe: object = None):
        observed["signals"] = signals; observed["universe"] = list(quality_universe or [])
        return original_apply(self, signals, quality_universe=quality_universe)
    monkeypatch.setattr(evaluator.PaperV2QualityGate, "apply", spy)
    (_, metrics, _), captured = _run(monkeypatch, rows, [candidate])
    assert {row["symbol"] for row in observed["universe"]} == {"AAA", "FAIL"}
    assert captured["candidates"] == []  # FAIL changes AAA's percentile below Q70.
    assert metrics["base_entry_candidates"] == 1
    assert metrics["q70_rejection_counts"] == {"quality<0.70": 1}


def test_database_coverage_filters_reference_rows_before_scoring(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = _trade("AAA")
    rows = [_row("AAA", passed=True, score=50), _row("FUT", passed=False, score=100)]
    members = {"2020-03-01": frozenset({"AAA"})}
    coverage = CoverageUniverseIndex(
        start_date="2020-02-01", end_date="2020-04-01",
        effective_start_date="2020-03-01", effective_end_date="2020-03-01",
        minimum_history_sessions=50, maximum_staleness_sessions=5,
        session_dates=("2020-03-01",), candidate_symbols=("AAA", "FUT"),
        eligible_count_by_session={"2020-03-01": 1}, _members_by_session=members,
    )
    captured = _patch_pipeline(monkeypatch, rows, [candidate])
    monkeypatch.setattr(PaperV2QualityGate, "_add_sector_rs_telemetry", lambda self, signal: dict(signal))
    trades, metrics, _ = evaluator.run_frozen_q70_backtest(
        start_date="2020-02-01", end_date="2020-04-01", universe_mode="database_coverage",
        coverage_index=coverage, breadth_index=_Breadth(), parity_config=_parity(),
    )
    assert captured["candidates"] == [candidate]
    assert trades == [candidate] and trades[0] is candidate
    assert metrics["effective_universe_mode"] == "database_coverage"
    assert metrics["coverage_eligible_count_min"] == 1


@pytest.mark.parametrize(
    ("regime", "fields", "reason"),
    [
        ("BEAR", {"breadth_ema50_pct": 70, "breadth_ema50_change_10d": 0}, "BEAR"),
        ("BULL", {"breadth_ema50_pct": 40, "breadth_ema50_change_10d": -1}, "DIVERGENT_BULL"),
        ("BULL", {"breadth_ema50_pct": 70, "breadth_ema50_change_10d": 0}, None),
        ("BULL", {"breadth_ema50_pct": float("nan"), "breadth_ema50_change_10d": 0}, None),
        ("SIDEWAY", {"breadth_ema50_pct": 60, "breadth_ema50_change_10d": 1}, None),
        ("SIDEWAY", {"breadth_ema50_pct": 59, "breadth_ema50_change_10d": 1}, None),
    ],
)
def test_gate_state_branches_and_original_trade_keying(monkeypatch: pytest.MonkeyPatch, regime: str, fields: dict[str, float], reason: str | None) -> None:
    candidate = _trade("AAA")
    # Different entry date proves lookup is signal-date based.
    candidate.entry_date = DAY + timedelta(days=7)
    monkeypatch.setattr(PaperV2QualityGate, "_add_sector_rs_telemetry", lambda self, signal: dict(signal))
    (_, metrics, _), captured = _run(monkeypatch, [_row("AAA", passed=True, score=100, regime=regime)], [candidate], breadth_index=_Breadth(fields=fields))
    if reason is None:
        assert captured["candidates"] == [candidate]
    else:
        assert captured["candidates"] == []
        assert metrics["q70_rejection_counts"] == {reason: 1}


def test_current_vn100_resolves_once_and_fixed_execution_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = _trade("AAA")
    calls = {"vn100": 0}
    def current() -> list[str]:
        calls["vn100"] += 1
        return ["AAA", "VNINDEX"]
    monkeypatch.setattr(evaluator, "get_vn100_symbols", current)
    monkeypatch.setattr(PaperV2QualityGate, "_add_sector_rs_telemetry", lambda self, signal: dict(signal))
    captured = _patch_pipeline(monkeypatch, [_row("AAA", passed=True, score=100)], [candidate])
    _, metrics, _ = evaluator.run_frozen_q70_backtest(
        start_date="2020-02-01", end_date="2020-04-01", breadth_index=_Breadth(), parity_config=_parity(),
    )
    assert calls["vn100"] == 1
    assert metrics["effective_universe_mode"] == "legacy_current_vn100_retroactive"
    assert metrics["retrospective_current_vn100_warning"]
    assert metrics["atr_stop_multiplier"] == 2.0 and metrics["atr_target_multiplier"] == 5.0
    assert not metrics["trailing_enabled"] and not metrics["break_even_enabled"]
    assert captured["simulator_kwargs"]["lot_size"] == 100
    assert captured["simulator_kwargs"]["max_positions"] == 10


def test_gate_retains_production_tie_and_invalid_value_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = _trade("AAA"), _trade("BBB")
    monkeypatch.setattr(PaperV2QualityGate, "_add_sector_rs_telemetry", lambda self, signal: dict(signal))
    (_, metrics, _), captured = _run(
        monkeypatch,
        [_row("AAA", passed=True, score=50), _row("BBB", passed=True, score=50)],
        [first, second],
    )
    assert captured["candidates"] == [first, second]  # right-side percentile ties are retained.
    invalid = HistoricalEvaluationRow("AAA", DAY, None, float("nan"), None, "BULL", True)
    (_, invalid_metrics, _), invalid_captured = _run(monkeypatch, [invalid], [first])
    assert invalid_captured["candidates"] == []
    assert invalid_metrics["q70_rejection_counts"] == {"quality<0.70": 1}
