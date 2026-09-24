from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from quantlab.catalog import build_market_data_snapshot
from quantlab.features.universe_context import PointInTimeUniverseContext
from quantlab.outcomes import ForwardOutcomeSpec, label_candidate_forward_outcomes
from quantlab.panels import (
    POINT_IN_TIME_FORWARD_OUTCOMES_5_10_20_V1,
    PanelForwardOutcomeAvailability,
    PointInTimeOutcomePanelSpec,
    build_point_in_time_observation_index,
    build_point_in_time_outcome_panel,
)
from quantlab.panels.outcome_contracts import OUTCOME_BASE_COLUMNS
from tests.quantlab_panel_test_support import (
    InMemoryMarketDataSnapshot as _Snapshot,
    make_ohlcv_frame,
)


def _frame(symbol: str, sessions: tuple[str, ...], closes: tuple[object, ...]) -> pd.DataFrame:
    return make_ohlcv_frame(
        symbol,
        sessions,
        closes,
        volume_values=(1_000,) * len(sessions),
        close_dtype="object",
    )


def _sessions(count: int = 30) -> tuple[str, ...]:
    # Business sessions deliberately cross weekends; horizons use these rows,
    # never calendar-day or weekday arithmetic.
    dates = tuple(pd.bdate_range("2024-01-02", periods=count + 1).strftime("%Y-%m-%d"))
    return tuple(date for index, date in enumerate(dates) if index != 7)[:count]


def _basic_fixture():
    sessions = _sessions()
    frames = {
        "VNINDEX": _frame("VNINDEX", sessions, tuple(100.0 + index for index in range(len(sessions)))),
        "AAA": _frame("AAA", sessions, tuple(10.0 + index for index in range(len(sessions)))),
        "EXIT": _frame("EXIT", sessions, tuple(20.0 + index for index in range(len(sessions)))),
        "MISSING": _frame("MISSING", sessions[1:], tuple(30.0 + index for index in range(len(sessions) - 1))),
        "EXTRA": _frame("EXTRA", sessions, tuple(40.0 + index for index in range(len(sessions)))),
    }
    snapshot = _Snapshot(frames)
    context = PointInTimeUniverseContext.from_memberships(
        universe_mode="test_point_in_time",
        memberships={
            sessions[0]: ("MISSING", "EXIT", "AAA"),
            sessions[1]: ("AAA",),
            sessions[2]: (),
        },
    )
    observations = build_point_in_time_observation_index(
        snapshot, context, benchmark_symbol="VNINDEX",
        start_date=sessions[0], through_date=sessions[2],
    )
    snapshot.calls.clear()
    return sessions, snapshot, observations


def _custom_spec(*horizons: int) -> PointInTimeOutcomePanelSpec:
    return PointInTimeOutcomePanelSpec("test_forward_outcomes", "v1", tuple(horizons))


def test_exact_targets_returns_row_preservation_and_one_future_bounded_load() -> None:
    sessions, snapshot, observations = _basic_fixture()
    original = observations.frame
    panel = build_point_in_time_outcome_panel(observations, snapshot)
    frame = panel.frame
    assert tuple(frame.columns) == POINT_IN_TIME_FORWARD_OUTCOMES_5_10_20_V1.output_columns
    assert len(frame) == len(original) == 4
    pd.testing.assert_frame_equal(frame.loc[:, OUTCOME_BASE_COLUMNS], original.loc[:, OUTCOME_BASE_COLUMNS])
    assert snapshot.calls == [
        (("AAA", "EXIT", "MISSING", "VNINDEX"), sessions[0], sessions[-1])
    ]
    aaa = frame.loc[(frame["session_date"] == sessions[0]) & (frame["symbol"] == "AAA")].iloc[0]
    for horizon in (5, 10, 20):
        assert aaa[f"target_session_{horizon}"] == sessions[horizon]
        stock = ((10.0 + horizon) / 10.0 - 1.0) * 100.0
        benchmark = ((100.0 + horizon) / 100.0 - 1.0) * 100.0
        assert aaa[f"stock_forward_return_{horizon}_pct"] == stock
        assert aaa[f"benchmark_forward_return_{horizon}_pct"] == benchmark
        assert aaa[f"excess_forward_return_{horizon}_pct_points"] == stock - benchmark
        assert aaa[f"outcome_{horizon}__availability"] == "AVAILABLE"
    # EXIT left the universe immediately after the signal, but its already
    # formed observation remains labeled.
    exit_row = frame.loc[frame["symbol"] == "EXIT"].iloc[0]
    assert exit_row["available_horizon_count"] == 3
    missing = frame.loc[frame["symbol"] == "MISSING"].iloc[0]
    assert missing["available_horizon_count"] == 0
    assert all(
        missing[f"outcome_{horizon}__availability"]
        == PanelForwardOutcomeAvailability.OBSERVATION_MARKET_ROW_MISSING.value
        for horizon in (5, 10, 20)
    )
    assert not frame["symbol"].eq("EXTRA").any()
    assert panel.metadata["future_looking"] is True
    assert panel.metadata["permitted_use"] == "research_evaluation_only"
    assert panel.session_audit == observations.session_audit
    assert panel.session_audit[-1].emitted_observation_row_count == 0
    assert observations.frame.equals(original)


