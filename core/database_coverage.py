from __future__ import annotations

from bisect import bisect_right
from datetime import date, datetime
from pathlib import Path
import sqlite3

from core.paths import resolve_market_database_path


def _normalize_as_of_date(as_of_date: str | date | datetime) -> str:
    if isinstance(as_of_date, datetime):
        return as_of_date.date().isoformat()
    if isinstance(as_of_date, date):
        return as_of_date.isoformat()
    try:
        return datetime.fromisoformat(str(as_of_date)).date().isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError("as_of_date phải là ngày hợp lệ.") from exc


def _readonly_connection(database_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(
        database_path.as_uri() + "?mode=ro",
        uri=True,
    )


def eligible_symbols_as_of(
    as_of_date: str | date | datetime,
    minimum_history_sessions: int = 50,
    maximum_staleness_sessions: int = 5,
    database_path: str | Path | None = None,
) -> list[str]:
    """Return the read-only database-coverage equity universe at a snapshot.

    This is not a historical VN100 reconstruction. It uses only observations
    stored in ``prices`` through the effective VNINDEX session.
    """
    if minimum_history_sessions < 0:
        raise ValueError("minimum_history_sessions không được âm.")
    if maximum_staleness_sessions < 0:
        raise ValueError("maximum_staleness_sessions không được âm.")

    requested_date = _normalize_as_of_date(as_of_date)
    resolved_path = resolve_market_database_path(database_path)

    with _readonly_connection(resolved_path) as connection:
        snapshot_row = connection.execute(
            """
            SELECT MAX(date(time))
            FROM prices
            WHERE UPPER(symbol) = 'VNINDEX'
              AND date(time) <= date(?)
            """,
            (requested_date,),
        ).fetchone()
        effective_snapshot = snapshot_row[0] if snapshot_row else None
        if effective_snapshot is None:
            return []

        market_sessions = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT DISTINCT date(time)
                FROM prices
                WHERE UPPER(symbol) = 'VNINDEX'
                  AND date(time) <= date(?)
                ORDER BY date(time)
                """,
                (effective_snapshot,),
            ).fetchall()
        ]
        observations = connection.execute(
            """
            SELECT UPPER(symbol), date(time)
            FROM prices
            WHERE symbol IS NOT NULL
              AND TRIM(symbol) <> ''
              AND UPPER(symbol) <> 'VNINDEX'
              AND date(time) <= date(?)
            GROUP BY UPPER(symbol), date(time)
            ORDER BY UPPER(symbol), date(time)
            """,
            (effective_snapshot,),
        ).fetchall()

    snapshot_rank = len(market_sessions) - 1
    dates_by_symbol: dict[str, list[str]] = {}
    for symbol, observed_date in observations:
        dates_by_symbol.setdefault(str(symbol), []).append(str(observed_date))

    eligible: list[str] = []
    for symbol, dates in dates_by_symbol.items():
        if len(dates) < minimum_history_sessions:
            continue

        latest_observation = dates[-1]
        latest_session_rank = (
            bisect_right(market_sessions, latest_observation) - 1
        )
        if latest_session_rank < 0:
            continue
        if snapshot_rank - latest_session_rank <= maximum_staleness_sessions:
            eligible.append(symbol)

    return sorted(eligible)
