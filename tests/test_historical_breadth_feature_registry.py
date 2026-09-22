from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from core.database_coverage import CoverageUniverseIndex
from core.historical_breadth import build_historical_breadth_index
from quantlab.adapters import prepare_historical_breadth_context_subset
from quantlab.catalog.market_data_snapshot import build_market_data_snapshot
from quantlab.features import FeatureRegistry, PointInTimeUniverseContext, PreparedFeatureCache, builtin_definitions


def _snapshot(tmp_path: Path):
    path = tmp_path / "market.db"; dates = [(date(2020, 1, 1) + timedelta(days=index)).isoformat() for index in range(230)]; rows = []
    for symbol, offset in (("AAA", 0.0), ("BBB", 20.0), ("VNINDEX", 1000.0)):
        for index, day in enumerate(dates):
            close = offset + 10 + (index * .3 if symbol == "AAA" else index * .1)
            rows.append((symbol, day, close - .2, close + .5, close - .7, close, index + 1))
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE prices (symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER)")
        connection.executemany("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return build_market_data_snapshot(path), dates


def _context(dates: list[str]) -> PointInTimeUniverseContext:
    return PointInTimeUniverseContext.static(["BBB", "AAA", "VNINDEX"], dates)


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


def test_static_context_identity_normalization_and_historical_breadth_parity(tmp_path: Path) -> None:
    snapshot, dates = _snapshot(tmp_path); context = _context(dates); start, end = dates[190], dates[-1]
    reordered = PointInTimeUniverseContext.static(["AAA", "VNINDEX", "BBB"], reversed(dates))
    changed = PointInTimeUniverseContext.from_memberships(universe_mode="explicit_static_symbols", memberships={**{day: ("AAA", "BBB") for day in dates}, dates[200]: ("AAA",)})
    assert context.membership_identity == reordered.membership_identity != changed.membership_identity
    assert "VNINDEX" not in context.candidate_symbols
    bundle = prepare_historical_breadth_context_subset(snapshot, ["AAA"], benchmark_symbol="VNINDEX", universe_context=context, start_date=start, through_date=end)
    authority = build_historical_breadth_index(start, end, universe_mode="current_vn100", symbols=["AAA", "BBB"], database_path=snapshot.canonical_db_path)
    frame = bundle.frame_for("AAA")
    for day in authority.session_dates:
        row = frame.loc[frame["time"] == pd.Timestamp(day)].iloc[0]
        fields = authority.signal_fields_as_of(day)
        assert row["breadth_ema50_pct"] == pytest.approx(fields["breadth_ema50_pct"])
        assert row["breadth_ema50_change_10d"] == pytest.approx(fields["breadth_ema50_change_10d"], nan_ok=True)
    assert bundle.metadata["breadth_membership_identity"] == context.membership_identity


def test_database_coverage_context_and_future_membership_do_not_change_earlier_values(tmp_path: Path) -> None:
    snapshot, dates = _snapshot(tmp_path); start, early, end = dates[190], dates[210], dates[-1]
    members = {day: frozenset({"AAA"}) for day in dates}
    members[dates[211]] = frozenset({"AAA", "BBB"})
    coverage = CoverageUniverseIndex(dates[0], end, dates[0], end, 50, 5, tuple(dates), ("AAA", "BBB"), {day: len(values) for day, values in members.items()}, members)
    context = PointInTimeUniverseContext.from_coverage_index(coverage)
    first = prepare_historical_breadth_context_subset(snapshot, ["AAA"], benchmark_symbol="VNINDEX", universe_context=context, start_date=start, through_date=early)
    second = prepare_historical_breadth_context_subset(snapshot, ["AAA"], benchmark_symbol="VNINDEX", universe_context=context, start_date=start, through_date=end)
    pd.testing.assert_frame_equal(first.frame_for("AAA"), second.frame_for("AAA").loc[lambda frame: frame["time"] <= pd.Timestamp(early)].reset_index(drop=True))
    assert context.universe_mode == "database_coverage"
    assert "historical VN100" in str(context.metadata["limitation"])


def test_v5_one_union_load_breadth_once_and_npz_warm_hit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot, dates = _snapshot(tmp_path); context = _context(dates); start, end = dates[190], dates[-1]
    cache = PreparedFeatureCache(tmp_path / "cache", codec="npz_numeric_v1"); registry, counts = _counted_registry(); calls = []
    original = type(snapshot).load_ohlcv
    def counted_load(self, symbols, *args, **kwargs):
        calls.append(tuple(symbols)); return original(self, symbols, *args, **kwargs)
    monkeypatch.setattr(type(snapshot), "load_ohlcv", counted_load)
    cold = prepare_historical_breadth_context_subset(snapshot, ["AAA"], benchmark_symbol="VNINDEX", universe_context=context, start_date=start, through_date=end, cache=cache, registry=registry)
    assert calls == [("AAA", "BBB", "VNINDEX")]
    assert counts["historical_breadth_context"] == 1 and counts["historical_market_regime"] == 1 and counts["historical_candidate_breadth_context_subset"] == 1
    warm_registry, warm_counts = _counted_registry()
    warm = prepare_historical_breadth_context_subset(snapshot, ["AAA"], benchmark_symbol="VNINDEX", universe_context=context, start_date=start, through_date=end, cache=cache, registry=warm_registry)
    assert len(calls) == 1 and warm_counts == {} and cold.metadata["cache"]["hit"] is False and warm.metadata["cache"]["hit"] is True
    pd.testing.assert_frame_equal(cold.frame_for("AAA"), warm.frame_for("AAA"), check_dtype=True)


def test_context_must_match_execution_identity(tmp_path: Path) -> None:
    snapshot, dates = _snapshot(tmp_path); context = _context(dates)
    with pytest.raises(ValueError, match="market_context_identity"):
        prepare_historical_breadth_context_subset(snapshot, ["AAA"], benchmark_symbol="VNINDEX", universe_context=context, start_date=dates[190], through_date=dates[-1], market_context_identity="other")
