from __future__ import annotations

from types import MappingProxyType
from typing import Any

import pandas as pd
import pytest

import backtesting.walk_forward_optimizer as optimizer
from backtesting.walk_forward import WalkForwardConfig
from backtesting.walk_forward_optimizer import (
    OptimizationConfig,
    WalkForwardParameterGrid,
)
from core.database_coverage import CoverageUniverseIndex


def _coverage_index(
    memberships: dict[str, frozenset[str]],
    *,
    start_date: str = "2020-01-01",
    end_date: str = "2020-03-31",
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


def _walk_forward_config() -> WalkForwardConfig:
    return WalkForwardConfig(
        start_date="2020-01-01",
        end_date="2020-03-31",
        train_months=1,
        test_months=1,
        step_months=1,
    )


def _parameter_grid() -> WalkForwardParameterGrid:
    return WalkForwardParameterGrid(
        atr_stop_multipliers=(1.0, 2.0),
        atr_target_multipliers=(3.0,),
        holding_days=(20,),
        min_adx_values=(15.0,),
    )


def _run_backtest_stub(calls: list[dict[str, Any]]):
    def run_backtest_fn(**kwargs: Any):
        calls.append(kwargs)
        stop_multiplier = kwargs["exit_model"]["stop_atr_multiplier"]
        return [], {
            "final_equity": kwargs["initial_capital"],
            "total_trades": 1,
            "total_return_pct": stop_multiplier,
            "sharpe_ratio": stop_multiplier,
            "sortino_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "profit_factor": 1.0,
            "win_rate_pct": 0.0,
        }, pd.DataFrame()

    return run_backtest_fn


def _build_exit_model(**kwargs: Any) -> dict[str, Any]:
    return kwargs


def _run_optimizer(
    calls: list[dict[str, Any]],
    **kwargs: Any,
):
    return optimizer.run_walk_forward_optimization(
        walk_forward_config=_walk_forward_config(),
        parameter_grid=_parameter_grid(),
        optimization_config=OptimizationConfig(minimum_train_trades=1),
        initial_capital=1_000_000.0,
        run_backtest_fn=_run_backtest_stub(calls),
        build_exit_model_fn=_build_exit_model,
        base_backtest_kwargs={},
        **kwargs,
    )


def test_default_optimizer_backtest_calls_remain_unchanged() -> None:
    calls: list[dict[str, Any]] = []

    result = _run_optimizer(calls)

    assert calls
    assert all("coverage_index" not in call for call in calls)
    assert all("universe_mode" not in call for call in calls)
    assert set(result.folds["requested_universe_mode"]) == {"current_vn100"}


def test_explicit_symbols_bypass_coverage_index_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        optimizer,
        "build_database_coverage_index",
        lambda *args, **kwargs: pytest.fail("explicit symbols must bypass coverage construction"),
    )

    result = optimizer.run_walk_forward_optimization(
        walk_forward_config=_walk_forward_config(),
        parameter_grid=_parameter_grid(),
        optimization_config=OptimizationConfig(minimum_train_trades=1),
        initial_capital=1_000_000.0,
        run_backtest_fn=_run_backtest_stub(calls),
        build_exit_model_fn=_build_exit_model,
        base_backtest_kwargs={"symbols": ["AAA"]},
        universe_mode="database_coverage",
    )

    assert all("coverage_index" not in call for call in calls)
    assert all(call["universe_mode"] == "database_coverage" for call in calls)
    assert set(result.folds["effective_universe_mode"]) == {"explicit_symbols"}


def test_database_coverage_builds_once_and_reuses_one_index_for_all_backtests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    index = _coverage_index(
        {
            "2020-01-15": frozenset({"AAA"}),
            "2020-02-15": frozenset({"AAA", "FUT"}),
            "2020-03-15": frozenset({"AAA", "FUT"}),
        }
    )
    build_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def build_index(*args: Any, **kwargs: Any) -> CoverageUniverseIndex:
        build_calls.append((args, kwargs))
        return index

    monkeypatch.setattr(optimizer, "build_database_coverage_index", build_index)
    result = _run_optimizer(calls, universe_mode="database_coverage")

    assert len(build_calls) == 1
    assert build_calls[0][0] == ("2020-01-01", "2020-03-31")
    assert len(calls) == len(result.folds) * 3
    assert all(call["coverage_index"] is index for call in calls)
    assert all(call["universe_mode"] == "database_coverage" for call in calls)
    assert all("symbols" not in call for call in calls)


def test_optimizer_keeps_parameter_fitting_train_only_and_tests_selected_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    index = _coverage_index(
        {
            "2020-01-15": frozenset({"AAA"}),
            "2020-02-15": frozenset({"AAA", "FUT"}),
            "2020-03-15": frozenset({"AAA", "FUT"}),
        }
    )
    monkeypatch.setattr(optimizer, "build_database_coverage_index", lambda *args, **kwargs: index)
    result = _run_optimizer(calls, universe_mode="database_coverage")

    for fold_index, fold in enumerate(result.folds.itertuples()):
        fold_calls = calls[fold_index * 3 : (fold_index + 1) * 3]
        train_calls = fold_calls[:2]
        test_calls = fold_calls[2:]
        assert len(train_calls) == 2
        assert len(test_calls) == 1
        assert all(
            call["start_date"] == str(fold.train_start)
            and call["end_date"] == str(fold.train_end)
            for call in train_calls
        )
        assert test_calls[0]["start_date"] == str(fold.test_start)
        assert test_calls[0]["end_date"] == str(fold.test_end)
        assert test_calls[0]["exit_model"]["stop_atr_multiplier"] == 2.0


def test_fold_metadata_uses_train_and_test_coverage_ranges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    index = _coverage_index(
        {
            "2020-01-15": frozenset({"AAA"}),
            "2020-02-15": frozenset({"AAA", "FUT"}),
            "2020-03-15": frozenset({"AAA", "FUT"}),
        }
    )
    monkeypatch.setattr(optimizer, "build_database_coverage_index", lambda *args, **kwargs: index)

    result = _run_optimizer(calls, universe_mode="database_coverage")

    first_fold = result.folds.iloc[0]
    assert first_fold["train_eligible_symbol_count_min"] == 1
    assert first_fold["test_eligible_symbol_count_min"] == 2
    assert first_fold["minimum_history_sessions"] == 50
    assert first_fold["maximum_staleness_sessions"] == 5


def test_compatible_injected_index_is_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    index = _coverage_index({"2020-01-15": frozenset({"AAA", "FUT"})})
    monkeypatch.setattr(
        optimizer,
        "build_database_coverage_index",
        lambda *args, **kwargs: pytest.fail("compatible injected index must be reused"),
    )

    _run_optimizer(
        calls,
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
def test_incompatible_injected_indexes_fail_clearly(
    index: CoverageUniverseIndex,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _run_optimizer(
            [],
            universe_mode="database_coverage",
            coverage_index=index,
        )
