from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import sqlite3
from types import MappingProxyType

from core.paths import resolve_market_database_path


@dataclass(frozen=True, slots=True)
class CoverageUniverseIndex:
    """Immutable database-coverage membership snapshots for VNINDEX sessions."""

    start_date: str
    end_date: str
    effective_start_date: str | None
    effective_end_date: str | None
    minimum_history_sessions: int
    maximum_staleness_sessions: int
    session_dates: tuple[str, ...]
    candidate_symbols: tuple[str, ...]
    eligible_count_by_session: Mapping[str, int]
    _members_by_session: Mapping[str, frozenset[str]]

    def members_as_of(self, as_of_date: date | datetime | str) -> frozenset[str]:
        """Return the immutable membership at the preceding stored VNINDEX session."""
        requested_date = _normalize_as_of_date(as_of_date)
        if requested_date > self.end_date:
            raise ValueError(
                f"as_of_date {requested_date} is after index end_date {self.end_date}"
            )

        session_index = bisect_right(self.session_dates, requested_date) - 1
        if session_index < 0:
            return frozenset()
        return self._members_by_session[self.session_dates[session_index]]

    def is_eligible(self, symbol: str, as_of_date: date | datetime | str) -> bool:
        """Return whether a normalized symbol belongs to the snapshot membership."""
        normalized_symbol = str(symbol).strip().upper()
        return normalized_symbol in self.members_as_of(as_of_date)


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


def build_database_coverage_index(
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    minimum_history_sessions: int = 50,
    maximum_staleness_sessions: int = 5,
    database_path: str | Path | None = None,
) -> CoverageUniverseIndex:
    """Precompute point-in-time database-coverage memberships for a date range.

    The index represents database coverage only; it does not infer historical
    index constituents or contact any external data source.
    """
    if minimum_history_sessions < 0:
        raise ValueError("minimum_history_sessions must be non-negative")
    if maximum_staleness_sessions < 0:
        raise ValueError("maximum_staleness_sessions must be non-negative")

    normalized_start_date = _normalize_as_of_date(start_date)
    normalized_end_date = _normalize_as_of_date(end_date)
    if normalized_start_date > normalized_end_date:
        raise ValueError("start_date must be on or before end_date")

    resolved_database_path = resolve_market_database_path(database_path)
    with _readonly_connection(resolved_database_path) as connection:
        market_sessions = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT DISTINCT date(time)
                FROM prices
                WHERE UPPER(TRIM(symbol)) = 'VNINDEX'
                  AND date(time) <= date(?)
                ORDER BY date(time)
                """,
                (normalized_end_date,),
            )
        )
        observations = tuple(
            connection.execute(
                """
                SELECT UPPER(TRIM(symbol)), date(time)
                FROM prices
                WHERE symbol IS NOT NULL
                  AND TRIM(symbol) <> ''
                  AND UPPER(TRIM(symbol)) <> 'VNINDEX'
                  AND date(time) <= date(?)
                GROUP BY UPPER(TRIM(symbol)), date(time)
                ORDER BY date(time), UPPER(TRIM(symbol))
                """,
                (normalized_end_date,),
            )
        )

    stored_session_dates = tuple(
        session_date
        for session_date in market_sessions
        if session_date >= normalized_start_date
    )
    members_by_session: dict[str, frozenset[str]] = {}
    counts_by_session: dict[str, int] = {}
    observation_counts: dict[str, int] = {}
    latest_observation_rank: dict[str, int] = {}
    observation_index = 0

    for session_rank, session_date in enumerate(market_sessions):
        while (
            observation_index < len(observations)
            and observations[observation_index][1] <= session_date
        ):
            symbol, observation_date = observations[observation_index]
            observation_counts[symbol] = observation_counts.get(symbol, 0) + 1
            latest_observation_rank[symbol] = (
                bisect_right(market_sessions, observation_date) - 1
            )
            observation_index += 1

        if session_date < normalized_start_date:
            continue

        members = frozenset(
            symbol
            for symbol, count in observation_counts.items()
            if count >= minimum_history_sessions
            and latest_observation_rank[symbol] >= 0
            and session_rank - latest_observation_rank[symbol]
            <= maximum_staleness_sessions
        )
        members_by_session[session_date] = members
        counts_by_session[session_date] = len(members)

    candidate_symbols = tuple(
        sorted({symbol for members in members_by_session.values() for symbol in members})
    )
    return CoverageUniverseIndex(
        start_date=normalized_start_date,
        end_date=normalized_end_date,
        effective_start_date=stored_session_dates[0] if stored_session_dates else None,
        effective_end_date=stored_session_dates[-1] if stored_session_dates else None,
        minimum_history_sessions=minimum_history_sessions,
        maximum_staleness_sessions=maximum_staleness_sessions,
        session_dates=stored_session_dates,
        candidate_symbols=candidate_symbols,
        eligible_count_by_session=MappingProxyType(counts_by_session),
        _members_by_session=MappingProxyType(members_by_session),
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
