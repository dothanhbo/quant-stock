from __future__ import annotations

"""Build a causal membership-by-benchmark-session observation index."""

from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable

import pandas as pd

from .contracts import (
    OBSERVATION_INDEX_COLUMNS,
    POINT_IN_TIME_OBSERVATION_INDEX_V1,
    ObservationAvailability,
    ObservationSessionAudit,
    PointInTimeObservationIndex,
    PointInTimeObservationIndexSpec,
    membership_set_hash,
)


def _date_text(value: str | date | datetime, *, name: str) -> str:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a valid date") from exc
    if pd.isna(timestamp):
        raise ValueError(f"{name} must be a valid date")
    return timestamp.date().isoformat()


def _symbol(value: Any, *, name: str) -> str:
    normalized = str(value).strip().upper()
    if not normalized:
        raise ValueError(f"{name} must be non-empty")
    return normalized


def _members(values: Iterable[Any], benchmark_symbol: str) -> tuple[str, ...]:
    return tuple(sorted({
        normalized
        for value in values
        if (normalized := str(value).strip().upper()) and normalized != benchmark_symbol
    }))


def _validate_inputs(snapshot: Any, universe_context: Any) -> None:
    snapshot_attributes = ("snapshot_id", "canonical_db_path", "load_ohlcv")
    if any(not hasattr(snapshot, name) for name in snapshot_attributes) or not callable(snapshot.load_ohlcv):
        raise TypeError("snapshot must provide MarketDataSnapshot-compatible provenance and load_ohlcv")
    if not isinstance(snapshot.snapshot_id, str) or not snapshot.snapshot_id.strip():
        raise ValueError("snapshot_id must be a non-empty string")
    context_attributes = ("membership_identity", "session_dates", "members_as_of")
    if any(not hasattr(universe_context, name) for name in context_attributes) or not callable(universe_context.members_as_of):
        raise TypeError("universe_context must provide immutable point-in-time membership")
    if not isinstance(universe_context.membership_identity, str) or not universe_context.membership_identity.strip():
        raise ValueError("universe membership identity must be non-empty")


def _requested_member_union(
    universe_context: Any,
    *,
    start_date: str,
    through_date: str,
    benchmark_symbol: str,
) -> tuple[str, ...]:
    boundaries = {start_date, through_date}
    for raw_date in universe_context.session_dates:
        session = _date_text(raw_date, name="universe session date")
        if start_date <= session <= through_date:
            boundaries.add(session)
    union: set[str] = set()
    for session in sorted(boundaries):
        union.update(_members(universe_context.members_as_of(session), benchmark_symbol))
    return tuple(sorted(union))


def _normalized_market_date(value: Any, *, symbol: str) -> str:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"invalid normalized market date for {symbol}") from exc
    if pd.isna(timestamp):
        raise ValueError(f"invalid normalized market date for {symbol}")
    if any((timestamp.hour, timestamp.minute, timestamp.second, timestamp.microsecond, timestamp.nanosecond)):
        raise ValueError(f"normalized market date must be midnight for {symbol}")
    return timestamp.date().isoformat()


def _date_ordinals(frame: pd.DataFrame, *, symbol: str) -> dict[str, int]:
    if frame.empty:
        return {}
    if "time" not in frame.columns:
        raise ValueError(f"normalized market frame is missing time: {symbol}")
    result: dict[str, int] = {}
    for ordinal, value in enumerate(frame["time"]):
        session = _normalized_market_date(value, symbol=symbol)
        if session in result:
            raise ValueError(f"duplicate normalized market row: {symbol}/{session}")
        result[session] = ordinal
    return result


def _observation_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(records, columns=OBSERVATION_INDEX_COLUMNS)
    string_columns = (
        "session_date", "symbol", "benchmark_symbol", "membership_set_hash",
        "availability", "source_market_time",
    )
    for column in string_columns:
        frame[column] = pd.Series(frame[column], dtype="string")
    frame["membership_count"] = pd.Series(frame["membership_count"], dtype="int64")
    frame["member_ordinal"] = pd.Series(frame["member_ordinal"], dtype="int64")
    frame["market_row_available"] = pd.Series(frame["market_row_available"], dtype="bool")
    frame["source_row_ordinal"] = pd.Series(frame["source_row_ordinal"], dtype="Int64")
    return frame.sort_values(["session_date", "symbol"], kind="stable").reset_index(drop=True)


