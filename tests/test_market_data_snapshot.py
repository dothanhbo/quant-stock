from __future__ import annotations

import os
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pandas as pd
import pytest

from backtesting.engine import load_price_data
import quantlab.catalog.market_data_snapshot as snapshot_module
from quantlab.catalog.market_data_snapshot import build_market_data_snapshot


def _database(path: Path, rows: list[tuple[object, ...]]) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE prices (symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)"
        )
        connection.executemany("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


@pytest.fixture
def market_db(tmp_path: Path) -> Path:
    path = tmp_path / "market.db"
    _database(path, [
        ("aaa", "2020-01-01", 10, 12, 9, 11, 100),
        ("AAA", "2020-01-01", 20, 22, 19, 21, 200),  # existing keep-last duplicate
        ("AAA", "2020-01-02", 21, 23, 20, 22, None),
        ("bbb", "2020-01-01", 30, 32, 29, 31, 300),
        ("VNINDEX", "2020-01-01", 100, 102, 99, 101, 1_000),
        ("CCC", "bad-date", 1, 2, 0, 1, 5),
        ("CCC", "2020-01-01", 1, 2, 0, None, 5),
    ])
    return path


def test_bulk_loader_matches_authoritative_normalization_and_bounds(market_db: Path) -> None:
    snapshot = build_market_data_snapshot(market_db)
    bundle = snapshot.load_ohlcv([" aaa ", "BBB", "vnindex", "MISSING"], start_date="2020-01-01", through_date="2020-01-01")
    assert bundle.requested_symbols == ("AAA", "BBB", "MISSING", "VNINDEX")
    assert bundle.available_symbols == ("AAA", "BBB", "VNINDEX")
    assert bundle.missing_symbols == ("MISSING",)
    assert bundle.row_count == 3
    existing = load_price_data("AAA", db_path=str(market_db)).assign(symbol=lambda x: x.symbol.str.strip().str.upper())
    actual = bundle.frame_for("aaa")
    pd.testing.assert_frame_equal(actual.reset_index(drop=True), existing.iloc[:1].reset_index(drop=True))
    assert float(actual.iloc[0]["close"]) == 21.0
    assert pd.api.types.is_datetime64_any_dtype(actual["time"])
    assert pd.isna(snapshot.load_ohlcv(["AAA"], through_date="2020-01-02").frame_for("AAA").iloc[1]["volume"])
    assert snapshot.load_ohlcv(["VNINDEX"]).available_symbols == ("VNINDEX",)


def test_snapshot_identity_is_logical_and_deterministic(market_db: Path) -> None:
    first = build_market_data_snapshot(market_db)
    os.utime(market_db, None)
    second = build_market_data_snapshot(market_db)
    assert first.logical_content_fingerprint == second.logical_content_fingerprint
    assert first.snapshot_id == second.snapshot_id
    assert first.symbols == ("AAA", "BBB", "VNINDEX")
    assert first.row_count == 4
    with sqlite3.connect(market_db) as connection:
        connection.execute("UPDATE prices SET close = 999 WHERE symbol = 'bbb'")
    changed = build_market_data_snapshot(market_db)
    assert changed.logical_content_fingerprint != first.logical_content_fingerprint
    assert changed.snapshot_id != first.snapshot_id


def test_streamed_identity_matches_reference_canonical_serializer_and_batch_sizes(market_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with snapshot_module._readonly_connection(market_db) as connection:
        metadata = snapshot_module._schema_metadata(connection)
        raw_rows = connection.execute(
            "SELECT symbol, time, open, high, low, close, volume, rowid FROM prices "
            "ORDER BY UPPER(TRIM(symbol)), time ASC, rowid ASC"
        ).fetchall()
    # Independent copy of the Phase-1 materialized reference normalization.
    reference_frame = pd.DataFrame(raw_rows, columns=[*snapshot_module._OHLCV_COLUMNS, "_rowid"])
    reference_frame["symbol"] = reference_frame["symbol"].map(
        lambda value: str(value).strip().upper() if value is not None else ""
    )
    reference_frame["time"] = pd.to_datetime(reference_frame["time"], errors="coerce")
    for column in snapshot_module._OHLCV_COLUMNS[2:]:
        reference_frame[column] = pd.to_numeric(reference_frame[column], errors="coerce")
    reference_frame = (
        reference_frame.loc[reference_frame["symbol"] != ""]
        .dropna(subset=["time", "open", "high", "low", "close"])
        .sort_values(["symbol", "time", "_rowid"], kind="stable")
        .drop_duplicates(subset=["symbol", "time"], keep="last")
        .sort_values(["symbol", "time"], kind="stable")
        .loc[:, snapshot_module._OHLCV_COLUMNS]
        .reset_index(drop=True)
    )
    schema_version = snapshot_module._schema_version(metadata)
    reference_fingerprint = sha256(snapshot_module._canonical_json({
        "schema_version": schema_version,
        "rows": snapshot_module._logical_rows(reference_frame),
    })).hexdigest()
    first = build_market_data_snapshot(market_db)
    monkeypatch.setattr(snapshot_module, "_STREAM_BATCH_SIZE", 1)
    second = build_market_data_snapshot(market_db)
    assert first.logical_content_fingerprint == reference_fingerprint == second.logical_content_fingerprint
    assert first.snapshot_id == second.snapshot_id


def test_insertion_order_of_nonconflicting_rows_does_not_change_identity(tmp_path: Path) -> None:
    rows = [
        ("BBB", "2020-01-02", 2, 3, 1, 2.5, 10),
        ("AAA", "2020-01-01", 1, 2, 0.5, 1.5, 5),
    ]
    left, right = tmp_path / "left.db", tmp_path / "right.db"
    _database(left, rows)
    _database(right, list(reversed(rows)))
    assert build_market_data_snapshot(left).logical_content_fingerprint == build_market_data_snapshot(right).logical_content_fingerprint


def test_schema_and_row_changes_change_snapshot_identity(market_db: Path) -> None:
    original = build_market_data_snapshot(market_db)
    with sqlite3.connect(market_db) as connection:
        connection.execute("ALTER TABLE prices ADD COLUMN vendor TEXT")
    schema_changed = build_market_data_snapshot(market_db)
    assert schema_changed.schema_version != original.schema_version
    assert schema_changed.snapshot_id != original.snapshot_id
    with sqlite3.connect(market_db) as connection:
        connection.execute("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("DDD", "2020-01-03", 1, 2, 0, 1, 1, None))
    added = build_market_data_snapshot(market_db)
    assert added.logical_content_fingerprint != schema_changed.logical_content_fingerprint
    with sqlite3.connect(market_db) as connection:
        connection.execute("DELETE FROM prices WHERE symbol = 'DDD'")
    assert build_market_data_snapshot(market_db).logical_content_fingerprint == schema_changed.logical_content_fingerprint


def test_loader_is_bounded_defensive_and_read_only(market_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = build_market_data_snapshot(market_db)
    before = market_db.read_bytes()
    statements: list[str] = []
    original_connection = snapshot_module._readonly_connection
    def traced_connection(path: Path):
        connection = original_connection(path)
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(snapshot_module, "_readonly_connection", traced_connection)
    bundle = snapshot.load_ohlcv(["AAA", "BBB", "VNINDEX", "MISSING"])
    assert sum(" PRICES" in statement.upper() for statement in statements) == 1
    assert bundle.query_mode == "json_each"
    assert bundle.sqlite_row_count == bundle.row_count
    first = bundle.frame_for("AAA")
    first.loc[:, "close"] = -1
    assert float(bundle.frame_for("AAA").iloc[0]["close"]) != -1
    frames = bundle.frames
    frames["AAA"].loc[:, "close"] = -2
    assert float(bundle.frame_for("AAA").iloc[0]["close"]) != -2
    assert market_db.read_bytes() == before
    with snapshot_module._readonly_connection(market_db) as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE must_fail (id INTEGER)")


def test_requested_symbol_json_path_and_forced_fallback_are_parity_equivalent(market_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = build_market_data_snapshot(market_db)
    json_bundle = snapshot.load_ohlcv(["AAA", "BBB", "VNINDEX", "MISSING"], through_date="2020-01-01")
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: json_each")
    monkeypatch.setattr(snapshot_module, "_execute_json_query", unavailable)
    fallback_bundle = snapshot.load_ohlcv(["AAA", "BBB", "VNINDEX", "MISSING"], through_date="2020-01-01")
    assert json_bundle.query_mode == "json_each"
    assert fallback_bundle.query_mode == "fallback_date_scan"
    assert json_bundle.available_symbols == fallback_bundle.available_symbols
    assert json_bundle.missing_symbols == fallback_bundle.missing_symbols
    for symbol in json_bundle.requested_symbols:
        pd.testing.assert_frame_equal(json_bundle.frame_for(symbol), fallback_bundle.frame_for(symbol))


def test_loader_matches_authoritative_dtypes_for_integral_null_and_invalid_volume(tmp_path: Path) -> None:
    path = tmp_path / "volume.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE prices (symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER)"
        )
        connection.executemany("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", [
        ("AAA", "2020-01-01", 1, 2, .5, 1.5, 10),
        ("AAA", "2020-01-02", 2, 3, 1, 2.5, 11),
        ("BBB", "2020-01-01", 1, 2, .5, 1.5, None),
        ("BBB", "2020-01-02", 2, 3, 1, 2.5, "invalid"),
        ("CCC", "2020-01-01", 1, 2, .5, 1.5, 1),
        ("CCC", "2020-01-01", 3, 4, 2, 3.5, 2),
        ])
    snapshot = build_market_data_snapshot(path)
    bundle = snapshot.load_ohlcv(["AAA", "BBB", "CCC"])
    for symbol in ("AAA", "BBB", "CCC"):
        expected = load_price_data(symbol, db_path=str(path)).assign(
            symbol=lambda frame: frame.symbol.str.strip().str.upper()
        )
        pd.testing.assert_frame_equal(bundle.frame_for(symbol), expected)
    assert str(bundle.frame_for("AAA")["volume"].dtype) == "int64"
    assert pd.isna(bundle.frame_for("BBB")["volume"]).all()
    assert float(bundle.frame_for("CCC").iloc[0]["close"]) == 3.5


def test_json_query_is_single_select_without_probe_and_filters_before_window(market_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = build_market_data_snapshot(market_db)
    statements: list[str] = []
    original_connection = snapshot_module._readonly_connection
    def traced_connection(path: Path):
        connection = original_connection(path)
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(snapshot_module, "_readonly_connection", traced_connection)
    bundle = snapshot.load_ohlcv(["AAA"])
    assert bundle.available_symbols == ("AAA",) and bundle.sqlite_row_count == 2
    assert len(statements) == 1 and statements[0].lstrip().upper().startswith("WITH")
    query, params = snapshot_module._query_parts(
        start_date=None, through_date=None, requested_symbols_json='["AAA"]',
    )
    with sqlite3.connect(market_db) as connection:
        plan = [row[-1].upper() for row in connection.execute("EXPLAIN QUERY PLAN " + query, params)]
    assert any("MATERIALIZE FILTERED" in row for row in plan)
    assert any("LIST SUBQUERY" in row for row in plan)
    assert any("USE TEMP B-TREE FOR ORDER BY" in row for row in plan)


def test_fallback_executes_one_bounded_data_select_without_symbol_queries(market_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = build_market_data_snapshot(market_db)
    statements: list[str] = []
    original_connection = snapshot_module._readonly_connection
    def traced_connection(path: Path):
        connection = original_connection(path)
        connection.set_trace_callback(statements.append)
        return connection
    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("no such table: json_each")
    monkeypatch.setattr(snapshot_module, "_readonly_connection", traced_connection)
    monkeypatch.setattr(snapshot_module, "_execute_json_query", unavailable)
    bundle = snapshot.load_ohlcv(["AAA", "BBB"], through_date="2020-01-01")
    assert bundle.query_mode == "fallback_date_scan"
    assert len(statements) == 1 and statements[0].lstrip().upper().startswith("WITH")


def test_json_requested_symbols_avoid_variable_limits_and_use_one_data_select(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "many.db"
    rows = [(f"S{index:04d}", "2020-01-01", 1, 2, .5, 1.5, 1) for index in range(1_100)]
    _database(path, rows)
    snapshot = build_market_data_snapshot(path)
    statements: list[str] = []
    original_connection = snapshot_module._readonly_connection
    def traced_connection(database_path: Path):
        connection = original_connection(database_path)
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(snapshot_module, "_readonly_connection", traced_connection)
    bundle = snapshot.load_ohlcv([*snapshot.symbols, "MISSING"])
    assert bundle.query_mode == "json_each"
    assert bundle.row_count == 1_100 and bundle.missing_symbols == ("MISSING",)
    assert sum(" PRICES" in statement.upper() for statement in statements) == 1


def test_stream_uses_fetchmany_without_requesting_all_rows() -> None:
    class GuardedCursor:
        def __init__(self) -> None:
            self.rows = [
                ("AAA", "2020-01-01", 1, 2, .5, 1.5, 1, 1),
                ("BBB", "2020-01-01", 2, 3, 1, 2.5, 1, 2),
            ]
            self.calls: list[int] = []
        def fetchmany(self, size: int):
            self.calls.append(size)
            result, self.rows = self.rows[:1], self.rows[1:]
            return result
        def fetchall(self):  # pragma: no cover - assertion protects the contract
            raise AssertionError("stream must not request all rows")
    cursor = GuardedCursor()
    assert [row[0] for row in snapshot_module._stream_normalized_rows(cursor)] == ["AAA", "BBB"]
    assert cursor.calls and all(size == snapshot_module._STREAM_BATCH_SIZE for size in cursor.calls)


def test_invalid_ranges_empty_dataset_and_missing_database_are_safe(tmp_path: Path, market_db: Path) -> None:
    snapshot = build_market_data_snapshot(market_db)
    with pytest.raises(ValueError, match="start_date"):
        snapshot.load_ohlcv(["AAA"], start_date="2020-02-01", through_date="2020-01-01")
    with pytest.raises(ValueError, match="match"):
        snapshot.load_ohlcv(["AAA"], through_date="2020-01-01", end_date="2020-01-02")
    empty = tmp_path / "empty.db"
    _database(empty, [])
    observed = build_market_data_snapshot(empty)
    assert observed.row_count == 0 and observed.symbols == ()
    assert observed.snapshot_id == build_market_data_snapshot(empty).snapshot_id
    missing = tmp_path / "does-not-exist.db"
    with pytest.raises(FileNotFoundError):
        build_market_data_snapshot(missing)
    assert not missing.exists()


def test_import_has_no_database_or_filesystem_side_effect(tmp_path: Path) -> None:
    missing = tmp_path / "not-created.db"
    environment = dict(os.environ, MARKET_DATABASE_PATH=str(missing))
    completed = subprocess.run(
        [sys.executable, "-c", "import quantlab.catalog.market_data_snapshot"],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert not missing.exists()
