from __future__ import annotations

from datetime import datetime
from types import MappingProxyType, SimpleNamespace
import sys

import pandas as pd
import pytest

import backtesting.engine as engine
from backtesting.trade import ExitReason, Trade
from core.database_coverage import CoverageUniverseIndex


def _coverage_index(
    memberships: dict[str, frozenset[str]],
    *,
    start_date: str = "2020-01-01",
    end_date: str = "2020-01-03",
    minimum_history_sessions: int = 50,
    maximum_staleness_sessions: int = 5,
) -> CoverageUniverseIndex:
    session_dates = tuple(memberships)
    return CoverageUniverseIndex(
        start_date=start_date,
        end_date=end_date,
        effective_start_date=session_dates[0] if session_dates else None,
        effective_end_date=session_dates[-1] if session_dates else None,
        minimum_history_sessions=minimum_history_sessions,
        maximum_staleness_sessions=maximum_staleness_sessions,
        session_dates=session_dates,
        candidate_symbols=tuple(
            sorted({symbol for members in memberships.values() for symbol in members})
        ),
        eligible_count_by_session=MappingProxyType(
            {session_date: len(members) for session_date, members in memberships.items()}
        ),
        _members_by_session=MappingProxyType(memberships),
    )


def _trade(symbol: str, signal_date: str) -> Trade:
    signal_time = datetime.fromisoformat(signal_date)
    trade = Trade(
        symbol=symbol,
        signal_date=signal_time,
        entry_date=signal_time,
        entry_price=100.0,
        quantity=100,
    )
    trade.close(signal_time, 100.0, ExitReason.TIME_EXIT)
    return trade


def _patch_backtest_pipeline(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Trade]]:
    captured: dict[str, list[Trade]] = {}

    class FakePortfolioSimulator:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def simulate(self, trades: list[Trade]) -> SimpleNamespace:
            captured["simulated"] = list(trades)
            return SimpleNamespace(
                executed_trades=list(trades),
                rejected_trades=[],
                final_cash=1_000_000_000.0,
                final_market_value=0.0,
                final_open_positions=0,
                equity_curve=pd.DataFrame(),
                final_equity=1_000_000_000.0,
            )

    def fake_calculate_portfolio_metrics(
        equity_curve: pd.DataFrame,
        *,
        final_equity: float,
        risk_free_rate_pct: float = 0.0,
    ) -> dict[str, float]:
        return {"cagr_pct": 0.0}

    monkeypatch.setattr(engine, "PortfolioSimulator", FakePortfolioSimulator)
    monkeypatch.setattr(
        engine,
        "calculate_portfolio_metrics",
        fake_calculate_portfolio_metrics,
    )
    monkeypatch.setattr(engine, "calculate_trade_distribution", lambda *args: {})
    monkeypatch.setattr(engine, "calculate_trade_analytics", lambda *args: {})
    return captured


def test_default_current_vn100_behavior_remains_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_backtest_pipeline(monkeypatch)
    generated_symbols: list[str] = []
    monkeypatch.setattr(engine, "get_vn100_symbols", lambda: ["BBB", "AAA"])
    monkeypatch.setattr(
        engine,
        "build_database_coverage_index",
        lambda *args, **kwargs: pytest.fail("default mode must not build coverage index"),
    )
    monkeypatch.setattr(
        engine,
        "generate_candidate_trades",
        lambda symbol, **kwargs: generated_symbols.append(symbol) or [],
    )

    _, metrics, _ = engine.run_backtest()

    assert generated_symbols == ["AAA", "BBB"]
    assert captured["simulated"] == []
    assert metrics["requested_universe_mode"] == "current_vn100"
    assert metrics["effective_universe_mode"] == "current_vn100"
    assert metrics["candidate_symbol_count"] == 2


def test_explicit_symbols_bypass_coverage_index_building_and_filtering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_backtest_pipeline(monkeypatch)
    candidate = _trade("AAA", "2020-01-01")
    monkeypatch.setattr(
        engine,
        "build_database_coverage_index",
        lambda *args, **kwargs: pytest.fail("explicit symbols must bypass coverage index"),
    )
    monkeypatch.setattr(
        engine,
        "generate_candidate_trades",
        lambda symbol, **kwargs: [candidate] if symbol == "AAA" else [],
    )

    _, metrics, _ = engine.run_backtest(
        symbols=["BBB", "AAA"],
        universe_mode="database_coverage",
    )

    assert captured["simulated"] == [candidate]
    assert metrics["effective_universe_mode"] == "explicit_symbols"
    assert metrics["candidate_symbol_count"] == 2


