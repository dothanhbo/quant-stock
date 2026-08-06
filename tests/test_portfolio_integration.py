from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from execution.exit_engine import ExitEngine
from execution.lifecycle_manager import PaperLifecycleManager
from execution.signal_executor import (
    PaperExecutionConfig,
    PaperSignalExecutor,
)


def signal(
    symbol: str,
    *,
    signal_date: str,
    entry: float,
    stop: float,
    target: float,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "date": signal_date,
        "entry": entry,
        "stop_loss": stop,
        "take_profit": target,
        "score": 90,
        "atr": 1.0,
    }


def create_market_database(
    path: Path,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                time TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL
            )
            """
        )


def add_bar(
    path: Path,
    *,
    symbol: str,
    trading_date: str,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO prices(
                symbol,
                time,
                open,
                high,
                low,
                close,
                volume
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                trading_date,
                open_price,
                high_price,
                low_price,
                close_price,
                1_000_000,
            ),
        )


def build_executor(
    paper_database: Path,
) -> PaperSignalExecutor:
    return PaperSignalExecutor(
        PaperExecutionConfig(
            enabled=True,
            database_path=paper_database,
            initial_cash=100_000_000,
            position_sizer="fixed_fraction",
            fixed_fraction_pct=20.0,
            risk_per_trade_pct=1.0,
            atr_stop_multiplier=2.0,
            maximum_orders_per_scan=3,
            lot_size=100,
            commission_rate=0.0015,
            slippage_bps=5.0,
            maximum_position_pct=25.0,
            maximum_gross_exposure_pct=80.0,
            maximum_open_positions=2,
            maximum_daily_loss_pct=3.0,
            minimum_cash_buffer_pct=5.0,
        )
    )


def test_multi_day_portfolio_behavior(
    tmp_path: Path,
) -> None:
    paper_database = tmp_path / "paper.db"
    market_database = tmp_path / "market.db"
    create_market_database(market_database)

    executor = build_executor(paper_database)

    # Day 1: two positions are opened; the third is rejected by
    # maximum_open_positions.
    day_1 = executor.execute_signals(
        [
            signal(
                "AAA",
                signal_date="2026-08-05",
                entry=50.0,
                stop=47.0,
                target=56.0,
            ),
            signal(
                "BBB",
                signal_date="2026-08-05",
                entry=40.0,
                stop=37.0,
                target=46.0,
            ),
            signal(
                "CCC",
                signal_date="2026-08-05",
                entry=30.0,
                stop=27.0,
                target=36.0,
            ),
        ]
    )

    assert [item.status for item in day_1.executions] == [
        "FILLED",
        "FILLED",
        "REJECTED",
    ]
    assert day_1.open_positions == 2
    assert {position.symbol for position in executor.broker.get_positions()} == {
        "AAA",
        "BBB",
    }

    cash_after_day_1 = day_1.cash
    assert cash_after_day_1 < 100_000_000
    assert day_1.gross_exposure_pct <= 80.0

    # Day 2: repeated AAA signal is ignored. DDD is rejected because
    # the portfolio still has the maximum number of open positions.
    day_2 = executor.execute_signals(
        [
            signal(
                "AAA",
                signal_date="2026-08-06",
                entry=51.0,
                stop=48.0,
                target=57.0,
            ),
            signal(
                "DDD",
                signal_date="2026-08-06",
                entry=25.0,
                stop=22.0,
                target=31.0,
            ),
        ]
    )

    assert day_2.executions[0].status == "SKIPPED"
    assert "Đã có vị thế" in day_2.executions[0].reason
    assert day_2.executions[1].status == "REJECTED"
    assert day_2.open_positions == 2
    assert day_2.cash == pytest.approx(cash_after_day_1)

    # Day 3: AAA hits its stop. BBB remains open.
    add_bar(
        market_database,
        symbol="AAA",
        trading_date="2026-08-07",
        open_price=48.0,
        high_price=49.0,
        low_price=46.0,
        close_price=47.0,
    )
    add_bar(
        market_database,
        symbol="BBB",
        trading_date="2026-08-07",
        open_price=41.0,
        high_price=42.0,
        low_price=39.5,
        close_price=41.5,
    )

    lifecycle = PaperLifecycleManager(
        broker=executor.broker,
        order_manager=executor.order_manager,
        exit_engine=ExitEngine(),
        market_database_path=market_database,
    )
    day_3 = lifecycle.run(
        valuation_date=date(2026, 8, 7)
    )

    assert [item.symbol for item in day_3.exited] == ["AAA"]
    assert day_3.exited[0].reason == "STOP_LOSS"
    assert [item.symbol for item in day_3.held] == ["BBB"]
    assert day_3.open_positions == 1
    assert executor.broker.get_position("AAA") is None
    assert executor.broker.get_position("BBB") is not None

    cash_after_exit = day_3.cash
    assert cash_after_exit > cash_after_day_1

    # Day 4: AAA can be bought again after the previous lifecycle has
    # been fully closed and deleted.
    day_4 = executor.execute_signals(
        [
            signal(
                "AAA",
                signal_date="2026-08-08",
                entry=49.0,
                stop=46.0,
                target=55.0,
            )
        ]
    )

    assert day_4.executions[0].status == "FILLED"
    assert day_4.open_positions == 2
    assert executor.broker.get_position("AAA") is not None
    assert executor.broker.get_position("BBB") is not None

    snapshot = executor.broker.get_portfolio_snapshot()
    assert snapshot.cash == pytest.approx(day_4.cash)
    assert snapshot.equity == pytest.approx(day_4.equity)
    assert snapshot.open_positions == 2
    assert snapshot.gross_exposure_pct <= 80.0
