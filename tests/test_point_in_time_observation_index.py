from __future__ import annotations

from bisect import bisect_right
from dataclasses import FrozenInstanceError
from hashlib import sha256
from pathlib import Path
import sqlite3
from types import MappingProxyType, SimpleNamespace

import pandas as pd
import pytest

from quantlab.catalog import build_market_data_snapshot
from quantlab.features.universe_context import PointInTimeUniverseContext
from quantlab.identity import canonical_identity_value, canonical_json
from quantlab.panels import (
    POINT_IN_TIME_OBSERVATION_INDEX_V1,
    ObservationAvailability,
    PointInTimeObservationIndexSpec,
    build_point_in_time_observation_index,
)
from quantlab.panels.contracts import (
    OBSERVATION_INDEX_COLUMNS,
    observation_content_identity,
)


SESSIONS = ("2024-01-02", "2024-01-04", "2024-01-05")


def _write_database(path: Path, rows: list[tuple[object, ...]]) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE prices (symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER)"
        )
        connection.executemany("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def _row(symbol: str, session: str, close: float = 10.0) -> tuple[object, ...]:
    return symbol, session, close, close + 1.0, close - 1.0, close, 1_000


def _fixture(tmp_path: Path):
    path = tmp_path / "market.db"
    rows = [_row("VNINDEX", session, 1_000 + index) for index, session in enumerate(SESSIONS)]
    rows += [
        _row("AAA", SESSIONS[0]), _row("AAA", SESSIONS[1]),
        _row("BBB", "2024-01-03"), _row("CCC", SESSIONS[1]),
    ]
    _write_database(path, rows)
    snapshot = build_market_data_snapshot(path)
    context = PointInTimeUniverseContext.from_memberships(
        universe_mode="test_point_in_time",
        memberships={
            SESSIONS[0]: ("bbb", "AAA", "AAA", "VNINDEX"),
            SESSIONS[1]: ("ccc", "AAA"),
            SESSIONS[2]: (),
        },
    )
    return path, snapshot, context


class _CountingSnapshot:
    def __init__(self, snapshot) -> None:
        self._snapshot = snapshot
        self.calls: list[tuple[tuple[str, ...], str | None, str | None]] = []
        for name in (
            "canonical_db_path", "schema_version", "logical_content_fingerprint",
            "snapshot_id", "symbols",
        ):
            setattr(self, name, getattr(snapshot, name))

    def load_ohlcv(self, symbols, *, start_date=None, through_date=None, **kwargs):
        ordered = tuple(symbols)
        self.calls.append((ordered, start_date, through_date))
        return self._snapshot.load_ohlcv(
            ordered, start_date=start_date, through_date=through_date, **kwargs,
        )


def _build(tmp_path: Path):
    path, snapshot, context = _fixture(tmp_path)
    counted = _CountingSnapshot(snapshot)
    index = build_point_in_time_observation_index(
        counted,
        context,
        benchmark_symbol=" vnindex ",
        start_date=SESSIONS[0],
        through_date=SESSIONS[-1],
    )
    return path, counted, context, index


def test_exact_calendar_membership_availability_and_empty_session(tmp_path: Path) -> None:
    _, _, context, index = _build(tmp_path)
    frame = index.frame
    assert tuple(item.session_date for item in index.session_audit) == SESSIONS
    assert not frame["session_date"].isin(["2024-01-03", "2024-01-06"]).any()
    assert list(zip(frame["session_date"], frame["symbol"])) == [
        (SESSIONS[0], "AAA"), (SESSIONS[0], "BBB"),
        (SESSIONS[1], "AAA"), (SESSIONS[1], "CCC"),
    ]
    assert frame["benchmark_symbol"].unique().tolist() == ["VNINDEX"]
    assert "VNINDEX" not in set(frame["symbol"])
    assert index.requested_start_date == SESSIONS[0]
    assert index.requested_through_date == SESSIONS[-1]
    assert index.effective_first_session_date == SESSIONS[0]
    assert index.effective_last_session_date == SESSIONS[-1]
    assert index.benchmark_session_count == 3
    assert index.total_membership_row_count == 4
    assert index.available_row_count == 3
    assert index.missing_row_count == 1
    assert index.distinct_member_symbol_count == 3
    assert index.symbols == ("AAA", "BBB", "CCC")
    assert index.universe_membership_identity == context.membership_identity
    empty = index.session_audit[-1]
    assert empty.membership_count == empty.emitted_observation_row_count == 0
    assert empty.available_row_count == empty.missing_row_count == 0


def test_missing_exact_row_is_retained_without_nearest_date_fill(tmp_path: Path) -> None:
    _, _, _, index = _build(tmp_path)
    bbb = index.frame.loc[index.frame["symbol"] == "BBB"].iloc[0]
    assert not bool(bbb["market_row_available"])
    assert bbb["availability"] == ObservationAvailability.MISSING_EXACT_MARKET_ROW.value
    assert pd.isna(bbb["source_market_time"])
    assert pd.isna(bbb["source_row_ordinal"])
    aaa = index.frame.loc[
        (index.frame["session_date"] == SESSIONS[1]) & (index.frame["symbol"] == "AAA")
    ].iloc[0]
    assert bool(aaa["market_row_available"])
    assert aaa["availability"] == ObservationAvailability.AVAILABLE.value
    assert aaa["source_market_time"] == SESSIONS[1]
    assert aaa["source_row_ordinal"] == 1


def test_membership_hash_count_and_ordinal_are_stable(tmp_path: Path) -> None:
    _, snapshot, _ = _fixture(tmp_path)
    first = PointInTimeUniverseContext.from_memberships(
        universe_mode="test", memberships={day: values for day, values in {
            SESSIONS[0]: ("BBB", "AAA", "AAA"), SESSIONS[1]: ("CCC", "AAA"), SESSIONS[2]: (),
        }.items()},
    )
    second = PointInTimeUniverseContext.from_memberships(
        universe_mode="test", memberships={
            SESSIONS[2]: (), SESSIONS[1]: ("AAA", "CCC"), SESSIONS[0]: ("AAA", "BBB"),
        },
    )
    one = build_point_in_time_observation_index(snapshot, first, benchmark_symbol="VNINDEX", start_date=SESSIONS[0], through_date=SESSIONS[-1])
    two = build_point_in_time_observation_index(snapshot, second, benchmark_symbol="VNINDEX", start_date=SESSIONS[0], through_date=SESSIONS[-1])
    pd.testing.assert_frame_equal(one.frame, two.frame)
    assert one.content_identity == two.content_identity
    assert one.identity == two.identity
    day = one.frame.loc[one.frame["session_date"] == SESSIONS[0]]
    assert tuple(day["member_ordinal"]) == (0, 1)
    assert day["membership_count"].tolist() == [2, 2]
    assert day["membership_set_hash"].nunique() == 1


def test_one_bounded_snapshot_load_requests_benchmark_and_member_union(tmp_path: Path) -> None:
    _, counted, _, index = _build(tmp_path)
    assert len(counted.calls) == 1
    symbols, start, through = counted.calls[0]
    assert symbols == ("AAA", "BBB", "CCC", "VNINDEX")
    assert (start, through) == (SESSIONS[0], SESSIONS[-1])
    assert index.metadata["availability_evidence"].startswith("normalized exact-date")


def test_defensive_frame_immutable_metadata_and_frozen_audit(tmp_path: Path) -> None:
    _, _, _, index = _build(tmp_path)
    changed = index.frame
    changed.loc[0, "symbol"] = "MUTATED"
    assert "MUTATED" not in set(index.frame["symbol"])
    with pytest.raises(TypeError):
        index.metadata["x"] = "y"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        index.session_audit[0].membership_count = 99  # type: ignore[misc]


def test_counts_reconcile_and_frame_schema_has_no_object_cells(tmp_path: Path) -> None:
    _, _, _, index = _build(tmp_path)
    frame = index.frame
    assert tuple(frame.columns) == OBSERVATION_INDEX_COLUMNS
    assert index.total_membership_row_count == sum(item.emitted_observation_row_count for item in index.session_audit)
    assert index.available_row_count == sum(item.available_row_count for item in index.session_audit)
    assert index.missing_row_count == sum(item.missing_row_count for item in index.session_audit)
    assert not any(str(dtype) == "object" for dtype in frame.dtypes)


def test_membership_and_market_availability_changes_affect_content_identity(tmp_path: Path) -> None:
    path, snapshot, context = _fixture(tmp_path)
    original = build_point_in_time_observation_index(snapshot, context, benchmark_symbol="VNINDEX", start_date=SESSIONS[0], through_date=SESSIONS[-1])
    changed_context = PointInTimeUniverseContext.from_memberships(
        universe_mode="test_point_in_time",
        memberships={SESSIONS[0]: ("AAA",), SESSIONS[1]: ("AAA", "CCC"), SESSIONS[2]: ()},
    )
    changed_membership = build_point_in_time_observation_index(snapshot, changed_context, benchmark_symbol="VNINDEX", start_date=SESSIONS[0], through_date=SESSIONS[-1])
    assert changed_membership.content_identity != original.content_identity
    assert changed_membership.identity != original.identity
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", _row("BBB", SESSIONS[0]))
    changed_snapshot = build_market_data_snapshot(path)
    changed_availability = build_point_in_time_observation_index(changed_snapshot, context, benchmark_symbol="VNINDEX", start_date=SESSIONS[0], through_date=SESSIONS[-1])
    assert changed_availability.content_identity != original.content_identity
    assert changed_availability.identity != original.identity


def test_future_rows_preserve_bounded_content_but_change_snapshot_identity(tmp_path: Path) -> None:
    path, snapshot, context = _fixture(tmp_path)
    before = build_point_in_time_observation_index(snapshot, context, benchmark_symbol="VNINDEX", start_date=SESSIONS[0], through_date=SESSIONS[-1])
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)", _row("AAA", "2024-02-01"))
    later_snapshot = build_market_data_snapshot(path)
    after = build_point_in_time_observation_index(later_snapshot, context, benchmark_symbol="VNINDEX", start_date=SESSIONS[0], through_date=SESSIONS[-1])
    pd.testing.assert_frame_equal(before.frame, after.frame)
    assert before.content_identity == after.content_identity
    assert before.snapshot_id != after.snapshot_id
    assert before.identity != after.identity


