from __future__ import annotations

"""Small, side-effect-free fakes shared by Quant Lab panel tests."""

from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence

import pandas as pd


def _symbols(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({
        str(value).strip().upper() for value in values if str(value).strip()
    }))


def make_ohlcv_frame(
    symbol: str,
    dates: Sequence[str],
    close_values: Sequence[object],
    *,
    open_values: Sequence[object] | None = None,
    high_values: Sequence[object] | None = None,
    low_values: Sequence[object] | None = None,
    volume_values: Sequence[object] | None = None,
    close_dtype: str | None = None,
) -> pd.DataFrame:
    """Build an explicit normalized OHLCV fixture frame."""
    sessions, closes = tuple(dates), tuple(close_values)

    def resolved(values: Sequence[object] | None, default: Sequence[object]) -> tuple[object, ...]:
        return tuple(default if values is None else values)

    opens = resolved(open_values, closes)
    highs = resolved(high_values, closes)
    lows = resolved(low_values, closes)
    volumes = resolved(volume_values, (1_000,) * len(sessions))
    if any(len(values) != len(sessions) for values in (closes, opens, highs, lows, volumes)):
        raise ValueError("every OHLCV value sequence must align with dates")
    close_series = pd.Series(closes, dtype=close_dtype) if close_dtype else pd.Series(closes)
    return pd.DataFrame({
        "symbol": [str(symbol).strip().upper()] * len(sessions),
        "time": pd.to_datetime(sessions),
        "open": opens,
        "high": highs,
        "low": lows,
        "close": close_series,
        "volume": volumes,
    })


class InMemoryMarketDataBundle:
    """Defensive fake exposing the complete public MarketDataBundle surface."""

    def __init__(
        self,
        frames: Mapping[str, pd.DataFrame],
        requested_symbols: Iterable[str],
        *,
        start_date: str | None,
        through_date: str | None,
    ) -> None:
        self.requested_symbols = _symbols(requested_symbols)
        copied = {
            symbol: frames[symbol].copy(deep=True)
            for symbol in self.requested_symbols
            if symbol in frames and not frames[symbol].empty
        }
        self.available_symbols = tuple(sorted(copied))
        self.missing_symbols = tuple(
            symbol for symbol in self.requested_symbols if symbol not in copied
        )
        self.start_date, self.through_date = start_date, through_date
        self.row_count = sum(len(frame) for frame in copied.values())
        self.query_mode = "in_memory"
        self.sqlite_row_count = self.row_count
        self._frames = MappingProxyType(copied)

    def frame_for(self, symbol: str) -> pd.DataFrame:
        frame = self._frames.get(str(symbol).strip().upper())
        return pd.DataFrame() if frame is None else frame.copy(deep=True)

    @property
    def frames(self) -> Mapping[str, pd.DataFrame]:
        return MappingProxyType({
            symbol: frame.copy(deep=True) for symbol, frame in self._frames.items()
        })


class InMemoryMarketDataSnapshot:
    """Deterministic bounded snapshot fake with load-call recording."""

    def __init__(
        self,
        frames: Mapping[str, pd.DataFrame],
        *,
        snapshot_id: str = "snapshot-v1",
        canonical_db_path: Path | str = Path("unused-in-memory-market.db"),
        schema_version: str = "schema-v1",
        logical_content_fingerprint: str = "logical-v1",
    ) -> None:
        normalized = {
            str(symbol).strip().upper(): frame.copy(deep=True)
            for symbol, frame in frames.items()
        }
        self._frames = MappingProxyType(normalized)
        self.snapshot_id = snapshot_id
        self.canonical_db_path = Path(canonical_db_path)
        self.schema_version = schema_version
        self.logical_content_fingerprint = logical_content_fingerprint
        self.symbols = tuple(sorted(normalized))
        self.symbol_count = len(self.symbols)
        dates = tuple(
            pd.Timestamp(value).date().isoformat()
            for frame in normalized.values() if "time" in frame
            for value in frame["time"]
        )
        self.first_session_date = min(dates) if dates else None
        self.last_session_date = max(dates) if dates else None
        self.calls: list[tuple[tuple[str, ...], str | None, str | None]] = []

    def load_ohlcv(
        self,
        symbols: Iterable[str],
        *,
        start_date: str | None = None,
        through_date: str | None = None,
        **_kwargs: object,
    ) -> InMemoryMarketDataBundle:
        requested = _symbols(symbols)
        self.calls.append((requested, start_date, through_date))
        selected: dict[str, pd.DataFrame] = {}
        for symbol in requested:
            frame = self._frames.get(symbol)
            if frame is None:
                continue
            bounded = frame
            if start_date is not None:
                bounded = bounded.loc[bounded["time"] >= pd.Timestamp(start_date)]
            if through_date is not None:
                bounded = bounded.loc[bounded["time"] <= pd.Timestamp(through_date)]
            if not bounded.empty:
                selected[symbol] = bounded.reset_index(drop=True)
        return InMemoryMarketDataBundle(
            selected,
            requested,
            start_date=start_date,
            through_date=through_date,
        )
