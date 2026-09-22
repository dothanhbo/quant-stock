from __future__ import annotations

"""Parity-built minimal causal features from ``strategy.indicators`` semantics."""

from typing import Mapping

import numpy as np
import pandas as pd

from .contracts import FeatureDefinition, FeatureRequest, FeatureScope


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
    )