def test_content_hash_matches_independent_materialized_reference_and_batch_sizes(tmp_path: Path) -> None:
    _, _, _, index = _build(tmp_path)
    rows = []
    for values in index.frame.itertuples(index=False, name=None):
        row = dict(zip(OBSERVATION_INDEX_COLUMNS, values))
        for name in ("source_market_time", "source_row_ordinal"):
            if pd.isna(row[name]):
                row[name] = None
        for name in ("membership_count", "member_ordinal"):
            row[name] = int(row[name])
        row["market_row_available"] = bool(row["market_row_available"])
        if row["source_row_ordinal"] is not None:
            row["source_row_ordinal"] = int(row["source_row_ordinal"])
        rows.append(row)
    payload = {
        "effective_sessions": [item.canonical_content() for item in index.session_audit],
        "rows": rows,
    }
    reference = sha256(canonical_json(canonical_identity_value(payload))).hexdigest()
    assert index.content_identity == reference
    assert observation_content_identity(index.session_audit, index.frame, batch_size=1) == reference
    assert observation_content_identity(index.session_audit, index.frame, batch_size=10_000) == reference


@pytest.mark.parametrize(
    ("start", "through", "message"),
    [
        ("2024-02-01", "2024-01-01", "on or before"),
        ("2024-01-06", "2024-01-07", "no benchmark sessions"),
    ],
)
def test_invalid_or_empty_intervals_fail(tmp_path: Path, start: str, through: str, message: str) -> None:
    _, snapshot, context = _fixture(tmp_path)
    with pytest.raises(ValueError, match=message):
        build_point_in_time_observation_index(snapshot, context, benchmark_symbol="VNINDEX", start_date=start, through_date=through)