def test_tail_censoring_is_independent_by_horizon_and_missing_future_is_not_a_loss() -> None:
    sessions, snapshot, _ = _basic_fixture()
    context = PointInTimeUniverseContext.from_memberships(
        universe_mode="tail", memberships={session: (("AAA",) if session == sessions[14] else ()) for session in sessions[:15]},
    )
    observations = build_point_in_time_observation_index(
        snapshot, context, benchmark_symbol="VNINDEX",
        start_date=sessions[0], through_date=sessions[14],
    )
    snapshot.calls.clear()
    panel = build_point_in_time_outcome_panel(observations, snapshot)
    row = panel.frame.iloc[0]
    assert row["outcome_5__availability"] == "AVAILABLE"
    assert row["target_session_5"] == sessions[19]
    assert row["outcome_10__availability"] == "AVAILABLE"
    assert row["target_session_10"] == sessions[24]
    assert row["outcome_20__availability"] == "CENSORED_AFTER_DATA_END"
    assert pd.isna(row["target_session_20"])
    assert pd.isna(row["stock_forward_return_20_pct"])

    changed = {symbol: frame.copy(deep=True) for symbol, frame in snapshot._frames.items()}
    changed["AAA"] = changed["AAA"].loc[changed["AAA"]["time"] != pd.Timestamp(sessions[19])]
    missing_target_snapshot = _Snapshot(changed, snapshot_id=snapshot.snapshot_id)
    missing_target_snapshot.last_session_date = snapshot.last_session_date
    missing_panel = build_point_in_time_outcome_panel(observations, missing_target_snapshot)
    missing_row = missing_panel.frame.iloc[0]
    assert missing_row["outcome_5__availability"] == "MISSING_STOCK_TARGET_CLOSE"
    assert pd.isna(missing_row["stock_forward_return_5_pct"])
    assert missing_row["stock_forward_return_5_pct"] is not -100.0


