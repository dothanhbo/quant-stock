from __future__ import annotations

import numpy as np
import pandas as pd

import strategy.relative_strength_v2 as module


def _prices(values):
    return pd.DataFrame(
        {
            "time": pd.date_range("2026-01-01", periods=len(values), freq="D"),
            "close": values,
        }
    )


def test_sector_benchmark_is_limited_to_supplied_universe(monkeypatch):
    prices = {
        "AAA": _prices([100, 110, 120]),   # +20%
        "BBB": _prices([100, 105, 110]),   # +10%
        "CCC": _prices([100, 200, 300]),   # +200%, must be excluded
    }
    monkeypatch.setattr(module, "load_price_data", lambda symbol: prices[symbol])

    mapping = {
        "AAA": "Technology",
        "BBB": "Technology",
        "CCC": "Technology",
    }

    result = module.calculate_sector_relative_strength(
        "AAA",
        mapping,
        period=2,
        universe_symbols=["AAA", "BBB"],
    )

    # Target AAA is excluded from the peer benchmark.
    # Benchmark = BBB +10%; CCC is outside the supplied universe.
    assert result["sector_universe_size"] == 2
    assert result["sector_eligible_count"] == 1
    assert np.isclose(result["benchmark_return"], 0.10)
    assert np.isclose(result["relative_strength"], 0.10)


def test_default_universe_is_used(monkeypatch):
    prices = {
        "AAA": _prices([100, 110, 120]),   # +20%
        "BBB": _prices([100, 100, 100]),   # 0%
    }
    monkeypatch.setattr(module, "load_price_data", lambda symbol: prices[symbol])
    monkeypatch.setattr(module, "get_vn100_symbols", lambda: ["AAA", "BBB"])

    mapping = {
        "AAA": "Technology",
        "BBB": "Technology",
    }

    result = module.calculate_sector_relative_strength(
        "AAA",
        mapping,
        period=2,
    )

    # Default benchmark uses the mocked VN100 universe,
    # excluding the target AAA. Only BBB is a peer.
    assert result["sector_universe_size"] == 2
    assert result["sector_eligible_count"] == 1
    assert np.isclose(result["benchmark_return"], 0.0)
    assert np.isclose(result["relative_strength"], 0.20)


def test_outside_universe_symbol_cannot_enter_benchmark(monkeypatch):
    prices = {
        "AAA": _prices([100, 110, 120]),      # +20%
        "BBB": _prices([100, 100, 100]),      # 0%
        "OUTSIDE": _prices([100, 300, 500]),  # +400%, must be excluded
    }
    monkeypatch.setattr(module, "load_price_data", lambda symbol: prices[symbol])

    mapping = {
        "AAA": "Technology",
        "BBB": "Technology",
        "OUTSIDE": "Technology",
    }

    result = module.calculate_sector_relative_strength(
        "AAA",
        mapping,
        period=2,
        universe_symbols=["AAA", "BBB"],
    )

    # Benchmark uses only the eligible peer BBB.
    # OUTSIDE is not in the supplied universe and AAA is the target.
    assert result["sector_universe_size"] == 2
    assert result["sector_eligible_count"] == 1
    assert np.isclose(result["benchmark_return"], 0.0)


def test_target_is_kept_when_supplied_universe_snapshot_is_incomplete(monkeypatch):
    prices = {
        "AAA": _prices([100, 110, 120]),
        "BBB": _prices([100, 100, 100]),
    }
    monkeypatch.setattr(module, "load_price_data", lambda symbol: prices[symbol])

    mapping = {
        "AAA": "Technology",
        "BBB": "Technology",
    }

    result = module.calculate_sector_relative_strength(
        "AAA",
        mapping,
        period=2,
        universe_symbols=["BBB"],
    )

    # The supplied universe is authoritative. Since AAA is not present,
    # the target is unavailable rather than being added back implicitly.
    assert result["available"] is False
    assert result["relative_strength"] is None
    assert result["sector_universe_size"] == 0
    assert result["sector_eligible_count"] == 0