def test_missing_benchmark_fails_clearly(tmp_path: Path) -> None:
    path = tmp_path / "no-benchmark.db"
    _write_database(path, [_row("AAA", SESSIONS[0])])
    snapshot = build_market_data_snapshot(path)
    context = PointInTimeUniverseContext.static(["AAA"], [SESSIONS[0]])
    with pytest.raises(ValueError, match="benchmark symbol is missing"):
        build_point_in_time_observation_index(snapshot, context, benchmark_symbol="VNINDEX", start_date=SESSIONS[0], through_date=SESSIONS[0])


def test_contract_validation_rejects_incompatible_inputs_and_schema(tmp_path: Path) -> None:
    _, snapshot, context = _fixture(tmp_path)
    with pytest.raises(TypeError, match="MarketDataSnapshot-compatible"):
        build_point_in_time_observation_index(object(), context, benchmark_symbol="VNINDEX", start_date=SESSIONS[0], through_date=SESSIONS[-1])
    invalid_context = SimpleNamespace(membership_identity="", session_dates=(), members_as_of=lambda _date: ())
    with pytest.raises(ValueError, match="identity"):
        build_point_in_time_observation_index(snapshot, invalid_context, benchmark_symbol="VNINDEX", start_date=SESSIONS[0], through_date=SESSIONS[-1])
    with pytest.raises(ValueError, match="output schema"):
        PointInTimeObservationIndexSpec(name="x", version="1", columns=("wrong",))


