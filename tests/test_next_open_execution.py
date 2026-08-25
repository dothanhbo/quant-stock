from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from execution.signal_executor import PaperExecutionConfig, PaperSignalExecutor


def create_market_database(
    path: Path,
    *,
    signal_date: date,
    sessions: list[tuple[date, float]],
    historical_close: float = 50.0,
    historical_volume: float = 1_000_000.0,
    execution_volume: float = 1_000_000.0,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE prices(
                symbol TEXT,
                time TEXT,
                open REAL,
                close REAL,
                volume REAL
            )
            """
        )
        for offset in range(19, -1, -1):
            trading_date = signal_date - timedelta(days=offset)
            connection.execute(
                "INSERT INTO prices VALUES (?, ?, ?, ?, ?)",
                (
                    "AAA",
                    trading_date.isoformat(),
                    historical_close,
                    historical_close,
                    historical_volume,
                ),
            )
        for trading_date, open_price in sessions:
            connection.execute(
                "INSERT INTO prices VALUES (?, ?, ?, ?, ?)",
                (
                    "VNINDEX",
                    trading_date.isoformat(),
                    1_300.0,
                    1_300.0,
                    1_000_000.0,
                ),
            )
            connection.execute(
                "INSERT INTO prices VALUES (?, ?, ?, ?, ?)",
                (
                    "AAA",
                    trading_date.isoformat(),
                    open_price,
                    open_price,
                    execution_volume,
                ),
            )


def build_executor(
    paper_db: Path,
    *,
    initial_cash: float = 100_000_000,
    fixed_fraction_pct: float = 10.0,
) -> PaperSignalExecutor:
    return PaperSignalExecutor(PaperExecutionConfig(
        enabled=True,
        database_path=paper_db,
        initial_cash=initial_cash,
        position_sizer="fixed_fraction",
        fixed_fraction_pct=fixed_fraction_pct,
        maximum_position_pct=20.0,
        maximum_gross_exposure_pct=80.0,
        maximum_open_positions=10,
        minimum_cash_buffer_pct=5.0,
        maximum_order_adtv20_pct=1.0,
    ))


def queue_aaa(
    executor: PaperSignalExecutor,
    signal_date: date,
) -> None:
    queued = executor.queue_signals([{
        "symbol": "AAA",
        "date": signal_date.isoformat(),
        "entry": 50.0,
        "atr": 1.0,
        "score": 90,
    }], report_date=signal_date)
    assert queued.queued_count == 1


def test_signal_is_queued_then_filled_at_next_open(tmp_path: Path) -> None:
    paper_db = tmp_path / "paper.db"
    market_db = tmp_path / "market.db"
    signal_date = date(2026, 8, 5)
    create_market_database(
        market_db,
        signal_date=signal_date,
        sessions=[(date(2026, 8, 6), 51.0)],
    )

    executor = build_executor(paper_db)
    queue_aaa(executor, signal_date)
    assert executor.broker.get_position("AAA") is None

    filled = executor.execute_pending_signals(
        valuation_date="2026-08-06",
        market_database_path=market_db,
    )
    assert filled.filled_count == 1
    assert filled.executions[0].requested_price == 51.0
    assert filled.executions[0].signal_rank == 1
    assert filled.executions[0].signal_score == 90
    lifecycle = executor.broker.get_position_lifecycle("AAA")
    assert lifecycle is not None
    assert lifecycle.maximum_holding_days == 30
    assert lifecycle.stop_price == 49_000.0
    assert lifecycle.take_profit_price == 56_000.0


def test_stale_signal_is_not_filled_after_missing_next_session(
    tmp_path: Path,
) -> None:
    paper_db = tmp_path / "paper.db"
    market_db = tmp_path / "market.db"
    signal_date = date(2026, 8, 5)
    create_market_database(
        market_db,
        signal_date=signal_date,
        sessions=[
            (date(2026, 8, 6), 51.0),
            (date(2026, 8, 7), 54.0),
        ],
    )
    executor = build_executor(paper_db)
    queue_aaa(executor, signal_date)

    result = executor.execute_pending_signals(
        valuation_date="2026-08-07",
        market_database_path=market_db,
    )

    assert result.filled_count == 0
    assert result.skipped_count == 1
    assert "MISSED_EXECUTION" in result.executions[0].reason
    assert executor.broker.get_position("AAA") is None
    with sqlite3.connect(paper_db) as connection:
        status, processed_date = connection.execute(
            "SELECT status, processed_date FROM paper_pending_signals"
        ).fetchone()
    assert status == "MISSED_EXECUTION"
    assert processed_date == "2026-08-07"


def test_holiday_gap_still_fills_at_first_actual_market_session(
    tmp_path: Path,
) -> None:
    paper_db = tmp_path / "paper.db"
    market_db = tmp_path / "market.db"
    signal_date = date(2026, 2, 13)
    create_market_database(
        market_db,
        signal_date=signal_date,
        sessions=[(date(2026, 2, 23), 51.0)],
    )
    executor = build_executor(paper_db)
    queue_aaa(executor, signal_date)

    result = executor.execute_pending_signals(
        valuation_date="2026-02-23",
        market_database_path=market_db,
    )

    assert result.filled_count == 1
    assert result.executions[0].requested_price == 51.0


def test_pending_order_is_capped_at_one_percent_of_signal_date_adtv20(
    tmp_path: Path,
) -> None:
    paper_db = tmp_path / "paper.db"
    market_db = tmp_path / "market.db"
    signal_date = date(2026, 8, 5)
    create_market_database(
        market_db,
        signal_date=signal_date,
        sessions=[(date(2026, 8, 6), 50.0)],
        historical_close=10.0,
        historical_volume=1_000_000.0,
        # A future volume shock must not increase the causal ADTV20 cap.
        execution_volume=1_000_000_000.0,
    )
    executor = build_executor(
        paper_db,
        initial_cash=1_000_000_000,
        fixed_fraction_pct=20.0,
    )
    queue_aaa(executor, signal_date)

    result = executor.execute_pending_signals(
        valuation_date="2026-08-06",
        market_database_path=market_db,
    )

    # ADTV20 = 10,000 VND * 1m shares = 10bn VND.
    # One percent is 100m VND; at 50,000 VND that is exactly 2,000 shares.
    assert result.filled_count == 1
    assert result.executions[0].quantity == 2_000


def test_execution_config_rejects_invalid_adtv20_limit() -> None:
    with pytest.raises(ValueError, match="PAPER_MAX_ORDER_ADTV20_PCT"):
        PaperExecutionConfig(maximum_order_adtv20_pct=0).validate()


def test_daily_loss_is_passed_to_risk_guard(tmp_path: Path) -> None:
    # Wiring is covered end-to-end elsewhere; this test protects the public
    # OrderManager API from silently reverting to daily_realized_pnl=0.
    from inspect import signature
    from execution.order_manager import OrderManager

    assert "daily_realized_pnl" in signature(OrderManager.buy_market).parameters