def test_every_availability_branch_and_precedence() -> None:
    sessions = _sessions(32)
    signal_positions = tuple(range(0, 20, 2)) + (31,)
    names = (
        "OBSMISS", "SIGMISS", "SIGZERO", "BENCHSIGMISS", "BENCHSIGZERO",
        "BENCHTARGETMISS", "BENCHTARGETZERO", "STOCKTARGETMISS", "STOCKTARGETZERO",
        "GOOD", "CENSORED",
    )
    base_frames = {
        "VNINDEX": _frame("VNINDEX", sessions, tuple(100.0 + index for index in range(len(sessions))))
    }
    for name in names:
        base_frames[name] = _frame(name, sessions, tuple(10.0 + index for index in range(len(sessions))))
    # OBSMISS is already absent at observation construction time.
    base_frames["OBSMISS"] = base_frames["OBSMISS"].loc[
        base_frames["OBSMISS"]["time"] != pd.Timestamp(sessions[signal_positions[0]])
    ]
    base = _Snapshot(base_frames)
    memberships = {session: () for session in sessions}
    for name, position in zip(names, signal_positions):
        memberships[sessions[position]] = (name,)
    context = PointInTimeUniverseContext.from_memberships(universe_mode="statuses", memberships=memberships)
    observations = build_point_in_time_observation_index(
        base, context, benchmark_symbol="VNINDEX", start_date=sessions[0], through_date=sessions[31],
    )

    changed = {symbol: frame.copy(deep=True) for symbol, frame in base_frames.items()}
    changed["SIGMISS"] = changed["SIGMISS"].loc[changed["SIGMISS"]["time"] != pd.Timestamp(sessions[2])]
    changed["SIGZERO"].loc[changed["SIGZERO"]["time"] == pd.Timestamp(sessions[4]), "close"] = 0.0
    changed["VNINDEX"].loc[changed["VNINDEX"]["time"] == pd.Timestamp(sessions[6]), "close"] = np.nan
    changed["VNINDEX"].loc[changed["VNINDEX"]["time"] == pd.Timestamp(sessions[8]), "close"] = 0.0
    changed["VNINDEX"].loc[changed["VNINDEX"]["time"] == pd.Timestamp(sessions[11]), "close"] = np.nan
    changed["VNINDEX"].loc[changed["VNINDEX"]["time"] == pd.Timestamp(sessions[13]), "close"] = 0.0
    changed["STOCKTARGETMISS"] = changed["STOCKTARGETMISS"].loc[
        changed["STOCKTARGETMISS"]["time"] != pd.Timestamp(sessions[15])
    ]
    changed["STOCKTARGETZERO"].loc[
        changed["STOCKTARGETZERO"]["time"] == pd.Timestamp(sessions[17]), "close"
    ] = 0.0
    label_snapshot = _Snapshot(changed, snapshot_id=base.snapshot_id)
    spec = _custom_spec(1)
    panel = build_point_in_time_outcome_panel(observations, label_snapshot, horizons=(1,), spec=spec)
    actual = {
        row.symbol: row.outcome_1__availability for row in panel.frame.itertuples(index=False)
    }
    assert actual == {
        "OBSMISS": "OBSERVATION_MARKET_ROW_MISSING",
        "SIGMISS": "MISSING_STOCK_SIGNAL_CLOSE",
        "SIGZERO": "NONPOSITIVE_STOCK_SIGNAL_CLOSE",
        "BENCHSIGMISS": "MISSING_BENCHMARK_SIGNAL_CLOSE",
        "BENCHSIGZERO": "NONPOSITIVE_BENCHMARK_SIGNAL_CLOSE",
        "BENCHTARGETMISS": "MISSING_BENCHMARK_TARGET_CLOSE",
        "BENCHTARGETZERO": "NONPOSITIVE_BENCHMARK_TARGET_CLOSE",
        "STOCKTARGETMISS": "MISSING_STOCK_TARGET_CLOSE",
        "STOCKTARGETZERO": "NONPOSITIVE_STOCK_TARGET_CLOSE",
        "GOOD": "AVAILABLE",
        "CENSORED": "CENSORED_AFTER_DATA_END",
    }
    unavailable = panel.frame.loc[panel.frame["outcome_1__availability"] != "AVAILABLE"]
    assert unavailable[[
        "stock_forward_return_1_pct", "benchmark_forward_return_1_pct",
        "excess_forward_return_1_pct_points",
    ]].isna().all(axis=None)


@pytest.mark.parametrize(
    ("mismatch", "message"),
    [
        pytest.param("nonpositive-horizon", "positive integers", id="nonpositive-horizon"),
        pytest.param("duplicate-horizon", "unique", id="duplicate-horizon"),
        pytest.param("requested-order", "exactly match", id="requested-order"),
        pytest.param("snapshot-identity", "snapshot identity", id="snapshot-identity"),
        pytest.param("missing-benchmark", "benchmark series is unavailable", id="missing-benchmark"),
    ],
)
def test_spec_horizon_snapshot_and_provenance_validation(
    mismatch: str,
    message: str,
) -> None:
    _, snapshot, observations = _basic_fixture()
    assert POINT_IN_TIME_FORWARD_OUTCOMES_5_10_20_V1.horizons == (5, 10, 20)
    with pytest.raises(ValueError, match=message):
        if mismatch == "nonpositive-horizon":
            PointInTimeOutcomePanelSpec("bad", "v1", (0,))
        elif mismatch == "duplicate-horizon":
            PointInTimeOutcomePanelSpec("bad", "v1", (5, 5))
        elif mismatch == "requested-order":
            build_point_in_time_outcome_panel(
                observations,
                snapshot,
                horizons=(10, 5, 20),
            )
        elif mismatch == "snapshot-identity":
            wrong = _Snapshot(snapshot._frames, snapshot_id="other-snapshot")
            build_point_in_time_outcome_panel(observations, wrong)
        else:
            no_benchmark = _Snapshot(
                {
                    symbol: frame
                    for symbol, frame in snapshot._frames.items()
                    if symbol != "VNINDEX"
                },
                snapshot_id=snapshot.snapshot_id,
            )
            no_benchmark.last_session_date = snapshot.last_session_date
            build_point_in_time_outcome_panel(observations, no_benchmark)


