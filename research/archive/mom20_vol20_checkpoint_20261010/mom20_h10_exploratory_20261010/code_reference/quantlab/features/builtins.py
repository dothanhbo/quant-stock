from __future__ import annotations

"""Parity-built minimal causal features from ``strategy.indicators`` semantics."""

from typing import Mapping

import numpy as np
import pandas as pd

from .contracts import FeatureDefinition, FeatureRequest, FeatureScope
from .universe_context import BREADTH_CONTEXT_KEY, PointInTimeUniverseContext


NEUTRAL_RESEARCH_SOURCE_TO_CANONICAL_V1 = (
    ("close", "close"),
    ("volume", "volume"),
    ("EMA10", "ema_10"),
    ("EMA20", "ema_20"),
    ("EMA50", "ema_50"),
    ("ATR14", "atr_14"),
    ("ATR_Percent", "atr_percent_14"),
    ("RSI", "rsi_14"),
    ("ADX14", "adx_14"),
    ("Vol_MA20", "volume_ma_20"),
    ("Vol_Ratio", "volume_ratio_20"),
    ("Stock_Return_20D", "stock_return_20d_pct"),
    ("Index_Return_20D", "benchmark_return_20d_pct"),
    ("Relative_Strength_20D", "relative_strength_20d_pct_points"),
    ("Distance_EMA20_Pct", "ema20_distance_pct"),
    ("Return_3D_Pct", "return_3d_pct"),
    ("breadth_ema50_pct", "breadth_ema50_pct"),
    ("breadth_ema50_change_10d", "breadth_ema50_change_10d_pct_points"),
)
NEUTRAL_RESEARCH_BENCHMARK_ROLE_V1 = "explicit_exact_date_benchmark_reference_v1"
NEUTRAL_RESEARCH_UNIVERSE_ROLE_V1 = "point_in_time_breadth_membership_context_v1"


def _period(parameters: Mapping[str, object]) -> int:
    period = parameters.get("period", 14)
    if not isinstance(period, int) or isinstance(period, bool) or period <= 0:
        raise ValueError("period must be a positive integer")
    return period


def _ema(frames, _dependencies, parameters):
    period = _period(parameters); column = f"EMA{period}"
    return {symbol: pd.DataFrame({"time": frame["time"], column: frame["close"].ewm(span=period, adjust=False).mean()}) for symbol, frame in frames.items()}


def _atr(frames, _dependencies, parameters):
    period = _period(parameters); column = f"ATR{period}"; result = {}
    for symbol, frame in frames.items():
        previous_close = frame["close"].shift(1)
        true_range = pd.concat([frame["high"] - frame["low"], (frame["high"] - previous_close).abs(), (frame["low"] - previous_close).abs()], axis=1).max(axis=1)
        result[symbol] = pd.DataFrame({"time": frame["time"], column: true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()})
    return result


def _donchian(frames, _dependencies, parameters):
    period = _period(parameters); reference = f"Previous_{period}D_High"; breakout = f"Breakout_{period}D"
    return {symbol: pd.DataFrame({"time": frame["time"], reference: frame["high"].shift(1).rolling(window=period, min_periods=period).max(), breakout: frame["close"] > frame["high"].shift(1).rolling(window=period, min_periods=period).max()}) for symbol, frame in frames.items()}


def _historical_candidate_core_subset(frames, dependencies, parameters):
    """Combine dependency outputs only; no indicator formula is duplicated."""
    ema10 = dependencies[FeatureRequest("ema", "v1", {"period": 10})]
    ema20 = dependencies[FeatureRequest("ema", "v1", {"period": 20})]
    ema50 = dependencies[FeatureRequest("ema", "v1", {"period": 50})]
    atr14 = dependencies[FeatureRequest("atr", "v1", {"period": 14})]
    donchian20 = dependencies[FeatureRequest("donchian", "v1", {"period": 20})]
    result = {}
    for symbol, raw in frames.items():
        result[symbol] = pd.DataFrame({
            # Symbol identity belongs to the bundle key.  Keeping the frame
            # itself numeric/datetime/bool allows the explicitly opted-in
            # ``npz_numeric_v1`` codec to cache this top-level bundle.
            "time": raw["time"], "open": raw["open"], "high": raw["high"], "low": raw["low"], "close": raw["close"], "volume": raw["volume"],
            "EMA10": ema10[symbol]["EMA10"], "EMA20": ema20[symbol]["EMA20"], "EMA50": ema50[symbol]["EMA50"], "ATR14": atr14[symbol]["ATR14"],
            "Previous_20D_High": donchian20[symbol]["Previous_20D_High"], "Breakout_20D": donchian20[symbol]["Breakout_20D"],
        })
    return result


