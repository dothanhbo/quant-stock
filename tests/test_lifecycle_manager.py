from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from execution.exit_engine import (
    ExitEngine,
)
from execution.lifecycle_manager import (
    PaperLifecycleManager,
)
from execution.lifecycle_models import (
    PositionLifecycleState,
)
from execution.order_manager import (
    OrderManager,
)
from execution.paper_broker import (
    PaperBroker,
)
from execution.risk_guard import (
    RiskGuard,
    RiskLimits,
)


def create_market_db(
    path: Path,
) -> None:
    with sqlite3.connect(
        path
    ) as connection:
        connection.execute(
            """
            CREATE TABLE prices (
                id INTEGER PRIMARY KEY,
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
            [
                (
                    "VNM",
                    "2026-08-05",
                    61.5,
                    63.0,
                    61.2,
                    62.5,
                    1_000_000,
                ),
                (
                    "VNM",
                    "2026-08-06",
                    61.5,
                    62.0,
                    60.5,
                    60.8,
                    1_100_000,
                ),
            ],
        )


def build_runtime(
    *,
    paper_db: Path,
    market_db: Path,
):
    broker = PaperBroker(
        initial_cash=100_000_000,
        commission_rate=0.0015,
        slippage_bps=5.0,
        database_path=paper_db,
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
    lifecycle = PaperLifecycleManager(
        broker=broker,
        order_manager=manager,
        exit_engine=ExitEngine(),
        market_database_path=market_db,
    )
    return broker, manager, lifecycle


def test_hold_then_sell_and_restore(
    tmp_path: Path,
) -> None:
    paper_db = tmp_path / "paper.db"
    market_db = tmp_path / "market.db"
    create_market_db(
        market_db
    )

    (
        broker,
        order_manager,
        lifecycle,
    ) = build_runtime(
        paper_db=paper_db,
        market_db=market_db,
    )

    fill = order_manager.buy_market(
        symbol="VNM",
        quantity=300,
        price=59_500,
    )
    assert fill is not None

    broker.save_position_lifecycle(
        PositionLifecycleState(
            symbol="VNM",
            entry_date=date(
                2026,
                8,
                1,
            ),
            entry_price=fill.price,
            initial_quantity=300,
            stop_price=58_000,
            take_profit_price=70_000,
            highest_price=fill.price,
            trailing_stop_price=61_000,
            trailing_atr_multiplier=2.0,
        )
    )

    first = lifecycle.run(
        valuation_date="2026-08-05"
    )

    assert len(first.held) == 1
    assert len(first.exited) == 0
    assert (
        broker.get_position("VNM")
        is not None
    )

    second = lifecycle.run(
        valuation_date="2026-08-06"
    )

    assert len(second.exited) == 1
    assert (
        second.exited[0].reason
        == "TRAILING_STOP"
    )
    assert (
        broker.get_position("VNM")
        is None
    )
    assert (
        broker.get_position_lifecycle(
            "VNM"
        )
        is None
    )

    closed = broker.get_closed_trades()
    assert len(closed) == 1
    assert closed[0].symbol == "VNM"
    assert closed[0].realized_pnl > 0

    restored = PaperBroker(
        initial_cash=100_000_000,
        commission_rate=0.0015,
        slippage_bps=5.0,
        database_path=paper_db,
        restore_state=True,
    )

    assert restored.get_position(
        "VNM"
    ) is None
    assert len(
        restored.get_closed_trades()
    ) == 1


def test_missing_state_does_not_sell(
    tmp_path: Path,
) -> None:
    paper_db = tmp_path / "paper.db"
    market_db = tmp_path / "market.db"
    create_market_db(
        market_db
    )

    (
        broker,
        order_manager,
        lifecycle,
    ) = build_runtime(
        paper_db=paper_db,
        market_db=market_db,
    )

    assert order_manager.buy_market(
        symbol="VNM",
        quantity=300,
        price=59_500,
    ) is not None

    result = lifecycle.run(
        valuation_date="2026-08-06"
    )

    assert result.missing_states == [
        "VNM"
    ]
    assert (
        broker.get_position("VNM")
        is not None
    )
