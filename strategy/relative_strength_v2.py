from __future__ import annotations

import numpy as np
import pandas as pd

from core.database import load_price_data
from core.sector import get_sector_symbols
from core.universe import get_vn100_symbols


DEFAULT_SECTOR_RS_PERIOD = 60


def _prepare_price_frame(df: pd.DataFrame, as_of_date=None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["time", "close"])

    out = df.copy()
    if "time" not in out.columns or "close" not in out.columns:
        return pd.DataFrame(columns=["time", "close"])

    out["time"] = pd.to_datetime(out["time"], errors="coerce")
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna(subset=["time", "close"])
    out = out.drop_duplicates("time", keep="last")

    if as_of_date is not None:
        cutoff = pd.Timestamp(as_of_date)
        if cutoff.tzinfo is not None:
            cutoff = cutoff.tz_localize(None)
        out = out[out["time"] <= cutoff]

    return out.sort_values("time")[["time", "close"]]


def _empty_relative_strength() -> dict:
    return {
        "available": False,
        "relative_strength": None,
        "stock_return": None,
        "benchmark_return": None,
        "observations": 0,
    }


def calculate_relative_strength(
    symbol: str,
    benchmark: str = "VNINDEX",
    period: int = 20,
    as_of_date=None,
) -> dict:
    stock = _prepare_price_frame(load_price_data(symbol), as_of_date)
    index = _prepare_price_frame(load_price_data(benchmark), as_of_date)

    merged = stock.merge(index, on="time", suffixes=("_stock", "_benchmark"))
    if len(merged) < period + 1:
        return _empty_relative_strength()

    rows = merged.tail(period + 1)
    stock_start = float(rows.iloc[0]["close_stock"])
    stock_end = float(rows.iloc[-1]["close_stock"])
    benchmark_start = float(rows.iloc[0]["close_benchmark"])
    benchmark_end = float(rows.iloc[-1]["close_benchmark"])

    if min(stock_start, benchmark_start) <= 0:
        return _empty_relative_strength()

    stock_return = stock_end / stock_start - 1.0
    benchmark_return = benchmark_end / benchmark_start - 1.0

    return {
        "available": True,
        "relative_strength": stock_return - benchmark_return,
        "stock_return": stock_return,
        "benchmark_return": benchmark_return,
        "observations": len(rows),
    }


def calculate_sector_relative_strength(
    symbol: str,
    sector_mapping: dict[str, str],
    period: int = DEFAULT_SECTOR_RS_PERIOD,
    as_of_date=None,
    universe_symbols=None,
) -> dict:
    normalized_symbol = str(symbol).strip().upper()
    normalized_mapping = {
        str(k).strip().upper(): str(v).strip()
        for k, v in sector_mapping.items()
        if k is not None and v is not None and str(v).strip()
    }

    sector = normalized_mapping.get(normalized_symbol)
    if not sector:
        result = _empty_relative_strength()
        result.update({
            "sector": None,
            "sector_universe_size": 0,
            "sector_eligible_count": 0,
        })
        return result

    if universe_symbols is None:
        universe_symbols = get_vn100_symbols()

    universe = {
        str(s).strip().upper()
        for s in universe_symbols
        if s is not None and str(s).strip()
    }

    if normalized_symbol not in universe:
        result = _empty_relative_strength()
        result.update({
            "sector": sector,
            "sector_universe_size": 0,
            "sector_eligible_count": 0,
        })
        return result

    sector_symbols = tuple(
        sorted(
            set(get_sector_symbols(sector, normalized_mapping))
            & universe
        )
    )

    # Sector RS is explicitly peer-relative: never include the target itself.
    peers = tuple(s for s in sector_symbols if s != normalized_symbol)

    stock = _prepare_price_frame(
        load_price_data(normalized_symbol),
        as_of_date,
    )

    if len(stock) < period + 1 or not peers:
        result = _empty_relative_strength()
        result.update({
            "sector": sector,
            "sector_universe_size": len(sector_symbols),
            "sector_eligible_count": 0,
        })
        return result

    stock_rows = stock.tail(period + 1)
    stock_start = float(stock_rows.iloc[0]["close"])
    stock_end = float(stock_rows.iloc[-1]["close"])

    if stock_start <= 0:
        result = _empty_relative_strength()
        result.update({
            "sector": sector,
            "sector_universe_size": len(sector_symbols),
            "sector_eligible_count": 0,
        })
        return result

    stock_return = stock_end / stock_start - 1.0

    peer_returns = []
    for member in peers:
        member_prices = _prepare_price_frame(
            load_price_data(member),
            as_of_date,
        )

        if len(member_prices) < period + 1:
            continue

        rows = member_prices.tail(period + 1)
        start = float(rows.iloc[0]["close"])
        end = float(rows.iloc[-1]["close"])

        if start <= 0:
            continue

        peer_returns.append(end / start - 1.0)

    if not peer_returns:
        result = _empty_relative_strength()
        result.update({
            "sector": sector,
            "sector_universe_size": len(sector_symbols),
            "sector_eligible_count": 0,
        })
        return result

    sector_return = float(np.mean(peer_returns))

    return {
        "available": True,
        "relative_strength": stock_return - sector_return,
        "stock_return": stock_return,
        "benchmark_return": sector_return,
        "observations": len(stock_rows),
        "sector": sector,
        "sector_universe_size": len(sector_symbols),
        "sector_eligible_count": len(peer_returns),
    }