def test_panel_defensive_copy_immutability_and_not_a_feature_source() -> None:
    _, snapshot, observations = _basic_fixture()
    panel = build_point_in_time_outcome_panel(observations, snapshot)
    changed = panel.frame
    changed.loc[0, "symbol"] = "MUTATED"
    assert "MUTATED" not in set(panel.frame["symbol"])
    with pytest.raises(FrozenInstanceError):
        panel.snapshot_id = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        panel.metadata["future_looking"] = False  # type: ignore[index]
    assert not hasattr(panel, "computation_identity")
    assert not hasattr(panel, "available_symbols")
    assert not hasattr(panel, "missing_symbols")
    assert not hasattr(panel, "frame_for")


def test_presentation_invariance_and_outcome_value_status_target_identity_sensitivity() -> None:
    sessions, snapshot, observations = _basic_fixture()
    first = build_point_in_time_outcome_panel(observations, snapshot)
    reordered_frames = {key: snapshot._frames[key] for key in reversed(tuple(snapshot._frames))}
    reordered = _Snapshot(reordered_frames, snapshot_id=snapshot.snapshot_id)
    second = build_point_in_time_outcome_panel(observations, reordered)
    assert first.outcome_content_identity == second.outcome_content_identity
    assert first.identity == second.identity

    changed_frames = {symbol: frame.copy(deep=True) for symbol, frame in snapshot._frames.items()}
    changed_frames["AAA"].loc[
        changed_frames["AAA"]["time"] == pd.Timestamp(sessions[5]), "close"
    ] += 0.125
    changed = build_point_in_time_outcome_panel(
        observations, _Snapshot(changed_frames, snapshot_id=snapshot.snapshot_id),
    )
    assert changed.outcome_content_identity != first.outcome_content_identity
    assert changed.identity != first.identity

    missing_frames = {symbol: frame.copy(deep=True) for symbol, frame in snapshot._frames.items()}
    missing_frames["AAA"] = missing_frames["AAA"].loc[
        missing_frames["AAA"]["time"] != pd.Timestamp(sessions[5])
    ]
    missing = build_point_in_time_outcome_panel(
        observations, _Snapshot(missing_frames, snapshot_id=snapshot.snapshot_id),
    )
    assert missing.outcome_content_identity != first.outcome_content_identity
    assert "MISSING_STOCK_TARGET_CLOSE" in set(missing.frame["outcome_5__availability"])

    inserted_date = "2024-01-06"
    shifted_frames = {symbol: frame.copy(deep=True) for symbol, frame in snapshot._frames.items()}
    shifted_frames["VNINDEX"] = pd.concat([
        shifted_frames["VNINDEX"], _frame("VNINDEX", (inserted_date,), (100.5,)),
    ], ignore_index=True).sort_values("time", kind="stable").reset_index(drop=True)
    shifted = build_point_in_time_outcome_panel(
        observations, _Snapshot(shifted_frames, snapshot_id=snapshot.snapshot_id),
    )
    original_target = first.frame.loc[
        (first.frame["session_date"] == sessions[0]) & (first.frame["symbol"] == "AAA"),
        "target_session_5",
    ].iloc[0]
    shifted_target = shifted.frame.loc[
        (shifted.frame["session_date"] == sessions[0]) & (shifted.frame["symbol"] == "AAA"),
        "target_session_5",
    ].iloc[0]
    assert shifted_target != original_target
    assert shifted.outcome_content_identity != first.outcome_content_identity


