from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

import benchmarks.benchmark_market_data_snapshot as benchmark
from quantlab.catalog.market_data_snapshot import build_market_data_snapshot


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE prices (symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL)")
        connection.executemany(
            "INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("AAA", "2020-01-01", 1, 2, .5, 1.5, 10),
                ("AAA", "2020-01-02", 2, 3, 1, 2.5, 11),
                ("BBB", "2020-01-01", 3, 4, 2, 3.5, 12),
                ("VNINDEX", "2020-01-01", 100, 101, 99, 100.5, 1000),
            ],
        )


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "market.db"
    _database(path)
    return path


def test_sampling_is_deterministic_and_excludes_vnindex_by_default(database: Path) -> None:
    snapshot = build_market_data_snapshot(database)
    assert benchmark._sample_symbols(snapshot, size=10, include_vnindex=False) == ("AAA", "BBB")
    assert benchmark._sample_symbols(snapshot, size=10, include_vnindex=True) == ("AAA", "BBB", "VNINDEX")
    with pytest.raises(ValueError):
        benchmark._sample_symbols(snapshot, size=0, include_vnindex=False)
    assert benchmark._parse_sample_sizes("10, 50,10") == (10, 50)
    with pytest.raises(ValueError):
        benchmark._parse_sample_sizes("0,10")


def test_benchmark_runs_parity_query_count_and_read_only(database: Path) -> None:
    before = database.read_bytes()
    result = benchmark.run_benchmark(database_path=database, sample_sizes=(1, 10), repeat=1)
    assert result["parity"] and result["database_unchanged"]
    assert result["snapshot_construction"]["query_counts"]["data_selects"] == 1
    assert result["scenarios"][0]["bulk_loader"]["query_counts"]["data_selects"] == 1
    assert result["scenarios"][0]["bulk_loader"]["query_mode"] in {"json_each", "fallback_date_scan"}
    assert result["scenarios"][0]["bulk_loader"]["sqlite_rows_returned"] == result["scenarios"][0]["bulk_loader"]["row_count"]
    assert result["scenarios"][0]["existing_loader"]["query_counts"]["data_selects"] == 1
    assert result["scenarios"][1]["actual_sample_size"] == 2
    assert result["scenarios"][1]["sample_capped"]
    assert database.read_bytes() == before


def test_mismatch_is_reported_and_main_exits_nonzero(database: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = benchmark.historical_engine.load_price_data
    def mismatching(*args, **kwargs):
        frame = original(*args, **kwargs).copy()
        if not frame.empty:
            frame.loc[frame.index[0], "close"] = 999.0
        return frame
    monkeypatch.setattr(benchmark.historical_engine, "load_price_data", mismatching)
    result = benchmark.run_benchmark(database_path=database, sample_sizes=(1,), repeat=1)
    assert not result["parity"]
    assert result["scenarios"][0]["parity_mismatch"]
    monkeypatch.setattr(benchmark, "parse_args", lambda: type("Args", (), {
        "database_path": str(database), "start_date": None, "end_date": None,
        "sample_sizes": "1", "repeat": 1, "output_json": None,
        "include_vnindex": False,
    })())
    assert benchmark.main() == 1


def test_json_output_invalid_arguments_and_import_safety(database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "result.json"
    monkeypatch.setattr(benchmark, "parse_args", lambda: type("Args", (), {
        "database_path": str(database), "start_date": None, "end_date": None,
        "sample_sizes": "1", "repeat": 1, "output_json": str(output),
        "include_vnindex": False,
    })())
    assert benchmark.main() == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert {"runtime", "snapshot_construction", "scenarios", "parity", "database_unchanged"}.issubset(payload)
    with pytest.raises(ValueError):
        benchmark.run_benchmark(database_path=database, sample_sizes=(1,), repeat=0)
    completed = subprocess.run(
        [sys.executable, "-c", "import benchmarks.benchmark_market_data_snapshot"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_documented_direct_script_invocation_works(database: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "benchmarks" / "benchmark_market_data_snapshot.py"
    completed = subprocess.run(
        [
            sys.executable, str(script), "--database-path", str(database),
            "--sample-sizes", "1", "--repeat", "1",
        ],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Snapshot:" in completed.stdout
