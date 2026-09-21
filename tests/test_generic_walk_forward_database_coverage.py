from __future__ import annotations

from types import MappingProxyType
from typing import Any

import pandas as pd
import pytest

import backtesting.walk_forward as walk_forward
from backtesting.walk_forward import WalkForwardConfig
from core.database_coverage import CoverageUniverseIndex


def _coverage_index(
    memberships: dict[str, frozenset[str]],
    *,
    start_date: str = "2020-01-01",
    end_date: str = "2020-06-30",
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


def _config() -> WalkForwardConfig:
    return WalkForwardConfig(
        start_date="2020-01-01",
        end_date="2020-06-30",
        train_months=1,
        test_months=1,
        step_months=1,
    )


def _run_backtest_stub(calls: list[dict[str, Any]]):
    def run_backtest_fn(**kwargs: Any):
        calls.append(kwargs)
        return [], {
            "final_equity": kwargs["initial_capital"],
            "total_return_pct": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "profit_factor": 0.0,
            "win_rate_pct": 0.0,
            "total_trades": 0,
        }, pd.DataFrame()

    return run_backtest_fn


def test_default_walk_forward_does_not_change_backtest_call_contract() -> None:
    calls: list[dict[str, Any]] = []

    result = walk_forward.run_walk_forward(
        config=_config(),
        initial_capital=1_000_000.0,
        run_backtest_fn=_run_backtest_stub(calls),
        backtest_kwargs={"symbols": ["AAA"]},
    )

    assert calls
    assert all("coverage_index" not in call for call in calls)
    assert all("universe_mode" not in call for call in calls)
    assert set(result.folds["requested_universe_mode"]) == {"current_vn100"}
    assert set(result.folds["effective_universe_mode"]) == {"explicit_symbols"}


def test_explicit_symbols_bypass_coverage_index_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        walk_forward,
        "build_database_coverage_index",
        lambda *args, **kwargs: pytest.fail("explicit symbols must bypass coverage construction"),
    )

    result = walk_forward.run_walk_forward(
        config=_config(),
        initial_capital=1_000_000.0,
        run_backtest_fn=_run_backtest_stub(calls),
        backtest_kwargs={"symbols": ["AAA"]},
        universe_mode="database_coverage",
    )

    assert all("coverage_index" not in call for call in calls)
    assert all(call["universe_mode"] == "database_coverage" for call in calls)
    assert set(result.folds["effective_universe_mode"]) == {"explicit_symbols"}


def test_database_coverage_builds_once_and_injects_same_index_for_all_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    index = _coverage_index(
        {
            "2020-01-15": frozenset({"AAA"}),
            "2020-02-15": frozenset({"AAA", "FUT"}),
            "2020-03-15": frozenset({"AAA", "FUT"}),
            "2020-04-15": frozenset({"AAA", "FUT"}),
            "2020-05-15": frozenset({"AAA", "FUT"}),
            "2020-06-15": frozenset({"AAA", "FUT"}),
        }
    )
    build_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def build_index(*args: Any, **kwargs: Any) -> CoverageUniverseIndex:
        build_calls.append((args, kwargs))
        return index

    monkeypatch.setattr(walk_forward, "build_database_coverage_index", build_index)

    result = walk_forward.run_walk_forward(
        config=_config(),
        initial_capital=1_000_000.0,
        run_backtest_fn=_run_backtest_stub(calls),
        backtest_kwargs={},
        universe_mode="database_coverage",
    )

    assert len(build_calls) == 1
    assert build_calls[0][0] == ("2020-01-01", "2020-06-30")
    assert len(calls) == len(result.folds) * 2
    assert all(call["coverage_index"] is index for call in calls)
    assert all(call["universe_mode"] == "database_coverage" for call in calls)
    assert all("symbols" not in call for call in calls)
    assert set(result.folds["requested_universe_mode"]) == {"database_coverage"}
    assert set(result.folds["effective_universe_mode"]) == {"database_coverage"}


