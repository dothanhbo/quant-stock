from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from execution.models import (
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)
from execution.portfolio_state import PortfolioState


class PaperTradingStore:
    """SQLite persistence for paper-trading state and audit history."""

    def __init__(
        self,
        database_path: str | Path = "paper_trading.db",
    ) -> None:
        self.database_path = Path(
            database_path
        )
        self.database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self.initialize()

    @contextmanager
    def _connection(
        self,
    ) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.database_path
        )
        connection.row_factory = sqlite3.Row

        try:
            connection.execute(
                "PRAGMA foreign_keys = ON"
            )
            connection.execute(
                "PRAGMA journal_mode = WAL"
            )
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(
        self,
    ) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS paper_orders (
                    client_order_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    order_type TEXT NOT NULL,
                    limit_price REAL,
                    reference_price REAL,
                    status TEXT NOT NULL,
                    filled_quantity INTEGER NOT NULL,
                    average_fill_price REAL,
                    rejection_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS paper_fills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    gross_value REAL NOT NULL,
                    commission REAL NOT NULL,
                    slippage_cost REAL NOT NULL,
                    net_cash_flow REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (order_id)
                        REFERENCES paper_orders(client_order_id)
                );

                CREATE TABLE IF NOT EXISTS paper_positions (
                    symbol TEXT PRIMARY KEY,
                    quantity INTEGER NOT NULL,
                    average_price REAL NOT NULL,
                    market_price REAL NOT NULL,
                    realized_pnl REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS paper_portfolio_snapshots (
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

                CREATE INDEX IF NOT EXISTS
                    idx_paper_orders_status
                ON paper_orders(status);

                CREATE INDEX IF NOT EXISTS
                    idx_paper_fills_symbol
                ON paper_fills(symbol);

                CREATE INDEX IF NOT EXISTS
                    idx_paper_snapshots_created_at
                ON paper_portfolio_snapshots(created_at);
                """
            )

    def has_state(
        self,
    ) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT value
                FROM paper_metadata
                WHERE key = 'cash'
                """
            ).fetchone()

        return row is not None

    def save_initial_cash(
        self,
        initial_cash: float,
    ) -> None:
        self._upsert_metadata(
            "initial_cash",
            initial_cash,
        )

    def save_order(
        self,
        order: Order,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO paper_orders (
                    client_order_id,
                    symbol,
                    side,
                    quantity,
                    order_type,
                    limit_price,
                    reference_price,
                    status,
                    filled_quantity,
                    average_fill_price,
                    rejection_reason,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(client_order_id)
                DO UPDATE SET
                    symbol = excluded.symbol,
                    side = excluded.side,
                    quantity = excluded.quantity,
                    order_type = excluded.order_type,
                    limit_price = excluded.limit_price,
                    reference_price = excluded.reference_price,
                    status = excluded.status,
                    filled_quantity = excluded.filled_quantity,
                    average_fill_price = excluded.average_fill_price,
                    rejection_reason = excluded.rejection_reason,
                    updated_at = excluded.updated_at
                """,
                (
                    order.client_order_id,
                    order.symbol,
                    order.side.value,
                    order.quantity,
                    order.order_type.value,
                    order.limit_price,
                    order.reference_price,
                    order.status.value,
                    order.filled_quantity,
                    order.average_fill_price,
                    order.rejection_reason,
                    order.created_at.isoformat(),
                    order.updated_at.isoformat(),
                ),
            )

    def save_fill(
        self,
        fill: Fill,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO paper_fills (
                    order_id,
                    symbol,
                    side,
                    quantity,
                    price,
                    gross_value,
                    commission,
                    slippage_cost,
                    net_cash_flow,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill.order_id,
                    fill.symbol,
                    fill.side.value,
                    fill.quantity,
                    fill.price,
                    fill.gross_value,
                    fill.commission,
                    fill.slippage_cost,
                    fill.net_cash_flow,
                    fill.created_at.isoformat(),
                ),
            )

    def save_portfolio_state(
        self,
        portfolio: PortfolioState,
    ) -> None:
        snapshot = portfolio.snapshot()

        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO paper_metadata(key, value)
                VALUES ('cash', ?)
                ON CONFLICT(key)
                DO UPDATE SET value = excluded.value
                """,
                (
                    json.dumps(
                        portfolio.cash
                    ),
                ),
            )

            connection.execute(
                """
                INSERT INTO paper_metadata(key, value)
                VALUES ('realized_pnl', ?)
                ON CONFLICT(key)
                DO UPDATE SET value = excluded.value
                """,
                (
                    json.dumps(
                        portfolio.realized_pnl
                    ),
                ),
            )

            connection.execute(
                """
                DELETE FROM paper_positions
                """
            )

            connection.executemany(
                """
                INSERT INTO paper_positions (
                    symbol,
                    quantity,
                    average_price,
                    market_price,
                    realized_pnl
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        position.symbol,
                        position.quantity,
                        position.average_price,
                        position.market_price,
                        position.realized_pnl,
                    )
                    for position
                    in portfolio.get_positions()
                ],
            )

            connection.execute(
                """
                INSERT INTO paper_portfolio_snapshots (
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
                    snapshot.cash,
                    snapshot.positions_value,
                    snapshot.equity,
                    snapshot.realized_pnl,
                    snapshot.unrealized_pnl,
                    snapshot.gross_exposure_pct,
                    snapshot.open_positions,
                    snapshot.created_at.isoformat(),
                ),
            )

    def load_portfolio_state(
        self,
        *,
        fallback_initial_cash: float,
    ) -> PortfolioState:
        initial_cash = float(
            self._get_metadata(
                "initial_cash",
                fallback_initial_cash,
            )
        )

        cash = float(
            self._get_metadata(
                "cash",
                initial_cash,
            )
        )

        realized_pnl = float(
            self._get_metadata(
                "realized_pnl",
                0.0,
            )
        )

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    symbol,
                    quantity,
                    average_price,
                    market_price,
                    realized_pnl
                FROM paper_positions
                ORDER BY symbol
                """
            ).fetchall()

        positions = {
            str(row["symbol"]): Position(
                symbol=str(
                    row["symbol"]
                ),
                quantity=int(
                    row["quantity"]
                ),
                average_price=float(
                    row["average_price"]
                ),
                market_price=float(
                    row["market_price"]
                ),
                realized_pnl=float(
                    row["realized_pnl"]
                ),
            )
            for row in rows
        }

        return PortfolioState(
            initial_cash=initial_cash,
            cash=cash,
            positions=positions,
            realized_pnl=realized_pnl,
        )

    def load_orders(
        self,
    ) -> dict[str, Order]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM paper_orders
                ORDER BY created_at
                """
            ).fetchall()

        orders: dict[str, Order] = {}

        for row in rows:
            order = Order(
                symbol=str(
                    row["symbol"]
                ),
                side=OrderSide(
                    row["side"]
                ),
                quantity=int(
                    row["quantity"]
                ),
                order_type=OrderType(
                    row["order_type"]
                ),
                limit_price=row[
                    "limit_price"
                ],
                reference_price=row[
                    "reference_price"
                ],
                client_order_id=str(
                    row["client_order_id"]
                ),
                status=OrderStatus(
                    row["status"]
                ),
                filled_quantity=int(
                    row["filled_quantity"]
                ),
                average_fill_price=row[
                    "average_fill_price"
                ],
                rejection_reason=row[
                    "rejection_reason"
                ],
                created_at=datetime.fromisoformat(
                    row["created_at"]
                ),
                updated_at=datetime.fromisoformat(
                    row["updated_at"]
                ),
            )

            orders[
                order.client_order_id
            ] = order

        return orders

    def load_fills(
        self,
    ) -> list[Fill]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    order_id,
                    symbol,
                    side,
                    quantity,
                    price,
                    gross_value,
                    commission,
                    slippage_cost,
                    net_cash_flow,
                    created_at
                FROM paper_fills
                ORDER BY id
                """
            ).fetchall()

        return [
            Fill(
                order_id=str(
                    row["order_id"]
                ),
                symbol=str(
                    row["symbol"]
                ),
                side=OrderSide(
                    row["side"]
                ),
                quantity=int(
                    row["quantity"]
                ),
                price=float(
                    row["price"]
                ),
                gross_value=float(
                    row["gross_value"]
                ),
                commission=float(
                    row["commission"]
                ),
                slippage_cost=float(
                    row["slippage_cost"]
                ),
                net_cash_flow=float(
                    row["net_cash_flow"]
                ),
                created_at=datetime.fromisoformat(
                    row["created_at"]
                ),
            )
            for row in rows
        ]

    def reset(
        self,
    ) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                DELETE FROM paper_fills;
                DELETE FROM paper_orders;
                DELETE FROM paper_positions;
                DELETE FROM paper_portfolio_snapshots;
                DELETE FROM paper_metadata;
                """
            )

    def _upsert_metadata(
        self,
        key: str,
        value: object,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO paper_metadata(key, value)
                VALUES (?, ?)
                ON CONFLICT(key)
                DO UPDATE SET value = excluded.value
                """,
                (
                    key,
                    json.dumps(value),
                ),
            )

    def _get_metadata(
        self,
        key: str,
        default: object,
    ) -> object:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT value
                FROM paper_metadata
                WHERE key = ?
                """,
                (
                    key,
                ),
            ).fetchone()

        if row is None:
            return default

        return json.loads(
            row["value"]
        )