def test_observation_index_identity_is_bound_separately_from_bounded_outcome_content() -> None:
    sessions, snapshot, first_observations = _basic_fixture()
    alternate_context = PointInTimeUniverseContext.from_memberships(
        universe_mode="different_provenance_same_memberships",
        memberships={sessions[0]: ("AAA", "EXIT", "MISSING"), sessions[1]: ("AAA",), sessions[2]: ()},
    )
    alternate_observations = build_point_in_time_observation_index(
        snapshot, alternate_context, benchmark_symbol="VNINDEX",
        start_date=sessions[0], through_date=sessions[2],
    )
    first = build_point_in_time_outcome_panel(first_observations, snapshot)
    alternate = build_point_in_time_outcome_panel(alternate_observations, snapshot)
    pd.testing.assert_frame_equal(first.frame, alternate.frame, check_exact=True)
    assert first_observations.content_identity == alternate_observations.content_identity
    assert first_observations.identity != alternate_observations.identity
    assert first.outcome_content_identity == alternate.outcome_content_identity
    assert first.identity != alternate.identity


def test_future_bounded_content_and_full_snapshot_provenance_are_separate() -> None:
    sessions, snapshot, observations = _basic_fixture()
    before = build_point_in_time_outcome_panel(observations, snapshot)
    future_date = "2025-12-31"
    later_frames = {symbol: frame.copy(deep=True) for symbol, frame in snapshot._frames.items()}
    for symbol, frame in later_frames.items():
        later_frames[symbol] = pd.concat([
            frame,
            _frame(symbol, (future_date,), (999.0,)),
        ], ignore_index=True)
    later_snapshot = _Snapshot(later_frames, snapshot_id="snapshot-with-later-row")
    context = PointInTimeUniverseContext.from_memberships(
        universe_mode="test_point_in_time",
        memberships={sessions[0]: ("MISSING", "EXIT", "AAA"), sessions[1]: ("AAA",), sessions[2]: ()},
    )
    later_observations = build_point_in_time_observation_index(
        later_snapshot, context, benchmark_symbol="VNINDEX",
        start_date=sessions[0], through_date=sessions[2],
    )
    after = build_point_in_time_outcome_panel(later_observations, later_snapshot)
    pd.testing.assert_frame_equal(before.frame, after.frame, check_exact=True)
    assert observations.content_identity == later_observations.content_identity
    assert before.outcome_content_identity == after.outcome_content_identity
    assert before.snapshot_id != after.snapshot_id
    assert before.identity != after.identity


def test_generic_candidate_outcome_parity_without_candidate_routing() -> None:
    sessions, snapshot, observations = _basic_fixture()
    custom = _custom_spec(5, 10, 20)
    panel = build_point_in_time_outcome_panel(
        observations, snapshot, horizons=(5, 10, 20), spec=custom,
    )
    candidate = SimpleNamespace(
        candidate_key="AAA-key", symbol="AAA", signal_date=sessions[0], signal_close=10.0,
    )
    batch = SimpleNamespace(candidates=(candidate,), batch_identity="batch", snapshot_id=snapshot.snapshot_id)
    generic = label_candidate_forward_outcomes(
        snapshot, batch, ForwardOutcomeSpec("parity", "v1", (5, 10, 20), benchmark_symbol="VNINDEX"),
    )
    row = panel.frame.loc[
        (panel.frame["session_date"] == sessions[0]) & (panel.frame["symbol"] == "AAA")
    ].iloc[0]
    for outcome in generic.outcomes:
        horizon = outcome.horizon_sessions
        assert row[f"target_session_{horizon}"] == outcome.target_market_session_date
        assert row[f"stock_forward_return_{horizon}_pct"] == outcome.stock_forward_return_pct
        assert row[f"benchmark_forward_return_{horizon}_pct"] == outcome.benchmark_forward_return_pct
        assert row[f"excess_forward_return_{horizon}_pct_points"] == outcome.excess_forward_return_percentage_points


