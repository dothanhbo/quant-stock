from __future__ import annotations

"""Opt-in reproducible benchmark for Phase 2.2 prepared-feature cache.

No strategy is run and no network provider is contacted.  Timings exclude
snapshot construction. ``tracemalloc`` measures Python allocations only; it
does not necessarily include pandas or native allocations.
"""

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import platform
import shutil
import sqlite3
import sys
import tempfile
import time
import tracemalloc
from typing import Any, Iterator
from uuid import uuid4

import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.paths import resolve_market_database_path
from quantlab.catalog.market_data_snapshot import MarketDataSnapshot, build_market_data_snapshot
from quantlab.features import FeatureDefinition, FeatureRegistry, FeatureRequest, PreparedFeatureCache, builtin_definitions


_FEATURES = (("ema", 50), ("atr", 14), ("donchian", 20))
_OWNER = ".prepared_feature_cache_benchmark_owner"


def _parse_sizes(value: str) -> tuple[int, ...]:
    values = tuple(dict.fromkeys(int(item.strip()) for item in value.split(",") if item.strip()))
    if not values or any(item <= 0 for item in values):
        raise ValueError("sample_sizes must contain positive integers")
    return values


def _measure(callable_: Any) -> tuple[Any, float, int]:
    tracemalloc.start(); started = time.perf_counter()
    value = callable_(); elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
    return value, elapsed, peak


def _summary(times: list[float]) -> dict[str, Any]:
    return {"individual_seconds": times, "min_seconds": min(times), "median_seconds": float(pd.Series(times).median()), "max_seconds": max(times)}


def _parity_detail(left, right) -> str | None:
    if left.computation_identity != right.computation_identity:
        return "computation identity differs"
    if (left.available_symbols, left.missing_symbols) != (right.available_symbols, right.missing_symbols):
        return "available/missing symbols differ"
    if tuple(left.frames) != tuple(right.frames):
        return "frame symbols differ"
    for symbol in left.frames:
        try:
            pd.testing.assert_frame_equal(left.frame_for(symbol), right.frame_for(symbol), check_dtype=True)
        except AssertionError as error:
            return f"{symbol}: {error}"
    return None


@contextmanager
def _instrument(registry: FeatureRegistry, snapshot: MarketDataSnapshot, request: FeatureRequest) -> Iterator[dict[str, int]]:
    counts = {"snapshot_loads": 0, "top_level_feature_calls": 0, "dependency_feature_calls": 0}
    original_load = type(snapshot).load_ohlcv
    definition = registry.definition_for(request); original_compute = definition.compute
    def load(self, *args, **kwargs):
        counts["snapshot_loads"] += 1
        return original_load(self, *args, **kwargs)
    def compute(*args, **kwargs):
        counts["top_level_feature_calls"] += 1
        return original_compute(*args, **kwargs)
    type(snapshot).load_ohlcv = load
    registry._definitions[(definition.name, definition.version)] = FeatureDefinition(**{field: getattr(definition, field) for field in definition.__dataclass_fields__} | {"compute": compute})
    try:
        yield counts
    finally:
        type(snapshot).load_ohlcv = original_load
        registry._definitions[(definition.name, definition.version)] = definition


def _owned_cache(parent: Path) -> Path:
    child = parent.resolve() / f"prepared-feature-cache-benchmark-{uuid4().hex}"
    child.mkdir(parents=True, exist_ok=False)
    (child / _OWNER).write_text("owned by benchmark", encoding="utf-8")
    return child


def _remove_owned(path: Path) -> None:
    if not (path / _OWNER).is_file():
        raise RuntimeError("refusing to remove an unowned cache directory")
    shutil.rmtree(path)


def _entry_bytes(cache: PreparedFeatureCache, identity) -> int:
    entry = cache._entry_path(identity)
    return sum(item.stat().st_size for item in entry.rglob("*") if item.is_file())


