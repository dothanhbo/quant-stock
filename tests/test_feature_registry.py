from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from quantlab.catalog.market_data_snapshot import build_market_data_snapshot
from quantlab.features import FeatureDefinition, FeatureRegistry, FeatureRequest, FeatureScope, builtin_definitions
from quantlab.features.contracts import FeatureComputationIdentity, FeatureIdentity
from strategy.indicators import calculate_atr


def _database(path: Path, rows: list[tuple[object, ...]]) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE prices (symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER)")
        connection.executemany("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


@pytest.fixture
def snapshot(tmp_path: Path):
    dates = pd.date_range("2020-01-01", periods=30, freq="B")
    rows = []
    for symbol, offset in (("AAA", 0), ("BBB", 10)):
        for index, date in enumerate(dates):
            close = 10 + offset + index
            rows.append((symbol, date.date().isoformat(), close - .5, close + 1, close - 1, close, index + 1))
    rows.append(("AAA", dates[5].date().isoformat(), 99, 101, 98, 100, 9))
    path = tmp_path / "market.db"; _database(path, rows)
    return build_market_data_snapshot(path)


def _registry() -> FeatureRegistry:
    return FeatureRegistry(builtin_definitions())


def test_canonical_parameters_and_identities_are_stable(snapshot) -> None:
    left = FeatureRequest("ema", "v1", {"period": 10, "nested": [True, "x"]})
    right = FeatureRequest("ema", "v1", {"nested": [True, "x"], "period": 10})
    definition = _registry().definition_for(left)
    first = FeatureIdentity.create(left, (), definition)
    second = FeatureIdentity.create(right, (), definition)
    assert left.parameters == right.parameters and first.sha256 == second.sha256
    computation = FeatureComputationIdentity.create(feature_identity=first, snapshot_id=snapshot.snapshot_id, symbols=("AAA",), start_date=None, through_date="2020-01-10", universe_identity=None)
    assert computation.sha256 != FeatureComputationIdentity.create(feature_identity=first, snapshot_id="other", symbols=("AAA",), start_date=None, through_date="2020-01-10", universe_identity=None).sha256
    with pytest.raises(TypeError):
        FeatureRequest("ema", "v1", {"bad": object()})
    with pytest.raises(ValueError):
        FeatureRequest("ema", "v1", {"bad": float("nan")})


def test_registration_unknown_version_cycles_and_additive_warmup(snapshot) -> None:
    calls: list[str] = []
    def compute(frames, dependencies, parameters):
        calls.append("computed")
        return {symbol: pd.DataFrame({"time": frame["time"], "x": 1.0}) for symbol, frame in frames.items()}
    leaf = FeatureDefinition("leaf", "v1", FeatureScope.PER_SYMBOL, ("time",), direct_warmup_sessions=3, output_columns=("time", "x"), compute=compute)
    root = FeatureDefinition("root", "v1", FeatureScope.PER_SYMBOL, ("time",), dependencies=(FeatureRequest("leaf", "v1"),), direct_warmup_sessions=4, output_columns=("time", "x"), compute=compute)
    registry = FeatureRegistry((leaf, root))
    assert registry.compute(FeatureRequest("root", "v1"), snapshot, ["AAA"]).metadata["resolved_warmup_sessions"] == 7
    assert calls == ["computed", "computed"]  # leaf dependency was evaluated once.
    with pytest.raises(ValueError, match="duplicate"):
        registry.register(leaf)
    with pytest.raises(KeyError, match="unknown"):
        registry.compute(FeatureRequest("missing", "v1"), snapshot, ["AAA"])
    cyclic = FeatureDefinition("cycle", "v1", FeatureScope.PER_SYMBOL, ("time",), dependencies=(FeatureRequest("cycle", "v1"),), compute=compute)
    with pytest.raises(ValueError, match="cycle"):
        FeatureRegistry((cyclic,)).compute(FeatureRequest("cycle", "v1"), snapshot, ["AAA"])


def test_builtin_ema_atr_and_donchian_match_authoritative_formulas(snapshot) -> None:
    registry = _registry()
    raw = snapshot.load_ohlcv(["AAA"]).frame_for("AAA")
    ema = registry.compute(FeatureRequest("ema", "v1", {"period": 10}), snapshot, ["AAA"]).frame_for("AAA")
    atr = registry.compute(FeatureRequest("atr", "v1", {"period": 14}), snapshot, ["AAA"]).frame_for("AAA")
    donchian = registry.compute(FeatureRequest("donchian", "v1", {"period": 20}), snapshot, ["AAA"]).frame_for("AAA")
    np.testing.assert_allclose(ema["EMA10"], raw["close"].ewm(span=10, adjust=False).mean(), equal_nan=True)
    np.testing.assert_allclose(atr["ATR14"], calculate_atr(raw, 14), equal_nan=True)
    expected_reference = raw["high"].shift(1).rolling(window=20, min_periods=20).max()
    pd.testing.assert_series_equal(donchian["Previous_20D_High"], expected_reference, check_names=False)
    pd.testing.assert_series_equal(donchian["Breakout_20D"], raw["close"] > expected_reference, check_names=False)


def test_start_warmup_causality_defensive_copies_and_single_snapshot_load(snapshot, monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _registry(); calls = 0
    before = snapshot.canonical_db_path.read_bytes()
    original = type(snapshot).load_ohlcv
    def counted(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        return original(self, *args, **kwargs)
    monkeypatch.setattr(type(snapshot), "load_ohlcv", counted)
    result = registry.compute(FeatureRequest("atr", "v1", {"period": 14}), snapshot, ["AAA", "BBB"], start_date="2020-01-20", through_date="2020-02-01")
    assert calls == 1 and result.metadata["resolved_warmup_sessions"] == 14
    assert result.frame_for("AAA")["time"].min() >= pd.Timestamp("2020-01-20")
    changed = result.frame_for("AAA"); changed.loc[:, "ATR14"] = -1
    assert not (result.frame_for("AAA")["ATR14"] == -1).all()
    first = registry.compute(FeatureRequest("ema", "v1", {"period": 10}), snapshot, ["AAA"], through_date="2020-01-20").frame_for("AAA")
    second = registry.compute(FeatureRequest("ema", "v1", {"period": 10}), snapshot, ["AAA"], through_date="2020-02-01").frame_for("AAA")
    pd.testing.assert_frame_equal(first, second.loc[second["time"] <= pd.Timestamp("2020-01-20")].reset_index(drop=True))
    assert snapshot.canonical_db_path.read_bytes() == before


def test_scope_context_required_raw_schema_and_import_are_safe(snapshot, tmp_path: Path) -> None:
    def compute(frames, dependencies, parameters):
        return {symbol: pd.DataFrame({"time": frame["time"], "x": 1.0}) for symbol, frame in frames.items()}
    cross = FeatureDefinition("cross", "v1", FeatureScope.CROSS_SECTIONAL, ("time",), output_columns=("time", "x"), compute=compute)
    registry = FeatureRegistry((cross,))
    with pytest.raises(ValueError, match="universe_identity"):
        registry.compute(FeatureRequest("cross", "v1"), snapshot, ["AAA"])
    assert registry.compute(FeatureRequest("cross", "v1"), snapshot, ["AAA"], universe_identity="u1").available_symbols == ("AAA",)
    bad = FeatureDefinition("bad", "v1", FeatureScope.PER_SYMBOL, ("not_a_column",), compute=compute)
    with pytest.raises(ValueError, match="missing raw"):
        FeatureRegistry((bad,)).compute(FeatureRequest("bad", "v1"), snapshot, ["AAA"])
    def malformed(frames, dependencies, parameters):
        return {symbol: pd.DataFrame({"time": frame["time"]}) for symbol, frame in frames.items()}
    with pytest.raises(ValueError, match="declared columns"):
        FeatureRegistry((FeatureDefinition("schema", "v1", FeatureScope.PER_SYMBOL, ("time",), output_columns=("time", "x"), compute=malformed),)).compute(FeatureRequest("schema", "v1"), snapshot, ["AAA"])
    missing = tmp_path / "not-created.db"
    completed = subprocess.run([sys.executable, "-c", "import quantlab.features"], cwd=Path(__file__).resolve().parents[1], env=dict(os.environ, MARKET_DATABASE_PATH=str(missing)), capture_output=True, text=True)
    assert completed.returncode == 0 and not missing.exists()
