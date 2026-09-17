from __future__ import annotations

"""Causal production Market State.

All values are computed using information available through ``as_of_date``.
The helper intentionally does not read research-result CSVs.
"""

from functools import lru_cache
from typing import Iterable

import numpy as np
import pandas as pd
from sqlalchemy import text

from core.database import engine
from core.universe import get_vn100_symbols
from strategy.market_regime import get_market_regime

MIN_HISTORY_SESSIONS = 50


def _classify(regime: str, breadth: float, change_10d: float) -> str:
    regime = str(regime).upper()
    if regime == "BEAR":
        return "BEAR"
    if regime == "BULL":
        if breadth < 50.0 and change_10d < 0.0:
            return "DIVERGENT_BULL"
        if breadth >= 70.0 and change_10d >= 0.0:
            return "HEALTHY_BULL"
        return "FRAGILE_BULL"
    if breadth >= 60.0 and change_10d > 0.0:
        return "RECOVERY"
    return "NEUTRAL"


def _load_prices(as_of_date: str, symbols: tuple[str, ...]) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame(columns=["symbol", "time", "close"])
    placeholders = ", ".join(f":s{i}" for i in range(len(symbols)))
    params = {f"s{i}": symbol for i, symbol in enumerate(symbols)}
    params["as_of_date"] = as_of_date
    query = text(
        f"""
        SELECT symbol, date(time) AS time, close
        FROM prices
        WHERE symbol IN ({placeholders})
          AND date(time) <= :as_of_date
        ORDER BY symbol, date(time)
        """
    )
    with engine.connect() as connection:
        return pd.read_sql(query, connection, params=params)


def _compute_breadth(as_of_date: str, symbols: tuple[str, ...]) -> tuple[float, float, int]:
    prices = _load_prices(as_of_date, symbols)
    if prices.empty:
        return float("nan"), float("nan"), 0

    prices["time"] = pd.to_datetime(prices["time"], errors="coerce")
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    prices = prices.dropna(subset=["symbol", "time", "close"])
    prices = prices.drop_duplicates(["symbol", "time"], keep="last")
    prices = prices.sort_values(["symbol", "time"])
    prices["ema50"] = prices.groupby("symbol")["close"].transform(
        lambda s: s.ewm(span=50, adjust=False).mean()
    )
    prices["history_n"] = prices.groupby("symbol").cumcount() + 1
    eligible = prices[prices["history_n"] >= MIN_HISTORY_SESSIONS].copy()
    if eligible.empty:
        return float("nan"), float("nan"), 0

    daily = (
        eligible.assign(above_ema50=eligible["close"] > eligible["ema50"])
        .groupby("time", as_index=False)
        .agg(breadth=("above_ema50", "mean"), universe=("symbol", "nunique"))
        .sort_values("time")
        .reset_index(drop=True)
    )
    daily["breadth_pct"] = daily["breadth"] * 100.0
    daily["change_10d"] = daily["breadth_pct"] - daily["breadth_pct"].shift(10)

    row = daily[daily["time"] == pd.Timestamp(as_of_date)]
    if row.empty:
        return float("nan"), float("nan"), 0
    latest = row.iloc[-1]
    return float(latest["breadth_pct"]), float(latest["change_10d"]), int(latest["universe"])


@lru_cache(maxsize=8)
def _get_market_state_cached(as_of_date: str, symbols: tuple[str, ...]) -> dict:
    market_config = get_market_regime(end_date=as_of_date)
    breadth, change_10d, breadth_universe = _compute_breadth(as_of_date, symbols)
    if not np.isfinite(breadth) or not np.isfinite(change_10d):
        state = "UNKNOWN"
    else:
        state = _classify(market_config["regime"], breadth, change_10d)
    return {
        "as_of_date": as_of_date,
        "regime": market_config["regime"],
        "market_state": state,
        "breadth_ema50_pct": breadth,
        "breadth_ema50_change_10d": change_10d,
        "breadth_universe": breadth_universe,
    }


def get_market_state(as_of_date: str, *, symbols: Iterable[str] | None = None) -> dict:
    parsed = pd.to_datetime(as_of_date, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"as_of_date không hợp lệ: {as_of_date}")
    normalized = parsed.strftime("%Y-%m-%d")
    universe = tuple(sorted({
        str(symbol).strip().upper()
        for symbol in (symbols if symbols is not None else get_vn100_symbols())
        if str(symbol).strip() and str(symbol).strip().upper() != "VNINDEX"
    }))
    return dict(_get_market_state_cached(normalized, universe))


def clear_market_state_cache() -> None:
    _get_market_state_cached.cache_clear()
