from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from threading import Barrier, Thread

import pandas as pd
import pytest

from quantlab.catalog.market_data_snapshot import build_market_data_snapshot
from quantlab.features import CacheCorruptionError, FeatureRegistry, FeatureRequest, PreparedFeatureCache, builtin_definitions


def _snapshot(tmp_path: Path):
    path = tmp_path / "market.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE prices (symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER)")
        for symbol, offset in (("AAA", 0), ("BBB", 10)):
            connection.executemany("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", [
                (symbol, date.date().isoformat(), 10 + offset + index, 11 + offset + index, 9 + offset + index, 10.5 + offset + index, index + 1)
                for index, date in enumerate(pd.date_range("2020-01-01", periods=25, freq="B"))
            ])
    return build_market_data_snapshot(path)


def _registry() -> FeatureRegistry:
    return FeatureRegistry(builtin_definitions())


def test_cache_root_is_explicit_lazy_and_warm_hit_skips_load_and_compute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot = _snapshot(tmp_path); root = tmp_path / "cache"; cache = PreparedFeatureCache(root)
    assert not root.exists()
    registry = _registry(); request = FeatureRequest("ema", "v1", {"period": 10})
    loads = computes = 0
    original_load = type(snapshot).load_ohlcv
    original = registry.definition_for(request).compute
    def counted_load(self, *args, **kwargs):
        nonlocal loads; loads += 1; return original_load(self, *args, **kwargs)
    def counted_compute(*args, **kwargs):
        nonlocal computes; computes += 1; return original(*args, **kwargs)
    monkeypatch.setattr(type(snapshot), "load_ohlcv", counted_load)
    # Replace only this registry definition with an equivalent counted copy.
    definition = registry.definition_for(request)
    registry._definitions[("ema", "v1")] = type(definition)(**{field: getattr(definition, field) for field in definition.__dataclass_fields__} | {"compute": counted_compute})
    cold = registry.compute(request, snapshot, ["AAA", "MISSING"], cache=cache)
    assert root.exists() and loads == 1 and computes == 1 and cold.metadata["cache"]["hit"] is False
    warm = registry.compute(request, snapshot, ["AAA", "MISSING"], cache=cache)
    assert loads == 1 and computes == 1 and warm.metadata["cache"]["hit"] is True
    pd.testing.assert_frame_equal(cold.frame_for("AAA"), warm.frame_for("AAA"))
    assert warm.available_symbols == ("AAA",) and warm.missing_symbols == ("MISSING",)


def test_cache_keys_and_round_trip_fidelity(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path); cache = PreparedFeatureCache(tmp_path / "cache"); registry = _registry()
    result = registry.compute(FeatureRequest("donchian", "v1", {"period": 5}), snapshot, ["AAA", "BBB"], start_date="2020-01-10", through_date="2020-01-31", cache=cache)
    uncached = registry.compute(FeatureRequest("donchian", "v1", {"period": 5}), snapshot, ["AAA", "BBB"], start_date="2020-01-10", through_date="2020-01-31")
    for symbol in result.available_symbols:
        pd.testing.assert_frame_equal(result.frame_for(symbol), uncached.frame_for(symbol), check_dtype=True)
    first = result.computation_identity.sha256
    assert registry.compute(FeatureRequest("donchian", "v1", {"period": 6}), snapshot, ["AAA", "BBB"], start_date="2020-01-10", through_date="2020-01-31", cache=cache).computation_identity.sha256 != first
    assert registry.compute(FeatureRequest("donchian", "v1", {"period": 5}), snapshot, ["AAA"], start_date="2020-01-10", through_date="2020-01-31", cache=cache).computation_identity.sha256 != first
    assert registry.compute(FeatureRequest("donchian", "v1", {"period": 5}), snapshot, ["AAA", "BBB"], start_date="2020-01-11", through_date="2020-01-31", cache=cache).computation_identity.sha256 != first


def test_corruption_is_loud_and_defensive_copy_is_preserved(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path); cache = PreparedFeatureCache(tmp_path / "cache"); registry = _registry(); request = FeatureRequest("atr", "v1", {"period": 14})
    result = registry.compute(request, snapshot, ["AAA"], cache=cache)
    changed = result.frame_for("AAA"); changed.loc[:, "ATR14"] = -1
    assert not (registry.compute(request, snapshot, ["AAA"], cache=cache).frame_for("AAA")["ATR14"] == -1).all()
    entry = cache._entry_path(result.computation_identity)
    (entry / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(CacheCorruptionError):
        cache.get(result.computation_identity)


def test_unsupported_object_data_temp_entries_and_import_have_no_side_effect(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path); cache = PreparedFeatureCache(tmp_path / "cache"); registry = _registry(); request = FeatureRequest("ema", "v1", {"period": 10})
    result = registry.compute(request, snapshot, ["AAA"])
    frame = result._frames["AAA"].copy(); frame["bad"] = "object"
    from quantlab.features.contracts import FeatureResult
    bad = FeatureResult(result.computation_identity, result.metadata, result.available_symbols, result.missing_symbols, {"AAA": frame})
    with pytest.raises(TypeError, match="object"):
        cache.put(bad)
    assert not cache.cache_root.exists()
    incomplete = cache._entry_path(result.computation_identity).parent / ".partial.tmp"; incomplete.mkdir(parents=True)
    assert cache.get(result.computation_identity) is None
    missing = tmp_path / "not-created"
    completed = subprocess.run([sys.executable, "-c", "import quantlab.features.cache"], cwd=Path(__file__).resolve().parents[1], env=dict(os.environ, MARKET_DATABASE_PATH=str(missing)), capture_output=True, text=True)
    assert completed.returncode == 0 and not missing.exists()


def test_same_key_writer_race_publishes_one_valid_entry(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path); cache = PreparedFeatureCache(tmp_path / "cache"); registry = _registry(); result = registry.compute(FeatureRequest("ema", "v1", {"period": 10}), snapshot, ["AAA"])
    barrier = Barrier(2); outcomes = []
    def writer():
        barrier.wait(); outcomes.append(cache.put(result))
    first, second = Thread(target=writer), Thread(target=writer); first.start(); second.start(); first.join(); second.join()
    assert len(outcomes) == 2 and cache.get(result.computation_identity) is not None
    assert sum(not item.hit for item in outcomes) == 1