def _rsi(frames, _dependencies, parameters):
    period = _period(parameters); column = f"RSI{period}"; result = {}
    for symbol, frame in frames.items():
        delta = frame["close"].diff()
        gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
        average_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        average_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        relative = average_gain / average_loss.replace(0, np.nan)
        value = 100 - (100 / (1 + relative))
        value = value.where(average_loss != 0, 100).where(average_gain != 0, 0)
        result[symbol] = pd.DataFrame({"time": frame["time"], column: value})
    return result


def _adx(frames, _dependencies, parameters):
    """The exact Wilder/min-period sequence used by strategy.calculate_adx."""
    period = _period(parameters); column = f"ADX{period}"; result = {}
    for symbol, frame in frames.items():
        high_diff, low_diff = frame["high"].diff(), -frame["low"].diff()
        plus = pd.Series(np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0), index=frame.index)
        minus = pd.Series(np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0.0), index=frame.index)
        previous_close = frame["close"].shift(1)
        true_range = pd.concat([frame["high"] - frame["low"], (frame["high"] - previous_close).abs(), (frame["low"] - previous_close).abs()], axis=1).max(axis=1)
        atr = true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        plus_smoothed = plus.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        minus_smoothed = minus.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
        plus_di, minus_di = 100 * plus_smoothed / atr.replace(0, np.nan), 100 * minus_smoothed / atr.replace(0, np.nan)
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
        result[symbol] = pd.DataFrame({"time": frame["time"], column: dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()})
    return result


def _volume_context(frames, _dependencies, parameters):
    average_period = _period(parameters); result = {}
    for symbol, frame in frames.items():
        average = frame["volume"].rolling(window=average_period, min_periods=average_period).mean()
        previous_max = frame["volume"].shift(1).rolling(window=5, min_periods=5).max()
        result[symbol] = pd.DataFrame({
            "time": frame["time"], "Vol_MA20": average,
            "Vol_Ratio": frame["volume"] / average.replace(0, np.nan),
            "Previous_5D_Max_Volume": previous_max,
            "Volume_Breakout_5D": frame["volume"] > previous_max,
        })
    return result


def _atr_percent(frames, dependencies, _parameters):
    atr = dependencies[FeatureRequest("atr", "v1", {"period": 14})]
    return {symbol: pd.DataFrame({"time": raw["time"], "ATR_Percent": atr[symbol]["ATR14"] / raw["close"].replace(0, np.nan) * 100}) for symbol, raw in frames.items()}


def _price_context(frames, dependencies, _parameters):
    ema10 = dependencies[FeatureRequest("ema", "v1", {"period": 10})]
    ema20 = dependencies[FeatureRequest("ema", "v1", {"period": 20})]
    donchian = dependencies[FeatureRequest("donchian", "v1", {"period": 20})]
    result = {}
    for symbol, raw in frames.items():
        close, high, low, opening = raw["close"], raw["high"], raw["low"], raw["open"]
        breakout = donchian[symbol]["Breakout_20D"]
        candle_range = high - low
        result[symbol] = pd.DataFrame({
            "time": raw["time"],
            "EMA20_Rising": ema20[symbol]["EMA20"] > ema20[symbol]["EMA20"].shift(1),
            "Recent_Breakout_10D": breakout.shift(1).rolling(window=10, min_periods=1).max().fillna(False).astype(bool),
            "Touched_EMA10": low <= ema10[symbol]["EMA10"] * 1.01,
            "Reclaimed_EMA10": close >= ema10[symbol]["EMA10"],
            "Distance_EMA20_Pct": (close - ema20[symbol]["EMA20"]) / ema20[symbol]["EMA20"].replace(0, np.nan) * 100,
            "Return_3D_Pct": close.pct_change(periods=3) * 100,
            "Body_Ratio": np.where(candle_range > 0, (close - opening).abs() / candle_range, 0.0),
            "Green_Candle": close > opening,
            "Close_Upper_Half": np.where(candle_range > 0, close >= low + candle_range * 0.5, True),
        })
    return result