def test_database_coverage_uses_index_union_without_current_vn100(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_backtest_pipeline(monkeypatch)
    index = _coverage_index(
        {
            "2020-01-01": frozenset({"AAA"}),
            "2020-01-02": frozenset({"BBB"}),
        }
    )
    generated_symbols: list[str] = []
    monkeypatch.setattr(
        engine,
        "get_vn100_symbols",
        lambda: pytest.fail("database_coverage must not retrieve current VN100"),
    )
    monkeypatch.setattr(engine, "build_database_coverage_index", lambda *args, **kwargs: index)
    monkeypatch.setattr(
        engine,
        "generate_candidate_trades",
        lambda symbol, **kwargs: generated_symbols.append(symbol) or [],
    )

    _, metrics, _ = engine.run_backtest(
        universe_mode="database_coverage",
        start_date="2020-01-01",
        end_date="2020-01-03",
    )

    assert generated_symbols == ["AAA", "BBB"]
    assert captured["simulated"] == []
    assert metrics["effective_universe_mode"] == "database_coverage"
    assert metrics["requested_universe_mode"] == "database_coverage"
    assert metrics["minimum_history_sessions"] == 50
    assert metrics["maximum_staleness_sessions"] == 5
    assert metrics["candidate_symbol_count"] == 2
    assert metrics["eligible_symbol_count_min"] == 1
    assert metrics["eligible_symbol_count_max"] == 1
    assert metrics["eligible_symbol_count_mean"] == 1.0


def test_cli_accepts_database_coverage_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "engine.py",
            "--all",
            "--universe-mode",
            "database_coverage",
            "--minimum-history-sessions",
            "75",
            "--maximum-staleness-sessions",
            "3",
        ],
    )

    args = engine.parse_args()

    assert args.universe_mode == "database_coverage"
    assert args.minimum_history_sessions == 75
    assert args.maximum_staleness_sessions == 3


def test_database_coverage_filters_candidates_using_signal_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_backtest_pipeline(monkeypatch)
    index = _coverage_index(
        {
            "2020-01-01": frozenset(),
            "2020-01-02": frozenset({"FUT", "OTHER"}),
            "2020-01-03": frozenset({"FUT", "OTHER"}),
        }
    )
    before_eligibility = _trade("FUT", "2020-01-01")
    at_eligibility = _trade("FUT", "2020-01-02")
    at_eligibility.entry_date = datetime.fromisoformat("2020-01-03")
    monkeypatch.setattr(engine, "build_database_coverage_index", lambda *args, **kwargs: index)
    monkeypatch.setattr(
        engine,
        "generate_candidate_trades",
        lambda symbol, **kwargs: [before_eligibility, at_eligibility] if symbol == "FUT" else [],
    )

    engine.run_backtest(
        universe_mode="database_coverage",
        start_date="2020-01-01",
        end_date="2020-01-03",
    )

    assert captured["simulated"] == [at_eligibility]


def test_database_coverage_builds_one_index_per_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_backtest_pipeline(monkeypatch)
    index = _coverage_index({"2020-01-01": frozenset({"AAA", "BBB"})})
    calls = 0

    def build_index(*args: object, **kwargs: object) -> CoverageUniverseIndex:
        nonlocal calls
        calls += 1
        return index

    monkeypatch.setattr(engine, "build_database_coverage_index", build_index)
    monkeypatch.setattr(engine, "generate_candidate_trades", lambda *args, **kwargs: [])

    engine.run_backtest(
        universe_mode="database_coverage",
        start_date="2020-01-01",
        end_date="2020-01-01",
    )

    assert calls == 1


def test_compatible_injected_coverage_index_is_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_backtest_pipeline(monkeypatch)
    index = _coverage_index({"2020-01-01": frozenset({"AAA", "BBB"})})
    monkeypatch.setattr(
        engine,
        "build_database_coverage_index",
        lambda *args, **kwargs: pytest.fail("compatible injected index must be reused"),
    )
    monkeypatch.setattr(engine, "generate_candidate_trades", lambda *args, **kwargs: [])

    engine.run_backtest(
        universe_mode="database_coverage",
        start_date="2020-01-01",
        end_date="2020-01-03",
        coverage_index=index,
    )


@pytest.mark.parametrize(
    ("index", "kwargs", "message"),
    [
        (
            _coverage_index({"2020-01-02": frozenset({"AAA"})}, start_date="2020-01-02"),
            {},
            "does not cover",
        ),
        (
            _coverage_index(
                {"2020-01-01": frozenset({"AAA"})},
                minimum_history_sessions=20,
            ),
            {},
            "minimum_history_sessions",
        ),
        (
            _coverage_index(
                {"2020-01-01": frozenset({"AAA"})},
                maximum_staleness_sessions=2,
            ),
            {},
            "maximum_staleness_sessions",
        ),
    ],
)
def test_incompatible_injected_coverage_indexes_fail_clearly(
    monkeypatch: pytest.MonkeyPatch,
    index: CoverageUniverseIndex,
    kwargs: dict[str, object],
    message: str,
) -> None:
    _patch_backtest_pipeline(monkeypatch)

    with pytest.raises(ValueError, match=message):
        engine.run_backtest(
            universe_mode="database_coverage",
            start_date="2020-01-01",
            end_date="2020-01-03",
            coverage_index=index,
            **kwargs,
        )
