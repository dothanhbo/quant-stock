from __future__ import annotations

"""Read-only identity and bulk access for logical ``prices`` datasets.

``MarketDataSnapshot`` identifies observed, normalized logical content.  It
is not a physical database backup: changing the underlying database and
building a new snapshot can produce a new identity.  Fingerprints serialize
schema metadata and normalized OHLCV rows as compact, sorted UTF-8 JSON.
SQLite file bytes, WAL state, mtime, VACUUM layout, and rowids are excluded
from that serialization.

Duplicate rows follow the existing historical loader's ``keep='last'``
semantics after ascending time order.  SQLite's existing loader does not
define a tie-breaker for conflicting duplicate times; this module makes its
observed query order explicit with ascending rowid.  Consequently conflicting
duplicates remain a data-quality ambiguity and should be cleaned upstream.
"""

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
from types import MappingProxyType
from typing import Any, Iterable, Mapping

import pandas as pd

from core.paths import resolve_market_database_path


_OHLCV_COLUMNS = ("symbol", "time", "open", "high", "low", "close", "volume")
_SCHEMA_CONTRACT = "quantlab.market_data_snapshot.prices.v1"


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


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
        raise ValueError(
            "prices table is missing required columns: " + ", ".join(sorted(missing))
        )
    return metadata


def _schema_version(metadata: tuple[tuple[Any, ...], ...]) -> str:
    digest = sha256(_canonical_json({"contract": _SCHEMA_CONTRACT, "columns": metadata})).hexdigest()
    return f"prices-v1-{digest}"


def _read_price_rows(
    connection: sqlite3.Connection,
    *,
    start_date: str | None = None,
    through_date: str | None = None,
) -> list[tuple[Any, ...]]:
    predicates: list[str] = []
    params: list[str] = []
    if start_date is not None:
        predicates.append("date(time) >= date(?)")
        params.append(start_date)
    if through_date is not None:
        predicates.append("date(time) <= date(?)")
        params.append(through_date)
    where = " WHERE " + " AND ".join(predicates) if predicates else ""
    return connection.execute(
        "SELECT symbol, time, open, high, low, close, volume, rowid "
        "FROM prices" + where + " "
        "ORDER BY UPPER(TRIM(symbol)), time ASC, rowid ASC",
        params,
    ).fetchall()


