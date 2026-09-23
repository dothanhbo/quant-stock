from __future__ import annotations

"""Future-looking close-return labels; never imported by production paths."""

from bisect import bisect_right
from datetime import date
import math
from typing import Any, Protocol

import pandas as pd

from .contracts import (
    CandidateForwardOutcome,
    CandidateForwardOutcomeSet,
    ForwardOutcomeSpec,
    ForwardOutcomeStatus,
)

class _CandidateLike(Protocol):
    candidate_key: str
    symbol: str
    signal_date: str
    signal_close: object


class _CandidateBatchLike(Protocol):
    candidates: tuple[_CandidateLike, ...]
    batch_identity: str
    snapshot_id: str


_SIGNAL_CLOSE_REL_TOL = 1e-9
_SIGNAL_CLOSE_ABS_TOL = 1e-9


def _positive_close(value: Any, *, symbol: str, date_text: str) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if number <= 0:
        raise ValueError(f"close must be positive: {symbol}:{date_text}")
    return number


def _close_map(frame: pd.DataFrame, *, symbol: str) -> tuple[tuple[str, ...], dict[str, float | None]]:
    if frame.empty:
        return (), {}
    missing = {"time", "close"}.difference(frame.columns)
    if missing:
        raise ValueError(f"market data for {symbol} is missing columns: {', '.join(sorted(missing))}")
    dates: list[str] = []
    values: dict[str, float | None] = {}
    for raw_date, raw_close in frame[["time", "close"]].itertuples(index=False, name=None):
        parsed = pd.to_datetime(raw_date, errors="coerce")
        if pd.isna(parsed):
            continue
        date_text = pd.Timestamp(parsed).date().isoformat()
        dates.append(date_text)
        # MarketDataSnapshot already applies deterministic keep-last duplicate
        # handling. Assignment keeps that same final-row convention for test
        # doubles that expose an equivalent bundle.
        values[date_text] = _positive_close(raw_close, symbol=symbol, date_text=date_text)
    return tuple(sorted(set(dates))), values


def _return_pct(signal_close: float | None, target_close: float | None) -> float | None:
    if signal_close is None or target_close is None:
        return None
    return (target_close / signal_close - 1.0) * 100.0


def _validated_candidates(candidate_batch: object) -> tuple[_CandidateLike, ...]:
    missing = tuple(
        name for name in ("candidates", "batch_identity", "snapshot_id")
        if not hasattr(candidate_batch, name)
    )
    if missing:
        raise TypeError("candidate_batch is missing required fields: " + ", ".join(missing))
    candidates = getattr(candidate_batch, "candidates")
    if not isinstance(candidates, tuple):
        raise TypeError("candidate_batch.candidates must be an immutable tuple")
    for name in ("batch_identity", "snapshot_id"):
        value = getattr(candidate_batch, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"candidate_batch.{name} must be a non-empty string")
    keys: list[str] = []
    for candidate in candidates:
        absent = tuple(
            name for name in ("candidate_key", "symbol", "signal_date", "signal_close")
            if not hasattr(candidate, name)
        )
        if absent:
            raise TypeError("candidate is missing required fields: " + ", ".join(absent))
        key = getattr(candidate, "candidate_key")
        symbol = getattr(candidate, "symbol")
        signal_date = getattr(candidate, "signal_date")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("candidate_key must be a non-empty string")
        if not isinstance(symbol, str) or not symbol.strip() or symbol != symbol.strip().upper():
            raise ValueError(f"candidate symbol must be normalized uppercase: {key}")
        try:
            date.fromisoformat(str(signal_date))
        except ValueError as exc:
            raise ValueError(f"candidate signal_date must be a valid ISO date: {key}") from exc
        keys.append(key)
    if len(set(keys)) != len(keys):
        raise ValueError("candidate keys must be unique")
    return candidates


