from __future__ import annotations

from datetime import date, timedelta
from hashlib import sha256
import math
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

import core.historical_breadth as historical_breadth
from core.database_coverage import CoverageUniverseIndex
from core.historical_breadth import build_historical_breadth_index
from strategy import market_state
from strategy.paper_v2_gate import classify_state


def _sessions(count: int) -> list[str]:
    return [(date(2020, 1, 1) + timedelta(days=index)).isoformat() for index in range(count)]


def _database(path: Path, rows: list[tuple[str, str, float]]) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE prices (symbol TEXT, time TEXT, close REAL)")
        connection.executemany("INSERT INTO prices VALUES (?, ?, ?)", rows)


def _coverage_index(sessions: list[str], members: dict[str, frozenset[str]]) -> CoverageUniverseIndex:
    stored = tuple(sessions)
    return CoverageUniverseIndex(
        start_date=stored[0], end_date=stored[-1],
        effective_start_date=stored[0], effective_end_date=stored[-1],
        minimum_history_sessions=50, maximum_staleness_sessions=5,
        session_dates=stored,
        candidate_symbols=tuple(sorted(set().union(*members.values()))),
        eligible_count_by_session={key: len(value) for key, value in members.items()},
        _members_by_session=members,
    )


def _rows(sessions: list[str], symbol: str, closes: list[float]) -> list[tuple[str, str, float]]:
    return [(symbol, session, close) for session, close in zip(sessions, closes)]


def test_future_rows_do_not_change_earlier_breadth_and_fiftieth_boundary(tmp_path: Path) -> None:
    sessions = _sessions(61)
    base = _rows(sessions[:60], "AAA", [100.0] * 49 + [101.0] * 11)
    first = tmp_path / "first.db"; second = tmp_path / "second.db"
    _database(first, base)
    _database(second, base + [("AAA", sessions[60], 10000.0)])
    one = build_historical_breadth_index(sessions[48], sessions[60], universe_mode="current_vn100", symbols=["AAA"], database_path=first)
    two = build_historical_breadth_index(sessions[48], sessions[60], universe_mode="current_vn100", symbols=["AAA"], database_path=second)
    assert one.snapshot_as_of(sessions[48]).valid is False
    assert one.snapshot_as_of(sessions[49]).breadth_ema50_pct == 100.0
    assert one.snapshot_as_of(sessions[59]) == two.snapshot_as_of(sessions[59])


def test_equality_missing_rows_and_ten_produced_row_change(tmp_path: Path) -> None:
    sessions = _sessions(62)
    # CONST equals its EMA and is never above. MISS lacks one exact produced date.
    rows = _rows(sessions, "CONST", [10.0] * 62) + _rows(sessions, "UP", [10.0] * 49 + list(range(11, 24)))
    rows += _rows([item for index, item in enumerate(sessions) if index != 55], "MISS", [10.0] * 49 + list(range(11, 23)))
    path = tmp_path / "breadth.db"; _database(path, rows)
    index = build_historical_breadth_index(sessions[49], sessions[61], universe_mode="current_vn100", symbols=["CONST", "UP", "MISS"], database_path=path)
    assert index.snapshot_as_of(sessions[49]).breadth_ema50_pct == pytest.approx(
        200.0 / 3.0
    )
    assert index.snapshot_as_of(sessions[55]).breadth_universe_count == 2
    # Produced rows skip no date here because other symbols exist; the 60th row
    # uses the breadth exactly ten produced rows earlier.
    assert index.snapshot_as_of(sessions[59]).breadth_ema50_change_10d == pytest.approx(
        index.snapshot_as_of(sessions[59]).breadth_ema50_pct - index.snapshot_as_of(sessions[49]).breadth_ema50_pct
    )


def test_change_uses_produced_rows_when_calendar_dates_are_skipped(tmp_path: Path) -> None:
    sparse_sessions = [
        (date(2020, 1, 1) + timedelta(days=2 * index)).isoformat()
        for index in range(62)
    ]
    path = tmp_path / "sparse.db"
    _database(path, _rows(sparse_sessions, "AAA", [10.0] * 49 + list(range(11, 24))))
    index = build_historical_breadth_index(
        sparse_sessions[49], sparse_sessions[61],
        universe_mode="current_vn100", symbols=["AAA"], database_path=path,
    )
    assert index.snapshot_as_of(sparse_sessions[60]).breadth_ema50_change_10d == pytest.approx(
        index.snapshot_as_of(sparse_sessions[60]).breadth_ema50_pct
        - index.snapshot_as_of(sparse_sessions[50]).breadth_ema50_pct
    )
    assert index.snapshot_as_of("2020-05-01") == index.snapshot_as_of(sparse_sessions[60])