def _normalized_frame(rows: Iterable[tuple[Any, ...]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=(*_OHLCV_COLUMNS, "_rowid"))
    if frame.empty:
        return pd.DataFrame(columns=_OHLCV_COLUMNS)
    frame["symbol"] = frame["symbol"].map(lambda value: str(value).strip().upper() if value is not None else "")
    frame["time"] = pd.to_datetime(frame["time"], errors="coerce")
    for column in _OHLCV_COLUMNS[2:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    # Matches backtesting.engine.load_price_data: volume may be missing, but
    # time and executable OHLC values may not be.
    frame = frame.loc[frame["symbol"] != ""].dropna(
        subset=["time", "open", "high", "low", "close"]
    )
    frame = (
        frame.sort_values(["symbol", "time", "_rowid"], kind="stable")
        .drop_duplicates(subset=["symbol", "time"], keep="last")
        .sort_values(["symbol", "time"], kind="stable")
        .loc[:, _OHLCV_COLUMNS]
        .reset_index(drop=True)
    )
    return frame


def _number_token(value: Any) -> str | None:
    if pd.isna(value):
        return None
    number = float(value)
    if math.isnan(number):
        return None
    if math.isinf(number):
        return "+inf" if number > 0 else "-inf"
    return format(number, ".17g")


def _logical_rows(frame: pd.DataFrame) -> list[list[str | None]]:
    return [
        [
            str(row.symbol),
            pd.Timestamp(row.time).isoformat(),
            *(_number_token(getattr(row, column)) for column in _OHLCV_COLUMNS[2:]),
        ]
        for row in frame.itertuples(index=False)
    ]


@dataclass(frozen=True, slots=True)
class MarketDataBundle:
    """Defensive, per-symbol raw OHLCV result from one bounded query."""

    requested_symbols: tuple[str, ...]
    available_symbols: tuple[str, ...]
    missing_symbols: tuple[str, ...]
    start_date: str | None
    through_date: str | None
    row_count: int
    _frames: Mapping[str, pd.DataFrame]

    def frame_for(self, symbol: str) -> pd.DataFrame:
        """Return a deep copy; consumers cannot mutate this bundle's data."""
        normalized = str(symbol).strip().upper()
        frame = self._frames.get(normalized)
        return pd.DataFrame() if frame is None else frame.copy(deep=True)

    @property
    def frames(self) -> Mapping[str, pd.DataFrame]:
        """Return a fresh immutable mapping of defensive dataframe copies."""
        return MappingProxyType({key: value.copy(deep=True) for key, value in self._frames.items()})


@dataclass(frozen=True, slots=True)
class MarketDataSnapshot:
    """Immutable identity of normalized logical rows observed from ``prices``."""

    canonical_db_path: Path
    schema_version: str
    first_session_date: str | None
    last_session_date: str | None
    symbol_count: int
    row_count: int
    symbols: tuple[str, ...]
    logical_content_fingerprint: str
    snapshot_id: str

    def load_ohlcv(
        self,
        symbols: Iterable[str],
        *,
        start_date: str | date | datetime | None = None,
        through_date: str | date | datetime | None = None,
        end_date: str | date | datetime | None = None,
    ) -> MarketDataBundle:
        """Load requested normalized raw rows using one SQLite data query.

        ``through_date`` and ``end_date`` are aliases; supplying conflicting
        values is rejected.  The database is scanned only within the explicit
        bounds, then requested symbols are filtered in memory to avoid SQLite
        variable-count limits.
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
            return MarketDataBundle((), (), (), start, through, 0, MappingProxyType({}))
        with _readonly_connection(self.canonical_db_path) as connection:
            frame = _normalized_frame(_read_price_rows(connection, start_date=start, through_date=through))
        frame = frame.loc[frame["symbol"].isin(normalized_symbols)].copy()
        frames = {
            symbol: group.drop(columns=[]).reset_index(drop=True).copy(deep=True)
            for symbol, group in frame.groupby("symbol", sort=True)
        }
        available = tuple(frames)
        missing = tuple(symbol for symbol in normalized_symbols if symbol not in frames)
        return MarketDataBundle(
            normalized_symbols, available, missing, start, through, len(frame), MappingProxyType(frames)
        )


def build_market_data_snapshot(
    database_path: str | Path | None = None,
) -> MarketDataSnapshot:
    """Observe and fingerprint all normalized logical OHLCV rows read-only."""
    canonical_path = resolve_market_database_path(database_path)
    with _readonly_connection(canonical_path) as connection:
        metadata = _schema_metadata(connection)
        frame = _normalized_frame(_read_price_rows(connection))
    symbols = tuple(sorted(frame["symbol"].unique().tolist())) if not frame.empty else ()
    first = pd.Timestamp(frame["time"].min()).date().isoformat() if not frame.empty else None
    last = pd.Timestamp(frame["time"].max()).date().isoformat() if not frame.empty else None
    schema_version = _schema_version(metadata)
    logical_fingerprint = sha256(_canonical_json({
        "schema_version": schema_version,
        "rows": _logical_rows(frame),
    })).hexdigest()
    snapshot_id = sha256(_canonical_json({
        "schema_version": schema_version,
        "logical_content_fingerprint": logical_fingerprint,
        "first_session_date": first,
        "last_session_date": last,
        "row_count": len(frame),
        "symbols": symbols,
    })).hexdigest()
    return MarketDataSnapshot(
        canonical_db_path=canonical_path,
        schema_version=schema_version,
        first_session_date=first,
        last_session_date=last,
        symbol_count=len(symbols),
        row_count=len(frame),
        symbols=symbols,
        logical_content_fingerprint=logical_fingerprint,
        snapshot_id=snapshot_id,
    )
