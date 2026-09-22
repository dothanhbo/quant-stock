from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pandas as pd
import pytest

from quantlab.adapters.historical_prepared_features import prepare_historical_core_subset
from quantlab.catalog.market_data_snapshot import build_market_data_snapshot
from quantlab.features import FeatureRegistry, PreparedFeatureCache, builtin_definitions
from strategy.indicators import add_indicators


_COLUMNS = (
    "time", "open", "high", "low", "close", "volume", "EMA10", "EMA20",
    "EMA50", "ATR14", "Previous_20D_High", "Breakout_20D",
)


def _snapshot(tmp_path: Path):
    path = tmp_path / "market.db"
    dates = pd.date_range("2020-01-01", periods=75, freq="B")
    rows: list[tuple[object, ...]] = []
    for symbol, offset in (("AAA", 0.0), ("BBB", 40.0)):
        for ordinal, date in enumerate(dates):
            close = 10.0 + offset + ordinal * 0.3
            rows.append((symbol, date.date().isoformat(), close - 0.2, close + 0.8, close - 0.7, close, ordinal + 1))
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE prices (symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER)")
        connection.executemany("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return build_market_data_snapshot(path), dates


def _authority(snapshot, symbol: str, start_date: str, through_date: str) -> pd.DataFrame:
    raw = snapshot.load_ohlcv([symbol], through_date=through_date).frame_for(symbol)
    prepared = add_indicators(raw)
    return prepared.loc[prepared["time"] >= pd.Timestamp(start_date), list(_COLUMNS)].reset_index(drop=True)


def _counted_registry() -> tuple[FeatureRegistry, dict[str, int]]:
    counts: dict[str, int] = {}
    definitions = []
    for definition in builtin_definitions():
        original = definition.compute
        assert original is not None

        def counted(*args, _name=definition.name, _original=original, **kwargs):
            counts[_name] = counts.get(_name, 0) + 1
            return _original(*args, **kwargs)

        definitions.append(replace(definition, compute=counted))
    return FeatureRegistry(definitions), counts


def test_supported_subset_matches_authoritative_pre_drop_indicator_boundary(tmp_path: Path) -> None:
    snapshot, dates = _snapshot(tmp_path)
    start, through = dates[55].date().isoformat(), dates[70].date().isoformat()
    bundle = prepare_historical_core_subset(snapshot, ["bbb", "AAA", "MISSING"], start_date=start, through_date=through)
    assert bundle.available_symbols == ("AAA", "BBB") and bundle.missing_symbols == ("MISSING",)
    assert tuple(bundle.frame_for("AAA").columns) == _COLUMNS
    for symbol in bundle.available_symbols:
        pd.testing.assert_frame_equal(bundle.frame_for(symbol), _authority(snapshot, symbol, start, through), check_dtype=True)


def test_donchian_semantics_warmup_trimming_and_future_row_invariance(tmp_path: Path) -> None:
    snapshot, dates = _snapshot(tmp_path)
    start, early, late = dates[55].date().isoformat(), dates[62].date().isoformat(), dates[72].date().isoformat()
    early_bundle = prepare_historical_core_subset(snapshot, ["AAA"], start_date=start, through_date=early)
    late_bundle = prepare_historical_core_subset(snapshot, ["AAA"], start_date=start, through_date=late)
    early_frame = early_bundle.frame_for("AAA")
    pd.testing.assert_frame_equal(early_frame, late_bundle.frame_for("AAA").loc[lambda frame: frame["time"] <= pd.Timestamp(early)].reset_index(drop=True))
    expected = _authority(snapshot, "AAA", start, early)
    pd.testing.assert_series_equal(early_frame["Previous_20D_High"], expected["Previous_20D_High"], check_names=False)
    pd.testing.assert_series_equal(early_frame["Breakout_20D"], expected["Breakout_20D"], check_names=False)
    assert early_frame["time"].min() == pd.Timestamp(start)


def test_one_load_one_graph_and_explicit_npz_warm_hit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot, dates = _snapshot(tmp_path)
    start, through = dates[55].date().isoformat(), dates[70].date().isoformat()
    cache = PreparedFeatureCache(tmp_path / "cache", codec="npz_numeric_v1")
    registry, counts = _counted_registry()
    loads = 0
    original_load = type(snapshot).load_ohlcv

    def counted_load(self, *args, **kwargs):
        nonlocal loads
        loads += 1
        return original_load(self, *args, **kwargs)

    monkeypatch.setattr(type(snapshot), "load_ohlcv", counted_load)
    cold = prepare_historical_core_subset(snapshot, ["AAA", "BBB"], start_date=start, through_date=through, cache=cache, registry=registry)
    assert loads == 1 and counts == {"ema": 3, "atr": 1, "donchian": 1, "historical_candidate_core_subset": 1}
    assert cold.metadata["cache"]["hit"] is False
    warm_registry, warm_counts = _counted_registry()
    warm = prepare_historical_core_subset(snapshot, ["BBB", "AAA"], start_date=start, through_date=through, cache=cache, registry=warm_registry)
    assert loads == 1 and warm_counts == {} and warm.metadata["cache"]["hit"] is True
    for symbol in cold.available_symbols:
        pd.testing.assert_frame_equal(cold.frame_for(symbol), warm.frame_for(symbol), check_dtype=True)
    assert len(list((tmp_path / "cache" / "npz_numeric_v1").rglob("data.npz"))) == 1


def test_metadata_identity_defensive_copies_and_required_through_date(tmp_path: Path) -> None:
    snapshot, dates = _snapshot(tmp_path)
    start, through = dates[55].date().isoformat(), dates[70].date().isoformat()
    bundle = prepare_historical_core_subset(snapshot, ["AAA"], start_date=start, through_date=through)
    assert bundle.metadata["partial_subset"] is True
    assert bundle.computation_identity.start_date == start and bundle.computation_identity.through_date == through
    assert bundle.metadata["resolved_warmup_sessions"] == 20
    alternate_universe = prepare_historical_core_subset(
        snapshot,
        ["AAA"],
        start_date=start,
        through_date=through,
        universe_identity="coverage-50-5",
    )
    assert alternate_universe.computation_identity.sha256 != bundle.computation_identity.sha256
    changed = bundle.frame_for("AAA"); changed.loc[:, "EMA10"] = -1
    assert not (bundle.frame_for("AAA")["EMA10"] == -1).all()
    with pytest.raises(ValueError, match="through_date"):
        prepare_historical_core_subset(snapshot, ["AAA"], start_date=start, through_date="")


def test_adapter_import_has_no_database_cache_or_network_side_effects(tmp_path: Path) -> None:
    missing = tmp_path / "not-created.db"
    completed = subprocess.run(
        [sys.executable, "-c", "import quantlab.adapters.historical_prepared_features"],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, MARKET_DATABASE_PATH=str(missing)),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0 and not missing.exists()
