from __future__ import annotations

"""Read-only market-data safety gate for the canonical daily pipeline."""

from collections import Counter
from dataclasses import dataclass
from datetime import date
from enum import Enum
import math
from pathlib import Path
import sqlite3

from core.paths import resolve_market_database_path


REQUIRED_PRICE_COLUMNS = frozenset(
    {"symbol", "time", "open", "high", "low", "close", "volume"}
)


class MarketDataIntegrityState(str, Enum):
    PASS = "PASS"
    FAIL_CLOSED = "FAIL_CLOSED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class MarketDataIntegrityResult:
    state: MarketDataIntegrityState
    required_session: str | None
    required_symbols: tuple[str, ...]
    reasons: tuple[str, ...]
    database_path: Path

    @property
    def message(self) -> str:
        return "; ".join(self.reasons)


def _result(
    state: MarketDataIntegrityState,
    path: Path,
    symbols: tuple[str, ...],
    *reasons: str,
    session: str | None = None,
) -> MarketDataIntegrityResult:
    return MarketDataIntegrityResult(
        state=state,
        required_session=session,
        required_symbols=symbols,
        reasons=tuple(reasons),
        database_path=path,
    )


def _valid_ohlcv(row: sqlite3.Row) -> bool:
    try:
        open_price = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        volume = float(row["volume"])
    except (TypeError, ValueError):
        return False
    values = (open_price, high, low, close, volume)
    return (
        all(math.isfinite(value) for value in values)
        and min(open_price, high, low, close) > 0
        and volume >= 0
        and high >= max(open_price, low, close)
        and low <= min(open_price, high, close)
    )


def check_market_data_integrity(
    *,
    required_symbols: tuple[str, ...] | list[str],
    database_path: str | Path | None = None,
    as_of_date: date | str | None = None,
) -> MarketDataIntegrityResult:
    """Validate the current required universe without creating or writing a DB.

    The required universe is the same current VN100+VNINDEX set resolved by
    the daily command. Its modal equity latest date is the reference session.
    Every required symbol, including VNINDEX, must have exactly one structurally
    valid row on that session. A fully valid prior session is NOT_APPLICABLE
    rather than corrupted when it does not equal the operator's current date.
    """
    path = resolve_market_database_path(database_path)
    symbols = tuple(sorted({str(symbol).strip().upper() for symbol in required_symbols if str(symbol).strip()}))
    if "VNINDEX" not in symbols or len(symbols) < 2:
        return _result(
            MarketDataIntegrityState.FAIL_CLOSED,
            path,
            symbols,
            "required universe must contain VNINDEX and at least one equity",
        )
    if not path.is_file():
        return _result(
            MarketDataIntegrityState.FAIL_CLOSED,
            path,
            symbols,
            f"market database does not exist: {path}",
        )

    placeholders = ",".join("?" for _ in symbols)
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        with connection:
            columns = {
                str(row["name"]).lower()
                for row in connection.execute("PRAGMA table_info(prices)")
            }
            missing_columns = tuple(sorted(REQUIRED_PRICE_COLUMNS - columns))
            if missing_columns:
                return _result(
                    MarketDataIntegrityState.FAIL_CLOSED,
                    path,
                    symbols,
                    "prices table is missing required columns: " + ", ".join(missing_columns),
                )

            latest_rows = connection.execute(
                f"""
                SELECT UPPER(TRIM(symbol)) AS symbol, MAX(date(time)) AS latest_date
                FROM prices
                WHERE UPPER(TRIM(symbol)) IN ({placeholders})
                GROUP BY UPPER(TRIM(symbol))
                """,
                symbols,
            ).fetchall()
            latest_by_symbol = {str(row["symbol"]): row["latest_date"] for row in latest_rows}
            absent = tuple(symbol for symbol in symbols if not latest_by_symbol.get(symbol))
            if absent:
                return _result(
                    MarketDataIntegrityState.FAIL_CLOSED,
                    path,
                    symbols,
                    "required symbols have no stored observations: " + ", ".join(absent),
                )

            equity_dates = [latest_by_symbol[symbol] for symbol in symbols if symbol != "VNINDEX"]
            session = str(Counter(equity_dates).most_common(1)[0][0])
            stale = tuple(symbol for symbol in symbols if str(latest_by_symbol[symbol]) != session)
            if stale:
                details = ", ".join(f"{symbol}={latest_by_symbol[symbol]}" for symbol in stale)
                return _result(
                    MarketDataIntegrityState.FAIL_CLOSED,
                    path,
                    symbols,
                    f"required symbols are stale or ahead of reference session {session}: {details}",
                    session=session,
                )

            duplicate_rows = connection.execute(
                f"""
                SELECT UPPER(TRIM(symbol)) AS symbol, COUNT(*) AS row_count
                FROM prices
                WHERE UPPER(TRIM(symbol)) IN ({placeholders})
                  AND date(time) = ?
                GROUP BY UPPER(TRIM(symbol)), date(time)
                HAVING COUNT(*) != 1
                """,
                (*symbols, session),
            ).fetchall()
            if duplicate_rows:
                details = ", ".join(
                    f"{row['symbol']}={row['row_count']}" for row in duplicate_rows
                )
                return _result(
                    MarketDataIntegrityState.FAIL_CLOSED,
                    path,
                    symbols,
                    f"duplicate required symbol/date rows for {session}: {details}",
                    session=session,
                )

            rows = connection.execute(
                f"""
                SELECT UPPER(TRIM(symbol)) AS symbol, open, high, low, close, volume
                FROM prices
                WHERE UPPER(TRIM(symbol)) IN ({placeholders})
                  AND date(time) = ?
                ORDER BY UPPER(TRIM(symbol))
                """,
                (*symbols, session),
            ).fetchall()
    except sqlite3.Error as error:
        return _result(
            MarketDataIntegrityState.FAIL_CLOSED,
            path,
            symbols,
            f"cannot read market database safely: {error}",
        )
    finally:
        if "connection" in locals():
            connection.close()

    rows_by_symbol = {str(row["symbol"]): row for row in rows}
    missing_session = tuple(symbol for symbol in symbols if symbol not in rows_by_symbol)
    if missing_session:
        return _result(
            MarketDataIntegrityState.FAIL_CLOSED,
            path,
            symbols,
            f"required symbols are missing on reference session {session}: " + ", ".join(missing_session),
            session=session,
        )
    invalid = tuple(symbol for symbol in symbols if not _valid_ohlcv(rows_by_symbol[symbol]))
    if invalid:
        return _result(
            MarketDataIntegrityState.FAIL_CLOSED,
            path,
            symbols,
            f"required symbols have invalid OHLCV on {session}: " + ", ".join(invalid),
            session=session,
        )

    current_date = (
        as_of_date.isoformat()
        if isinstance(as_of_date, date)
        else str(as_of_date or date.today().isoformat())
    )
    if session != current_date:
        return _result(
            MarketDataIntegrityState.NOT_APPLICABLE,
            path,
            symbols,
            f"latest complete required market session is {session}; current date is {current_date}",
            session=session,
        )
    return _result(
        MarketDataIntegrityState.PASS,
        path,
        symbols,
        f"all {len(symbols)} required symbols are complete and valid for {session}",
        session=session,
    )


