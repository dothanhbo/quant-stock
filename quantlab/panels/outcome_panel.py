from __future__ import annotations

"""Build future-looking close-return panels without crossing feature boundaries."""

from bisect import bisect_right
import math
from types import MappingProxyType
from typing import Any

import pandas as pd

from .contracts import OBSERVATION_INDEX_COLUMNS, PointInTimeObservationIndex
from .outcome_contracts import (
    OUTCOME_BASE_COLUMNS,
    PERMITTED_USE,
    POINT_IN_TIME_FORWARD_OUTCOMES_5_10_20_V1,
    PanelForwardOutcomeAvailability,
    PointInTimeOutcomePanel,
    PointInTimeOutcomePanelSpec,
)


def _close_value(value: Any) -> float | None:
    if value is None or value is pd.NA or pd.isna(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _close_map(frame: pd.DataFrame, *, symbol: str) -> tuple[tuple[str, ...], dict[str, float | None]]:
    if frame.empty:
        return (), {}
    missing = {"time", "close"}.difference(frame.columns)
    if missing:
        raise ValueError(f"market data for {symbol} is missing columns: {', '.join(sorted(missing))}")
    values: dict[str, float | None] = {}
    for raw_date, raw_close in frame.loc[:, ["time", "close"]].itertuples(index=False, name=None):
        parsed = pd.to_datetime(raw_date, errors="coerce")
        if pd.isna(parsed):
            continue
        date_text = pd.Timestamp(parsed).date().isoformat()
        values[date_text] = _close_value(raw_close)
    return tuple(sorted(values)), values


def _status(
    *,
    market_row_available: bool,
    stock_signal: float | None,
    benchmark_signal: float | None,
    target_session: str | None,
    benchmark_target: float | None,
    stock_target: float | None,
) -> PanelForwardOutcomeAvailability:
    if not market_row_available:
        return PanelForwardOutcomeAvailability.OBSERVATION_MARKET_ROW_MISSING
    if stock_signal is None:
        return PanelForwardOutcomeAvailability.MISSING_STOCK_SIGNAL_CLOSE
    if stock_signal <= 0:
        return PanelForwardOutcomeAvailability.NONPOSITIVE_STOCK_SIGNAL_CLOSE
    if benchmark_signal is None:
        return PanelForwardOutcomeAvailability.MISSING_BENCHMARK_SIGNAL_CLOSE
    if benchmark_signal <= 0:
        return PanelForwardOutcomeAvailability.NONPOSITIVE_BENCHMARK_SIGNAL_CLOSE
    if target_session is None:
        return PanelForwardOutcomeAvailability.CENSORED_AFTER_DATA_END
    if benchmark_target is None:
        return PanelForwardOutcomeAvailability.MISSING_BENCHMARK_TARGET_CLOSE
    if benchmark_target <= 0:
        return PanelForwardOutcomeAvailability.NONPOSITIVE_BENCHMARK_TARGET_CLOSE
    if stock_target is None:
        return PanelForwardOutcomeAvailability.MISSING_STOCK_TARGET_CLOSE
    if stock_target <= 0:
        return PanelForwardOutcomeAvailability.NONPOSITIVE_STOCK_TARGET_CLOSE
    return PanelForwardOutcomeAvailability.AVAILABLE


def _return_pct(signal_close: float, target_close: float) -> float:
    return (target_close / signal_close - 1.0) * 100.0


def _freeze_status_counts(
    counts: dict[str, dict[str, int]],
) -> MappingProxyType:
    return MappingProxyType({
        horizon: MappingProxyType(dict(status_counts))
        for horizon, status_counts in counts.items()
    })


def build_point_in_time_outcome_panel(
    observation_index: PointInTimeObservationIndex,
    snapshot: Any,
    *,
    horizons: tuple[int, ...] = (5, 10, 20),
    spec: PointInTimeOutcomePanelSpec = POINT_IN_TIME_FORWARD_OUTCOMES_5_10_20_V1,
) -> PointInTimeOutcomePanel:
    """Label every observation using future benchmark-session closes.

    This function intentionally reads through the snapshot's last session. Its
    result is future-looking and is restricted to offline research evaluation.
    """
    if not isinstance(observation_index, PointInTimeObservationIndex):
        raise TypeError("observation_index must be PointInTimeObservationIndex")
    if not isinstance(spec, PointInTimeOutcomePanelSpec):
        raise TypeError("spec must be PointInTimeOutcomePanelSpec")
    requested_horizons = tuple(horizons)
    if not requested_horizons or any(
        isinstance(item, bool) or not isinstance(item, int) or item <= 0
        for item in requested_horizons
    ):
        raise ValueError("outcome horizons must be positive integers")
    if len(set(requested_horizons)) != len(requested_horizons):
        raise ValueError("outcome horizons must be unique")
    if requested_horizons != spec.horizons:
        raise ValueError("requested horizons must exactly match the outcome-panel specification")
    if not hasattr(snapshot, "snapshot_id") or not callable(getattr(snapshot, "load_ohlcv", None)):
        raise TypeError("snapshot must provide identity and load_ohlcv")
    if snapshot.snapshot_id != observation_index.snapshot_id:
        raise ValueError("snapshot identity does not match observation index")
    last_snapshot_session = getattr(snapshot, "last_session_date", None)
    if not isinstance(last_snapshot_session, str) or not last_snapshot_session.strip():
        raise ValueError("snapshot last_session_date is required for forward-outcome labeling")

    source = observation_index.frame
    if tuple(source.columns) != OBSERVATION_INDEX_COLUMNS:
        raise ValueError("observation index exposes an unsupported schema")
    benchmark = observation_index.benchmark_symbol
    if not source.empty and not bool(source["benchmark_symbol"].astype(str).eq(benchmark).all()):
        raise ValueError("observation benchmark metadata is inconsistent")
    requested_symbols = tuple(sorted({benchmark, *observation_index.symbols}))
    bundle = snapshot.load_ohlcv(
        requested_symbols,
        start_date=observation_index.effective_first_session_date,
        through_date=last_snapshot_session,
    )
    benchmark_frame = bundle.frame_for(benchmark)
    if benchmark_frame.empty:
        raise ValueError(f"benchmark series is unavailable for outcome labeling: {benchmark}")
    benchmark_sessions, benchmark_closes = _close_map(benchmark_frame, symbol=benchmark)
    closes_by_symbol = {
        symbol: _close_map(bundle.frame_for(symbol), symbol=symbol)[1]
        for symbol in observation_index.symbols
    }

    output = source.loc[:, OUTCOME_BASE_COLUMNS].copy(deep=True)
    available_per_row = [0] * len(source)
    status_counts: dict[str, dict[str, int]] = {}
    for horizon in spec.horizons:
        target_values: list[str | None] = []
        stock_returns: list[float | None] = []
        benchmark_returns: list[float | None] = []
        excess_returns: list[float | None] = []
        statuses: list[str] = []
        horizon_counts = {item.value: 0 for item in PanelForwardOutcomeAvailability}
        for position, row in enumerate(source.itertuples(index=False)):
            signal_date = str(row.session_date)
            symbol = str(row.symbol)
            target_position = bisect_right(benchmark_sessions, signal_date) + horizon - 1
            target_session = (
                benchmark_sessions[target_position]
                if target_position < len(benchmark_sessions)
                else None
            )
            stock_closes = closes_by_symbol.get(symbol, {})
            stock_signal = stock_closes.get(signal_date)
            benchmark_signal = benchmark_closes.get(signal_date)
            stock_target = None if target_session is None else stock_closes.get(target_session)
            benchmark_target = None if target_session is None else benchmark_closes.get(target_session)
            availability = _status(
                market_row_available=bool(row.market_row_available),
                stock_signal=stock_signal,
                benchmark_signal=benchmark_signal,
                target_session=target_session,
                benchmark_target=benchmark_target,
                stock_target=stock_target,
            )
            stock_return = benchmark_return = excess_return = None
            if availability is PanelForwardOutcomeAvailability.AVAILABLE:
                assert stock_signal is not None and stock_target is not None
                assert benchmark_signal is not None and benchmark_target is not None
                stock_return = _return_pct(stock_signal, stock_target)
                benchmark_return = _return_pct(benchmark_signal, benchmark_target)
                excess_return = stock_return - benchmark_return
                if not all(math.isfinite(item) for item in (stock_return, benchmark_return, excess_return)):
                    raise ValueError("available outcome calculation produced a non-finite value")
                available_per_row[position] += 1
            target_values.append(target_session)
            stock_returns.append(stock_return)
            benchmark_returns.append(benchmark_return)
            excess_returns.append(excess_return)
            statuses.append(availability.value)
            horizon_counts[availability.value] += 1
        output[f"target_session_{horizon}"] = pd.Series(target_values, dtype="string")
        output[f"stock_forward_return_{horizon}_pct"] = pd.Series(stock_returns, dtype="Float64")
        output[f"benchmark_forward_return_{horizon}_pct"] = pd.Series(benchmark_returns, dtype="Float64")
        output[f"excess_forward_return_{horizon}_pct_points"] = pd.Series(excess_returns, dtype="Float64")
        output[f"outcome_{horizon}__availability"] = pd.Series(statuses, dtype="string")
        status_counts[str(horizon)] = horizon_counts
    output["requested_horizon_count"] = pd.Series([len(spec.horizons)] * len(output), dtype="int64")
    output["available_horizon_count"] = pd.Series(available_per_row, dtype="int64")
    output["fully_labeled_outcome_row"] = pd.Series(
        [count == len(spec.horizons) for count in available_per_row], dtype="bool",
    )
    output = output.loc[:, spec.output_columns]
    observation_keys = tuple(
        tuple(row)
        for row in source.loc[:, OUTCOME_BASE_COLUMNS].itertuples(index=False, name=None)
    )
    fully = sum(count == len(spec.horizons) for count in available_per_row)
    partial = sum(0 < count < len(spec.horizons) for count in available_per_row)
    zero = sum(count == 0 for count in available_per_row)
    metadata = MappingProxyType({
        "future_looking": True,
        "permitted_use": PERMITTED_USE,
        "warning": "future closes are research labels, not causal features or tradable portfolio returns",
        "observation_row_count": len(output),
        "horizons": spec.horizons,
        "per_horizon_status_counts": _freeze_status_counts(status_counts),
        "per_horizon_available_counts": MappingProxyType({
            str(horizon): status_counts[str(horizon)][PanelForwardOutcomeAvailability.AVAILABLE.value]
            for horizon in spec.horizons
        }),
        "fully_labeled_row_count": fully,
        "partially_labeled_row_count": partial,
        "zero_available_row_count": zero,
        "first_observation_session": observation_index.effective_first_session_date,
        "last_observation_session": observation_index.effective_last_session_date,
        "last_snapshot_session_used_for_labeling": last_snapshot_session,
        "observation_index_identity": observation_index.identity,
        "snapshot_id": snapshot.snapshot_id,
        "universe_membership_identity": observation_index.universe_membership_identity,
        "benchmark_symbol": benchmark,
        "requested_start_date": observation_index.requested_start_date,
        "requested_through_date": observation_index.requested_through_date,
    })
    return PointInTimeOutcomePanel(
        spec=spec,
        observation_index_identity=observation_index.identity,
        observation_content_identity=observation_index.content_identity,
        snapshot_id=snapshot.snapshot_id,
        universe_membership_identity=observation_index.universe_membership_identity,
        benchmark_symbol=benchmark,
        requested_start_date=observation_index.requested_start_date,
        requested_through_date=observation_index.requested_through_date,
        session_audit=observation_index.session_audit,
        metadata=metadata,
        _frame=output,
        _observation_keys=observation_keys,
        _benchmark_session_dates=benchmark_sessions,
    )
