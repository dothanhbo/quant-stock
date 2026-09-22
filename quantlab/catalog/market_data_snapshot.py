from __future__ import annotations

"""Read-only identity and bulk access for logical ``prices`` datasets.

The snapshot fingerprint is a canonical JSON stream, rather than a materialized
JSON document. This keeps construction memory bounded by ``_STREAM_BATCH_SIZE``
plus small metadata. Physical SQLite details are deliberately excluded.
"""

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
from types import MappingProxyType
from typing import Any, Iterable, Iterator, Mapping

import pandas as pd

from core.paths import resolve_market_database_path


_OHLCV_COLUMNS = ("symbol", "time", "open", "high", "low", "close", "volume")
_SCHEMA_CONTRACT = "quantlab.market_data_snapshot.prices.v1"
# Private, documented test seam. The cursor never requests all rows at once.
_STREAM_BATCH_SIZE = 2_048


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _normalize_symbols(symbols: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}))


def _normalize_bound(value: str | date | datetime | None, *, name: str) -> str | None:
    if value is None:
        return None
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"{name} must be a valid date")
    return pd.Timestamp(parsed).date().isoformat()


def _readonly_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"market database not found: {path}")
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)


def _schema_metadata(connection: sqlite3.Connection) -> tuple[tuple[Any, ...], ...]:
    rows = connection.execute("PRAGMA table_info(prices)").fetchall()
    if not rows:
        raise ValueError("market database does not contain a prices table")
    metadata = tuple((row[1], row[2], row[3], row[4], row[5]) for row in rows)
    missing = set(_OHLCV_COLUMNS).difference(row[0] for row in metadata)
    if missing:
        raise ValueError("prices table is missing required columns: " + ", ".join(sorted(missing)))
    return metadata


def _schema_version(metadata: tuple[tuple[Any, ...], ...]) -> str:
    digest = sha256(_canonical_json({"contract": _SCHEMA_CONTRACT, "columns": metadata})).hexdigest()
    return f"prices-v1-{digest}"


def _query_parts(*, start_date: str | None, through_date: str | None, requested_symbols_json: str | None) -> tuple[str, list[str]]:
    """Build one deterministic keep-last data query.

    The window function makes the original ascending-rowid/keep-last duplicate
    rule explicit in SQLite. Market data is normalized to daily dates.
    """
    predicates = [
        "TRIM(p.symbol) <> ''", "date(p.time) IS NOT NULL", "p.open IS NOT NULL",
        "p.high IS NOT NULL", "p.low IS NOT NULL", "p.close IS NOT NULL",
    ]
    params: list[str] = []
    prefix = ""
    requested_predicate = ""
    if requested_symbols_json is not None:
        prefix = "requested(symbol) AS (SELECT value FROM json_each(?)), "
        params.append(requested_symbols_json)
        # IN materializes the JSON values as a membership set.  Unlike the
        # former join, it does not make SQLite scan prices once per JSON row.
        requested_predicate = "UPPER(TRIM(p.symbol)) IN (SELECT symbol FROM requested)"
    if start_date is not None:
        predicates.append("date(p.time) >= date(?)")
        params.append(start_date)
    if through_date is not None:
        predicates.append("date(p.time) <= date(?)")
        params.append(through_date)
    if requested_predicate:
        predicates.insert(0, requested_predicate)
    query = (
        "WITH " + prefix + "filtered AS MATERIALIZED ("
        "SELECT p.symbol, p.time, p.open, p.high, p.low, p.close, p.volume, p.rowid AS _rowid "
        "FROM prices AS p "
        f"WHERE {' AND '.join(predicates)}"
        "), normalized AS ("
        "SELECT UPPER(TRIM(p.symbol)) AS symbol, date(p.time) AS time, "
        "p.open, p.high, p.low, p.close, p.volume, p._rowid AS _rowid, "
        "ROW_NUMBER() OVER (PARTITION BY UPPER(TRIM(p.symbol)), date(p.time) ORDER BY p._rowid DESC) AS duplicate_rank "
        "FROM filtered AS p"
        ") SELECT symbol, time, open, high, low, close, volume, _rowid "
        "FROM normalized WHERE duplicate_rank = 1 ORDER BY symbol ASC, time ASC, _rowid ASC"
    )
    return query, params


def _read_price_rows(connection: sqlite3.Connection, *, start_date: str | None = None, through_date: str | None = None) -> list[tuple[Any, ...]]:
    """Compatibility helper retained for focused test instrumentation."""
    query, params = _query_parts(start_date=start_date, through_date=through_date, requested_symbols_json=None)
    return connection.execute(query, params).fetchall()