def test_generic_outcome_parity_for_tail_censoring_and_missing_exact_target() -> None:
    sessions, snapshot, observations = _basic_fixture()
    missing_frames = {symbol: frame.copy(deep=True) for symbol, frame in snapshot._frames.items()}
    missing_frames["AAA"] = missing_frames["AAA"].loc[
        missing_frames["AAA"]["time"] != pd.Timestamp(sessions[5])
    ]
    missing_snapshot = _Snapshot(missing_frames, snapshot_id=snapshot.snapshot_id)
    panel = build_point_in_time_outcome_panel(observations, missing_snapshot)
    candidate = SimpleNamespace(
        candidate_key="missing-target", symbol="AAA", signal_date=sessions[0], signal_close=10.0,
    )
    batch = SimpleNamespace(candidates=(candidate,), batch_identity="batch", snapshot_id=snapshot.snapshot_id)
    generic = label_candidate_forward_outcomes(
        missing_snapshot, batch, ForwardOutcomeSpec("missing", "v1", (5,), benchmark_symbol="VNINDEX"),
    )
    row = panel.frame.loc[
        (panel.frame["session_date"] == sessions[0]) & (panel.frame["symbol"] == "AAA")
    ].iloc[0]
    assert row["outcome_5__availability"] == "MISSING_STOCK_TARGET_CLOSE"
    assert generic.outcomes[0].status.value == "MISSING_TARGET_CLOSE"
    assert pd.isna(row["stock_forward_return_5_pct"])
    assert generic.outcomes[0].stock_forward_return_pct is None

    tail_context = PointInTimeUniverseContext.from_memberships(
        universe_mode="tail-parity",
        memberships={session: (("AAA",) if session == sessions[14] else ()) for session in sessions[:15]},
    )
    tail_observations = build_point_in_time_observation_index(
        snapshot, tail_context, benchmark_symbol="VNINDEX",
        start_date=sessions[0], through_date=sessions[14],
    )
    tail_panel = build_point_in_time_outcome_panel(tail_observations, snapshot)
    tail_candidate = SimpleNamespace(
        candidate_key="tail", symbol="AAA", signal_date=sessions[14], signal_close=24.0,
    )
    tail_batch = SimpleNamespace(
        candidates=(tail_candidate,), batch_identity="tail-batch", snapshot_id=snapshot.snapshot_id,
    )
    tail_generic = label_candidate_forward_outcomes(
        snapshot, tail_batch, ForwardOutcomeSpec("tail", "v1", (20,), benchmark_symbol="VNINDEX"),
    )
    assert tail_panel.frame.iloc[0]["outcome_20__availability"] == "CENSORED_AFTER_DATA_END"
    assert tail_generic.outcomes[0].status.value == "CENSORED_AFTER_DATA_END"


def test_import_and_runtime_boundaries_do_not_load_or_call_forbidden_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, snapshot, observations = _basic_fixture()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("outcome panel crossed a forbidden strategy/feature/simulator boundary")

    import strategy.scanner as scanner
    import backtesting.portfolio_simulator as simulator
    import quantlab.features.registry as registry
    monkeypatch.setattr(scanner, "evaluate_prepared_row", forbidden)
    monkeypatch.setattr(simulator, "PortfolioSimulator", forbidden)
    monkeypatch.setattr(registry.FeatureRegistry, "compute", forbidden)
    result = build_point_in_time_outcome_panel(observations, snapshot)
    assert result.observation_row_count == observations.total_membership_row_count


def test_real_database_bytes_are_unchanged(tmp_path: Path) -> None:
    sessions = _sessions(25)
    path = tmp_path / "market.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE prices (symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER)"
        )
        rows = []
        for symbol, offset in (("VNINDEX", 100.0), ("AAA", 10.0)):
            rows.extend(
                (symbol, date, offset + index, offset + index, offset + index, offset + index, 1_000)
                for index, date in enumerate(sessions)
            )
        connection.executemany("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    snapshot = build_market_data_snapshot(path)
    context = PointInTimeUniverseContext.static(("AAA",), sessions[:2])
    observations = build_point_in_time_observation_index(
        snapshot, context, benchmark_symbol="VNINDEX", start_date=sessions[0], through_date=sessions[1],
    )
    before = path.read_bytes()
    panel = build_point_in_time_outcome_panel(observations, snapshot)
    assert panel.observation_row_count == 2
    assert path.read_bytes() == before
