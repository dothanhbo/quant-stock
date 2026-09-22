from __future__ import annotations

"""Read-only, causal historical breadth snapshots.

This module mirrors the production EMA50 breadth calculation without changing
``strategy.market_state``.  It deliberately does not classify market state.
"""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime
import math
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Literal, Mapping

from core.database_coverage import (
    CoverageUniverseIndex,
    _normalize_as_of_date,
    _readonly_connection,
    build_database_coverage_index,
)
from core.paths import resolve_market_database_path
from core.universe import get_vn100_symbols


UniverseMode = Literal["current_vn100", "database_coverage"]
_EMA_SPAN = 50
_MIN_HISTORY_SESSIONS = 50
_CHANGE_ROWS = 10


@dataclass(frozen=True, slots=True)
class HistoricalBreadthSnapshot:
    effective_date: str | None
    breadth_ema50_pct: float
    breadth_ema50_change_10d: float
    breadth_universe_count: int
    valid: bool


@dataclass(frozen=True, slots=True)
class HistoricalBreadthIndex:
    start_date: str
    end_date: str
    universe_mode: UniverseMode
    universe_label: str
    retrospective_current_membership: bool
    session_dates: tuple[str, ...]
    _snapshots: Mapping[str, HistoricalBreadthSnapshot]

    def snapshot_as_of(self, as_of_date: str | date | datetime) -> HistoricalBreadthSnapshot:
        requested_date = _normalize_as_of_date(as_of_date)
        if requested_date > self.end_date:
            raise ValueError(
                f"as_of_date {requested_date} is after index end_date {self.end_date}"
            )
        position = bisect_right(self.session_dates, requested_date) - 1
        if position < 0:
            return _invalid_snapshot()
        return self._snapshots[self.session_dates[position]]

    def signal_fields_as_of(self, as_of_date: str | date | datetime) -> dict[str, float]:
        snapshot = self.snapshot_as_of(as_of_date)
        return {
            "breadth_ema50_pct": _round_for_signal(snapshot.breadth_ema50_pct),
            "breadth_ema50_change_10d": _round_for_signal(
                snapshot.breadth_ema50_change_10d
            ),
        }


def _invalid_snapshot() -> HistoricalBreadthSnapshot:
    return HistoricalBreadthSnapshot(
        effective_date=None,
        breadth_ema50_pct=float("nan"),
        breadth_ema50_change_10d=float("nan"),
        breadth_universe_count=0,
        valid=False,
    )


def _round_for_signal(value: float) -> float:
    return round(float(value), 4) if math.isfinite(value) else float("nan")


def _normalize_symbols(symbols: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(symbol).strip().upper()
                for symbol in symbols
                if str(symbol).strip()
                and str(symbol).strip().upper() != "VNINDEX"
            }
        )
    )


