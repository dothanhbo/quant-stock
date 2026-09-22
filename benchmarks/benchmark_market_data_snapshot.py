from __future__ import annotations

"""Opt-in correctness/performance benchmark for ``MarketDataSnapshot``.

This command never contacts a market provider and does not run a strategy. It
only reads a supplied SQLite market database. ``tracemalloc`` reports Python
allocations and does not capture every native/pandas allocation.
"""

import argparse
from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import platform
import sqlite3
import sys
import time
import tracemalloc
from typing import Any, Callable, Iterator

import pandas as pd

from backtesting import engine as historical_engine
from core.paths import resolve_market_database_path
import quantlab.catalog.market_data_snapshot as snapshot_module
from quantlab.catalog.market_data_snapshot import MarketDataSnapshot, build_market_data_snapshot


def _parse_sample_sizes(value: str) -> tuple[int, ...]:
    try:
        sizes = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise ValueError("sample_sizes must be comma-separated integers") from exc
    if not sizes or any(size <= 0 for size in sizes):
        raise ValueError("sample_sizes must contain only positive integers")
    return tuple(dict.fromkeys(sizes))


def _sample_symbols(snapshot: MarketDataSnapshot, *, size: int, include_vnindex: bool) -> tuple[str, ...]:
    if size <= 0:
        raise ValueError("sample size must be positive")
    available = tuple(symbol for symbol in snapshot.symbols if include_vnindex or symbol != "VNINDEX")
    return available[:size]


def _readonly_engine_connection(path: str) -> sqlite3.Connection:
    database_path = Path(path).resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"market database not found: {database_path}")
    connection = sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _query_counter() -> tuple[dict[str, int], Callable[[str], None]]:
    counts = {"data_selects": 0, "pragma_reads": 0, "other_reads": 0}

    def trace(statement: str) -> None:
        normalized = statement.lstrip().upper()
        if normalized.startswith("PRAGMA"):
            counts["pragma_reads"] += 1
        elif normalized.startswith("SELECT"):
            if " FROM PRICES" in normalized:
                counts["data_selects"] += 1
            else:
                counts["other_reads"] += 1

    return counts, trace


@contextmanager
def _instrument_existing_loader() -> Iterator[dict[str, int]]:
    original = historical_engine._connect
    counts, trace = _query_counter()

    def connect(path: str) -> sqlite3.Connection:
        connection = _readonly_engine_connection(path)
        connection.set_trace_callback(trace)
        return connection

    historical_engine._connect = connect
    try:
        yield counts
    finally:
        historical_engine._connect = original


@contextmanager
def _instrument_snapshot_loader() -> Iterator[dict[str, int]]:
    original = snapshot_module._readonly_connection
    counts, trace = _query_counter()

    def connect(path: Path) -> sqlite3.Connection:
        connection = original(path)
        connection.set_trace_callback(trace)
        return connection

    snapshot_module._readonly_connection = connect
    try:
        yield counts
    finally:
        snapshot_module._readonly_connection = original