def _execute_json_query(
    connection: sqlite3.Connection,
    *,
    start_date: str | None,
    through_date: str | None,
    requested_symbols: tuple[str, ...],
) -> sqlite3.Cursor:
    query, params = _query_parts(
        start_date=start_date,
        through_date=through_date,
        requested_symbols_json=json.dumps(requested_symbols, ensure_ascii=False, separators=(",", ":")),
    )
    return connection.execute(query, params)


def _is_json_each_unavailable(error: sqlite3.OperationalError) -> bool:
    message = str(error).lower()
    return "json_each" in message and ("no such" in message or "not authorized" in message)


def _number_token(value: Any) -> str | None:
    if pd.isna(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        # Same coercion rule as the authoritative loader for a non-executable
        # volume value: it is represented as a logical null, not a dropped row.
        return None
    if math.isnan(number):
        return None
    if math.isinf(number):
        return "+inf" if number > 0 else "-inf"
    return format(number, ".17g")


def _normalized_row(raw: tuple[Any, ...]) -> tuple[Any, ...] | None:
    symbol, raw_time, *values, _rowid = raw
    normalized_symbol = str(symbol).strip().upper() if symbol is not None else ""
    # ``_query_parts`` already applies SQLite ``date()``: valid values here
    # are canonical YYYY-MM-DD strings. Avoid per-row pandas parsing while
    # streaming the full database solely for identity construction.
    normalized_time = str(raw_time) if raw_time is not None else ""
    if not normalized_symbol or len(normalized_time) != 10:
        return None
    normalized: list[Any] = []
    for index, value in enumerate(values):
        if value is None:
            if index < 4:
                return None
            normalized.append(None)
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            if index < 4:
                return None
            # Match ``pd.to_numeric(errors='coerce')`` used by the legacy
            # loader: invalid volume is retained until vectorized coercion.
            normalized.append(value)
            continue
        if index < 4 and math.isnan(number):
            return None
        # Retain SQLite's original numeric scalar so DataFrame construction
        # reproduces legacy int64 volume when every value is integral.
        normalized.append(value)
    if len(normalized) != 5:
        return None
    return (normalized_symbol, normalized_time, *normalized)


def _stream_normalized_rows(cursor: sqlite3.Cursor) -> Iterator[tuple[Any, ...]]:
    """Yield rows from bounded ``fetchmany`` batches; never call ``fetchall``."""
    while batch := cursor.fetchmany(_STREAM_BATCH_SIZE):
        for raw in batch:
            normalized = _normalized_row(raw)
            if normalized is not None:
                yield normalized


def _logical_row(row: tuple[Any, ...]) -> list[str | None]:
    time_value = row[1]
    timestamp = (
        f"{time_value}T00:00:00"
        if isinstance(time_value, str) and len(time_value) == 10
        else pd.Timestamp(time_value).isoformat()
    )
    return [str(row[0]), timestamp, *(_number_token(value) for value in row[2:])]


def _logical_rows(frame: pd.DataFrame) -> list[list[str | None]]:
    """Reference serializer retained for benchmark compatibility and tests."""
    return [_logical_row(tuple(row)) for row in frame.loc[:, _OHLCV_COLUMNS].itertuples(index=False, name=None)]


def _normalized_frame(rows: Iterable[tuple[Any, ...]]) -> pd.DataFrame:
    """Reference normalization used only for focused-test compatibility."""
    normalized = [row for raw in rows if (row := _normalized_row(raw)) is not None]
    return pd.DataFrame(normalized, columns=_OHLCV_COLUMNS) if normalized else pd.DataFrame(columns=_OHLCV_COLUMNS)


def _frame_from_rows(rows: list[tuple[Any, ...]]) -> pd.DataFrame:
    """Apply the authoritative loader's vectorized dtype coercion per symbol."""
    frame = pd.DataFrame(rows, columns=_OHLCV_COLUMNS)
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    for column in _OHLCV_COLUMNS[2:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.reset_index(drop=True)


@dataclass(frozen=True, slots=True)
class MarketDataBundle:
    requested_symbols: tuple[str, ...]
    available_symbols: tuple[str, ...]
    missing_symbols: tuple[str, ...]
    start_date: str | None
    through_date: str | None
    row_count: int
    _frames: Mapping[str, pd.DataFrame]
    query_mode: str = "fallback_date_scan"
    sqlite_row_count: int = 0

    def frame_for(self, symbol: str) -> pd.DataFrame:
        frame = self._frames.get(str(symbol).strip().upper())
        return pd.DataFrame() if frame is None else frame.copy(deep=True)

    @property
    def frames(self) -> Mapping[str, pd.DataFrame]:
        return MappingProxyType({key: value.copy(deep=True) for key, value in self._frames.items()})


@dataclass(frozen=True, slots=True)
class MarketDataSnapshot:
    canonical_db_path: Path
    schema_version: str
    first_session_date: str | None
    last_session_date: str | None
    symbol_count: int
    row_count: int
    symbols: tuple[str, ...]
    logical_content_fingerprint: str
    snapshot_id: str

    def load_ohlcv(self, symbols: Iterable[str], *, start_date: str | date | datetime | None = None, through_date: str | date | datetime | None = None, end_date: str | date | datetime | None = None) -> MarketDataBundle:
        """Load only requested symbols in one data SELECT where JSON1 exists.

        JSON1 receives one encoded list parameter, avoiding variable limits. A
        JSON1-less SQLite build uses one read-only bounded-date query and an
        in-memory filter, never one query per symbol.
        """
        normalized_symbols = _normalize_symbols(symbols)
        start = _normalize_bound(start_date, name="start_date")
        through = _normalize_bound(through_date, name="through_date")
        end = _normalize_bound(end_date, name="end_date")
        if through is not None and end is not None and through != end:
            raise ValueError("through_date and end_date must match when both are supplied")
        through = through if through is not None else end
        if start is not None and through is not None and start > through:
            raise ValueError("start_date must be on or before through_date")
        if not normalized_symbols:
            return MarketDataBundle((), (), (), start, through, 0, MappingProxyType({}), "empty", 0)
        with _readonly_connection(self.canonical_db_path) as connection:
            try:
                cursor = _execute_json_query(
                    connection, start_date=start, through_date=through, requested_symbols=normalized_symbols,
                )
                query_mode = "json_each"
            except sqlite3.OperationalError as error:
                if not _is_json_each_unavailable(error):
                    raise
                query, params = _query_parts(start_date=start, through_date=through, requested_symbols_json=None)
                cursor = connection.execute(query, params)
                query_mode = "fallback_date_scan"
            rows = list(_stream_normalized_rows(cursor))
        if query_mode == "fallback_date_scan":
            requested = set(normalized_symbols)
            rows = [row for row in rows if row[0] in requested]
        grouped_rows: dict[str, list[tuple[Any, ...]]] = {}
        for row in rows:
            grouped_rows.setdefault(row[0], []).append(row)
        frames = {
            symbol: _frame_from_rows(group).copy(deep=True)
            for symbol, group in sorted(grouped_rows.items())
        }
        available = tuple(frames)
        missing = tuple(symbol for symbol in normalized_symbols if symbol not in frames)
        return MarketDataBundle(normalized_symbols, available, missing, start, through, len(rows), MappingProxyType(frames), query_mode, len(rows))


def build_market_data_snapshot(database_path: str | Path | None = None) -> MarketDataSnapshot:
    """Observe and fingerprint all normalized rows as one incremental JSON stream.

    Hash bytes exactly match the earlier compact serializer's sorted object:
    ``{"rows":[...],"schema_version":"..."}``.
    """
    canonical_path = resolve_market_database_path(database_path)
    with _readonly_connection(canonical_path) as connection:
        schema_version = _schema_version(_schema_metadata(connection))
        query, params = _query_parts(start_date=None, through_date=None, requested_symbols_json=None)
        digest = sha256(); digest.update(b'{"rows":[')
        row_count = 0; symbols: set[str] = set(); first: str | None = None; last: str | None = None
        for row in _stream_normalized_rows(connection.execute(query, params)):
            if row_count:
                digest.update(b",")
            digest.update(_canonical_json(_logical_row(row)))
            row_count += 1; symbols.add(row[0])
            session = row[1]
            first = session if first is None else min(first, session)
            last = session if last is None else max(last, session)
        digest.update(b'],"schema_version":'); digest.update(_canonical_json(schema_version)); digest.update(b"}")
    ordered_symbols = tuple(sorted(symbols))
    logical_fingerprint = digest.hexdigest()
    snapshot_id = sha256(_canonical_json({
        "schema_version": schema_version, "logical_content_fingerprint": logical_fingerprint,
        "first_session_date": first, "last_session_date": last, "row_count": row_count, "symbols": ordered_symbols,
    })).hexdigest()
    return MarketDataSnapshot(canonical_path, schema_version, first, last, len(ordered_symbols), row_count, ordered_symbols, logical_fingerprint, snapshot_id)
