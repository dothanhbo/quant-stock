from __future__ import annotations

from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from quantlab.adapters import prepare_historical_paper_state_subset
from quantlab.catalog.market_data_snapshot import build_market_data_snapshot
from quantlab.features import PointInTimeUniverseContext, PreparedFeatureCache
from quantlab.features.builtins import _classify_paper_v2_state
from strategy.paper_v2_gate import classify_state


def _snapshot(tmp_path: Path):
    path = tmp_path / "market.db"; dates = pd.date_range("2020-01-01", periods=230, freq="B"); rows = []
    for symbol, offset in (("AAA", 10.0), ("BBB", 30.0), ("VNINDEX", 1000.0)):
        for index, day in enumerate(dates):
            close = offset + index * (.3 if symbol != "VNINDEX" else 2)
            rows.append((symbol, day.date().isoformat(), close - .1, close + .4, close - .5, close, index + 1))
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE prices (symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER)")
        connection.executemany("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return build_market_data_snapshot(path), dates


@pytest.mark.parametrize(
    ("signal", "expected"),
    [
        ({"regime": "BEAR", "breadth_ema50_pct": 100, "breadth_ema50_change_10d": 1}, "BEAR"),
        ({"regime": "BULL", "breadth_ema50_pct": 49.9, "breadth_ema50_change_10d": -.1}, "DIVERGENT_BULL"),
        ({"regime": "BULL", "breadth_ema50_pct": 70, "breadth_ema50_change_10d": 0}, "HEALTHY_BULL"),
        ({"regime": "BULL", "breadth_ema50_pct": float("nan"), "breadth_ema50_change_10d": 0}, "FRAGILE_BULL"),
        ({"regime": "SIDEWAY", "breadth_ema50_pct": 60, "breadth_ema50_change_10d": .1}, "RECOVERY"),
        ({"regime": "UNKNOWN", "breadth_ema50_pct": 100, "breadth_ema50_change_10d": 1}, "NEUTRAL"),
    ],
)
def test_authoritative_paper_v2_state_boundaries(signal: dict[str, float | str], expected: str) -> None:
    assert classify_state(signal) == expected == _classify_paper_v2_state(signal)


def test_v6_attaches_authoritative_state_and_npz_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot, dates = _snapshot(tmp_path); sessions = tuple(day.date().isoformat() for day in dates)
    context = PointInTimeUniverseContext.static(["AAA", "BBB"], sessions); start, end = sessions[190], sessions[-1]
    cache = PreparedFeatureCache(tmp_path / "cache", codec="npz_numeric_v1"); loads = 0; original = type(snapshot).load_ohlcv
    def counted(self, *args, **kwargs):
        nonlocal loads
        loads += 1; return original(self, *args, **kwargs)
    monkeypatch.setattr(type(snapshot), "load_ohlcv", counted)
    cold = prepare_historical_paper_state_subset(snapshot, ["AAA"], benchmark_symbol="VNINDEX", universe_context=context, start_date=start, through_date=end, cache=cache)
    frame = cold.frame_for("AAA")
    expected = [classify_state({"regime": row.Market_Regime, "breadth_ema50_pct": row.breadth_ema50_pct, "breadth_ema50_change_10d": row.breadth_ema50_change_10d}) for row in frame[["Market_Regime", "breadth_ema50_pct", "breadth_ema50_change_10d"]].itertuples(index=False)]
    assert list(frame["paper_v2_state"]) == expected and loads == 1
    warm = prepare_historical_paper_state_subset(snapshot, ["AAA"], benchmark_symbol="VNINDEX", universe_context=context, start_date=start, through_date=end, cache=cache)
    assert loads == 1 and cold.metadata["cache"]["hit"] is False and warm.metadata["cache"]["hit"] is True
    pd.testing.assert_frame_equal(frame, warm.frame_for("AAA"), check_dtype=True)
    assert "q70_quality_score" in cold.metadata["excluded_feature_groups"]


def test_v6_membership_mutation_changes_identity(tmp_path: Path) -> None:
    snapshot, dates = _snapshot(tmp_path); sessions = tuple(day.date().isoformat() for day in dates); start, end = sessions[190], sessions[-1]
    first_context = PointInTimeUniverseContext.static(["AAA", "BBB"], sessions)
    second_context = PointInTimeUniverseContext.from_memberships(universe_mode="explicit_static_symbols", memberships={**{day: ("AAA", "BBB") for day in sessions}, sessions[210]: ("AAA",)})
    first = prepare_historical_paper_state_subset(snapshot, ["AAA"], benchmark_symbol="VNINDEX", universe_context=first_context, start_date=start, through_date=end)
    second = prepare_historical_paper_state_subset(snapshot, ["AAA"], benchmark_symbol="VNINDEX", universe_context=second_context, start_date=start, through_date=end)
    assert first.computation_identity.sha256 != second.computation_identity.sha256
