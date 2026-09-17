import numpy as np
import pandas as pd

from strategy import paper_v2_gate as gate


def test_sector_rs_60d_is_telemetry_only(monkeypatch):
    calls = []

    def fake_rs(*args, **kwargs):
        calls.append(kwargs)
        return {
            "available": True,
            "relative_strength": 0.123456,
            "stock_return": 0.20,
            "benchmark_return": 0.076544,
            "sector": "TECH",
            "sector_universe_size": 3,
            "sector_eligible_count": 2,
        }

    monkeypatch.setattr(gate, "calculate_sector_relative_strength", fake_rs)

    signals = [{
        "symbol": "AAA",
        "date": "2026-09-04",
        "score": 80,
        "relative_strength_20d": 5,
        "adx": 30,
        "volume_ratio": 1.5,
        "regime": "BULL",
        "breadth_ema50_pct": 75,
        "breadth_ema50_change_10d": 2,
    }]

    v2 = gate.PaperV2QualityGate(
        threshold=0.70,
        sector_mapping={"AAA": "TECH"},
        sector_universe_symbols=("AAA", "BBB", "CCC"),
    )

    accepted, rejected = v2.apply(signals)

    assert len(accepted) == 1
    assert not rejected
    assert accepted[0]["paper_v2_quality"] == 1.0
    assert accepted[0]["sector_rs_60d_available"] is True
    assert accepted[0]["sector_rs_60d"] == 0.123456
    assert accepted[0]["sector_rs_60d_eligible_peers"] == 2
    assert calls[0]["period"] == 60


def test_sector_rs_60d_does_not_change_gate_decision(monkeypatch):
    def fake_rs(*args, **kwargs):
        return {
            "available": True,
            "relative_strength": -0.99,
            "stock_return": -0.50,
            "benchmark_return": 0.49,
            "sector": "TECH",
            "sector_universe_size": 10,
            "sector_eligible_count": 9,
        }

    monkeypatch.setattr(gate, "calculate_sector_relative_strength", fake_rs)

    signal = {
        "symbol": "AAA",
        "date": "2026-09-04",
        "score": 80,
        "relative_strength_20d": 5,
        "adx": 30,
        "volume_ratio": 1.5,
        "regime": "BULL",
        "breadth_ema50_pct": 75,
        "breadth_ema50_change_10d": 2,
    }

    accepted, rejected = gate.PaperV2QualityGate(
        0.70,
        sector_mapping={"AAA": "TECH"},
        sector_universe_symbols=("AAA", "BBB"),
    ).apply([signal])

    assert len(accepted) == 1
    assert not rejected
    assert accepted[0]["paper_v2_gate"] == "Q0.70_PASS"


def test_unavailable_sector_rs_is_non_blocking(monkeypatch):
    monkeypatch.setattr(
        gate,
        "calculate_sector_relative_strength",
        lambda *args, **kwargs: {
            "available": False,
            "relative_strength": None,
            "sector": "INSURANCE",
            "sector_universe_size": 1,
            "sector_eligible_count": 0,
        },
    )

    signal = {
        "symbol": "AAA",
        "date": "2026-09-04",
        "score": 80,
        "relative_strength_20d": 5,
        "adx": 30,
        "volume_ratio": 1.5,
        "regime": "BULL",
        "breadth_ema50_pct": 75,
        "breadth_ema50_change_10d": 2,
    }

    accepted, rejected = gate.PaperV2QualityGate(
        0.70,
        sector_mapping={"AAA": "INSURANCE"},
        sector_universe_symbols=("AAA",),
    ).apply([signal])

    assert len(accepted) == 1
    assert not rejected
    assert accepted[0]["sector_rs_60d_available"] is False
    assert accepted[0]["sector_rs_60d"] is None


def test_sector_rs_error_is_non_blocking(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("sector provider unavailable")

    monkeypatch.setattr(gate, "calculate_sector_relative_strength", fail)

    signal = {
        "symbol": "AAA",
        "date": "2026-09-04",
        "score": 80,
        "relative_strength_20d": 5,
        "adx": 30,
        "volume_ratio": 1.5,
        "regime": "BULL",
        "breadth_ema50_pct": 75,
        "breadth_ema50_change_10d": 2,
    }

    accepted, rejected = gate.PaperV2QualityGate(
        0.70,
        sector_mapping={"AAA": "TECH"},
    ).apply([signal])

    assert len(accepted) == 1
    assert not rejected
    assert accepted[0]["sector_rs_60d_available"] is False
    assert accepted[0]["sector_rs_60d"] is None
    assert "sector_rs_60d_error" in accepted[0]


def test_sector_rs_is_not_a_quality_feature():
    assert "sector_rs_60d" not in gate.QUALITY_FEATURES
    assert gate.SECTOR_RS_PERIOD == 60
