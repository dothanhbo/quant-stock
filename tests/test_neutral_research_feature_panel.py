from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
import pytest

from quantlab.catalog import build_market_data_snapshot
from quantlab.features import FeatureRegistry, FeatureRequest, PointInTimeUniverseContext, PreparedFeatureCache, builtin_definitions
from quantlab.features import builtins as feature_builtins
from quantlab.features.universe_context import BREADTH_CONTEXT_KEY
from quantlab.panels import (
    NEUTRAL_RESEARCH_FEATURE_PANEL_V1,
    NEUTRAL_RESEARCH_NUMERIC_FEATURES_V1,
    FeatureValueAvailability,
    build_neutral_research_feature_panel,
    build_point_in_time_observation_index,
    prepare_neutral_research_feature_source,
)
from quantlab.panels.neutral_features import NEUTRAL_RESEARCH_SOURCE_TO_CANONICAL_V1


CANONICAL_NAMES = (
    "close", "volume", "ema_10", "ema_20", "ema_50", "atr_14",
    "atr_percent_14", "rsi_14", "adx_14", "volume_ma_20",
    "volume_ratio_20", "stock_return_20d_pct", "benchmark_return_20d_pct",
    "relative_strength_20d_pct_points", "ema20_distance_pct", "return_3d_pct",
    "breadth_ema50_pct", "breadth_ema50_change_10d_pct_points",
)


