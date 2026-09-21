from __future__ import annotations

from datetime import date, timedelta
import hashlib
from pathlib import Path
import sqlite3

import pytest

from core.database_coverage import eligible_symbols_as_of


def _session_dates() -> list[date]:
    return [date(2024, 1, 1) + timedelta(days=index * 7) for index in range(60)]


def _create_database(path: Path) -> list[date]:
    sessions = _session_dates()
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE prices(
                symbol TEXT,
                time TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER
            )
            """
        )

        def insert(symbol: str, indexes: range) -> None:
            connection.executemany(
                "INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (symbol, sessions[index].isoformat(), 1, 1, 1, 1, 1)
                    for index in indexes
                ],
            )

        insert("VNINDEX", range(60))
        insert("AAA", range(50))
        insert("AAB", range(50))
        insert("ZZZ", range(50))
        insert("STALE5", range(5, 55))
        insert("STALE6", range(4, 54))
        insert("FUT", range(10, 60))

    return sessions


@pytest.fixture
def coverage_database(tmp_path: Path) -> tuple[Path, list[date]]:
    path = tmp_path / "market.db"
    return path, _create_database(path)


def test_future_entrant_is_excluded_before_first_observation(
    coverage_database: tuple[Path, list[date]],
) -> None:
    path, sessions = coverage_database

    eligible = eligible_symbols_as_of(
        sessions[9],
        minimum_history_sessions=0,
        database_path=path,
    )

    assert "AAA" in eligible
    assert "FUT" not in eligible


def test_symbol_becomes_eligible_after_fifty_observed_sessions(
    coverage_database: tuple[Path, list[date]],
) -> None:
    path, sessions = coverage_database

    before = eligible_symbols_as_of(sessions[48], database_path=path)
    after = eligible_symbols_as_of(sessions[49], database_path=path)

    assert "AAA" not in before
    assert "AAA" in after


def test_staleness_uses_vnindex_session_order(
    coverage_database: tuple[Path, list[date]],
) -> None:
    path, sessions = coverage_database

    eligible = eligible_symbols_as_of(sessions[59], database_path=path)

    assert "STALE5" in eligible
    assert "STALE6" not in eligible
    assert (sessions[59] - sessions[54]).days == 35


def test_staleness_boundary_allows_five_and_rejects_six_sessions(
    coverage_database: tuple[Path, list[date]],
) -> None:
    path, sessions = coverage_database

    eligible = eligible_symbols_as_of(sessions[59], database_path=path)

    assert "STALE5" in eligible
    assert "STALE6" not in eligible


def test_vnindex_is_excluded_and_results_are_sorted(
    coverage_database: tuple[Path, list[date]],
) -> None:
    path, sessions = coverage_database

    eligible = eligible_symbols_as_of(sessions[49], database_path=path)

    assert "VNINDEX" not in eligible
    assert eligible == ["AAA", "AAB", "ZZZ"]


def test_between_session_date_uses_preceding_vnindex_session(
    coverage_database: tuple[Path, list[date]],
) -> None:
    path, sessions = coverage_database

    assert eligible_symbols_as_of(
        sessions[49] + timedelta(days=3),
        database_path=path,
    ) == ["AAA", "AAB", "ZZZ"]


def test_date_before_all_market_sessions_returns_empty(
    coverage_database: tuple[Path, list[date]],
) -> None:
    path, sessions = coverage_database

    assert eligible_symbols_as_of(
        sessions[0] - timedelta(days=1),
        database_path=path,
    ) == []


@pytest.mark.parametrize(
    ("minimum_history_sessions", "maximum_staleness_sessions"),
    [(-1, 5), (50, -1)],
)
def test_negative_thresholds_are_rejected(
    coverage_database: tuple[Path, list[date]],
    minimum_history_sessions: int,
    maximum_staleness_sessions: int,
) -> None:
    path, sessions = coverage_database

    with pytest.raises(ValueError):
        eligible_symbols_as_of(
            sessions[0],
            minimum_history_sessions=minimum_history_sessions,
            maximum_staleness_sessions=maximum_staleness_sessions,
            database_path=path,
        )


def test_readonly_query_does_not_modify_fixture_database(
    coverage_database: tuple[Path, list[date]],
) -> None:
    path, sessions = coverage_database
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    eligible_symbols_as_of(sessions[59], database_path=path)

    after = hashlib.sha256(path.read_bytes()).hexdigest()
    assert after == before
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM prices"
        ).fetchone()[0] == 360