def _expanded_coverage_index_if_needed(
    coverage_index: CoverageUniverseIndex,
    start_date: str,
    end_date: str,
    database_path: Path,
) -> CoverageUniverseIndex:
    """Return an index with enough pre-start membership for breadth warmup.

    Ten earlier stored VNINDEX sessions are the minimum possible warmup for the
    ten prior produced breadth rows.  If they are absent, construct one bounded
    expanded index rather than silently making an avoidable initial NaN.
    """
    if coverage_index.end_date < end_date:
        raise ValueError("coverage_index does not cover the requested end_date")
    pre_start_sessions = sum(
        date_text < start_date for date_text in coverage_index.session_dates
    )
    if coverage_index.start_date <= start_date and pre_start_sessions >= _CHANGE_ROWS:
        return coverage_index

    with _readonly_connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT MIN(date(time))
            FROM prices
            WHERE UPPER(TRIM(symbol)) = 'VNINDEX'
            """
        ).fetchone()
    earliest_session = row[0] if row else None
    if earliest_session is None:
        raise ValueError("cannot expand coverage_index without VNINDEX sessions")
    return build_database_coverage_index(
        earliest_session,
        end_date,
        minimum_history_sessions=coverage_index.minimum_history_sessions,
        maximum_staleness_sessions=coverage_index.maximum_staleness_sessions,
        database_path=database_path,
    )


def _load_close_rows(
    database_path: Path,
    end_date: str,
    symbols: tuple[str, ...],
) -> list[tuple[str, str, float]]:
    if not symbols:
        return []
    placeholders = ", ".join("?" for _ in symbols)
    query = f"""
        SELECT UPPER(TRIM(symbol)), date(time), close
        FROM prices
        WHERE symbol IS NOT NULL
          AND TRIM(symbol) <> ''
          AND UPPER(TRIM(symbol)) <> 'VNINDEX'
          AND UPPER(TRIM(symbol)) IN ({placeholders})
          AND date(time) <= date(?)
        ORDER BY UPPER(TRIM(symbol)), date(time), rowid
    """
    with _readonly_connection(database_path) as connection:
        rows = connection.execute(query, (*symbols, end_date)).fetchall()

    deduplicated: dict[tuple[str, str], float] = {}
    for symbol, observed_date, close in rows:
        try:
            numeric_close = float(close)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(numeric_close):
            continue
        deduplicated[(str(symbol), str(observed_date))] = numeric_close
    return [
        (symbol, observed_date, close)
        for (symbol, observed_date), close in sorted(deduplicated.items())
    ]


def build_historical_breadth_index(
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    *,
    universe_mode: UniverseMode,
    symbols: Iterable[str] | None = None,
    coverage_index: CoverageUniverseIndex | None = None,
    database_path: str | Path | None = None,
) -> HistoricalBreadthIndex:
    """Build causal EMA50 breadth snapshots without writing to the database."""
    normalized_start = _normalize_as_of_date(start_date)
    normalized_end = _normalize_as_of_date(end_date)
    if normalized_start > normalized_end:
        raise ValueError("start_date must be on or before end_date")
    if universe_mode not in {"current_vn100", "database_coverage"}:
        raise ValueError("universe_mode must be 'current_vn100' or 'database_coverage'")

    resolved_database_path = resolve_market_database_path(database_path)
    active_coverage_index: CoverageUniverseIndex | None = None
    if universe_mode == "current_vn100":
        if symbols is None:
            candidate_symbols = _normalize_symbols(get_vn100_symbols())
            universe_label = "retrospective_current_membership_biased"
            retrospective_current_membership = True
        else:
            candidate_symbols = _normalize_symbols(symbols)
            universe_label = "explicit_static_symbols"
            retrospective_current_membership = False
    else:
        if coverage_index is None:
            raise ValueError("database_coverage requires coverage_index")
        active_coverage_index = _expanded_coverage_index_if_needed(
            coverage_index,
            normalized_start,
            normalized_end,
            resolved_database_path,
        )
        candidate_symbols = active_coverage_index.candidate_symbols
        universe_label = "database_coverage_not_historical_vn100"
        retrospective_current_membership = False

    close_rows = _load_close_rows(
        resolved_database_path,
        normalized_end,
        candidate_symbols,
    )
    static_symbols = frozenset(candidate_symbols)
    # The recurrence and count are updated in time order per symbol.  Thus
    # bulk-loaded later rows cannot influence an earlier EMA or history count.
    rows_by_date: dict[str, list[tuple[str, float, int, float]]] = {}
    previous_ema: dict[str, float] = {}
    history_count: dict[str, int] = {}
    alpha = 2.0 / (_EMA_SPAN + 1.0)
    for symbol, observed_date, close in close_rows:
        previous = previous_ema.get(symbol)
        ema = close if previous is None else alpha * close + (1.0 - alpha) * previous
        previous_ema[symbol] = ema
        history_count[symbol] = history_count.get(symbol, 0) + 1
        rows_by_date.setdefault(observed_date, []).append(
            (symbol, close, history_count[symbol], ema)
        )

    prior_breadths: list[float] = []
    snapshots: dict[str, HistoricalBreadthSnapshot] = {}
    for observed_date in sorted(rows_by_date):
        if universe_mode == "current_vn100":
            applicable = static_symbols
        else:
            assert active_coverage_index is not None
            applicable = active_coverage_index.members_as_of(observed_date)
        above_values = [
            close > ema
            for symbol, close, history_n, ema in rows_by_date[observed_date]
            if symbol in applicable and history_n >= _MIN_HISTORY_SESSIONS
        ]
        if not above_values:
            continue
        breadth = sum(above_values) / len(above_values) * 100.0
        change = (
            breadth - prior_breadths[-_CHANGE_ROWS]
            if len(prior_breadths) >= _CHANGE_ROWS
            else float("nan")
        )
        prior_breadths.append(breadth)
        if normalized_start <= observed_date <= normalized_end:
            snapshots[observed_date] = HistoricalBreadthSnapshot(
                effective_date=observed_date,
                breadth_ema50_pct=float(breadth),
                breadth_ema50_change_10d=float(change),
                breadth_universe_count=len(above_values),
                valid=math.isfinite(change),
            )

    session_dates = tuple(sorted(snapshots))
    return HistoricalBreadthIndex(
        start_date=normalized_start,
        end_date=normalized_end,
        universe_mode=universe_mode,
        universe_label=universe_label,
        retrospective_current_membership=retrospective_current_membership,
        session_dates=session_dates,
        _snapshots=MappingProxyType(snapshots),
    )