def _historical_candidate_per_symbol_subset(frames, dependencies, _parameters):
    core = dependencies[FeatureRequest("historical_candidate_core_subset", "v1")]
    rsi = dependencies[FeatureRequest("rsi", "v1", {"period": 14})]
    adx = dependencies[FeatureRequest("adx", "v1", {"period": 14})]
    volume = dependencies[FeatureRequest("volume_context", "v1", {"period": 20})]
    atr_percent = dependencies[FeatureRequest("atr_percent", "v1")]
    context = dependencies[FeatureRequest("price_context", "v1")]
    result = {}
    for symbol, frame in core.items():
        result[symbol] = pd.concat([
            frame,
            rsi[symbol][["RSI14"]].rename(columns={"RSI14": "RSI"}),
            volume[symbol][["Vol_MA20", "Vol_Ratio", "Previous_5D_Max_Volume", "Volume_Breakout_5D"]],
            atr_percent[symbol][["ATR_Percent"]],
            adx[symbol][["ADX14"]],
            context[symbol][["EMA20_Rising", "Recent_Breakout_10D", "Touched_EMA10", "Reclaimed_EMA10", "Distance_EMA20_Pct", "Return_3D_Pct", "Body_Ratio", "Green_Candle", "Close_Upper_Half"]],
        ], axis=1)
    return result


def _benchmark_symbol(parameters: Mapping[str, object]) -> str:
    value = str(parameters.get("benchmark_symbol", "")).strip().upper()
    if not value:
        raise ValueError("benchmark_symbol is required")
    return value


def _benchmark_relative_context(frames, _dependencies, parameters):
    """Match add_relative_strength_columns' exact left-merge alignment."""
    benchmark_symbol = _benchmark_symbol(parameters)
    period = _period(parameters)
    if period != 20:
        raise ValueError("benchmark_relative_context v1 supports the authoritative 20-session lookback only")
    benchmark = frames.get(benchmark_symbol)
    if benchmark is None or benchmark.empty:
        raise ValueError(f"benchmark series is unavailable: {benchmark_symbol}")
    reference = benchmark[["time", "close"]].rename(columns={"close": "benchmark_close"})
    result = {}
    for symbol, frame in frames.items():
        aligned = frame[["time", "close"]].merge(reference, on="time", how="left")
        stock_return = (aligned["close"] / aligned["close"].shift(period) - 1) * 100
        index_return = (aligned["benchmark_close"] / aligned["benchmark_close"].shift(period) - 1) * 100
        result[symbol] = pd.DataFrame({
            "time": aligned["time"],
            "Stock_Return_20D": stock_return,
            "Index_Return_20D": index_return,
            "Relative_Strength_20D": stock_return - index_return,
        })
    return result


def _benchmark_relative_dependencies(request: FeatureRequest) -> tuple[FeatureRequest, ...]:
    parameters = request.parameter_mapping
    benchmark_symbol = _benchmark_symbol(parameters)
    period = _period(parameters)
    return (
        FeatureRequest("historical_candidate_per_symbol_subset", "v2"),
        FeatureRequest("benchmark_relative_context", "v1", {"benchmark_symbol": benchmark_symbol, "period": period}),
    )


def _historical_candidate_benchmark_relative_subset(frames, dependencies, parameters):
    core = dependencies[FeatureRequest("historical_candidate_per_symbol_subset", "v2")]
    context = dependencies[FeatureRequest("benchmark_relative_context", "v1", {
        "benchmark_symbol": _benchmark_symbol(parameters), "period": _period(parameters),
    })]
    return {
        symbol: pd.concat([frame, context[symbol][["Stock_Return_20D", "Index_Return_20D", "Relative_Strength_20D"]]], axis=1)
        for symbol, frame in core.items()
    }


def _historical_market_regime(frames, _dependencies, parameters):
    """Exact causal history authority from prepare_market_regime_history."""
    benchmark_symbol = _benchmark_symbol(parameters)
    benchmark = frames.get(benchmark_symbol)
    if benchmark is None or benchmark.empty:
        raise ValueError(f"benchmark series is unavailable: {benchmark_symbol}")
    frame = benchmark[["time", "close"]].copy()
    close = frame["close"]
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    slope = (ema50 / ema50.shift(10) - 1) * 100
    return_20d = (close / close.shift(20) - 1) * 100
    enough_history = pd.Series(np.arange(len(frame)), index=frame.index) >= 199
    bull = enough_history & (close > ema50) & (ema50 > ema200) & (slope > 0) & (return_20d > -2)
    bear = enough_history & (close < ema200) & (ema50 < ema200) & (slope < 0)
    regime = np.full(len(frame), "UNKNOWN", dtype=object)
    regime[bull] = "BULL"
    regime[bear] = "BEAR"
    regime[enough_history & ~bull & ~bear] = "SIDEWAY"
    return {benchmark_symbol: pd.DataFrame({"time": frame["time"], "Market_Regime": regime})}


