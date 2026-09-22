from __future__ import annotations

"""Parity-built minimal causal features from ``strategy.indicators`` semantics."""

from typing import Mapping

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


def _parameter_warmup(parameters: Mapping[str, object]) -> int:
    return _period(parameters)


def builtin_definitions() -> tuple[FeatureDefinition, ...]:
    return (
        FeatureDefinition("ema", "v1", FeatureScope.PER_SYMBOL, ("time", "close"), direct_warmup_sessions=0, output_columns=("time",), compute=_ema),
        FeatureDefinition("atr", "v1", FeatureScope.PER_SYMBOL, ("time", "high", "low", "close"), direct_warmup_sessions=_parameter_warmup, output_columns=("time",), compute=_atr),
        FeatureDefinition("donchian", "v1", FeatureScope.PER_SYMBOL, ("time", "high", "close"), direct_warmup_sessions=_parameter_warmup, output_columns=("time",), compute=_donchian),
    )