def _canonical_frame(frame: pd.DataFrame, *, start_date: str | None, end_date: str | None) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=["symbol", "time", "open", "high", "low", "close", "volume"])
    data = frame.loc[:, ["symbol", "time", "open", "high", "low", "close", "volume"]].copy()
    data["symbol"] = data["symbol"].map(lambda value: str(value).strip().upper())
    data["time"] = pd.to_datetime(data["time"], errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    if start_date is not None:
        data = data.loc[data["time"].dt.date >= pd.Timestamp(start_date).date()]
    if end_date is not None:
        data = data.loc[data["time"].dt.date <= pd.Timestamp(end_date).date()]
    return data.sort_values(["symbol", "time"], kind="stable").reset_index(drop=True)


def _frame_hash(frame: pd.DataFrame) -> str:
    return sha256(
        json.dumps(
            snapshot_module._logical_rows(frame),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _first_difference(left: pd.DataFrame, right: pd.DataFrame) -> str | None:
    if list(left.columns) != list(right.columns):
        return f"columns differ: {list(left.columns)} != {list(right.columns)}"
    if len(left) != len(right):
        return f"row count differs: {len(left)} != {len(right)}"
    for index in range(len(left)):
        for column in left.columns:
            a, b = left.iloc[index][column], right.iloc[index][column]
            if (pd.isna(a) and pd.isna(b)) or a == b:
                continue
            return f"row {index}, symbol={left.iloc[index]['symbol']}, date={left.iloc[index]['time']}, field={column}: {a!r} != {b!r}"
    try:
        pd.testing.assert_frame_equal(left, right, check_dtype=True)
    except AssertionError as exc:
        return str(exc)
    return None


def _measure(function: Callable[[], Any]) -> tuple[Any, float, int]:
    tracemalloc.start()
    started = time.perf_counter()
    value = function()
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return value, elapsed, peak


def _summary(values: list[float]) -> dict[str, Any]:
    return {
        "individual_seconds": values,
        "median_seconds": float(pd.Series(values).median()),
        "minimum_seconds": min(values),
        "maximum_seconds": max(values),
    }


def run_benchmark(
    *,
    database_path: str | Path | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    sample_sizes: tuple[int, ...] = (10, 50, 100),
    repeat: int = 3,
    include_vnindex: bool = False,
) -> dict[str, Any]:
    """Run the opt-in benchmark and return JSON-compatible measurements."""
    if repeat <= 0:
        raise ValueError("repeat must be positive")
    if any(size <= 0 for size in sample_sizes):
        raise ValueError("sample sizes must be positive")
    canonical_path = resolve_market_database_path(database_path)
    if not canonical_path.is_file():
        raise FileNotFoundError(f"market database not found: {canonical_path}")
    before = canonical_path.read_bytes()

    # Warm imports before timing and then independently measure construction.
    import backtesting.engine  # noqa: F401
    import quantlab.catalog.market_data_snapshot  # noqa: F401
    construction: list[float] = []
    construction_peaks: list[int] = []
    snapshots: list[MarketDataSnapshot] = []
    with _instrument_snapshot_loader() as construction_queries:
        for _ in range(repeat):
            snapshot, elapsed, peak = _measure(lambda: build_market_data_snapshot(canonical_path))
            snapshots.append(snapshot)
            construction.append(elapsed)
            construction_peaks.append(peak)
    snapshot = snapshots[0]
    if any(item.snapshot_id != snapshot.snapshot_id for item in snapshots[1:]):
        raise RuntimeError("unchanged database produced non-deterministic snapshot identities")

    scenarios: list[dict[str, Any]] = []
    for requested_size in sample_sizes:
        symbols = _sample_symbols(snapshot, size=requested_size, include_vnindex=include_vnindex)
        old_times: list[float] = []
        old_peaks: list[int] = []
        bulk_times: list[float] = []
        bulk_peaks: list[int] = []
        parity = True
        mismatch: str | None = None
        old_rows = bulk_rows = 0
        with _instrument_existing_loader() as old_queries, _instrument_snapshot_loader() as bulk_queries:
            for _ in range(repeat):
                def old_load() -> dict[str, pd.DataFrame]:
                    return {
                        symbol: _canonical_frame(
                            historical_engine.load_price_data(symbol, db_path=str(canonical_path)),
                            start_date=start_date, end_date=end_date,
                        )
                        for symbol in symbols
                    }
                old_frames, old_elapsed, old_peak = _measure(old_load)
                bundle, bulk_elapsed, bulk_peak = _measure(
                    lambda: snapshot.load_ohlcv(symbols, start_date=start_date, through_date=end_date)
                )
                old_times.append(old_elapsed)
                old_peaks.append(old_peak)
                bulk_times.append(bulk_elapsed)
                bulk_peaks.append(bulk_peak)
                old_rows = sum(len(frame) for frame in old_frames.values())
                bulk_rows = bundle.row_count
                for symbol in symbols:
                    difference = _first_difference(old_frames[symbol], bundle.frame_for(symbol))
                    if difference is not None:
                        parity, mismatch = False, f"{symbol}: {difference}"
                        break
                if not parity:
                    break
        scenarios.append({
            "requested_sample_size": requested_size,
            "actual_sample_size": len(symbols),
            "sample_capped": len(symbols) < requested_size,
            "symbols": list(symbols),
            "existing_loader": {**_summary(old_times), "row_count": old_rows, "peak_python_bytes": old_peaks, "query_counts": old_queries},
            "bulk_loader": {**_summary(bulk_times), "row_count": bulk_rows, "peak_python_bytes": bulk_peaks, "query_counts": bulk_queries},
            "speed_ratio_existing_over_bulk": (
                float(pd.Series(old_times).median() / pd.Series(bulk_times).median())
                if bulk_times else None
            ),
            "parity": parity,
            "parity_mismatch": mismatch,
        })
    unchanged = canonical_path.read_bytes() == before
    return {
        "runtime": {"python": sys.version, "pandas": pd.__version__, "sqlite": sqlite3.sqlite_version, "platform": platform.platform()},
        "database_path": str(canonical_path),
        "start_date": start_date,
        "end_date": end_date,
        "tracemalloc_note": "Peak bytes are Python allocations only; native/pandas allocations may not be captured.",
        "snapshot_construction": {**_summary(construction), "peak_python_bytes": construction_peaks, "row_count": snapshot.row_count, "symbol_count": snapshot.symbol_count, "snapshot_id": snapshot.snapshot_id, "logical_content_fingerprint": snapshot.logical_content_fingerprint, "query_counts": construction_queries},
        "scenarios": scenarios,
        "database_unchanged": unchanged,
        "parity": all(item["parity"] for item in scenarios),
    }


def _print_summary(result: dict[str, Any]) -> None:
    print(f"Database: {result['database_path']}")
    print(f"Snapshot: {result['snapshot_construction']['snapshot_id']}")
    print("requested actual rows(old/bulk) old median bulk median ratio parity")
    for scenario in result["scenarios"]:
        old = scenario["existing_loader"]; bulk = scenario["bulk_loader"]
        print(
            f"{scenario['requested_sample_size']:>9} {scenario['actual_sample_size']:>6} "
            f"{old['row_count']:>7}/{bulk['row_count']:<7} "
            f"{old['median_seconds']:.6f} {bulk['median_seconds']:.6f} "
            f"{scenario['speed_ratio_existing_over_bulk']:.2f} {scenario['parity']}"
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-path")
    parser.add_argument("--start-date")
    parser.add_argument("--end-date")
    parser.add_argument("--sample-sizes", default="10,50,100")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output-json")
    parser.add_argument("--include-vnindex", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_benchmark(
            database_path=args.database_path, start_date=args.start_date,
            end_date=args.end_date, sample_sizes=_parse_sample_sizes(args.sample_sizes),
            repeat=args.repeat, include_vnindex=args.include_vnindex,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"benchmark failed: {exc}", file=sys.stderr)
        return 2
    _print_summary(result)
    if args.output_json:
        Path(args.output_json).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return 0 if result["parity"] and result["database_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