def _market_context_dependencies(request: FeatureRequest) -> tuple[FeatureRequest, ...]:
    parameters = request.parameter_mapping
    return (
        FeatureRequest("historical_candidate_benchmark_relative_subset", "v3", {
            "benchmark_symbol": _benchmark_symbol(parameters), "period": 20,
        }),
        FeatureRequest("historical_market_regime", "v1", {"benchmark_symbol": _benchmark_symbol(parameters)}),
    )


def _historical_candidate_market_context_subset(frames, dependencies, parameters):
    benchmark_symbol = _benchmark_symbol(parameters)
    core = dependencies[FeatureRequest("historical_candidate_benchmark_relative_subset", "v3", {
        "benchmark_symbol": benchmark_symbol, "period": 20,
    })]
    regime = dependencies[FeatureRequest("historical_market_regime", "v1", {"benchmark_symbol": benchmark_symbol})][benchmark_symbol]
    return {
        symbol: frame.merge(regime, on="time", how="left")
        for symbol, frame in core.items()
    }


def _historical_breadth_context(frames, _dependencies, _parameters, context):
    """Mirror HistoricalBreadthIndex using the supplied immutable memberships."""
    if not isinstance(context, PointInTimeUniverseContext):
        raise ValueError("historical_breadth_context requires PointInTimeUniverseContext")
    rows_by_date: dict[str, list[tuple[str, float, int, float]]] = {}
    alpha = 2.0 / 51.0
    for symbol in context.candidate_symbols:
        frame = frames.get(symbol)
        if frame is None:
            continue
        previous: float | None = None; count = 0
        for time, close in frame[["time", "close"]].itertuples(index=False, name=None):
            try:
                value = float(close)
            except (TypeError, ValueError):
                continue
            if not np.isfinite(value):
                continue
            count += 1; ema = value if previous is None else alpha * value + (1.0 - alpha) * previous
            previous = ema
            rows_by_date.setdefault(pd.Timestamp(time).date().isoformat(), []).append((symbol, value, count, ema))
    prior: list[float] = []; output: list[dict[str, object]] = []
    for date_text in sorted(rows_by_date):
        applicable = context.members_as_of(date_text)
        values = [close > ema for symbol, close, count, ema in rows_by_date[date_text] if symbol in applicable and count >= 50]
        if not values:
            continue
        breadth = sum(values) / len(values) * 100.0
        change = breadth - prior[-10] if len(prior) >= 10 else float("nan")
        prior.append(breadth)
        output.append({
            "time": pd.Timestamp(date_text),
            "breadth_ema50_pct": round(breadth, 4),
            "breadth_ema50_change_10d": round(change, 4) if np.isfinite(change) else float("nan"),
            "breadth_universe_count": len(values),
        })
    return {BREADTH_CONTEXT_KEY: pd.DataFrame(output, columns=("time", "breadth_ema50_pct", "breadth_ema50_change_10d", "breadth_universe_count"))}


def _breadth_context_dependencies(request: FeatureRequest) -> tuple[FeatureRequest, ...]:
    parameters = request.parameter_mapping
    return (
        FeatureRequest("historical_candidate_market_context_subset", "v4", {"benchmark_symbol": _benchmark_symbol(parameters)}),
        FeatureRequest("historical_breadth_context", "v1"),
    )


def _historical_candidate_breadth_context_subset(frames, dependencies, parameters):
    benchmark = _benchmark_symbol(parameters)
    core = dependencies[FeatureRequest("historical_candidate_market_context_subset", "v4", {"benchmark_symbol": benchmark})]
    breadth = dependencies[FeatureRequest("historical_breadth_context", "v1")][BREADTH_CONTEXT_KEY]
    return {symbol: frame.merge(breadth, on="time", how="left") for symbol, frame in core.items()}


