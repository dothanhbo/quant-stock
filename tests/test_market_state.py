from __future__ import annotations

import pandas as pd
import pytest

from strategy.market_state import _classify, _compute_breadth, _load_prices, get_market_state


def _price_frame(
    *,
    start: str = "2026-01-01",
    periods: int = 60,
    symbol: str = "AAA",
    close: float = 100.0,
) -> pd.DataFrame:
    dates = pd.bdate_range(start=start, periods=periods)
    return pd.DataFrame(
        {
            "symbol": symbol,
            "time": dates,
            "close": close,
        }
    )


def test_market_state_mapping_matches_research_policy():
    assert _classify("BEAR", 80.0, 5.0) == "BEAR"
    assert _classify("BULL", 45.0, -2.0) == "DIVERGENT_BULL"
    assert _classify("BULL", 75.0, 0.0) == "HEALTHY_BULL"
    assert _classify("BULL", 65.0, 1.0) == "FRAGILE_BULL"
    assert _classify("SIDEWAY", 65.0, 1.0) == "RECOVERY"
    assert _classify("SIDEWAY", 65.0, -1.0) == "NEUTRAL"


def test_market_state_normalizes_date_and_excludes_vnindex(monkeypatch):
    import strategy.market_state as module

    monkeypatch.setattr(
        module,
        "_get_market_state_cached",
        lambda date, symbols: {"as_of_date": date, "symbols": symbols},
    )
    result = get_market_state(
        pd.Timestamp("2026-09-04 15:00:00"),
        symbols=["VPB", "GVR", "VNINDEX"],
    )
    assert result["as_of_date"] == "2026-09-04"
    assert result["symbols"] == ("GVR", "VPB")


def test_compute_breadth_requires_50_sessions(monkeypatch):
    import strategy.market_state as module

    short = _price_frame(periods=49)
    monkeypatch.setattr(module, "_load_prices", lambda as_of_date, symbols: short)

    breadth, change_10d, universe = _compute_breadth(
        "2026-03-09",
        ("AAA",),
    )

    assert pd.isna(breadth)
    assert pd.isna(change_10d)
    assert universe == 0


def test_compute_breadth_change_10d_uses_ten_observations(monkeypatch):
    import strategy.market_state as module

    prices = _price_frame(periods=61)
    prices.loc[50:55, "close"] = 200.0
    prices.loc[56:60, "close"] = 50.0
    monkeypatch.setattr(module, "_load_prices", lambda as_of_date, symbols: prices)

    breadth, change_10d, universe = _compute_breadth(
        "2026-03-26",
        ("AAA",),
    )

    assert universe == 1
    assert pd.notna(breadth)
    assert pd.notna(change_10d)

    # Recompute the same breadth series independently and verify the
    # production result is T minus T-10 observations.
    frame = prices.copy()
    frame["time"] = pd.to_datetime(frame["time"])
    frame["close"] = pd.to_numeric(frame["close"])
    frame["ema50"] = frame.groupby("symbol")["close"].transform(
        lambda s: s.ewm(span=50, adjust=False).mean()
    )
    frame["history_n"] = frame.groupby("symbol").cumcount() + 1
    eligible = frame[frame["history_n"] >= 50].copy()
    daily = (
        eligible.assign(above_ema50=eligible["close"] > eligible["ema50"])
        .groupby("time", as_index=False)
        .agg(breadth=("above_ema50", "mean"))
        .sort_values("time")
        .reset_index(drop=True)
    )
    daily["breadth_pct"] = daily["breadth"] * 100.0

    expected = daily.loc[len(daily) - 1, "breadth_pct"] - daily.loc[
        len(daily) - 11, "breadth_pct"
    ]
    assert change_10d == pytest.approx(expected)


def test_compute_breadth_denominator_is_symbols_with_eligible_data(monkeypatch):
    import strategy.market_state as module

    complete = _price_frame(periods=60, symbol="AAA", close=100.0)
    incomplete = _price_frame(periods=49, symbol="BBB", close=200.0)
    prices = pd.concat([complete, incomplete], ignore_index=True)
    monkeypatch.setattr(module, "_load_prices", lambda as_of_date, symbols: prices)

    breadth, change_10d, universe = _compute_breadth(
        "2026-03-25",
        ("AAA", "BBB"),
    )

    assert universe == 1
    assert pd.notna(breadth)
    assert pd.notna(change_10d)
    # BBB has fewer than 50 observations and is therefore excluded from
    # the current breadth denominator.
    assert breadth == pytest.approx(0.0)


def test_load_prices_enforces_as_of_date_cutoff(monkeypatch):
    import strategy.market_state as module

    captured = {}

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    def fake_read_sql(query, connection, params):
        captured["query"] = str(query)
        captured["params"] = params
        return pd.DataFrame(
            {
                "symbol": ["AAA"],
                "time": ["2026-03-25"],
                "close": [100.0],
            }
        )

    monkeypatch.setattr(module, "engine", FakeEngine())
    monkeypatch.setattr(module.pd, "read_sql", fake_read_sql)

    result = _load_prices("2026-03-25", ("AAA",))

    assert not result.empty
    assert "date(time) <= :as_of_date" in captured["query"]
    assert captured["params"]["as_of_date"] == "2026-03-25"
    assert captured["params"]["s0"] == "AAA"