def test_prestart_history_coverage_membership_and_lookup_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = _sessions(65)
    rows = _rows(sessions, "AAA", [10.0] * 49 + list(range(11, 27))) + _rows(sessions, "BBB", [10.0] * 49 + list(range(11, 27)))
    path = tmp_path / "coverage.db"; _database(path, rows)
    members = {session: frozenset({"AAA"}) for session in sessions}
    members[sessions[60]] = frozenset({"AAA", "BBB"})
    coverage = _coverage_index(sessions, members)
    monkeypatch.setattr(historical_breadth, "get_vn100_symbols", lambda: (_ for _ in ()).throw(AssertionError("must not load VN100")))
    index = build_historical_breadth_index(sessions[59], sessions[64], universe_mode="database_coverage", coverage_index=coverage, database_path=path)
    assert index.snapshot_as_of(sessions[59]).valid
    assert index.snapshot_as_of(sessions[59]).breadth_universe_count == 1
    assert index.snapshot_as_of(sessions[60]).breadth_universe_count == 2
    assert index.snapshot_as_of("2020-02-29") == index.snapshot_as_of(sessions[59])
    invalid = index.snapshot_as_of(sessions[48])
    assert not invalid.valid and math.isnan(invalid.breadth_ema50_pct)
    with pytest.raises(ValueError, match="after index end_date"):
        index.snapshot_as_of("2021-01-01")


def test_signal_rounding_nan_state_branches_and_production_formula_parity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = _sessions(61)
    rows = (
        _rows(sessions, "AAA", [10.0] * 49 + [10.123456] * 12)
        + _rows(sessions, "BBB", [11.0] * 61)
        + _rows(sessions, "CCC", [12.0] * 61)
        + _rows(sessions, "VNINDEX", [999.0] * 61)
    )
    path = tmp_path / "parity.db"; _database(path, rows)
    index = build_historical_breadth_index(sessions[49], sessions[60], universe_mode="current_vn100", symbols=["AAA", "BBB", "CCC", "VNINDEX"], database_path=path)
    fields = index.signal_fields_as_of(sessions[60])
    assert fields["breadth_ema50_pct"] == round(index.snapshot_as_of(sessions[60]).breadth_ema50_pct, 4)
    assert fields["breadth_ema50_pct"] == 33.3333
    assert math.isnan(build_historical_breadth_index(sessions[49], sessions[49], universe_mode="current_vn100", symbols=["AAA"], database_path=path).signal_fields_as_of(sessions[49])["breadth_ema50_change_10d"])

    def load_prices(as_of: str, symbols: tuple[str, ...]) -> pd.DataFrame:
        with sqlite3.connect(path) as connection:
            placeholders = ", ".join("?" for _ in symbols)
            return pd.read_sql_query(
                f"SELECT symbol, date(time) AS time, close FROM prices WHERE symbol IN ({placeholders}) AND date(time) <= ?",
                connection,
                params=(*symbols, as_of),
            )
    monkeypatch.setattr(market_state, "_load_prices", load_prices)
    breadth, change, universe = market_state._compute_breadth(sessions[60], ("AAA", "BBB", "CCC"))
    snapshot = index.snapshot_as_of(sessions[60])
    assert (snapshot.breadth_ema50_pct, snapshot.breadth_ema50_change_10d, snapshot.breadth_universe_count) == pytest.approx((breadth, change, universe))
    assert classify_state({"regime": "BEAR", **fields}) == "BEAR"
    assert classify_state({"regime": "BULL", "breadth_ema50_pct": 40, "breadth_ema50_change_10d": -1}) == "DIVERGENT_BULL"
    assert classify_state({"regime": "BULL", "breadth_ema50_pct": 70, "breadth_ema50_change_10d": 0}) == "HEALTHY_BULL"
    assert classify_state({"regime": "BULL", "breadth_ema50_pct": float("nan"), "breadth_ema50_change_10d": 0}) == "FRAGILE_BULL"
    assert classify_state({"regime": "SIDEWAY", "breadth_ema50_pct": 60, "breadth_ema50_change_10d": 1}) == "RECOVERY"
    assert classify_state({"regime": "SIDEWAY", "breadth_ema50_pct": 59, "breadth_ema50_change_10d": 1}) == "NEUTRAL"


def test_bounded_reads_and_no_database_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = _sessions(61)
    path = tmp_path / "readonly.db"; _database(path, _rows(sessions, "AAA", [10.0] * 61))
    before = sha256(path.read_bytes()).hexdigest()
    original = historical_breadth._readonly_connection
    calls = {"count": 0}
    def counted(database_path: Path):
        connection = original(database_path)
        class Proxy:
            def __enter__(self): return self
            def __exit__(self, *args): return connection.__exit__(*args)
            def execute(self, *args, **kwargs):
                calls["count"] += 1; return connection.execute(*args, **kwargs)
        return Proxy()
    monkeypatch.setattr(historical_breadth, "_readonly_connection", counted)
    index = build_historical_breadth_index(sessions[49], sessions[60], universe_mode="current_vn100", symbols=["AAA"], database_path=path)
    for session in index.session_dates:
        index.snapshot_as_of(session)
    assert calls["count"] == 1
    assert sha256(path.read_bytes()).hexdigest() == before
