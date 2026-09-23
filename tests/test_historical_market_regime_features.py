from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from quantlab.adapters import prepare_historical_benchmark_relative_subset, prepare_historical_market_context_subset
from quantlab.catalog.market_data_snapshot import build_market_data_snapshot
from quantlab.features import FeatureRegistry, FeatureRequest, PreparedFeatureCache, builtin_definitions
from strategy.market_regime import prepare_market_regime_history


def _snapshot(tmp_path: Path, *, benchmark_values: list[float] | None = None, missing_benchmark: set[int] = set()):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "market.db"; dates = pd.date_range("2020-01-01", periods=240, freq="B")
    benchmark_values = benchmark_values or [1000 + index * 2 for index in range(len(dates))]
    rows = []
    for symbol, offset in (("AAA", 0.0), ("BBB", 30.0)):
        for index, date in enumerate(dates):
            close = 20 + offset + index * .2
            rows.append((symbol, date.date().isoformat(), close - .2, close + .6, close - .7, close, index + 1))
    for index, date in enumerate(dates):
        if index not in missing_benchmark:
            close = benchmark_values[index]
            rows.append(("VNINDEX", date.date().isoformat(), close - 1, close + 2, close - 2, close, index + 1))
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE prices (symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER)")
        connection.executemany("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return build_market_data_snapshot(path), dates


def _authority(snapshot, through: str) -> pd.DataFrame:
    benchmark = snapshot.load_ohlcv(["VNINDEX"], through_date=through).frame_for("VNINDEX")
    return prepare_market_regime_history(benchmark)


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


@pytest.mark.parametrize(
    ("values", "expected_label"),
    [
        ([1000 + index * 2 for index in range(240)], "BULL"),
        ([1500 - index * 2 for index in range(240)], "BEAR"),
        ([1000.0 for _ in range(240)], "SIDEWAY"),
    ],
)
def test_market_regime_matches_historical_authority_and_all_labels(tmp_path: Path, values: list[float], expected_label: str) -> None:
    snapshot, dates = _snapshot(tmp_path, benchmark_values=values); through = dates[-1].date().isoformat()
    registry = FeatureRegistry(builtin_definitions())
    result = registry.compute(
        FeatureRequest("historical_market_regime", "v1", {"benchmark_symbol": "VNINDEX"}),
        snapshot,
        ["VNINDEX"],
        through_date=through,
        universe_identity="market-test",
    ).frame_for("VNINDEX")
    expected = _authority(snapshot, through)[["time", "Market_Regime"]].reset_index(drop=True)
    pd.testing.assert_frame_equal(result, expected, check_dtype=True)
    assert result["Market_Regime"].iloc[198] == "UNKNOWN" and result["Market_Regime"].iloc[199] == expected_label


def test_v4_exact_date_alignment_and_missing_benchmark_session(tmp_path: Path) -> None:
    snapshot, dates = _snapshot(tmp_path, missing_benchmark={215}); start, through = dates[190].date().isoformat(), dates[-1].date().isoformat()
    bundle = prepare_historical_market_context_subset(snapshot, ["BBB", "AAA", "VNINDEX"], benchmark_symbol="VNINDEX", start_date=start, through_date=through)
    actual = bundle.frame_for("AAA")
    authority = _authority(snapshot, through)[["time", "Market_Regime"]]
    stock = snapshot.load_ohlcv(["AAA"], through_date=through).frame_for("AAA")
    expected = stock.merge(authority, on="time", how="left")
    expected = expected.loc[expected["time"] >= pd.Timestamp(start), "Market_Regime"].reset_index(drop=True)
    pd.testing.assert_series_equal(actual["Market_Regime"], expected, check_names=False, check_dtype=True)
    missing = actual.loc[actual["time"] == pd.Timestamp(dates[215]), "Market_Regime"]
    assert len(missing) == 1 and pd.isna(missing.iloc[0])
    assert bundle.available_symbols == ("AAA", "BBB") and "VNINDEX" not in bundle.available_symbols
    assert bundle.metadata["market_context_identity"] == "historical_market_reference:VNINDEX"


def test_v4_identity_context_causality_and_v3_separation(tmp_path: Path) -> None:
    snapshot, dates = _snapshot(tmp_path); start, early, late = dates[190].date().isoformat(), dates[220].date().isoformat(), dates[-1].date().isoformat()
    first = prepare_historical_market_context_subset(snapshot, ["AAA"], benchmark_symbol="VNINDEX", start_date=start, through_date=early, market_context_identity="market-a")
    second = prepare_historical_market_context_subset(snapshot, ["AAA"], benchmark_symbol="VNINDEX", start_date=start, through_date=late, market_context_identity="market-a")
    changed_context = prepare_historical_market_context_subset(snapshot, ["AAA"], benchmark_symbol="VNINDEX", start_date=start, through_date=early, market_context_identity="market-b")
    pd.testing.assert_frame_equal(first.frame_for("AAA"), second.frame_for("AAA").loc[lambda frame: frame["time"] <= pd.Timestamp(early)].reset_index(drop=True))
    assert first.computation_identity.sha256 != changed_context.computation_identity.sha256
    v3 = prepare_historical_benchmark_relative_subset(snapshot, ["AAA"], benchmark_symbol="VNINDEX", start_date=start, through_date=early)
    assert v3.computation_identity.sha256 != first.computation_identity.sha256


def test_v4_one_union_load_market_node_once_and_npz_warm_hit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot, dates = _snapshot(tmp_path); start, through = dates[190].date().isoformat(), dates[-1].date().isoformat()
    cache = PreparedFeatureCache(tmp_path / "cache", codec="npz_numeric_v1"); registry, counts = _counted_registry(); calls = []
    original = type(snapshot).load_ohlcv

    def counted_load(self, symbols, *args, **kwargs):
        calls.append(tuple(symbols)); return original(self, symbols, *args, **kwargs)

    monkeypatch.setattr(type(snapshot), "load_ohlcv", counted_load)
    cold = prepare_historical_market_context_subset(snapshot, ["AAA", "BBB"], benchmark_symbol="VNINDEX", start_date=start, through_date=through, cache=cache, registry=registry)
    assert calls == [("AAA", "BBB", "VNINDEX")]
    assert counts["historical_market_regime"] == 1 and counts["historical_candidate_market_context_subset"] == 1
    assert cold.metadata["cache"]["hit"] is False
    warm_registry, warm_counts = _counted_registry()
    warm = prepare_historical_market_context_subset(snapshot, ["BBB", "AAA"], benchmark_symbol="VNINDEX", start_date=start, through_date=through, cache=cache, registry=warm_registry)
    assert len(calls) == 1 and warm_counts == {} and warm.metadata["cache"]["hit"] is True
    pd.testing.assert_frame_equal(cold.frame_for("AAA"), warm.frame_for("AAA"), check_dtype=True)


def test_market_scope_requires_context_and_missing_benchmark_fails(tmp_path: Path) -> None:
    snapshot, dates = _snapshot(tmp_path); registry = FeatureRegistry(builtin_definitions())
    with pytest.raises(ValueError, match="universe_identity"):
        registry.compute(FeatureRequest("historical_market_regime", "v1", {"benchmark_symbol": "VNINDEX"}), snapshot, ["VNINDEX"])
    absent, _ = _snapshot(tmp_path / "absent", benchmark_values=[], missing_benchmark=set(range(240)))
    with pytest.raises(ValueError, match="benchmark series is unavailable"):
        prepare_historical_market_context_subset(absent, ["AAA"], benchmark_symbol="VNINDEX", start_date=dates[190].date().isoformat(), through_date=dates[-1].date().isoformat())
