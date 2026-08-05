from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from execution.paper_broker import PaperBroker


@dataclass(frozen=True, slots=True)
class PositionPriceUpdate:
    symbol: str
    valuation_date: str
    previous_price: float
    market_price: float
    quantity: int
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float


@dataclass(slots=True)
class PositionUpdateResult:
    valuation_date: str
    updated: list[PositionPriceUpdate] = field(
        default_factory=list
    )
    missing_symbols: list[str] = field(
        default_factory=list
    )
    cash: float = 0.0
    positions_value: float = 0.0
    equity: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    gross_exposure_pct: float = 0.0
    open_positions: int = 0

    @property
    def updated_count(
        self,
    ) -> int:
        return len(
            self.updated
        )

    @property
    def missing_count(
        self,
    ) -> int:
        return len(
            self.missing_symbols
        )


class PositionUpdateEngine:
    """
    Mark all open paper positions to the latest closing price.

    Market data is stored in thousand VND, while PaperBroker uses
    actual VND. Conversion happens only at this boundary.
    """

    def __init__(
        self,
        *,
        broker: PaperBroker,
        market_database_path: str | Path = (
            "data/market.db"
        ),
        price_scale: float = 1000.0,
    ) -> None:
        self.broker = broker
        self.market_database_path = Path(
            market_database_path
        )
        self.price_scale = price_scale

        if self.price_scale <= 0:
            raise ValueError(
                "price_scale phải lớn hơn 0."
            )

    def update_open_positions(
        self,
        *,
        valuation_date: str | None = None,
    ) -> PositionUpdateResult:
        positions = (
            self.broker.get_positions()
        )

        if valuation_date is None:
            valuation_date = (
                self.get_latest_market_date()
            )

        if valuation_date is None:
            raise RuntimeError(
                "Không xác định được ngày dữ liệu "
                "thị trường mới nhất."
            )

        if not positions:
            snapshot = (
                self.broker
                .get_portfolio_snapshot()
            )

            return PositionUpdateResult(
                valuation_date=valuation_date,
                cash=snapshot.cash,
                positions_value=(
                    snapshot.positions_value
                ),
                equity=snapshot.equity,
                realized_pnl=(
                    snapshot.realized_pnl
                ),
                unrealized_pnl=(
                    snapshot.unrealized_pnl
                ),
                gross_exposure_pct=(
                    snapshot.gross_exposure_pct
                ),
                open_positions=(
                    snapshot.open_positions
                ),
            )

        symbols = [
            position.symbol
            for position in positions
        ]

        closing_prices = (
            self._load_closing_prices(
                symbols=symbols,
                valuation_date=valuation_date,
            )
        )

        updates: list[
            PositionPriceUpdate
        ] = []
        missing_symbols: list[str] = []

        for position in positions:
            close_price_display = (
                closing_prices.get(
                    position.symbol
                )
            )

            if close_price_display is None:
                missing_symbols.append(
                    position.symbol
                )
                continue

            market_price = (
                close_price_display
                * self.price_scale
            )
            previous_price = (
                position.market_price
            )

            self.broker.update_market_price(
                position.symbol,
                market_price,
                persist_snapshot=False,
            )

            refreshed_position = (
                self.broker.get_position(
                    position.symbol
                )
            )

            if refreshed_position is None:
                raise RuntimeError(
                    "Position biến mất trong lúc "
                    f"cập nhật: {position.symbol}"
                )

            updates.append(
                PositionPriceUpdate(
                    symbol=position.symbol,
                    valuation_date=valuation_date,
                    previous_price=(
                        previous_price
                    ),
                    market_price=market_price,
                    quantity=(
                        refreshed_position.quantity
                    ),
                    market_value=(
                        refreshed_position.market_value
                    ),
                    unrealized_pnl=(
                        refreshed_position
                        .unrealized_pnl
                    ),
                    unrealized_pnl_pct=(
                        refreshed_position
                        .unrealized_pnl_pct
                    ),
                )
            )

        # Persist all updated prices and only one portfolio snapshot.
        self.broker.persist_portfolio_state()

        snapshot = (
            self.broker
            .get_portfolio_snapshot()
        )

        return PositionUpdateResult(
            valuation_date=valuation_date,
            updated=updates,
            missing_symbols=missing_symbols,
            cash=snapshot.cash,
            positions_value=(
                snapshot.positions_value
            ),
            equity=snapshot.equity,
            realized_pnl=(
                snapshot.realized_pnl
            ),
            unrealized_pnl=(
                snapshot.unrealized_pnl
            ),
            gross_exposure_pct=(
                snapshot.gross_exposure_pct
            ),
            open_positions=(
                snapshot.open_positions
            ),
        )

    def get_latest_market_date(
        self,
    ) -> str | None:
        self._validate_market_database()

        with sqlite3.connect(
            self.market_database_path
        ) as connection:
            row = connection.execute(
                """
                SELECT MAX(
                    substr(time, 1, 10)
                )
                FROM prices
                """
            ).fetchone()

        if (
            row is None
            or row[0] is None
        ):
            return None

        return str(
            row[0]
        )

    def _load_closing_prices(
        self,
        *,
        symbols: list[str],
        valuation_date: str,
    ) -> dict[str, float]:
        self._validate_market_database()

        if not symbols:
            return {}

        placeholders = ", ".join(
            "?"
            for _ in symbols
        )

        query = f"""
            SELECT
                symbol,
                close
            FROM prices
            WHERE symbol IN ({placeholders})
              AND substr(time, 1, 10) = ?
              AND close IS NOT NULL
        """

        parameters = [
            *symbols,
            valuation_date,
        ]

        with sqlite3.connect(
            self.market_database_path
        ) as connection:
            rows = connection.execute(
                query,
                parameters,
            ).fetchall()

        result: dict[str, float] = {}

        for symbol, close in rows:
            try:
                close_value = float(
                    close
                )
            except (TypeError, ValueError):
                continue

            if close_value <= 0:
                continue

            result[
                str(symbol).strip().upper()
            ] = close_value

        return result

    def _validate_market_database(
        self,
    ) -> None:
        if not self.market_database_path.exists():
            raise FileNotFoundError(
                "Không tìm thấy market database: "
                f"{self.market_database_path}"
            )
