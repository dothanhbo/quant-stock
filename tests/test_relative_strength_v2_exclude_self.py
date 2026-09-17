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


def test_sector_rs_excludes_target(monkeypatch):
    data = {
        "AAA": _prices([100, 120]),
        "BBB": _prices([100, 110]),
        "CCC": _prices([100, 130]),
    }
    _patch_prices(monkeypatch, data)

    result = rs.calculate_sector_relative_strength(
        "AAA",
        {"AAA": "TECH", "BBB": "TECH", "CCC": "TECH"},
        period=1,
        universe_symbols=("AAA", "BBB", "CCC"),
    )

    # Peer benchmark = mean(BBB +10%, CCC +30%) = +20%.
    assert result["available"] is True
    assert result["sector"] == "TECH"
    assert result["sector_universe_size"] == 3
    assert result["sector_eligible_count"] == 2
    assert np.isclose(result["benchmark_return"], 0.20)
    assert np.isclose(result["stock_return"], 0.20)
    assert np.isclose(result["relative_strength"], 0.0)


def test_two_stock_sector_uses_only_the_other_stock(monkeypatch):
    data = {
        "AAA": _prices([100, 120]),
        "BBB": _prices([100, 110]),
    }
    _patch_prices(monkeypatch, data)

    result = rs.calculate_sector_relative_strength(
        "AAA",
        {"AAA": "BANK", "BBB": "BANK"},
        period=1,
        universe_symbols=("AAA", "BBB"),
    )

    assert result["available"] is True
    assert result["sector_eligible_count"] == 1
    assert np.isclose(result["benchmark_return"], 0.10)
    assert np.isclose(result["relative_strength"], 0.10)


def test_single_stock_sector_is_unavailable(monkeypatch):
    data = {"AAA": _prices([100, 120])}
    _patch_prices(monkeypatch, data)

    result = rs.calculate_sector_relative_strength(
        "AAA",
        {"AAA": "INSURANCE"},
        period=1,
        universe_symbols=("AAA",),
    )

    assert result["available"] is False
    assert result["relative_strength"] is None
    assert result["sector"] == "INSURANCE"
    assert result["sector_universe_size"] == 1
    assert result["sector_eligible_count"] == 0


def test_non_vn100_peer_is_excluded(monkeypatch):
    data = {
        "AAA": _prices([100, 120]),
        "BBB": _prices([100, 110]),
        "OUT": _prices([100, 200]),
    }
    _patch_prices(monkeypatch, data)

    result = rs.calculate_sector_relative_strength(
        "AAA",
        {"AAA": "TECH", "BBB": "TECH", "OUT": "TECH"},
        period=1,
        universe_symbols=("AAA", "BBB"),
    )

    assert result["available"] is True
    assert result["sector_universe_size"] == 2
    assert result["sector_eligible_count"] == 1
    assert np.isclose(result["benchmark_return"], 0.10)
    assert np.isclose(result["relative_strength"], 0.10)


def test_ineligible_peer_is_not_counted(monkeypatch):
    data = {
        "AAA": _prices([100, 120]),
        "BBB": _prices([100, 110]),
        # Only one observation -> ineligible.
        "CCC": _prices([100]),
    }
    _patch_prices(monkeypatch, data)

    result = rs.calculate_sector_relative_strength(
        "AAA",
        {"AAA": "TECH", "BBB": "TECH", "CCC": "TECH"},
        period=1,
        universe_symbols=("AAA", "BBB", "CCC"),
    )

    assert result["available"] is True
    assert result["sector_universe_size"] == 3
    assert result["sector_eligible_count"] == 1
    assert np.isclose(result["benchmark_return"], 0.10)
    assert np.isclose(result["relative_strength"], 0.10)
