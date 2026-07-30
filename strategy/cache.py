"""Cache chỉ báo trong bộ nhớ để tránh tính lại cùng một bộ dữ liệu."""

from __future__ import annotations

from collections import OrderedDict
from threading import RLock

import pandas as pd

from config.strategy_loader import COMMON_CONFIG
from strategy.indicators import add_indicators

_MAX_SIZE = int(COMMON_CONFIG.get("indicator_cache_size", 256))
_CACHE: OrderedDict[tuple, pd.DataFrame] = OrderedDict()
_LOCK = RLock()


def _fingerprint(symbol: str, df: pd.DataFrame, end_date=None) -> tuple:
    last_time = None if df.empty else str(df.iloc[-1].get("time"))
    last_close = None if df.empty else float(df.iloc[-1].get("close", 0) or 0)
    return symbol, str(end_date or ""), len(df), last_time, last_close


def get_indicators_cached(symbol: str, df: pd.DataFrame, end_date=None) -> pd.DataFrame:
    key = _fingerprint(symbol, df, end_date)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return cached.copy(deep=False)

    calculated = add_indicators(df)
    with _LOCK:
        _CACHE[key] = calculated
        _CACHE.move_to_end(key)
        while len(_CACHE) > _MAX_SIZE:
            _CACHE.popitem(last=False)
    return calculated.copy(deep=False)


def clear_indicator_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def cache_info() -> dict:
    with _LOCK:
        return {"size": len(_CACHE), "max_size": _MAX_SIZE}