def test_database_coverage_preserves_fold_boundaries_and_relevant_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    index = _coverage_index(
        {
            "2020-01-15": frozenset({"AAA"}),
            "2020-02-15": frozenset({"AAA", "FUT"}),
            "2020-03-15": frozenset({"AAA", "FUT"}),
            "2020-04-15": frozenset({"AAA", "FUT"}),
            "2020-05-15": frozenset({"AAA", "FUT"}),
            "2020-06-15": frozenset({"AAA", "FUT"}),
        }
    )
    monkeypatch.setattr(walk_forward, "build_database_coverage_index", lambda *args, **kwargs: index)

    result = walk_forward.run_walk_forward(
        config=_config(),
        initial_capital=1_000_000.0,
        run_backtest_fn=_run_backtest_stub(calls),
        backtest_kwargs={},
        universe_mode="database_coverage",
    )

    first_fold = result.folds.iloc[0]
    assert calls[0]["start_date"] == "2020-01-01"
    assert calls[0]["end_date"] == "2020-01-31"
    assert calls[1]["start_date"] == "2020-02-01"
    assert calls[1]["end_date"] == "2020-02-29"
    assert first_fold["train_eligible_symbol_count_min"] == 1
    assert first_fold["test_eligible_symbol_count_min"] == 2
    assert first_fold["minimum_history_sessions"] == 50
    assert first_fold["maximum_staleness_sessions"] == 5


def test_database_coverage_passes_later_entrant_index_to_signal_date_filtering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_eligibility: list[tuple[str, bool]] = []
    index = _coverage_index(
        {
            "2020-01-15": frozenset({"AAA"}),
            "2020-02-15": frozenset({"AAA", "FUT"}),
            "2020-03-15": frozenset({"AAA", "FUT"}),
            "2020-04-15": frozenset({"AAA", "FUT"}),
            "2020-05-15": frozenset({"AAA", "FUT"}),
            "2020-06-15": frozenset({"AAA", "FUT"}),
        }
    )
    monkeypatch.setattr(walk_forward, "build_database_coverage_index", lambda *args, **kwargs: index)

    def run_backtest_fn(**kwargs: Any):
        signal_date = kwargs["start_date"]
        seen_eligibility.append(
            (signal_date, kwargs["coverage_index"].is_eligible("FUT", signal_date))
        )
        return [], {
            "final_equity": kwargs["initial_capital"],
            "total_return_pct": 0.0,
            "sharpe_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "profit_factor": 0.0,
            "win_rate_pct": 0.0,
            "total_trades": 0,
        }, pd.DataFrame()

    walk_forward.run_walk_forward(
        config=_config(),
        initial_capital=1_000_000.0,
        run_backtest_fn=run_backtest_fn,
        backtest_kwargs={},
        universe_mode="database_coverage",
    )

    assert ("2020-01-01", False) in seen_eligibility
    assert ("2020-03-01", True) in seen_eligibility


def test_database_coverage_reuses_compatible_injected_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    index = _coverage_index({"2020-01-15": frozenset({"AAA", "FUT"})})
    monkeypatch.setattr(
        walk_forward,
        "build_database_coverage_index",
        lambda *args, **kwargs: pytest.fail("compatible injected index must be reused"),
    )

    walk_forward.run_walk_forward(
        config=_config(),
        initial_capital=1_000_000.0,
        run_backtest_fn=_run_backtest_stub(calls),
        backtest_kwargs={},
        universe_mode="database_coverage",
        coverage_index=index,
    )

    assert all(call["coverage_index"] is index for call in calls)


@pytest.mark.parametrize(
    ("index", "message"),
    [
        (
            _coverage_index(
                {"2020-02-15": frozenset({"AAA"})},
                start_date="2020-02-01",
            ),
            "does not cover",
        ),
        (
            _coverage_index(
                {"2020-01-15": frozenset({"AAA"})},
                minimum_history_sessions=20,
            ),
            "minimum_history_sessions",
        ),
        (
            _coverage_index(
                {"2020-01-15": frozenset({"AAA"})},
                maximum_staleness_sessions=2,
            ),
            "maximum_staleness_sessions",
        ),
    ],
)
def test_incompatible_coverage_indexes_fail_clearly(
    index: CoverageUniverseIndex,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        walk_forward.run_walk_forward(
            config=_config(),
            initial_capital=1_000_000.0,
            run_backtest_fn=lambda **kwargs: pytest.fail("backtest must not run"),
            backtest_kwargs={},
            universe_mode="database_coverage",
            coverage_index=index,
        )
