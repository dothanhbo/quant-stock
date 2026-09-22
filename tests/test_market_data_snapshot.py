from __future__ import annotations

import os
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
    calls = 0
    original = snapshot_module._read_price_rows
    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)
    monkeypatch.setattr(snapshot_module, "_read_price_rows", counted)
    bundle = snapshot.load_ohlcv(["AAA", "BBB", "VNINDEX", "MISSING"])
    assert calls == 1
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