def run_benchmark(*, database_path: str | Path | None = None, start_date: str = "2018-08-07", end_date: str = "2026-09-17", sample_sizes: tuple[int, ...] = (10, 50, 100), repeat: int = 3, cache_root: str | Path | None = None, codec: str = "sqlite-json-v1") -> dict[str, Any]:
    if repeat <= 0:
        raise ValueError("repeat must be positive")
    canonical_path = resolve_market_database_path(database_path)
    if not canonical_path.is_file():
        raise FileNotFoundError(f"market database not found: {canonical_path}")
    before = canonical_path.read_bytes()
    snapshot = build_market_data_snapshot(canonical_path)  # intentionally outside all feature trials
    symbols = tuple(symbol for symbol in snapshot.symbols if symbol != "VNINDEX")
    temporary_root: tempfile.TemporaryDirectory[str] | None = None
    if cache_root is None:
        temporary_root = tempfile.TemporaryDirectory(prefix="quantlab-feature-cache-benchmark-")
        parent = Path(temporary_root.name)
    else:
        parent = Path(cache_root).expanduser().resolve()
    scenarios: list[dict[str, Any]] = []
    try:
        for feature, period in _FEATURES:
            for size in sample_sizes:
                selected = symbols[:size]; request = FeatureRequest(feature, "v1", {"period": period})
                paths: dict[str, dict[str, Any]] = {name: {"times": [], "peaks": [], "counts": [], "cache_bytes": [], "checksums": [], "identities": [], "results": []} for name in ("uncached", "cold_cache", "warm_cache")}
                for _ in range(repeat):
                    registry = FeatureRegistry(builtin_definitions())
                    with _instrument(registry, snapshot, request) as counts:
                        result, elapsed, peak = _measure(lambda: registry.compute(request, snapshot, selected, start_date=start_date, through_date=end_date))
                    checksum = PreparedFeatureCache(parent / "checksum")._checksum(result.computation_identity, result.available_symbols, result.missing_symbols, result.metadata, result._frames)[0]
                    paths["uncached"]["times"].append(elapsed); paths["uncached"]["peaks"].append(peak); paths["uncached"]["counts"].append(counts); paths["uncached"]["cache_bytes"].append(0); paths["uncached"]["checksums"].append(checksum); paths["uncached"]["identities"].append(result.computation_identity.sha256); paths["uncached"]["results"].append(result)
                    owned = _owned_cache(parent)
                    try:
                        cold_cache = PreparedFeatureCache(owned, codec=codec); registry = FeatureRegistry(builtin_definitions())
                        with _instrument(registry, snapshot, request) as counts:
                            result, elapsed, peak = _measure(lambda: registry.compute(request, snapshot, selected, start_date=start_date, through_date=end_date, cache=cold_cache))
                        paths["cold_cache"]["times"].append(elapsed); paths["cold_cache"]["peaks"].append(peak); paths["cold_cache"]["counts"].append(counts); paths["cold_cache"]["cache_bytes"].append(_entry_bytes(cold_cache, result.computation_identity)); paths["cold_cache"]["checksums"].append(result.metadata["cache"]["content_checksum"]); paths["cold_cache"]["identities"].append(result.computation_identity.sha256); paths["cold_cache"]["results"].append(result)
                        registry = FeatureRegistry(builtin_definitions())
                        with _instrument(registry, snapshot, request) as counts:
                            result, elapsed, peak = _measure(lambda: registry.compute(request, snapshot, selected, start_date=start_date, through_date=end_date, cache=cold_cache))
                        paths["warm_cache"]["times"].append(elapsed); paths["warm_cache"]["peaks"].append(peak); paths["warm_cache"]["counts"].append(counts); paths["warm_cache"]["cache_bytes"].append(_entry_bytes(cold_cache, result.computation_identity)); paths["warm_cache"]["checksums"].append(result.metadata["cache"]["content_checksum"]); paths["warm_cache"]["identities"].append(result.computation_identity.sha256); paths["warm_cache"]["results"].append(result)
                    finally:
                        _remove_owned(owned)
                mismatch = next((detail for index in range(repeat) for detail in (_parity_detail(paths["uncached"]["results"][index], paths["cold_cache"]["results"][index]), _parity_detail(paths["uncached"]["results"][index], paths["warm_cache"]["results"][index])) if detail), None)
                warm_gate = all(count["snapshot_loads"] == 0 and count["top_level_feature_calls"] == 0 and count["dependency_feature_calls"] == 0 for count in paths["warm_cache"]["counts"])
                cold_gate = all(count["snapshot_loads"] == 1 and count["top_level_feature_calls"] == 1 for name in ("uncached", "cold_cache") for count in paths[name]["counts"])
                stable = all(len(set(paths[name]["identities"])) == 1 and len(set(paths[name]["checksums"])) == 1 for name in paths)
                scenario = {"feature": feature, "period": period, "requested_symbols": size, "actual_symbols": len(selected), "paths": {name: {**_summary(data["times"]), "peak_python_bytes": data["peaks"], "row_count": len(data["results"][-1]._frames) and sum(len(frame) for frame in data["results"][-1]._frames.values()), "cache_entry_bytes": data["cache_bytes"], "load_counts": data["counts"], "cache_hit": [item.metadata.get("cache", {}).get("hit") if "cache" in item.metadata else None for item in data["results"]], "cache_codec": [item.metadata.get("cache", {}).get("codec") if "cache" in item.metadata else None for item in data["results"]], "computation_identity": data["identities"][0], "logical_content_checksum": data["checksums"][0]} for name, data in paths.items()}, "parity": mismatch is None, "parity_mismatch": mismatch, "warm_gate": warm_gate, "cold_uncached_gate": cold_gate, "identity_stable": stable}
                scenarios.append(scenario)
    finally:
        if temporary_root is not None:
            temporary_root.cleanup()
    after = canonical_path.read_bytes()
    return {"codec": codec, "runtime": {"python": sys.version, "pandas": pd.__version__, "sqlite": sqlite3.sqlite_version, "platform": platform.platform()}, "database_path": str(canonical_path), "database_sha256_before": __import__("hashlib").sha256(before).hexdigest(), "database_sha256_after": __import__("hashlib").sha256(after).hexdigest(), "database_unchanged": before == after, "snapshot_id": snapshot.snapshot_id, "logical_content_fingerprint": snapshot.logical_content_fingerprint, "parameters": {"start_date": start_date, "end_date": end_date, "sample_sizes": list(sample_sizes), "repeat": repeat}, "tracemalloc_note": "Peak bytes are Python allocations only; native/pandas allocations may not be captured.", "scenarios": scenarios, "passed": before == after and all(item["parity"] and item["warm_gate"] and item["cold_uncached_gate"] and item["identity_stable"] for item in scenarios)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-path"); parser.add_argument("--start-date", default="2018-08-07"); parser.add_argument("--end-date", default="2026-09-17")
    parser.add_argument("--sample-sizes", default="10,50,100"); parser.add_argument("--repeat", type=int, default=3); parser.add_argument("--cache-root"); parser.add_argument("--codec", default="sqlite-json-v1", choices=("sqlite-json-v1", "npz_numeric_v1")); parser.add_argument("--output-json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_benchmark(database_path=args.database_path, start_date=args.start_date, end_date=args.end_date, sample_sizes=_parse_sizes(args.sample_sizes), repeat=args.repeat, cache_root=args.cache_root, codec=getattr(args, "codec", "sqlite-json-v1"))
    except (FileNotFoundError, ValueError, RuntimeError) as error:
        print(f"benchmark failed: {error}", file=sys.stderr); return 2
    print("feature symbols uncached_median cold_median warm_median warm_speedup parity warm_loads warm_compute cache_bytes")
    for item in result["scenarios"]:
        paths = item["paths"]; speedup = paths["uncached"]["median_seconds"] / paths["warm_cache"]["median_seconds"]
        print(f"{item['feature']:8} {item['actual_symbols']:7} {paths['uncached']['median_seconds']:.6f} {paths['cold_cache']['median_seconds']:.6f} {paths['warm_cache']['median_seconds']:.6f} {speedup:.2f} {item['parity']} {paths['warm_cache']['load_counts'][-1]['snapshot_loads']} {paths['warm_cache']['load_counts'][-1]['top_level_feature_calls']} {paths['warm_cache']['cache_entry_bytes'][-1]}")
    if args.output_json:
        Path(args.output_json).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