def label_candidate_forward_outcomes(
    snapshot,
    candidate_batch: _CandidateBatchLike,
    spec: ForwardOutcomeSpec,
) -> CandidateForwardOutcomeSet:
    """Label immutable candidates from one local snapshot union read.

    Labels deliberately use future closes.  Horizon ``h`` resolves to the
    ``h``-th benchmark session strictly after the candidate signal date; no
    stock or benchmark value is filled across dates.
    """
    if not isinstance(spec, ForwardOutcomeSpec):
        raise TypeError("spec must be ForwardOutcomeSpec")
    candidates = _validated_candidates(candidate_batch)
    if candidate_batch.snapshot_id != snapshot.snapshot_id:
        raise ValueError("candidate batch snapshot_id does not match supplied snapshot")
    if any(item.symbol == spec.benchmark_symbol for item in candidates):
        raise ValueError("benchmark cannot be treated as an equity candidate")
    if not candidates:
        return CandidateForwardOutcomeSet(
            (), candidate_batch.batch_identity, snapshot.snapshot_id,
            spec.fingerprint, spec.horizons,
        )

    requested_symbols = tuple(sorted({
        *(item.symbol for item in candidates),
        spec.benchmark_symbol,
    }))
    bundle = snapshot.load_ohlcv(
        requested_symbols,
        through_date=snapshot.last_session_date,
    )
    benchmark_dates, benchmark_closes = _close_map(
        bundle.frame_for(spec.benchmark_symbol), symbol=spec.benchmark_symbol,
    )
    closes_by_symbol: dict[str, dict[str, float | None]] = {}
    for symbol in requested_symbols:
        if symbol == spec.benchmark_symbol:
            continue
        _dates, closes_by_symbol[symbol] = _close_map(bundle.frame_for(symbol), symbol=symbol)

    outcomes: list[CandidateForwardOutcome] = []
    for candidate in candidates:
        symbol_closes = closes_by_symbol.get(candidate.symbol, {})
        stock_signal = symbol_closes.get(candidate.signal_date)
        benchmark_signal = benchmark_closes.get(candidate.signal_date)
        try:
            recorded_signal_close = float(candidate.signal_close)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"candidate signal_close must be a positive finite number: {candidate.candidate_key}") from exc
        if not math.isfinite(recorded_signal_close) or recorded_signal_close <= 0:
            raise ValueError(f"candidate signal_close must be a positive finite number: {candidate.candidate_key}")
        if stock_signal is not None:
            if not math.isclose(recorded_signal_close, stock_signal, rel_tol=_SIGNAL_CLOSE_REL_TOL, abs_tol=_SIGNAL_CLOSE_ABS_TOL):
                raise ValueError(
                    f"candidate signal_close does not match snapshot close: {candidate.candidate_key}; "
                    f"candidate={recorded_signal_close}, snapshot={stock_signal}"
                )
        signal_position = bisect_right(benchmark_dates, candidate.signal_date)
        for horizon in spec.horizons:
            target_position = signal_position + horizon - 1
            target_date = benchmark_dates[target_position] if target_position < len(benchmark_dates) else None
            stock_target = None if target_date is None else symbol_closes.get(target_date)
            benchmark_target = None if target_date is None else benchmark_closes.get(target_date)
            stock_return = _return_pct(stock_signal, stock_target)
            benchmark_return = _return_pct(benchmark_signal, benchmark_target)
            excess_return = None if stock_return is None or benchmark_return is None else stock_return - benchmark_return
            if target_date is None:
                status = ForwardOutcomeStatus.CENSORED_AFTER_DATA_END
            elif stock_signal is None:
                status = ForwardOutcomeStatus.MISSING_SIGNAL_CLOSE
            elif stock_target is None:
                status = ForwardOutcomeStatus.MISSING_TARGET_CLOSE
            elif benchmark_signal is None:
                status = ForwardOutcomeStatus.MISSING_BENCHMARK_SIGNAL_CLOSE
            elif benchmark_target is None:
                status = ForwardOutcomeStatus.MISSING_BENCHMARK_TARGET_CLOSE
            else:
                status = ForwardOutcomeStatus.AVAILABLE
            outcomes.append(CandidateForwardOutcome(
                candidate_key=candidate.candidate_key,
                symbol=candidate.symbol,
                signal_date=candidate.signal_date,
                horizon_sessions=horizon,
                target_market_session_date=target_date,
                status=status,
                signal_close=stock_signal,
                target_close=stock_target,
                stock_forward_return_pct=stock_return,
                benchmark_signal_close=benchmark_signal,
                benchmark_target_close=benchmark_target,
                benchmark_forward_return_pct=benchmark_return,
                excess_forward_return_percentage_points=excess_return,
                source_candidate_batch_identity=candidate_batch.batch_identity,
                snapshot_id=snapshot.snapshot_id,
                outcome_spec_fingerprint=spec.fingerprint,
            ))
    return CandidateForwardOutcomeSet(
        tuple(outcomes), candidate_batch.batch_identity, snapshot.snapshot_id,
        spec.fingerprint, spec.horizons,
    )