def _neutral_research_parameters(parameters: Mapping[str, object]) -> tuple[str, tuple[str, ...]]:
    benchmark = _benchmark_symbol(parameters)
    raw_primary = parameters.get("primary_symbols", ())
    if not isinstance(raw_primary, (list, tuple)):
        raise ValueError("neutral research primary_symbols must be a sequence")
    primary = tuple(sorted({str(symbol).strip().upper() for symbol in raw_primary if str(symbol).strip()} - {benchmark}))
    raw_mapping = parameters.get("source_to_canonical_mapping", ())
    if not isinstance(raw_mapping, (list, tuple)) or not all(
        isinstance(item, Mapping) for item in raw_mapping
    ):
        raise ValueError("neutral research source-to-canonical mapping must be an ordered sequence")
    supplied_mapping = tuple(
        (str(item.get("source", "")), str(item.get("canonical", "")))
        for item in raw_mapping
    )
    if supplied_mapping != NEUTRAL_RESEARCH_SOURCE_TO_CANONICAL_V1:
        raise ValueError("neutral research source-to-canonical mapping must match v1")
    if parameters.get("benchmark_role") != NEUTRAL_RESEARCH_BENCHMARK_ROLE_V1:
        raise ValueError("neutral research benchmark role must match v1")
    if parameters.get("universe_context_role") != NEUTRAL_RESEARCH_UNIVERSE_ROLE_V1:
        raise ValueError("neutral research universe-context role must match v1")
    return benchmark, primary


def _neutral_research_dependencies(request: FeatureRequest) -> tuple[FeatureRequest, ...]:
    benchmark, _primary = _neutral_research_parameters(request.parameter_mapping)
    return (
        FeatureRequest("historical_candidate_per_symbol_subset", "v2"),
        FeatureRequest("benchmark_relative_context", "v1", {
            "benchmark_symbol": benchmark,
            "period": 20,
        }),
        FeatureRequest("historical_breadth_context", "v1"),
    )


def _neutral_research_numeric_features(_frames, dependencies, parameters, context):
    if not isinstance(context, PointInTimeUniverseContext):
        raise ValueError("neutral research numeric features require PointInTimeUniverseContext")
    benchmark, primary = _neutral_research_parameters(parameters)
    per_symbol = dependencies[FeatureRequest("historical_candidate_per_symbol_subset", "v2")]
    relative = dependencies[FeatureRequest("benchmark_relative_context", "v1", {
        "benchmark_symbol": benchmark,
        "period": 20,
    })]
    breadth = dependencies[FeatureRequest("historical_breadth_context", "v1")][BREADTH_CONTEXT_KEY]
    result = {}
    per_symbol_columns = (
        "close", "volume", "EMA10", "EMA20", "EMA50", "ATR14",
        "ATR_Percent", "RSI", "ADX14", "Vol_MA20", "Vol_Ratio",
        "Distance_EMA20_Pct", "Return_3D_Pct",
    )
    relative_columns = ("Stock_Return_20D", "Index_Return_20D", "Relative_Strength_20D")
    breadth_columns = ("breadth_ema50_pct", "breadth_ema50_change_10d")
    rename = dict(NEUTRAL_RESEARCH_SOURCE_TO_CANONICAL_V1)
    canonical_columns = tuple(canonical for _source, canonical in NEUTRAL_RESEARCH_SOURCE_TO_CANONICAL_V1)
    for symbol in primary:
        base = per_symbol.get(symbol)
        reference = relative.get(symbol)
        if base is None or reference is None:
            continue
        frame = base.loc[:, ("time", *per_symbol_columns)].merge(
            reference.loc[:, ("time", *relative_columns)], on="time", how="left",
        ).merge(
            breadth.loc[:, ("time", *breadth_columns)], on="time", how="left",
        ).rename(columns=rename)
        result[symbol] = frame.loc[:, ("time", *canonical_columns)]
    return result


PAPER_STATE_CONTEXT_KEY = "__QUANTLAB_HISTORICAL_PAPER_STATE_CONTEXT__"