def test_database_bytes_are_unchanged_and_missing_database_is_not_created(tmp_path: Path) -> None:
    path, snapshot, context = _fixture(tmp_path)
    before = sha256(path.read_bytes()).hexdigest()
    build_point_in_time_observation_index(snapshot, context, benchmark_symbol="VNINDEX", start_date=SESSIONS[0], through_date=SESSIONS[-1])
    assert sha256(path.read_bytes()).hexdigest() == before
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        build_market_data_snapshot(missing)
    assert not missing.exists()


def test_large_membership_still_uses_one_snapshot_load(tmp_path: Path) -> None:
    member_symbols = tuple(f"S{index:04d}" for index in range(1_100))
    benchmark_frame = pd.DataFrame({
        "symbol": ["VNINDEX"], "time": [pd.Timestamp(SESSIONS[0])],
        "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1],
    })
    bundle = SimpleNamespace(
        frames=MappingProxyType({"VNINDEX": benchmark_frame}),
        missing_symbols=member_symbols,
    )

    class Snapshot:
        canonical_db_path = tmp_path / "unused.db"
        snapshot_id = "snapshot"
        schema_version = "schema"
        logical_content_fingerprint = "content"
        symbols = ("VNINDEX", *member_symbols)
        calls = 0

        def load_ohlcv(self, requested, **_kwargs):
            self.calls += 1
            assert set(requested) == {"VNINDEX", *member_symbols}
            return bundle

    snapshot = Snapshot()
    context = PointInTimeUniverseContext.static(reversed(member_symbols), [SESSIONS[0]])
    index = build_point_in_time_observation_index(snapshot, context, benchmark_symbol="VNINDEX", start_date=SESSIONS[0], through_date=SESSIONS[0])
    assert snapshot.calls == 1
    assert index.total_membership_row_count == len(member_symbols)
    assert index.available_row_count == 0

