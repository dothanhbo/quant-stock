from __future__ import annotations

import sqlite3
from pathlib import Path

from execution.order_manager import (
    OrderManager,
)
from execution.paper_broker import (
    PaperBroker,
)
from execution.position_updater import (
    PositionUpdateEngine,
)
from execution.risk_guard import (
    RiskGuard,
    RiskLimits,
)


def _create_market_database(
    path: Path,
) -> None:
    with sqlite3.connect(
        path
    ) as connection:
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
                volume INTEGER,
                UNIQUE(symbol, time)
            )
            """
        )

        connection.executemany(
            """
            INSERT INTO prices (
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
            [
                (
                    "HPG",
                    "2026-08-04",
                    24.5,
                    25.2,
                    24.4,
                    25.0,
                    10_000_000,
                ),
                (
                    "HPG",
                    "2026-08-05",
                    25.2,
                    26.8,
                    25.1,
                    26.5,
                    11_000_000,
                ),
            ],
        )


def test_position_update_engine(
    tmp_path: Path,
) -> None:
    market_database = (
        tmp_path
        / "market.db"
    )
    paper_database = (
        tmp_path
        / "paper.db"
    )

    _create_market_database(
        market_database
    )

    broker = PaperBroker(
        initial_cash=100_000_000,
        commission_rate=0.0015,
        slippage_bps=5.0,
        database_path=paper_database,
        restore_state=True,
    )

    manager = OrderManager(
        broker=broker,
        risk_guard=RiskGuard(
            RiskLimits(
                maximum_position_pct=50.0,
                maximum_gross_exposure_pct=80.0,
                minimum_cash_buffer_pct=5.0,
            )
        ),
    )

    fill = manager.buy_market(
        symbol="HPG",
        quantity=400,
        price=25_000,
    )

    assert fill is not None

    engine = PositionUpdateEngine(
        broker=broker,
        market_database_path=(
            market_database
        ),
    )

    result = engine.update_open_positions(
        valuation_date="2026-08-05"
    )

    assert result.updated_count == 1
    assert result.missing_count == 0
    assert result.open_positions == 1

    position = broker.get_position(
        "HPG"
    )

    assert position is not None
    assert position.market_price == 26_500
    assert position.quantity == 400
    assert position.unrealized_pnl > 0

    restored_broker = PaperBroker(
        initial_cash=100_000_000,
        commission_rate=0.0015,
        slippage_bps=5.0,
        database_path=paper_database,
        restore_state=True,
    )

    restored_position = (
        restored_broker.get_position(
            "HPG"
        )
    )

    assert restored_position is not None
    assert (
        restored_position.market_price
        == 26_500
    )
    assert (
        restored_broker
        .get_portfolio_snapshot()
        .equity
        == result.equity
    )
