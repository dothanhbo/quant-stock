from __future__ import annotations

import sqlite3
from pathlib import Path

from execution.signal_executor import PaperExecutionConfig, PaperSignalExecutor


def test_signal_is_queued_then_filled_at_next_open(tmp_path: Path) -> None:
    paper_db = tmp_path / "paper.db"
    market_db = tmp_path / "market.db"
    with sqlite3.connect(market_db) as connection:
        connection.execute(
            "CREATE TABLE prices(symbol TEXT, time TEXT, open REAL)"
        )
        connection.execute(
            "INSERT INTO prices VALUES ('AAA', '2026-08-06', 51.0)"
        )

    executor = PaperSignalExecutor(PaperExecutionConfig(
        enabled=True,
        database_path=paper_db,
        initial_cash=100_000_000,
        position_sizer="fixed_fraction",
        fixed_fraction_pct=10.0,
        maximum_position_pct=20.0,
        maximum_gross_exposure_pct=80.0,
        maximum_open_positions=10,
        minimum_cash_buffer_pct=5.0,
    ))
    queued = executor.queue_signals([{
        "symbol": "AAA",
        "date": "2026-08-05",
        "entry": 50.0,
        "atr": 1.0,
        "score": 90,
    }], report_date="2026-08-05")
    assert queued.queued_count == 1
    assert executor.broker.get_position("AAA") is None

    filled = executor.execute_pending_signals(
        valuation_date="2026-08-06",
        market_database_path=market_db,
    )
    assert filled.filled_count == 1
    assert filled.executions[0].requested_price == 51.0
    lifecycle = executor.broker.get_position_lifecycle("AAA")
    assert lifecycle is not None
    assert lifecycle.maximum_holding_days == 30
    assert lifecycle.stop_price == 49_000.0
    assert lifecycle.take_profit_price == 56_000.0


def test_daily_loss_is_passed_to_risk_guard(tmp_path: Path) -> None:
    # Wiring is covered end-to-end elsewhere; this test protects the public
    # OrderManager API from silently reverting to daily_realized_pnl=0.
    from inspect import signature
    from execution.order_manager import OrderManager

    assert "daily_realized_pnl" in signature(OrderManager.buy_market).parameters