def _classify_paper_v2_state(signal):
    """Isolated exact parity copy; importing the production gate has I/O side effects."""
    regime = str(signal.get("regime", "")).upper()
    try:
        breadth = float(signal.get("breadth_ema50_pct"))
    except (TypeError, ValueError):
        breadth = float("nan")
    try:
        change = float(signal.get("breadth_ema50_change_10d"))
    except (TypeError, ValueError):
        change = float("nan")
    breadth = breadth if np.isfinite(breadth) else float("nan")
    change = change if np.isfinite(change) else float("nan")
    if regime == "BEAR":
        return "BEAR"
    if regime == "BULL":
        if np.isfinite(breadth) and np.isfinite(change):
            if breadth < 50.0 and change < 0.0:
                return "DIVERGENT_BULL"
            if breadth >= 70.0 and change >= 0.0:
                return "HEALTHY_BULL"
        return "FRAGILE_BULL"
    if regime == "SIDEWAY" and np.isfinite(breadth) and np.isfinite(change):
        if breadth >= 60.0 and change > 0.0:
            return "RECOVERY"
    return "NEUTRAL"


def _paper_state_dependencies(request: FeatureRequest) -> tuple[FeatureRequest, ...]:
    return (FeatureRequest("historical_candidate_breadth_context_subset", "v5", {"benchmark_symbol": _benchmark_symbol(request.parameter_mapping)}),)


def _historical_paper_market_state(_frames, dependencies, parameters, context):
    if not isinstance(context, PointInTimeUniverseContext):
        raise ValueError("historical_paper_market_state requires PointInTimeUniverseContext")
    source = dependencies[FeatureRequest("historical_candidate_breadth_context_subset", "v5", {"benchmark_symbol": _benchmark_symbol(parameters)})]
    if not source:
        return {PAPER_STATE_CONTEXT_KEY: pd.DataFrame(columns=("time", "paper_v2_state"))}
    frame = source[sorted(source)[0]][["time", "Market_Regime", "breadth_ema50_pct", "breadth_ema50_change_10d"]]
    states = [_classify_paper_v2_state({"regime": row.Market_Regime, "breadth_ema50_pct": row.breadth_ema50_pct, "breadth_ema50_change_10d": row.breadth_ema50_change_10d}) for row in frame.itertuples(index=False)]
    return {PAPER_STATE_CONTEXT_KEY: pd.DataFrame({"time": frame["time"], "paper_v2_state": states})}


def _paper_state_composite_dependencies(request: FeatureRequest) -> tuple[FeatureRequest, ...]:
    benchmark = _benchmark_symbol(request.parameter_mapping)
    return (
        FeatureRequest("historical_candidate_breadth_context_subset", "v5", {"benchmark_symbol": benchmark}),
        FeatureRequest("historical_paper_market_state", "v1", {"benchmark_symbol": benchmark}),
    )


def _historical_candidate_paper_state_subset(frames, dependencies, parameters):
    benchmark = _benchmark_symbol(parameters)
    core = dependencies[FeatureRequest("historical_candidate_breadth_context_subset", "v5", {"benchmark_symbol": benchmark})]
    state = dependencies[FeatureRequest("historical_paper_market_state", "v1", {"benchmark_symbol": benchmark})][PAPER_STATE_CONTEXT_KEY]
    return {symbol: frame.merge(state, on="time", how="left") for symbol, frame in core.items()}


def _parameter_warmup(parameters: Mapping[str, object]) -> int:
    return _period(parameters)


