from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

import benchmarks.benchmark_prepared_feature_cache as benchmark


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "market.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE prices (symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER)")
        for symbol, offset in (("AAA", 0), ("BBB", 10)):
            connection.executemany("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", [
                (symbol, f"2020-01-{day:02d}", day + offset, day + offset + 1, day + offset - 1, day + offset + .5, day)
                for day in range(1, 26)
            ])
    return path


def test_benchmark_paths_parity_counters_cleanup_and_database_immutability(database: Path, tmp_path: Path) -> None:
    before = database.read_bytes(); root = tmp_path / "caller-root"; root.mkdir(); sentinel = root / "do-not-delete"; sentinel.write_text("owned", encoding="utf-8")
    result = benchmark.run_benchmark(database_path=database, start_date="2020-01-01", end_date="2020-01-25", sample_sizes=(1, 10), repeat=1, cache_root=root)
    assert result["passed"] and result["database_unchanged"] and database.read_bytes() == before and sentinel.exists()
    assert len(result["scenarios"]) == 6
    for scenario in result["scenarios"]:
        assert scenario["parity"] and scenario["warm_gate"] and scenario["cold_uncached_gate"] and scenario["identity_stable"]
        assert scenario["paths"]["warm_cache"]["load_counts"] == [{"snapshot_loads": 0, "top_level_feature_calls": 0, "dependency_feature_calls": 0}]
        assert scenario["paths"]["cold_cache"]["load_counts"][0]["snapshot_loads"] == 1
    assert not list(root.glob("prepared-feature-cache-benchmark-*"))


def test_cli_json_parse_and_import_safety(database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert benchmark._parse_sizes("1, 10,1") == (1, 10)
    with pytest.raises(ValueError):
        benchmark._parse_sizes("0")
    output = tmp_path / "benchmark.json"
    monkeypatch.setattr(benchmark, "parse_args", lambda: type("Args", (), {"database_path": str(database), "start_date": "2020-01-01", "end_date": "2020-01-25", "sample_sizes": "1", "repeat": 1, "cache_root": None, "output_json": str(output)})())
    assert benchmark.main() == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert {"snapshot_id", "logical_content_fingerprint", "database_unchanged", "scenarios", "passed"}.issubset(payload)
    completed = subprocess.run([sys.executable, "-c", "import benchmarks.benchmark_prepared_feature_cache"], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_failure_result_causes_nonzero_exit(database: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = benchmark._parity_detail
    monkeypatch.setattr(benchmark, "_parity_detail", lambda left, right: "forced mismatch")
    result = benchmark.run_benchmark(database_path=database, start_date="2020-01-01", end_date="2020-01-25", sample_sizes=(1,), repeat=1)
    assert not result["passed"] and result["scenarios"][0]["parity_mismatch"] == "forced mismatch"
    monkeypatch.setattr(benchmark, "_parity_detail", original)
