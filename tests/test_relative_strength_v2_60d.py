import numpy as np
import pandas as pd

from strategy import relative_strength_v2 as rs


def _prices(values):
    dates = pd.date_range("2026-01-01", periods=len(values), freq="D")
    return pd.DataFrame({"time": dates, "close": values})


def _patch_prices(monkeypatch, data):
    monkeypatch.setattr(
        rs,
        "load_price_data",
        lambda symbol: data.get(symbol, pd.DataFrame()),
    )


def test_default_sector_period_is_60():
    assert rs.DEFAULT_SECTOR_RS_PERIOD == 60


def test_sector_rs_uses_60_observations_when_requested(monkeypatch):
    aaa = [100.0] + [110.0] * 60
    bbb = [100.0] + [105.0] * 60

    _patch_prices(
        monkeypatch,
        {
            "AAA": _prices(aaa),
            "BBB": _prices(bbb),
        },
    )

    result = rs.calculate_sector_relative_strength(
        "AAA",
        {"AAA": "TECH", "BBB": "TECH"},
        universe_symbols=("AAA", "BBB"),
    )

    assert result["available"] is True
    assert result["observations"] == 61
    assert result["sector_eligible_count"] == 1
    assert np.isclose(result["stock_return"], 0.10)
    assert np.isclose(result["benchmark_return"], 0.05)
    assert np.isclose(result["relative_strength"], 0.05)


def test_60d_requires_61_rows(monkeypatch):
    _patch_prices(
        monkeypatch,
        {
            "AAA": _prices([100.0] * 60),
            "BBB": _prices([100.0] * 61),
        },
    )

    result = rs.calculate_sector_relative_strength(
        "AAA",
        {"AAA": "TECH", "BBB": "TECH"},
        universe_symbols=("AAA", "BBB"),
    )

    assert result["available"] is False
    assert result["relative_strength"] is None
    assert result["sector_eligible_count"] == 0


def test_60d_excludes_target_and_outside_universe(monkeypatch):
    aaa = [100.0] + [120.0] * 60
    bbb = [100.0] + [110.0] * 60
    outside = [100.0] + [200.0] * 60

    _patch_prices(
        monkeypatch,
        {
            "AAA": _prices(aaa),
            "BBB": _prices(bbb),
            "OUT": _prices(outside),
        },
    )

    result = rs.calculate_sector_relative_strength(
        "AAA",
        {
            "AAA": "TECH",
            "BBB": "TECH",
            "OUT": "TECH",
        },
        universe_symbols=("AAA", "BBB"),
    )

    assert result["available"] is True
    assert result["sector_universe_size"] == 2
    assert result["sector_eligible_count"] == 1
    assert np.isclose(result["benchmark_return"], 0.10)
    assert np.isclose(result["relative_strength"], 0.10)


def test_60d_single_stock_sector_is_unavailable(monkeypatch):
    _patch_prices(
        monkeypatch,
        {"AAA": _prices([100.0] + [120.0] * 60)},
    )

    result = rs.calculate_sector_relative_strength(
        "AAA",
        {"AAA": "INSURANCE"},
        universe_symbols=("AAA",),
    )

    assert result["available"] is False
    assert result["sector"] == "INSURANCE"
    assert result["sector_universe_size"] == 1
    assert result["sector_eligible_count"] == 0