def build_point_in_time_observation_index(
    snapshot: Any,
    universe_context: Any,
    *,
    benchmark_symbol: str,
    start_date: str | date | datetime,
    through_date: str | date | datetime,
    spec: PointInTimeObservationIndexSpec = POINT_IN_TIME_OBSERVATION_INDEX_V1,
) -> PointInTimeObservationIndex:
    """Build one immutable row per benchmark session and PIT universe member."""
    _validate_inputs(snapshot, universe_context)
    if not isinstance(spec, PointInTimeObservationIndexSpec):
        raise TypeError("spec must be PointInTimeObservationIndexSpec")
    benchmark = _symbol(benchmark_symbol, name="benchmark_symbol")
    start = _date_text(start_date, name="start_date")
    through = _date_text(through_date, name="through_date")
    if start > through:
        raise ValueError("start_date must be on or before through_date")
    requested_members = _requested_member_union(
        universe_context,
        start_date=start,
        through_date=through,
        benchmark_symbol=benchmark,
    )
    requested_symbols = tuple(sorted({benchmark, *requested_members}))
    bundle = snapshot.load_ohlcv(requested_symbols, start_date=start, through_date=through)
    if not hasattr(bundle, "frames"):
        raise TypeError("snapshot load_ohlcv must return a normalized market-data bundle")
    frames = bundle.frames
    if benchmark not in frames:
        snapshot_symbols = tuple(getattr(snapshot, "symbols", ()))
        if snapshot_symbols:
            if benchmark not in {_symbol(item, name="snapshot symbol") for item in snapshot_symbols}:
                raise ValueError(f"benchmark symbol is missing from snapshot: {benchmark}")
            raise ValueError("no benchmark sessions in requested interval")
        if benchmark in tuple(getattr(bundle, "missing_symbols", ())):
            raise ValueError(f"benchmark symbol is missing from snapshot: {benchmark}")
        raise ValueError("no benchmark sessions in requested interval")
    benchmark_ordinals = _date_ordinals(frames[benchmark], symbol=benchmark)
    sessions = tuple(sorted(session for session in benchmark_ordinals if start <= session <= through))
    if not sessions:
        raise ValueError("no benchmark sessions in requested interval")
    memberships = {
        session: _members(universe_context.members_as_of(session), benchmark)
        for session in sessions
    }
    actual_union = tuple(sorted(set().union(*memberships.values()))) if memberships else ()
    unloaded = sorted(set(actual_union).difference(requested_symbols))
    if unloaded:
        raise ValueError("universe context produced members outside its bounded session contract: " + ", ".join(unloaded))
    market_ordinals = {
        symbol: _date_ordinals(frames.get(symbol, pd.DataFrame()), symbol=symbol)
        for symbol in actual_union
    }
    records: list[dict[str, Any]] = []
    audits: list[ObservationSessionAudit] = []
    for session in sessions:
        members = memberships[session]
        set_hash = membership_set_hash(members)
        available_count = 0
        for ordinal, symbol in enumerate(members):
            source_ordinal = market_ordinals[symbol].get(session)
            available = source_ordinal is not None
            available_count += int(available)
            records.append({
                "session_date": session,
                "symbol": symbol,
                "benchmark_symbol": benchmark,
                "membership_set_hash": set_hash,
                "membership_count": len(members),
                "member_ordinal": ordinal,
                "market_row_available": available,
                "availability": (
                    ObservationAvailability.AVAILABLE.value
                    if available else ObservationAvailability.MISSING_EXACT_MARKET_ROW.value
                ),
                "source_market_time": session if available else None,
                "source_row_ordinal": source_ordinal,
            })
        audits.append(ObservationSessionAudit(
            session_date=session,
            membership_count=len(members),
            membership_set_hash=set_hash,
            emitted_observation_row_count=len(members),
            available_row_count=available_count,
            missing_row_count=len(members) - available_count,
        ))
    frame = _observation_frame(records)
    metadata = MappingProxyType({
        "contract": spec.name,
        "contract_version": spec.version,
        "canonical_database_path": str(Path(snapshot.canonical_db_path).resolve()),
        "snapshot_schema_version": getattr(snapshot, "schema_version", None),
        "snapshot_logical_content_fingerprint": getattr(snapshot, "logical_content_fingerprint", None),
        "universe_mode": getattr(universe_context, "universe_mode", None),
        "availability_evidence": "normalized exact-date market row only; malformed source rows are not distinguishable from absence",
    })
    return PointInTimeObservationIndex(
        spec=spec,
        canonical_database_path=str(Path(snapshot.canonical_db_path).resolve()),
        snapshot_id=snapshot.snapshot_id,
        universe_membership_identity=universe_context.membership_identity,
        benchmark_symbol=benchmark,
        requested_start_date=start,
        requested_through_date=through,
        session_audit=tuple(audits),
        symbols=tuple(sorted(set(frame["symbol"].astype(str)))),
        metadata=metadata,
        _frame=frame,
    )
