from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
import sqlite3

import pytest

import core.database_coverage as database_coverage
from core.database_coverage import build_database_coverage_index


def _create_coverage_database(path: Path) -> list[str]:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE prices (symbol TEXT, time TEXT)")

    first_session = date(2018, 1, 5)
    sessions = [
        (first_session + timedelta(days=7 * index)).isoformat()
        for index in range(60)
    ]
    rows: list[tuple[str, str]] = []

    def add(symbol: str, session_indexes: range) -> None:
        rows.extend((symbol, sessions[index]) for index in session_indexes)

    add("VNINDEX", range(60))
    add("BASE", range(60))
    add("EARLY", range(50))
    add("AAB", range(50))
    add("ZZZ", range(50))
    add("FUT", range(10, 60))
    add("STALE5", range(5, 55))
    add("STALE6", range(4, 54))
    connection.executemany("INSERT INTO prices (symbol, time) VALUES (?, ?)", rows)
    connection.commit()
    connection.close()
    return sessions


@pytest.fixture
def coverage_database(tmp_path: Path) -> tuple[Path, list[str]]:
    path = tmp_path / "coverage.db"
    return path, _create_coverage_database(path)


def _build_index(coverage_database: tuple[Path, list[str]]):
    path, sessions = coverage_database
    return build_database_coverage_index(
        sessions[49],
        sessions[59],
        database_path=path,
    )


def _price_row_count(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
    finally:
        connection.close()


def test_index_bulk_load_does_not_apply_future_observations_early(
    coverage_database: tuple[Path, list[str]],
) -> None:
    index = _build_index(coverage_database)
    _, sessions = coverage_database

    assert index.start_date == sessions[49]
    assert index.end_date == sessions[59]
    assert index.effective_start_date == sessions[49]
    assert index.effective_end_date == sessions[59]
    assert index.minimum_history_sessions == 50
    assert index.maximum_staleness_sessions == 5
    assert "BASE" in index.members_as_of(sessions[49])
    assert "FUT" not in index.members_as_of(sessions[49])
    assert "FUT" in index.members_as_of(sessions[59])


def test_index_future_entrant_becomes_eligible_at_fiftieth_observation(
    coverage_database: tuple[Path, list[str]],
) -> None:
    index = _build_index(coverage_database)
    _, sessions = coverage_database

    assert not index.is_eligible("FUT", sessions[58])
    assert index.is_eligible("fut", sessions[59])


def test_index_uses_vnindex_session_staleness(
    coverage_database: tuple[Path, list[str]],
) -> None:
    index = _build_index(coverage_database)
    _, sessions = coverage_database

    members = index.members_as_of(sessions[59])
    assert "STALE5" in members
    assert "STALE6" not in members


def test_index_resolves_between_sessions_and_before_range(
    coverage_database: tuple[Path, list[str]],
) -> None:
    index = _build_index(coverage_database)
    _, sessions = coverage_database

    between_sessions = (
        date.fromisoformat(sessions[50])
        - timedelta(days=3)
    ).isoformat()
    assert index.members_as_of(between_sessions) == index.members_as_of(sessions[49])
    assert index.members_as_of(sessions[48]) == frozenset()
    assert not index.is_eligible("BASE", sessions[48])


def test_index_rejects_queries_after_configured_end_date(
    coverage_database: tuple[Path, list[str]],
) -> None:
    index = _build_index(coverage_database)
    _, sessions = coverage_database

    after_end = (
        date.fromisoformat(sessions[59]) + timedelta(days=1)
    ).isoformat()
    with pytest.raises(ValueError, match="after index end_date"):
        index.members_as_of(after_end)


def test_index_candidate_symbols_are_sorted_union_of_range_members(
    coverage_database: tuple[Path, list[str]],
) -> None:
    index = _build_index(coverage_database)

    assert index.candidate_symbols == (
        "AAB",
        "BASE",
        "EARLY",
        "FUT",
        "STALE5",
        "STALE6",
        "ZZZ",
    )
    assert "VNINDEX" not in index.candidate_symbols


def test_index_membership_and_count_mapping_are_immutable(
    coverage_database: tuple[Path, list[str]],
) -> None:
    index = _build_index(coverage_database)

    members = index.members_as_of(index.session_dates[0])
    with pytest.raises(AttributeError):
        members.add("MUTABLE")
    with pytest.raises(TypeError):
        index.eligible_count_by_session[index.session_dates[0]] = 0  # type: ignore[index]


def test_index_counts_match_each_stored_membership(
    coverage_database: tuple[Path, list[str]],
) -> None:
    index = _build_index(coverage_database)

    assert tuple(index.eligible_count_by_session) == index.session_dates
    assert all(
        index.eligible_count_by_session[session_date]
        == len(index.members_as_of(session_date))
        for session_date in index.session_dates
    )


@pytest.mark.parametrize(
    ("start_date", "end_date", "minimum_history_sessions", "maximum_staleness_sessions"),
    [
        ("2018-01-02", "2018-01-01", 50, 5),
        ("2018-01-01", "2018-01-02", -1, 5),
        ("2018-01-01", "2018-01-02", 50, -1),
    ],
)
def test_index_rejects_invalid_ranges_and_thresholds(
    coverage_database: tuple[Path, list[str]],
    start_date: str,
    end_date: str,
    minimum_history_sessions: int,
    maximum_staleness_sessions: int,
) -> None:
    path, _ = coverage_database

    with pytest.raises(ValueError):
        build_database_coverage_index(
            start_date,
            end_date,
            minimum_history_sessions=minimum_history_sessions,
            maximum_staleness_sessions=maximum_staleness_sessions,
            database_path=path,
        )


def test_index_uses_two_reads_independent_of_member_lookups(
    coverage_database: tuple[Path, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, sessions = coverage_database
    original_connection = database_coverage._readonly_connection
    captured: dict[str, _CountingConnection] = {}

    def counting_connection(database_path: Path) -> _CountingConnection:
        connection = _CountingConnection(original_connection(database_path))
        captured["connection"] = connection
        return connection

    monkeypatch.setattr(database_coverage, "_readonly_connection", counting_connection)
    index = build_database_coverage_index(sessions[49], sessions[59], database_path=path)
    assert captured["connection"].execute_count == 2

    for session_date in index.session_dates:
        index.members_as_of(session_date)
        index.is_eligible("BASE", session_date)
    assert captured["connection"].execute_count == 2


def test_index_builder_does_not_modify_database(
    coverage_database: tuple[Path, list[str]],
) -> None:
    path, sessions = coverage_database
    before_hash = sha256(path.read_bytes()).hexdigest()
    before_rows = _price_row_count(path)

    build_database_coverage_index(sessions[49], sessions[59], database_path=path)

    after_hash = sha256(path.read_bytes()).hexdigest()
    after_rows = _price_row_count(path)
    assert after_hash == before_hash
    assert after_rows == before_rows


class _CountingConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self.execute_count = 0

    def __enter__(self) -> _CountingConnection:
        self._connection.__enter__()
        return self

    def __exit__(self, *args: object) -> None:
        self._connection.__exit__(*args)

    def execute(self, *args: object, **kwargs: object) -> sqlite3.Cursor:
        self.execute_count += 1
        return self._connection.execute(*args, **kwargs)
