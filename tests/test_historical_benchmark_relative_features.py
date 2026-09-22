from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
import pytest

from backtesting.prepared_data import add_relative_strength_columns
from quantlab.adapters import prepare_historical_benchmark_relative_subset, prepare_historical_per_symbol_subset
from quantlab.catalog.market_data_snapshot import build_market_data_snapshot
from quantlab.features import FeatureRegistry, PreparedFeatureCache, builtin_definitions


_RELATIVE_COLUMNS = ("Stock_Return_20D", "Index_Return_20D", "Relative_Strength_20D")


def _snapshot(tmp_path: Path, *, include_benchmark: bool = True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "market.db"; dates = pd.date_range("2021-01-01", periods=70, freq="B"); rows = []
    for symbol, offset, skipped in (("AAA", 0.0, {30}), ("BBB", 20.0, set())):
        for ordinal, date in enumerate(dates):
            if ordinal not in skipped:
                close = 10 + offset + ordinal * .3
                rows.append((symbol, date.date().isoformat(), close - .1, close + .4, close - .5, close, ordinal + 1))
    if include_benchmark:
        for ordinal, date in enumerate(dates):
            if ordinal != 35:
                close = 1000 + ordinal * 2
                rows.append(("VNINDEX", date.date().isoformat(), close - 1, close + 3, close - 2, close, ordinal + 1))
        # Snapshot's canonical keep-last normalization must be the reference.
        rows.append((" vnindex ", dates[40].date().isoformat(), 2000, 2003, 1999, 2001, 1))
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE prices (symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER)")
        connection.executemany("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return build_market_data_snapshot(path), dates


def _authority(snapshot, symbol: str, benchmark: str, through: str) -> pd.DataFrame:
    stock = snapshot.load_ohlcv([symbol], through_date=through).frame_for(symbol)
    reference = snapshot.load_ohlcv([benchmark], through_date=through).frame_for(benchmark)
    return add_relative_strength_columns(stock, reference)


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


def test_v3_matches_authoritative_left_merge_return_and_relative_strength(tmp_path: Path) -> None:
    snapshot, dates = _snapshot(tmp_path); start, through = dates[0].date().isoformat(), dates[65].date().isoformat()
    bundle = prepare_historical_benchmark_relative_subset(snapshot, ["AAA", "VNINDEX"], benchmark_symbol="vnindex", start_date=start, through_date=through)
    assert bundle.available_symbols == ("AAA",) and bundle.missing_symbols == ()
    actual = bundle.frame_for("AAA")
    expected = _authority(snapshot, "AAA", "VNINDEX", through)
    expected = expected.loc[expected["time"] >= pd.Timestamp(start), list(_RELATIVE_COLUMNS)].reset_index(drop=True)
    pd.testing.assert_frame_equal(actual.loc[:, _RELATIVE_COLUMNS], expected, check_dtype=True)
    # VNINDEX's missing session at index 35 remains an exact-date hole: no fill.
    row = actual.loc[actual["time"] == pd.Timestamp(dates[35]), "Index_Return_20D"]
    assert len(row) == 1 and pd.isna(row.iloc[0])
    assert bundle.metadata["benchmark_symbol"] == "VNINDEX" and bundle.metadata["primary_symbols"] == ("AAA",)


def test_v3_identity_roles_and_missing_benchmark_errors_are_explicit(tmp_path: Path) -> None:
    snapshot, dates = _snapshot(tmp_path); start, through = dates[30].date().isoformat(), dates[65].date().isoformat()
    first = prepare_historical_benchmark_relative_subset(snapshot, ["AAA"], benchmark_symbol="VNINDEX", start_date=start, through_date=through)
    second = prepare_historical_benchmark_relative_subset(snapshot, ["AAA", "BBB"], benchmark_symbol="VNINDEX", start_date=start, through_date=through)
    changed_reference = prepare_historical_benchmark_relative_subset(snapshot, ["AAA"], benchmark_symbol="BBB", start_date=start, through_date=through)
    assert first.computation_identity.sha256 != second.computation_identity.sha256
    assert first.computation_identity.sha256 != changed_reference.computation_identity.sha256
    assert "VNINDEX" not in first.available_symbols and "BBB" not in changed_reference.available_symbols
    with pytest.raises(ValueError, match="primary equity"):
        prepare_historical_benchmark_relative_subset(snapshot, ["VNINDEX"], benchmark_symbol="VNINDEX", start_date=start, through_date=through)
    missing, _ = _snapshot(tmp_path / "missing", include_benchmark=False)
    with pytest.raises(ValueError, match="benchmark series is unavailable"):
        prepare_historical_benchmark_relative_subset(missing, ["AAA"], benchmark_symbol="VNINDEX", start_date=start, through_date=through)


def test_v3_one_union_load_and_explicit_npz_warm_hit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot, dates = _snapshot(tmp_path); start, through = dates[35].date().isoformat(), dates[65].date().isoformat()
    cache = PreparedFeatureCache(tmp_path / "cache", codec="npz_numeric_v1"); registry, counts = _counted_registry(); calls = []
    original = type(snapshot).load_ohlcv

    def counted_load(self, symbols, *args, **kwargs):
        calls.append(tuple(symbols))
        return original(self, symbols, *args, **kwargs)

    monkeypatch.setattr(type(snapshot), "load_ohlcv", counted_load)
    cold = prepare_historical_benchmark_relative_subset(snapshot, ["BBB", "AAA"], benchmark_symbol="VNINDEX", start_date=start, through_date=through, cache=cache, registry=registry)
    assert calls == [("AAA", "BBB", "VNINDEX")]
    assert counts == {
        "ema": 3, "atr": 1, "donchian": 1, "historical_candidate_core_subset": 1,
        "rsi": 1, "adx": 1, "volume_context": 1, "atr_percent": 1, "price_context": 1,
        "historical_candidate_per_symbol_subset": 1, "benchmark_relative_context": 1,
        "historical_candidate_benchmark_relative_subset": 1,
    }
    assert cold.metadata["cache"]["hit"] is False
    warm_registry, warm_counts = _counted_registry()
    warm = prepare_historical_benchmark_relative_subset(snapshot, ["AAA", "BBB"], benchmark_symbol="VNINDEX", start_date=start, through_date=through, cache=cache, registry=warm_registry)
    assert len(calls) == 1 and warm_counts == {} and warm.metadata["cache"]["hit"] is True
    assert warm.available_symbols == ("AAA", "BBB") and "VNINDEX" not in warm.available_symbols
    pd.testing.assert_frame_equal(cold.frame_for("AAA"), warm.frame_for("AAA"), check_dtype=True)


def test_v1_v2_remain_separate_and_future_rows_do_not_change_earlier_relative_values(tmp_path: Path) -> None:
    snapshot, dates = _snapshot(tmp_path); start, early, late = dates[35].date().isoformat(), dates[55].date().isoformat(), dates[68].date().isoformat()
    early_bundle = prepare_historical_benchmark_relative_subset(snapshot, ["AAA"], benchmark_symbol="VNINDEX", start_date=start, through_date=early)
    late_bundle = prepare_historical_benchmark_relative_subset(snapshot, ["AAA"], benchmark_symbol="VNINDEX", start_date=start, through_date=late)
    pd.testing.assert_frame_equal(early_bundle.frame_for("AAA"), late_bundle.frame_for("AAA").loc[lambda frame: frame["time"] <= pd.Timestamp(early)].reset_index(drop=True))
    v2 = prepare_historical_per_symbol_subset(snapshot, ["AAA"], start_date=start, through_date=early)
    assert v2.computation_identity.sha256 != early_bundle.computation_identity.sha256


def test_zero_prior_close_uses_authoritative_nan_inf_semantics(tmp_path: Path) -> None:
    snapshot, dates = _snapshot(tmp_path); start, through = dates[0].date().isoformat(), dates[50].date().isoformat()
    with sqlite3.connect(snapshot.canonical_db_path) as connection:
        connection.execute("UPDATE prices SET close = 0 WHERE symbol = 'AAA' AND time = ?", (dates[5].date().isoformat(),))
    refreshed = build_market_data_snapshot(snapshot.canonical_db_path)
    actual = prepare_historical_benchmark_relative_subset(refreshed, ["AAA"], benchmark_symbol="VNINDEX", start_date=start, through_date=through).frame_for("AAA")
    expected = _authority(refreshed, "AAA", "VNINDEX", through)
    np.testing.assert_equal(actual["Stock_Return_20D"].to_numpy(), expected["Stock_Return_20D"].to_numpy())
