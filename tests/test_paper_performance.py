from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from analysis.paper_performance import (
    calculate_paper_performance,
    load_daily_equity_curve,
)


def create_database(
    path: Path,
) -> None:
    with sqlite3.connect(
        path
    ) as connection:
        connection.executescript(
            """
            CREATE TABLE paper_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE paper_closed_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                entry_date TEXT NOT NULL,
                exit_date TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                gross_proceeds REAL NOT NULL,
                commission REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                return_pct REAL NOT NULL,
                holding_days INTEGER NOT NULL,
                exit_reason TEXT NOT NULL,
                order_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE paper_portfolio_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cash REAL NOT NULL,
                positions_value REAL NOT NULL,
                equity REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                gross_exposure_pct REAL NOT NULL,
                open_positions INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )

        connection.execute(
            """
            INSERT INTO paper_metadata(
                key,
                value
            )
            VALUES ('initial_cash', ?)
            """,
            (
                json.dumps(
                    100_000_000
                ),
            ),
        )

        connection.executemany(
            """
            INSERT INTO paper_closed_trades(
                symbol,
                entry_date,
                exit_date,
                quantity,
                entry_price,
                exit_price,
                gross_proceeds,
                commission,
                realized_pnl,
                return_pct,
                holding_days,
                exit_reason,
                order_id,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "AAA",
                    "2026-08-01",
                    "2026-08-05",
                    100,
                    10_000,
                    11_000,
                    1_100_000,
                    1_650,
                    100_000,
                    10.0,
                    4,
                    "TAKE_PROFIT",
                    "sell-1",
                    "2026-08-05T15:00:00+07:00",
                ),
                (
                    "BBB",
                    "2026-08-02",
                    "2026-08-06",
                    100,
                    20_000,
                    19_000,
                    1_900_000,
                    2_850,
                    -100_000,
                    -5.0,
                    4,
                    "STOP_LOSS",
                    "sell-2",
                    "2026-08-06T15:00:00+07:00",
                ),
                (
                    "CCC",
                    "2026-08-03",
                    "2026-08-07",
                    100,
                    30_000,
                    31_500,
                    3_150_000,
                    4_725,
                    150_000,
                    5.0,
                    4,
                    "TAKE_PROFIT",
                    "sell-3",
                    "2026-08-07T15:00:00+07:00",
                ),
            ],
        )

        connection.executemany(
            """
            INSERT INTO paper_portfolio_snapshots(
                cash,
                positions_value,
                equity,
                realized_pnl,
                unrealized_pnl,
                gross_exposure_pct,
                open_positions,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    100_000_000,
                    0,
                    100_000_000,
                    0,
                    0,
                    0,
                    0,
                    "2026-08-05T15:00:00+07:00",
                ),
                (
                    99_000_000,
                    2_000_000,
                    101_000_000,
                    100_000,
                    900_000,
                    1.98,
                    1,
                    "2026-08-06T15:00:00+07:00",
                ),
                (
                    100_250_000,
                    0,
                    100_250_000,
                    150_000,
                    0,
                    0,
                    0,
                    "2026-08-07T15:00:00+07:00",
                ),
            ],
        )


def test_core_trade_metrics(
    tmp_path: Path,
) -> None:
    database = (
        tmp_path
        / "paper.db"
    )
    create_database(
        database
    )

    report = calculate_paper_performance(
        database
    )

    assert report.total_trades == 3
    assert report.winning_trades == 2
    assert report.losing_trades == 1
    assert report.win_rate_pct == pytest.approx(
        66.6666667
    )
    assert report.net_realized_pnl == pytest.approx(
        150_000
    )
    assert report.gross_profit == pytest.approx(
        250_000
    )
    assert report.gross_loss == pytest.approx(
        100_000
    )
    assert report.profit_factor == pytest.approx(
        2.5
    )
    assert report.expectancy_amount == pytest.approx(
        50_000
    )
    assert report.expectancy_pct == pytest.approx(
        10 / 3
    )
    assert report.average_win_amount == pytest.approx(
        125_000
    )
    assert report.average_loss_amount == pytest.approx(
        -100_000
    )
    assert report.payoff_ratio == pytest.approx(
        1.25
    )


def test_equity_metrics_and_daily_deduplication(
    tmp_path: Path,
) -> None:
    database = (
        tmp_path
        / "paper.db"
    )
    create_database(
        database
    )

    with sqlite3.connect(
        database
    ) as connection:
        connection.execute(
            """
            INSERT INTO paper_portfolio_snapshots(
                cash,
                positions_value,
                equity,
                realized_pnl,
                unrealized_pnl,
                gross_exposure_pct,
                open_positions,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                98_000_000,
                1_500_000,
                99_500_000,
                0,
                -500_000,
                1.51,
                1,
                "2026-08-06T12:00:00+07:00",
            ),
        )

    curve = load_daily_equity_curve(
        database,
        initial_equity=(
            100_000_000
        ),
    )

    assert len(curve) == 3
    assert curve[
        "date"
    ].nunique() == 3

    report = calculate_paper_performance(
        database
    )

    assert report.current_equity == pytest.approx(
        100_250_000
    )
    assert report.total_return_pct == pytest.approx(
        0.25
    )
    assert report.max_drawdown_pct < 0
    assert report.snapshot_days == 3


def test_empty_database_returns_zero_metrics(
    tmp_path: Path,
) -> None:
    database = (
        tmp_path
        / "empty.db"
    )

    with sqlite3.connect(
        database
    ) as connection:
        connection.execute(
            """
            CREATE TABLE paper_metadata(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )

    report = calculate_paper_performance(
        database
    )

    assert report.current_equity == pytest.approx(
        100_000_000
    )
    assert report.total_trades == 0
    assert report.profit_factor == 0
    assert report.max_drawdown_pct == 0