def builtin_definitions() -> tuple[FeatureDefinition, ...]:
    return (
        FeatureDefinition("ema", "v1", FeatureScope.PER_SYMBOL, ("time", "close"), direct_warmup_sessions=0, output_columns=("time",), compute=_ema),
        FeatureDefinition("atr", "v1", FeatureScope.PER_SYMBOL, ("time", "high", "low", "close"), direct_warmup_sessions=_parameter_warmup, output_columns=("time",), compute=_atr),
        FeatureDefinition("donchian", "v1", FeatureScope.PER_SYMBOL, ("time", "high", "close"), direct_warmup_sessions=_parameter_warmup, output_columns=("time",), compute=_donchian),
        FeatureDefinition("historical_candidate_core_subset", "v1", FeatureScope.PER_SYMBOL, ("time", "open", "high", "low", "close", "volume"), dependencies=(FeatureRequest("ema", "v1", {"period": 10}), FeatureRequest("ema", "v1", {"period": 20}), FeatureRequest("ema", "v1", {"period": 50}), FeatureRequest("atr", "v1", {"period": 14}), FeatureRequest("donchian", "v1", {"period": 20})), direct_warmup_sessions=0, output_columns=("time", "open", "high", "low", "close", "volume", "EMA10", "EMA20", "EMA50", "ATR14", "Previous_20D_High", "Breakout_20D"), compute=_historical_candidate_core_subset),
        # ``close.diff()`` makes the first gain/loss unavailable, so Wilder
        # RSI first becomes defined after period + 1 observed closes.
        FeatureDefinition("rsi", "v1", FeatureScope.PER_SYMBOL, ("time", "close"), direct_warmup_sessions=lambda parameters: _period(parameters) + 1, output_columns=("time",), compute=_rsi),
        FeatureDefinition("adx", "v1", FeatureScope.PER_SYMBOL, ("time", "high", "low", "close"), direct_warmup_sessions=lambda parameters: 2 * _period(parameters) - 1, output_columns=("time",), compute=_adx),
        FeatureDefinition("volume_context", "v1", FeatureScope.PER_SYMBOL, ("time", "volume"), direct_warmup_sessions=_parameter_warmup, output_columns=("time", "Vol_MA20", "Vol_Ratio", "Previous_5D_Max_Volume", "Volume_Breakout_5D"), compute=_volume_context),
        FeatureDefinition("atr_percent", "v1", FeatureScope.PER_SYMBOL, ("time", "close"), dependencies=(FeatureRequest("atr", "v1", {"period": 14}),), direct_warmup_sessions=0, output_columns=("time", "ATR_Percent"), compute=_atr_percent),
        FeatureDefinition("price_context", "v1", FeatureScope.PER_SYMBOL, ("time", "open", "high", "low", "close"), dependencies=(FeatureRequest("ema", "v1", {"period": 10}), FeatureRequest("ema", "v1", {"period": 20}), FeatureRequest("donchian", "v1", {"period": 20})), direct_warmup_sessions=0, output_columns=("time", "EMA20_Rising", "Recent_Breakout_10D", "Touched_EMA10", "Reclaimed_EMA10", "Distance_EMA20_Pct", "Return_3D_Pct", "Body_Ratio", "Green_Candle", "Close_Upper_Half"), compute=_price_context),
        FeatureDefinition("historical_candidate_per_symbol_subset", "v2", FeatureScope.PER_SYMBOL, ("time", "open", "high", "low", "close", "volume"), dependencies=(FeatureRequest("historical_candidate_core_subset", "v1"), FeatureRequest("rsi", "v1", {"period": 14}), FeatureRequest("adx", "v1", {"period": 14}), FeatureRequest("volume_context", "v1", {"period": 20}), FeatureRequest("atr_percent", "v1"), FeatureRequest("price_context", "v1")), direct_warmup_sessions=0, output_columns=("time", "open", "high", "low", "close", "volume", "EMA10", "EMA20", "EMA50", "ATR14", "Previous_20D_High", "Breakout_20D", "RSI", "Vol_MA20", "Vol_Ratio", "Previous_5D_Max_Volume", "Volume_Breakout_5D", "ATR_Percent", "ADX14", "EMA20_Rising", "Recent_Breakout_10D", "Touched_EMA10", "Reclaimed_EMA10", "Distance_EMA20_Pct", "Return_3D_Pct", "Body_Ratio", "Green_Candle", "Close_Upper_Half"), compute=_historical_candidate_per_symbol_subset),
        FeatureDefinition("benchmark_relative_context", "v1", FeatureScope.PER_SYMBOL, ("time", "close"), direct_warmup_sessions=lambda parameters: _period(parameters) + 1, output_columns=("time", "Stock_Return_20D", "Index_Return_20D", "Relative_Strength_20D"), compute=_benchmark_relative_context),
        FeatureDefinition("historical_candidate_benchmark_relative_subset", "v3", FeatureScope.PER_SYMBOL, ("time", "open", "high", "low", "close", "volume"), dependencies=_benchmark_relative_dependencies, direct_warmup_sessions=0, output_columns=("time", "open", "high", "low", "close", "volume", "EMA10", "EMA20", "EMA50", "ATR14", "Previous_20D_High", "Breakout_20D", "RSI", "Vol_MA20", "Vol_Ratio", "Previous_5D_Max_Volume", "Volume_Breakout_5D", "ATR_Percent", "ADX14", "EMA20_Rising", "Recent_Breakout_10D", "Touched_EMA10", "Reclaimed_EMA10", "Distance_EMA20_Pct", "Return_3D_Pct", "Body_Ratio", "Green_Candle", "Close_Upper_Half", "Stock_Return_20D", "Index_Return_20D", "Relative_Strength_20D"), compute=_historical_candidate_benchmark_relative_subset),
        FeatureDefinition("historical_market_regime", "v1", FeatureScope.MARKET, ("time", "close"), direct_warmup_sessions=200, output_columns=("time", "Market_Regime"), compute=_historical_market_regime),
        FeatureDefinition("historical_candidate_market_context_subset", "v4", FeatureScope.PER_SYMBOL, ("time", "open", "high", "low", "close", "volume"), dependencies=_market_context_dependencies, direct_warmup_sessions=0, output_columns=("time", "open", "high", "low", "close", "volume", "EMA10", "EMA20", "EMA50", "ATR14", "Previous_20D_High", "Breakout_20D", "RSI", "Vol_MA20", "Vol_Ratio", "Previous_5D_Max_Volume", "Volume_Breakout_5D", "ATR_Percent", "ADX14", "EMA20_Rising", "Recent_Breakout_10D", "Touched_EMA10", "Reclaimed_EMA10", "Distance_EMA20_Pct", "Return_3D_Pct", "Body_Ratio", "Green_Candle", "Close_Upper_Half", "Stock_Return_20D", "Index_Return_20D", "Relative_Strength_20D", "Market_Regime"), compute=_historical_candidate_market_context_subset),
        FeatureDefinition("historical_breadth_context", "v1", FeatureScope.CROSS_SECTIONAL, ("time", "close"), uses_execution_context=True, direct_warmup_sessions=50, output_columns=("time", "breadth_ema50_pct", "breadth_ema50_change_10d", "breadth_universe_count"), compute=_historical_breadth_context),
        FeatureDefinition("historical_candidate_breadth_context_subset", "v5", FeatureScope.PER_SYMBOL, ("time", "open", "high", "low", "close", "volume"), dependencies=_breadth_context_dependencies, direct_warmup_sessions=0, output_columns=("time", "open", "high", "low", "close", "volume", "EMA10", "EMA20", "EMA50", "ATR14", "Previous_20D_High", "Breakout_20D", "RSI", "Vol_MA20", "Vol_Ratio", "Previous_5D_Max_Volume", "Volume_Breakout_5D", "ATR_Percent", "ADX14", "EMA20_Rising", "Recent_Breakout_10D", "Touched_EMA10", "Reclaimed_EMA10", "Distance_EMA20_Pct", "Return_3D_Pct", "Body_Ratio", "Green_Candle", "Close_Upper_Half", "Stock_Return_20D", "Index_Return_20D", "Relative_Strength_20D", "Market_Regime", "breadth_ema50_pct", "breadth_ema50_change_10d", "breadth_universe_count"), compute=_historical_candidate_breadth_context_subset),
        FeatureDefinition("neutral_research_numeric_features", "v1", FeatureScope.CROSS_SECTIONAL, ("time", "open", "high", "low", "close", "volume"), dependencies=_neutral_research_dependencies, uses_execution_context=True, direct_warmup_sessions=0, output_columns=("time", *(canonical for _source, canonical in NEUTRAL_RESEARCH_SOURCE_TO_CANONICAL_V1)), compute=_neutral_research_numeric_features),
        FeatureDefinition("historical_paper_market_state", "v1", FeatureScope.CROSS_SECTIONAL, ("time",), dependencies=_paper_state_dependencies, uses_execution_context=True, direct_warmup_sessions=0, output_columns=("time", "paper_v2_state"), compute=_historical_paper_market_state),
        FeatureDefinition("historical_candidate_paper_state_subset", "v6", FeatureScope.PER_SYMBOL, ("time", "open", "high", "low", "close", "volume"), dependencies=_paper_state_composite_dependencies, direct_warmup_sessions=0, output_columns=("time", "open", "high", "low", "close", "volume", "EMA10", "EMA20", "EMA50", "ATR14", "Previous_20D_High", "Breakout_20D", "RSI", "Vol_MA20", "Vol_Ratio", "Previous_5D_Max_Volume", "Volume_Breakout_5D", "ATR_Percent", "ADX14", "EMA20_Rising", "Recent_Breakout_10D", "Touched_EMA10", "Reclaimed_EMA10", "Distance_EMA20_Pct", "Return_3D_Pct", "Body_Ratio", "Green_Candle", "Close_Upper_Half", "Stock_Return_20D", "Index_Return_20D", "Relative_Strength_20D", "Market_Regime", "breadth_ema50_pct", "breadth_ema50_change_10d", "breadth_universe_count", "paper_v2_state"), compute=_historical_candidate_paper_state_subset),
    )