def require_market_data_integrity(
    *,
    required_symbols: tuple[str, ...] | list[str] | None = None,
    database_path: str | Path | None = None,
    as_of_date: date | str | None = None,
) -> MarketDataIntegrityResult:
    """Enforce the canonical integrity contract for direct operations.

    Unlike the daily pipeline, direct operational entrypoints have no stage
    object in which to represent a failed gate.  They therefore require a
    strict PASS and raise before creating or mutating operational state.  An
    omitted universe is resolved from the already-local latest VNINDEX
    session; direct lifecycle checks must not make a fresh VN100 provider
    request merely to validate local state.
    """
    if required_symbols is None:
        path = resolve_market_database_path(database_path)
        try:
            connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            with connection:
                rows = connection.execute(
                    """
                    SELECT DISTINCT UPPER(TRIM(symbol))
                    FROM prices
                    WHERE date(time)=(
                        SELECT MAX(date(time)) FROM prices
                        WHERE UPPER(TRIM(symbol))='VNINDEX'
                    )
                    ORDER BY UPPER(TRIM(symbol))
                    """
                ).fetchall()
            required_symbols = tuple(str(row[0]) for row in rows if row[0])
        except sqlite3.Error:
            required_symbols = ()
        finally:
            if "connection" in locals():
                connection.close()

    result = check_market_data_integrity(
        required_symbols=required_symbols,
        database_path=database_path,
        as_of_date=as_of_date,
    )
    if result.state is not MarketDataIntegrityState.PASS:
        detail = result.message or result.state.value
        raise RuntimeError(
            "Market-data integrity gate failed closed: " + detail
        )
    return result


__all__ = (
    "MarketDataIntegrityResult",
    "MarketDataIntegrityState",
    "check_market_data_integrity",
    "require_market_data_integrity",
)