def _database(path: Path, sessions: tuple[str, ...], *, include_benchmark: bool = True) -> None:
    symbols = ("AAA", "BBB", "ALTINDEX") + (("VNINDEX",) if include_benchmark else ())
    rows = []
    for symbol_index, symbol in enumerate(symbols):
        for index, session in enumerate(sessions):
            baseline = 20.0 + symbol_index * 7.0 + index * (0.11 + symbol_index * 0.01)
            close = baseline + np.sin(index / 5.0) * (0.3 + symbol_index * 0.02)
            rows.append((
                symbol, session, close - 0.2, close + 0.5, close - 0.6, close,
                100_000 + symbol_index * 10_000 + index * 137,
            ))
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE prices (symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER)"
        )
        connection.executemany("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def _fixture(tmp_path: Path):
    sessions = tuple(pd.bdate_range("2021-01-04", periods=85).strftime("%Y-%m-%d"))
    path = tmp_path / "market.db"
    _database(path, sessions)
    snapshot = build_market_data_snapshot(path)
    context = PointInTimeUniverseContext.static(("MISSING", "BBB", "AAA"), sessions)
    observations = build_point_in_time_observation_index(
        snapshot,
        context,
        benchmark_symbol="VNINDEX",
        start_date=sessions[0],
        through_date=sessions[-1],
    )
    return path, sessions, snapshot, context, observations


class _CountingSnapshot:
    def __init__(self, snapshot) -> None:
        self._snapshot = snapshot
        self.calls: list[tuple[str, ...]] = []
        self.fail_on_load = False
        for name in (
            "canonical_db_path", "schema_version", "logical_content_fingerprint",
            "snapshot_id", "symbols",
        ):
            setattr(self, name, getattr(snapshot, name))

    def load_ohlcv(self, symbols, **kwargs):
        if self.fail_on_load:
            raise AssertionError("warm cache hit must not load market data")
        ordered = tuple(symbols)
        self.calls.append(ordered)
        return self._snapshot.load_ohlcv(ordered, **kwargs)


def _instrument_registry_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> Counter[FeatureRequest]:
    """Count callable execution at the public registry-definition boundary."""
    executions: Counter[FeatureRequest] = Counter()
    original_definition_for = FeatureRegistry.definition_for

    def definition_for(registry: FeatureRegistry, request: FeatureRequest):
        definition = original_definition_for(registry, request)
        original_compute = definition.compute
        assert original_compute is not None

        def counted_compute(*args, **kwargs):
            executions[request] += 1
            return original_compute(*args, **kwargs)

        return replace(definition, compute=counted_compute)

    monkeypatch.setattr(FeatureRegistry, "definition_for", definition_for)
    return executions


def test_exact_feature_order_mapping_and_exclusions() -> None:
    assert tuple(item.output_name for item in NEUTRAL_RESEARCH_NUMERIC_FEATURES_V1) == CANONICAL_NAMES
    assert tuple(item.source_column for item in NEUTRAL_RESEARCH_NUMERIC_FEATURES_V1) == CANONICAL_NAMES
    assert tuple(item.expected_numeric_type for item in NEUTRAL_RESEARCH_NUMERIC_FEATURES_V1) == (
        "float", "integer", *("float" for _ in range(16)),
    )
    assert NEUTRAL_RESEARCH_SOURCE_TO_CANONICAL_V1 == feature_builtins.NEUTRAL_RESEARCH_SOURCE_TO_CANONICAL_V1
    excluded = {
        "Market_Regime", "paper_v2_state", "score", "quality_score", "Breakout_20D",
        "EMA20_Rising", "Recent_Breakout_10D", "Touched_EMA10", "Reclaimed_EMA10",
        "Green_Candle", "Close_Upper_Half", "sector", "future_return", "net_pnl",
    }
    assert excluded.isdisjoint(CANONICAL_NAMES)


def test_composite_matches_every_authoritative_source_field_exactly(tmp_path: Path) -> None:
    _, sessions, snapshot, context, _ = _fixture(tmp_path)
    source = prepare_neutral_research_feature_source(
        snapshot, ("AAA", "BBB", "MISSING"), benchmark_symbol="VNINDEX",
        universe_context=context, start_date=sessions[0], through_date=sessions[-1],
    )
    registry = FeatureRegistry(builtin_definitions())
    loaded = ("AAA", "BBB", "MISSING", "VNINDEX")
    per_symbol = registry.compute(
        FeatureRequest("historical_candidate_per_symbol_subset", "v2"),
        snapshot, loaded, start_date=sessions[0], through_date=sessions[-1],
    ).frame_for("AAA")
    relative = registry.compute(
        FeatureRequest("benchmark_relative_context", "v1", {"benchmark_symbol": "VNINDEX", "period": 20}),
        snapshot, loaded, start_date=sessions[0], through_date=sessions[-1],
    ).frame_for("AAA")
    breadth = registry.compute(
        FeatureRequest("historical_breadth_context", "v1"),
        snapshot, loaded, start_date=sessions[0], through_date=sessions[-1],
        universe_identity=context.membership_identity, execution_context=context,
    ).frame_for(BREADTH_CONTEXT_KEY)
    expected = per_symbol.loc[:, [
        "time", "close", "volume", "EMA10", "EMA20", "EMA50", "ATR14",
        "ATR_Percent", "RSI", "ADX14", "Vol_MA20", "Vol_Ratio",
        "Distance_EMA20_Pct", "Return_3D_Pct",
    ]].merge(
        relative.loc[:, ["time", "Stock_Return_20D", "Index_Return_20D", "Relative_Strength_20D"]],
        on="time", how="left",
    ).merge(
        breadth.loc[:, ["time", "breadth_ema50_pct", "breadth_ema50_change_10d"]],
        on="time", how="left",
    ).rename(columns=dict(NEUTRAL_RESEARCH_SOURCE_TO_CANONICAL_V1))
    expected = expected.loc[:, ["time", *CANONICAL_NAMES]]
    actual = source.frame_for("AAA")
    pd.testing.assert_frame_equal(actual, expected, check_exact=True, check_dtype=True)
    assert str(actual["volume"].dtype) == "int64"
    assert source.metadata["resolved_warmup_sessions"] == 50


def test_exact_date_alignment_primary_only_and_missing_primary(tmp_path: Path) -> None:
    _, sessions, snapshot, context, _ = _fixture(tmp_path)
    source = prepare_neutral_research_feature_source(
        snapshot, ("MISSING", "BBB", "AAA", "VNINDEX"), benchmark_symbol="VNINDEX",
        universe_context=context, start_date=sessions[0], through_date=sessions[-1],
    )
    assert source.available_symbols == ("AAA", "BBB")
    assert source.missing_symbols == ("MISSING",)
    assert source.frame_for("VNINDEX").empty
    assert tuple(source.frame_for("AAA").columns) == ("time", *CANONICAL_NAMES)
    assert tuple(source.frame_for("AAA")["time"].dt.strftime("%Y-%m-%d")) == sessions
    assert source.frame_for("AAA")["benchmark_return_20d_pct"].first_valid_index() == 20
    assert source.frame_for("AAA")["breadth_ema50_pct"].first_valid_index() == 49


def test_panel_preserves_observations_and_warmup_missingness(tmp_path: Path) -> None:
    _, sessions, snapshot, context, observations = _fixture(tmp_path)
    base = observations.frame
    panel = build_neutral_research_feature_panel(
        observations, snapshot, benchmark_symbol="VNINDEX", universe_context=context,
    )
    frame = panel.frame
    assert len(frame) == len(base)
    pd.testing.assert_frame_equal(frame.loc[:, base.columns], base)
    assert list(zip(frame["session_date"], frame["symbol"])) == list(zip(base["session_date"], base["symbol"]))
    missing = frame.loc[frame["symbol"] == "MISSING"]
    assert len(missing) == len(sessions)
    assert missing["close__availability"].eq(FeatureValueAvailability.OBSERVATION_MARKET_ROW_MISSING.value).all()
    first_aaa = frame.loc[(frame["session_date"] == sessions[0]) & (frame["symbol"] == "AAA")].iloc[0]
    assert first_aaa["rsi_14__availability"] == FeatureValueAvailability.SOURCE_VALUE_NONFINITE.value
    assert first_aaa["breadth_ema50_pct__availability"] == FeatureValueAvailability.SOURCE_VALUE_NONFINITE.value
    assert not bool(first_aaa["complete_feature_row"])


def test_missing_benchmark_and_universe_context_fail_clearly(tmp_path: Path) -> None:
    sessions = tuple(pd.bdate_range("2021-01-04", periods=60).strftime("%Y-%m-%d"))
    path = tmp_path / "missing-benchmark.db"
    _database(path, sessions, include_benchmark=False)
    snapshot = build_market_data_snapshot(path)
    context = PointInTimeUniverseContext.static(("AAA", "BBB"), sessions)
    with pytest.raises(ValueError, match="benchmark series is unavailable"):
        prepare_neutral_research_feature_source(
            snapshot, ("AAA",), benchmark_symbol="VNINDEX", universe_context=context,
            start_date=sessions[0], through_date=sessions[-1],
        )
    with pytest.raises(ValueError, match="universe_context"):
        prepare_neutral_research_feature_source(
            snapshot, ("AAA",), benchmark_symbol="ALTINDEX", universe_context=None,
            start_date=sessions[0], through_date=sessions[-1],
        )


@pytest.mark.parametrize(
    ("mismatch", "message"),
    [
        pytest.param("snapshot", "snapshot identity", id="snapshot"),
        pytest.param("universe", "universe membership", id="universe"),
        pytest.param("benchmark", "benchmark", id="benchmark"),
    ],
)
def test_panel_provenance_mismatches_fail_before_computation(
    tmp_path: Path,
    mismatch: str,
    message: str,
) -> None:
    _, _, snapshot, context, observations = _fixture(tmp_path)
    supplied_snapshot = snapshot
    supplied_context = context
    benchmark = "VNINDEX"
    if mismatch == "snapshot":
        supplied_snapshot = _CountingSnapshot(snapshot)
        supplied_snapshot.snapshot_id = "different"
    elif mismatch == "universe":
        supplied_context = PointInTimeUniverseContext.from_memberships(
            universe_mode="changed",
            memberships={date: ("AAA",) for date in context.session_dates},
        )
    else:
        benchmark = "ALTINDEX"
    with pytest.raises(ValueError, match=message):
        build_neutral_research_feature_panel(
            observations,
            supplied_snapshot,
            benchmark_symbol=benchmark,
            universe_context=supplied_context,
        )


def test_cold_computation_loads_union_once_and_executes_each_node_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sessions, snapshot, context, _ = _fixture(tmp_path)
    expected = prepare_neutral_research_feature_source(
        snapshot,
        ("AAA", "MISSING"),
        benchmark_symbol="VNINDEX",
        universe_context=context,
        start_date=sessions[0],
        through_date=sessions[-1],
    )
    counted_snapshot = _CountingSnapshot(snapshot)
    executions = _instrument_registry_execution(monkeypatch)
    source = prepare_neutral_research_feature_source(
        counted_snapshot, ("AAA", "MISSING"), benchmark_symbol="VNINDEX",
        universe_context=context, start_date=sessions[0], through_date=sessions[-1],
    )
    assert len(counted_snapshot.calls) == 1
    assert set(counted_snapshot.calls[0]) == {"AAA", "BBB", "MISSING", "VNINDEX"}
    assert executions
    assert all(count == 1 for count in executions.values())
    ema_requests = tuple(request for request in executions if request.name == "ema")
    assert {
        request.parameter_mapping["period"] for request in ema_requests
    } == {10, 20, 50}
    assert all(executions[request] == 1 for request in ema_requests)
    top_level = tuple(
        request
        for request in executions
        if request.name == "neutral_research_numeric_features"
    )
    assert len(top_level) == 1
    assert executions[top_level[0]] == 1
    assert source.metadata["resolved_warmup_sessions"] == 50
    assert source.computation_identity.sha256 == expected.computation_identity.sha256
    for symbol in source.available_symbols:
        pd.testing.assert_frame_equal(
            source.frame_for(symbol),
            expected.frame_for(symbol),
            check_exact=True,
        )


def test_explicit_npz_warm_hit_loads_and_executes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, sessions, snapshot, context, _ = _fixture(tmp_path)
    counted_snapshot = _CountingSnapshot(snapshot)
    cache = PreparedFeatureCache(tmp_path / "cache", codec="npz_numeric_v1")
    kwargs = {
        "benchmark_symbol": "VNINDEX", "universe_context": context,
        "start_date": sessions[0], "through_date": sessions[-1], "cache": cache,
    }
    cold = prepare_neutral_research_feature_source(counted_snapshot, ("AAA", "BBB"), **kwargs)
    counted_snapshot.calls.clear()
    counted_snapshot.fail_on_load = True
    executions = _instrument_registry_execution(monkeypatch)
    warm = prepare_neutral_research_feature_source(counted_snapshot, ("BBB", "AAA"), **kwargs)
    assert counted_snapshot.calls == []
    assert sum(executions.values()) == 0
    assert warm.metadata["cache"]["hit"] is True
    assert warm.computation_identity.sha256 == cold.computation_identity.sha256
    for symbol in warm.available_symbols:
        pd.testing.assert_frame_equal(warm.frame_for(symbol), cold.frame_for(symbol), check_exact=True)


def test_cache_disabled_remains_uncached(tmp_path: Path) -> None:
    _, sessions, snapshot, context, _ = _fixture(tmp_path)
    counted = _CountingSnapshot(snapshot)
    kwargs = {
        "benchmark_symbol": "VNINDEX", "universe_context": context,
        "start_date": sessions[0], "through_date": sessions[-1],
    }
    first = prepare_neutral_research_feature_source(counted, ("AAA",), **kwargs)
    second = prepare_neutral_research_feature_source(counted, ("AAA",), **kwargs)
    assert len(counted.calls) == 2
    assert "cache" not in first.metadata and "cache" not in second.metadata
    assert first.computation_identity.sha256 == second.computation_identity.sha256


def test_identity_is_presentation_invariant_and_sensitive_to_context_bounds_and_benchmark(tmp_path: Path) -> None:
    _, sessions, snapshot, context, _ = _fixture(tmp_path)
    common = {
        "benchmark_symbol": "VNINDEX", "universe_context": context,
        "start_date": sessions[0], "through_date": sessions[-1],
    }
    one = prepare_neutral_research_feature_source(snapshot, ("BBB", "AAA"), **common)
    reordered = prepare_neutral_research_feature_source(snapshot, ("AAA", "BBB", "AAA"), **common)
    assert one.computation_identity.sha256 == reordered.computation_identity.sha256
    bounded = prepare_neutral_research_feature_source(
        snapshot, ("AAA", "BBB"), **{**common, "start_date": sessions[1]},
    )
    assert bounded.computation_identity.sha256 != one.computation_identity.sha256
    changed_context = PointInTimeUniverseContext.from_memberships(
        universe_mode="changed",
        memberships={date: (("AAA",) if date == sessions[50] else ("AAA", "BBB")) for date in sessions},
    )
    changed_universe = prepare_neutral_research_feature_source(
        snapshot, ("AAA", "BBB"), benchmark_symbol="VNINDEX", universe_context=changed_context,
        start_date=sessions[0], through_date=sessions[-1],
    )
    assert changed_universe.computation_identity.sha256 != one.computation_identity.sha256
    alternate = prepare_neutral_research_feature_source(
        snapshot, ("AAA", "BBB"), benchmark_symbol="ALTINDEX", universe_context=context,
        start_date=sessions[0], through_date=sessions[-1],
    )
    assert alternate.computation_identity.sha256 != one.computation_identity.sha256


def test_future_rows_preserve_bounded_values_but_change_provenance(tmp_path: Path) -> None:
    path, sessions, snapshot, context, observations = _fixture(tmp_path)
    before = build_neutral_research_feature_panel(
        observations, snapshot, benchmark_symbol="VNINDEX", universe_context=context,
    )
    future = "2022-12-30"
    with sqlite3.connect(path) as connection:
        for symbol in ("AAA", "BBB", "VNINDEX", "ALTINDEX"):
            connection.execute(
                "INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)",
                (symbol, future, 50.0, 51.0, 49.0, 50.0, 200_000),
            )
    later_snapshot = build_market_data_snapshot(path)
    later_observations = build_point_in_time_observation_index(
        later_snapshot, context, benchmark_symbol="VNINDEX",
        start_date=sessions[0], through_date=sessions[-1],
    )
    after = build_neutral_research_feature_panel(
        later_observations, later_snapshot, benchmark_symbol="VNINDEX", universe_context=context,
    )
    comparable = [
        column for column in before.frame.columns
        if not column.endswith("__availability")
    ]
    pd.testing.assert_frame_equal(before.frame.loc[:, comparable], after.frame.loc[:, comparable], check_exact=True)
    assert before.observation_content_identity == after.observation_content_identity
    assert before.snapshot_id != after.snapshot_id
    assert before.feature_source_identity != after.feature_source_identity
    assert before.identity != after.identity


