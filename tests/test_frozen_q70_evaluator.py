from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
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


def _parity(initial_cash: float = 100_000_000) -> BacktestPaperParityConfig:
    return BacktestPaperParityConfig(
        initial_cash=initial_cash, position_sizer="fixed_fraction",
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
    monkeypatch.setattr(
        evaluator,
        "calculate_portfolio_metrics",
        lambda *args, **kwargs: {"final_equity": kwargs["final_equity"]},
    )

    class Simulator:
        def __init__(self, **kwargs: Any) -> None:
            captured["simulator_kwargs"] = kwargs
            self.initial_cash = kwargs["initial_cash"]
        def simulate(self, candidates: list[Trade]) -> Any:
            captured["candidates"] = candidates
            final_equity = self.initial_cash + sum(
                candidate.net_pnl for candidate in candidates
            )
            return SimpleNamespace(
                executed_trades=candidates,
                equity_curve=pd.DataFrame({"date": [DAY], "equity": [final_equity]}),
                final_equity=final_equity,
            )
    monkeypatch.setattr(evaluator, "PortfolioSimulator", Simulator)
    return captured


def _run(monkeypatch: pytest.MonkeyPatch, rows: list[HistoricalEvaluationRow], trades: list[Trade], **kwargs: Any):
    captured = _patch_pipeline(monkeypatch, rows, trades)
    breadth_index = kwargs.pop("breadth_index", _Breadth())
    parity_config = kwargs.pop("parity_config", _parity())
    result = evaluator.run_frozen_q70_backtest(
        symbols=["AAA"], start_date="2020-02-01", end_date="2020-04-01",
        breadth_index=breadth_index, parity_config=parity_config, **kwargs,
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


def test_historical_gate_telemetry_cannot_reach_vnstock_or_change_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real gate remains in use, but its non-selection telemetry is offline."""
    import core.sector as sector
    import core.universe as universe
    import strategy.paper_v2_gate as paper_gate
    import strategy.relative_strength_v2 as sector_rs

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("historical Q70 must not retrieve a live VN100/sector universe")

    monkeypatch.setattr(evaluator, "get_vn100_symbols", forbidden)
    monkeypatch.setattr(universe, "get_vn100_symbols", forbidden)
    monkeypatch.setattr(paper_gate, "get_vn100_symbols", forbidden)
    monkeypatch.setattr(sector_rs, "get_vn100_symbols", forbidden)
    monkeypatch.setattr(paper_gate, "fetch_sector_mapping", forbidden)
    monkeypatch.setattr(sector, "fetch_sector_mapping", forbidden)

    candidate = _trade("AAA")
    (_, metrics, _), captured = _run(
        monkeypatch,
        [_row("AAA", passed=True, score=100)],
        [candidate],
    )
    assert captured["candidates"] == [candidate]
    assert metrics["q70_accepted_candidates"] == 1


def test_injected_legacy_and_coverage_universes_never_fall_back_to_live_vn100(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _trade("AAA")
    rows = [_row("AAA", passed=True, score=100)]
    captured = _patch_pipeline(monkeypatch, rows, [candidate])
    monkeypatch.setattr(
        evaluator,
        "get_vn100_symbols",
        lambda: (_ for _ in ()).throw(AssertionError("unexpected VN100 fallback")),
    )
    monkeypatch.setattr(PaperV2QualityGate, "_add_sector_rs_telemetry", lambda self, signal: dict(signal))
    evaluator.run_frozen_q70_backtest(
        start_date="2020-02-01", end_date="2020-04-01",
        current_vn100_symbols=("AAA", "BBB"), breadth_index=_Breadth(),
        parity_config=_parity(),
    )
    assert captured["candidates"] == [candidate]

    coverage = CoverageUniverseIndex(
        start_date="2020-02-01", end_date="2020-04-01",
        effective_start_date="2020-03-01", effective_end_date="2020-03-01",
        minimum_history_sessions=50, maximum_staleness_sessions=5,
        session_dates=("2020-03-01",), candidate_symbols=("AAA",),
        eligible_count_by_session={"2020-03-01": 1},
        _members_by_session={"2020-03-01": frozenset({"AAA"})},
    )
    evaluator.run_frozen_q70_backtest(
        start_date="2020-02-01", end_date="2020-04-01",
        universe_mode="database_coverage", coverage_index=coverage,
        breadth_index=_Breadth(), parity_config=_parity(),
    )


def test_executed_trade_cost_aggregation_reconciles_final_equity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    winner, loser = _trade("AAA"), _trade("BBB")
    winner.entry_price, winner.exit_price, winner.quantity = 100.0, 110.0, 2
    winner.buy_commission, winner.sell_commission, winner.sell_tax = 1.0, 2.0, 3.0
    loser.entry_price, loser.exit_price, loser.quantity = 100.0, 90.0, 1
    loser.buy_commission, loser.sell_commission, loser.sell_tax = 1.0, 2.0, 3.0
    monkeypatch.setattr(PaperV2QualityGate, "_add_sector_rs_telemetry", lambda self, signal: dict(signal))
    (_, metrics, _), _ = _run(
        monkeypatch,
        [_row("AAA", passed=True, score=50), _row("BBB", passed=True, score=50)],
        [winner, loser],
    )
    assert metrics["gross_trading_pnl"] == 10.0
    assert metrics["total_buy_commission"] == 2.0
    assert metrics["total_sell_commission"] == 4.0
    assert metrics["total_sell_tax"] == 6.0
    assert metrics["total_transaction_cost"] == 12.0
    assert metrics["net_trading_pnl"] == -2.0
    assert metrics["gross_profit"] == 14.0
    assert metrics["gross_loss"] == 16.0
    assert metrics["profit_factor"] == pytest.approx(14.0 / 16.0)
    assert metrics["final_equity"] == pytest.approx(100_000_000.0 + metrics["net_trading_pnl"])


def test_zero_cost_metrics_are_valid_when_no_trade_executes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(PaperV2QualityGate, "_add_sector_rs_telemetry", lambda self, signal: dict(signal))
    (_, metrics, _), captured = _run(monkeypatch, [_row("AAA", passed=True, score=100)], [])
    assert captured["candidates"] == []
    assert metrics["gross_trading_pnl"] == 0.0
    assert metrics["net_trading_pnl"] == 0.0
    assert metrics["total_transaction_cost"] == 0.0


@pytest.mark.parametrize(
    ("exit_price", "initial_cash", "expected_sign"),
    [
        (105.0, 250_000_000.0, 1),
        (95.0, 250_000_000.0, -1),
    ],
)
def test_total_return_uses_fold_specific_initial_cash_not_first_equity_row(
    monkeypatch: pytest.MonkeyPatch,
    exit_price: float,
    initial_cash: float,
    expected_sign: int,
) -> None:
    trade = _trade("AAA")
    trade.exit_price = exit_price
    # Deliberately conflicting portfolio metric proves the evaluator overrides
    # the first-equity-row return with the fold-specific parity cash.
    _patch_pipeline(monkeypatch, [_row("AAA", passed=True, score=100)], [trade])
    monkeypatch.setattr(
        evaluator,
        "calculate_portfolio_metrics",
        lambda *args, **kwargs: {
            "final_equity": kwargs["final_equity"],
            "total_return_pct": 999.0,
        },
    )
    monkeypatch.setattr(
        PaperV2QualityGate,
        "_add_sector_rs_telemetry",
        lambda self, signal: dict(signal),
    )
    _, metrics, _ = evaluator.run_frozen_q70_backtest(
        symbols=["AAA"],
        start_date="2020-02-01",
        end_date="2020-04-01",
        breadth_index=_Breadth(),
        parity_config=_parity(initial_cash),
    )
    expected = (metrics["final_equity"] / initial_cash - 1.0) * 100.0
    assert metrics["total_return_pct"] == pytest.approx(expected)
    assert math.copysign(1, metrics["total_return_pct"]) == expected_sign
    assert metrics["total_return_pct"] != 999.0


def test_total_return_is_zero_for_genuine_no_trade_fold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        PaperV2QualityGate,
        "_add_sector_rs_telemetry",
        lambda self, signal: dict(signal),
    )
    (_, metrics, _), _ = _run(
        monkeypatch,
        [_row("AAA", passed=True, score=100)],
        [],
        parity_config=_parity(250_000_000.0),
    )
    assert metrics["final_equity"] == 250_000_000.0
    assert metrics["total_return_pct"] == 0.0
