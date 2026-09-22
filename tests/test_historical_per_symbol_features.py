from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from quantlab.adapters import prepare_historical_core_subset, prepare_historical_per_symbol_subset
from quantlab.catalog.market_data_snapshot import build_market_data_snapshot
from quantlab.features import FeatureRegistry, FeatureRequest, PreparedFeatureCache, builtin_definitions
from strategy.indicators import add_indicators


_COLUMNS = (
    "time", "open", "high", "low", "close", "volume", "EMA10", "EMA20", "EMA50", "ATR14",
    "Previous_20D_High", "Breakout_20D", "RSI", "Vol_MA20", "Vol_Ratio", "Previous_5D_Max_Volume",
    "Volume_Breakout_5D", "ATR_Percent", "ADX14", "EMA20_Rising", "Recent_Breakout_10D",
    "Touched_EMA10", "Reclaimed_EMA10", "Distance_EMA20_Pct", "Return_3D_Pct", "Body_Ratio",
    "Green_Candle", "Close_Upper_Half",
)


def _snapshot(tmp_path: Path, *, flat: bool = False):
    path = tmp_path / "market.db"; dates = pd.date_range("2021-01-01", periods=80, freq="B")
    rows = []
    for symbol, offset in (("AAA", 0.0), ("BBB", 30.0)):
        for ordinal, date in enumerate(dates):
            close = 10.0 + offset if flat else 10.0 + offset + ordinal * 0.25
            rows.append((symbol, date.date().isoformat(), close - .2, close + .7, close - .8, close, 0 if flat else ordinal + 1))
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE prices (symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER)")
        connection.executemany("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return build_market_data_snapshot(path), dates


def _authority(snapshot, symbol: str, start: str, through: str) -> pd.DataFrame:
    raw = snapshot.load_ohlcv([symbol], through_date=through).frame_for(symbol)
    prepared = add_indicators(raw)
    return prepared.loc[prepared["time"] >= pd.Timestamp(start), list(_COLUMNS)].reset_index(drop=True)


def _counted_registry():
    counts: dict[str, int] = {}; definitions = []
    for definition in builtin_definitions():
        original = definition.compute
        assert original is not None

        def counted(*args, _name=definition.name, _original=original, **kwargs):
            counts[_name] = counts.get(_name, 0) + 1
            return _original(*args, **kwargs)

        definitions.append(replace(definition, compute=counted))
    return FeatureRegistry(definitions), counts


def test_v2_matches_authoritative_per_symbol_pre_global_boundary(tmp_path: Path) -> None:
    snapshot, dates = _snapshot(tmp_path); start, through = dates[55].date().isoformat(), dates[74].date().isoformat()
    bundle = prepare_historical_per_symbol_subset(snapshot, ["bbb", "AAA", "MISSING"], start_date=start, through_date=through)
    assert bundle.available_symbols == ("AAA", "BBB") and bundle.missing_symbols == ("MISSING",)
    assert tuple(bundle.frame_for("AAA").columns) == _COLUMNS
    for symbol in bundle.available_symbols:
        pd.testing.assert_frame_equal(bundle.frame_for(symbol), _authority(snapshot, symbol, start, through), check_dtype=True)
    assert bundle.metadata["resolved_warmup_sessions"] == 27
    assert "relative_strength_vs_vnindex" in bundle.metadata["excluded_feature_groups"]


def test_rsi_adx_volume_and_price_context_preserve_authoritative_edge_semantics(tmp_path: Path) -> None:
    snapshot, dates = _snapshot(tmp_path, flat=True); registry = FeatureRegistry(builtin_definitions())
    raw = snapshot.load_ohlcv(["AAA"]).frame_for("AAA"); expected = add_indicators(raw)
    for request, column in (
        (FeatureRequest("rsi", "v1", {"period": 14}), "RSI14"),
        (FeatureRequest("adx", "v1", {"period": 14}), "ADX14"),
        (FeatureRequest("volume_context", "v1", {"period": 20}), "Vol_Ratio"),
    ):
        actual = registry.compute(request, snapshot, ["AAA"]).frame_for("AAA")
        source = "RSI" if column == "RSI14" else column
        pd.testing.assert_series_equal(actual[column], expected[source], check_names=False)
    frame = prepare_historical_per_symbol_subset(snapshot, ["AAA"], start_date=dates[55].date().isoformat(), through_date=dates[70].date().isoformat()).frame_for("AAA")
    assert frame["RSI"].iloc[0] == 0.0 and frame["Vol_Ratio"].isna().all()
    assert frame["ADX14"].isna().all()


def test_v2_is_causal_and_v1_remains_explicitly_distinct(tmp_path: Path) -> None:
    snapshot, dates = _snapshot(tmp_path); start, early, late = dates[55].date().isoformat(), dates[65].date().isoformat(), dates[75].date().isoformat()
    first = prepare_historical_per_symbol_subset(snapshot, ["AAA"], start_date=start, through_date=early)
    second = prepare_historical_per_symbol_subset(snapshot, ["AAA"], start_date=start, through_date=late)
    pd.testing.assert_frame_equal(first.frame_for("AAA"), second.frame_for("AAA").loc[lambda value: value["time"] <= pd.Timestamp(early)].reset_index(drop=True))
    core = prepare_historical_core_subset(snapshot, ["AAA"], start_date=start, through_date=early)
    assert core.computation_identity.feature_identity.request.name == "historical_candidate_core_subset"
    assert first.computation_identity.feature_identity.request.name == "historical_candidate_per_symbol_subset"
    assert core.computation_identity.sha256 != first.computation_identity.sha256


def test_v2_one_load_one_graph_and_npz_warm_hit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot, dates = _snapshot(tmp_path); start, through = dates[55].date().isoformat(), dates[74].date().isoformat()
    cache = PreparedFeatureCache(tmp_path / "cache", codec="npz_numeric_v1"); registry, counts = _counted_registry(); loads = 0
    original = type(snapshot).load_ohlcv

    def counted_load(self, *args, **kwargs):
        nonlocal loads
        loads += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(type(snapshot), "load_ohlcv", counted_load)
    cold = prepare_historical_per_symbol_subset(snapshot, ["AAA", "BBB"], start_date=start, through_date=through, cache=cache, registry=registry)
    assert loads == 1 and counts == {
        "ema": 3, "atr": 1, "donchian": 1, "historical_candidate_core_subset": 1,
        "rsi": 1, "adx": 1, "volume_context": 1, "atr_percent": 1, "price_context": 1,
        "historical_candidate_per_symbol_subset": 1,
    }
    assert cold.metadata["cache"]["hit"] is False
    warm_registry, warm_counts = _counted_registry()
    warm = prepare_historical_per_symbol_subset(snapshot, ["BBB", "AAA"], start_date=start, through_date=through, cache=cache, registry=warm_registry)
    assert loads == 1 and warm_counts == {} and warm.metadata["cache"]["hit"] is True
    pd.testing.assert_frame_equal(cold.frame_for("AAA"), warm.frame_for("AAA"), check_dtype=True)
    assert len(list((tmp_path / "cache" / "npz_numeric_v1").rglob("data.npz"))) == 1


def test_explicit_through_date_and_defensive_copies(tmp_path: Path) -> None:
    snapshot, dates = _snapshot(tmp_path); start, through = dates[55].date().isoformat(), dates[74].date().isoformat()
    bundle = prepare_historical_per_symbol_subset(snapshot, ["AAA"], start_date=start, through_date=through)
    altered = bundle.frame_for("AAA"); altered.loc[:, "ADX14"] = -1
    assert not (bundle.frame_for("AAA")["ADX14"] == -1).all()
    with pytest.raises(ValueError, match="through_date"):
        prepare_historical_per_symbol_subset(snapshot, ["AAA"], start_date=start, through_date="")


def test_snapshot_normalized_duplicate_and_missing_price_rows_match_authority(tmp_path: Path) -> None:
    snapshot, dates = _snapshot(tmp_path)
    with sqlite3.connect(snapshot.canonical_db_path) as connection:
        # Snapshot construction is read-only; this fixture mutation happens
        # before constructing a replacement snapshot and models source data
        # that the snapshot's existing normalization discards/keeps-last.
        connection.execute("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", ("AAA", dates[30].date().isoformat(), 1, None, 1, 1, 1))
        connection.execute("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", (" aaa ", dates[40].date().isoformat(), 99, 100, 98, 99.5, 9))
    normalized = build_market_data_snapshot(snapshot.canonical_db_path)
    start, through = dates[55].date().isoformat(), dates[74].date().isoformat()
    actual = prepare_historical_per_symbol_subset(normalized, ["AAA"], start_date=start, through_date=through).frame_for("AAA")
    expected = _authority(normalized, "AAA", start, through)
    pd.testing.assert_frame_equal(actual, expected, check_dtype=True)
