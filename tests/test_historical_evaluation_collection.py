from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd

import backtesting.engine as engine
from backtesting.exit import ExitResult
from backtesting.trade import ExitExecution, ExitReason


class _EntryModel:
    name = "test_entry"


def _prepared_prices() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "time": "2020-01-03", "open": 103.0, "high": 104.0,
                "low": 102.0, "close": 103.0, "ATR14": 2.0, "ADX14": 30.0,
                "Stock_Return_20D": 2.0, "Index_Return_20D": 1.0,
                "Relative_Strength_20D": 1.0, "Market_Regime": "SIDEWAY",
            },
            {
                "time": "2020-01-01", "open": 100.0, "high": 101.0,
                "low": 99.0, "close": 100.0, "ATR14": 2.0, "ADX14": 20.0,
                "Stock_Return_20D": 1.0, "Index_Return_20D": 0.5,
                "Relative_Strength_20D": 0.5, "Market_Regime": "BULL",
            },
            {
                "time": "2020-01-02", "open": 101.0, "high": 102.0,
                "low": 100.0, "close": 101.0, "ATR14": 2.0, "ADX14": 25.0,
                "Stock_Return_20D": 1.5, "Index_Return_20D": 0.5,
                "Relative_Strength_20D": 1.0, "Market_Regime": "BULL",
            },
            {
                "time": "2020-01-06", "open": 104.0, "high": 105.0,
                "low": 103.0, "close": 104.0, "ATR14": 2.0, "ADX14": 35.0,
                "Stock_Return_20D": 3.0, "Index_Return_20D": 1.0,
                "Relative_Strength_20D": 2.0, "Market_Regime": "SIDEWAY",
            },
        ]
    )


def _patch_generation_dependencies(monkeypatch):
    monkeypatch.setattr(
        engine,
        "prepare_backtest_dataset",
        lambda *args, **kwargs: _prepared_prices(),
    )
    monkeypatch.setattr(
        engine,
        "build_market_config",
        lambda regime: {"regime": regime},
    )

    def fake_exit(*, price_df, entry_index, **kwargs):
        entry_date = pd.Timestamp(price_df.iloc[entry_index]["time"])
        return ExitResult(
            entry_index=entry_index,
            exit_index=entry_index,
            entry_date=entry_date,
            exit_date=entry_date,
            entry_price=float(price_df.iloc[entry_index]["open"]),
            exit_price=float(price_df.iloc[entry_index]["open"]),
            initial_stop_price=90.0,
            initial_target_price=110.0,
            stop_price=90.0,
            target_price=110.0,
            exit_reason=ExitReason.TIME_EXIT,
            execution=ExitExecution.NORMAL,
        )

    monkeypatch.setattr(engine, "_simulate_exit", fake_exit)


def _evaluation_for_date(latest):
    date = pd.Timestamp(latest["time"]).date().isoformat()
    if date == "2020-01-01":
        return {
            "status": "FAILED",
            "reason": "trend_filter",
            "score": np.nan,
            "relative_strength_20d": np.inf,
            "adx": np.nan,
            "regime": "BULL",
        }
    if date == "2020-01-02":
        return {
            "status": "PASSED",
            "score": 70.0,
            "relative_strength_20d": 1.0,
            "adx": 25.0,
            "atr": 2.0,
            "regime": "BULL",
        }
    return {
        "status": "FAILED",
        "reason": "adx_filter",
        "score": 55.0,
        "relative_strength_20d": -1.0,
        "adx": 30.0,
        "regime": "SIDEWAY",
    }


def test_collector_preserves_existing_candidate_wrapper_behavior(monkeypatch) -> None:
    _patch_generation_dependencies(monkeypatch)
    monkeypatch.setattr(
        engine,
        "evaluate_prepared_row",
        lambda **kwargs: _evaluation_for_date(kwargs["latest"]),
    )
    kwargs = {
        "symbol": "abc",
        "config": engine.BacktestConfig(),
        "warmup_bars": 0,
        "entry_model": _EntryModel(),
        "start_date": "2020-01-01",
        "end_date": "2020-01-06",
    }

    collection = engine.collect_candidate_generation(**kwargs)
    wrapped = engine.generate_candidate_trades(**kwargs)

    assert [trade.to_dict() for trade in wrapped] == [
        trade.to_dict() for trade in collection.candidates
    ]
    assert [trade.signal_date.date().isoformat() for trade in collection.candidates] == [
        "2020-01-02"
    ]


def test_collector_records_failures_and_raw_evaluation_fields(monkeypatch) -> None:
    _patch_generation_dependencies(monkeypatch)
    calls: Counter[str] = Counter()

    def fake_evaluate(**kwargs):
        date = pd.Timestamp(kwargs["latest"]["time"]).date().isoformat()
        calls[date] += 1
        return _evaluation_for_date(kwargs["latest"])

    monkeypatch.setattr(engine, "evaluate_prepared_row", fake_evaluate)

    collection = engine.collect_candidate_generation(
        "abc",
        engine.BacktestConfig(),
        warmup_bars=0,
        entry_model=_EntryModel(),
        start_date="2020-01-01",
        end_date="2020-01-06",
    )

    assert calls == Counter({"2020-01-01": 1, "2020-01-02": 1, "2020-01-03": 1})
    assert [row.signal_date.date().isoformat() for row in collection.evaluations] == [
        "2020-01-01", "2020-01-02", "2020-01-03"
    ]
    failed = collection.evaluations[0]
    passed = collection.evaluations[1]
    assert failed.symbol == "ABC"
    assert not failed.base_entry_passed
    assert failed.reason == "trend_filter"
    assert np.isnan(failed.score)
    assert np.isinf(failed.relative_strength_20d)
    assert np.isnan(failed.adx)
    assert passed.base_entry_passed
    assert (passed.score, passed.relative_strength_20d, passed.adx, passed.regime) == (
        70.0, 1.0, 25.0, "BULL"
    )
    assert [trade.symbol for trade in collection.candidates] == ["ABC"]


def test_collector_groups_non_candidate_evaluations_by_signal_date(monkeypatch) -> None:
    _patch_generation_dependencies(monkeypatch)
    monkeypatch.setattr(
        engine,
        "evaluate_prepared_row",
        lambda **kwargs: _evaluation_for_date(kwargs["latest"]),
    )

    collection = engine.collect_candidate_generation(
        "ABC",
        engine.BacktestConfig(),
        warmup_bars=0,
        entry_model=_EntryModel(),
        start_date="2020-01-01",
        end_date="2020-01-06",
    )

    failed_rows = collection.evaluations_for_signal_date("2020-01-01")
    passed_rows = collection.evaluations_for_signal_date("2020-01-02")
    assert len(failed_rows) == 1
    assert not failed_rows[0].base_entry_passed
    assert len(passed_rows) == 1
    assert passed_rows[0].base_entry_passed
    assert collection.evaluations_for_signal_date("2019-12-31") == ()
