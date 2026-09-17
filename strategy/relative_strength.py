from __future__ import annotations

import numpy as np
import pandas as pd

from core.database import load_price_data
from core.sector import get_sector_symbols


def _prepare_price_frame(df: pd.DataFrame, name: str, as_of_date=None) -> pd.DataFrame:
    if df.empty or not {"time", "close"}.issubset(df.columns):
        return pd.DataFrame(columns=["time", name])
    data = df[["time", "close"]].copy()
    data["time"] = pd.to_datetime(data["time"], errors="coerce")
    data[name] = pd.to_numeric(data["close"], errors="coerce")
    data = data[["time", name]].dropna().drop_duplicates("time", keep="last")
    if as_of_date is not None:
        cutoff = pd.to_datetime(as_of_date, errors="coerce")
        if pd.isna(cutoff):
            raise ValueError(f"as_of_date không hợp lệ: {as_of_date}")
        data = data[data["time"] <= cutoff]
    return data.sort_values("time")


def _empty_relative_strength(*, benchmark_key: str) -> dict:
    return {"available": False, "stock_return": np.nan, benchmark_key: np.nan, "relative_strength": np.nan}


def calculate_relative_strength(symbol: str, benchmark: str = "VNINDEX", period: int = 20, as_of_date=None) -> dict:
    stock = _prepare_price_frame(load_price_data(symbol), "close_stock", as_of_date)
    index = _prepare_price_frame(load_price_data(benchmark), "close_index", as_of_date)
    merged = stock.merge(index, on="time", how="inner")
    empty_result = _empty_relative_strength(benchmark_key="index_return")
    if len(merged) < period + 1:
        return empty_result
    rows = merged.tail(period + 1)
    stock_start, stock_end = float(rows["close_stock"].iloc[0]), float(rows["close_stock"].iloc[-1])
    index_start, index_end = float(rows["close_index"].iloc[0]), float(rows["close_index"].iloc[-1])
    if stock_start <= 0 or index_start <= 0:
        return empty_result
    stock_return = (stock_end / stock_start - 1) * 100
    index_return = (index_end / index_start - 1) * 100
    return {"available": True, "stock_return": round(stock_return, 2), "index_return": round(index_return, 2), "relative_strength": round(stock_return - index_return, 2)}


def calculate_sector_relative_strength(symbol: str, sector_mapping: dict[str, str], period: int = 20, as_of_date=None) -> dict:
    normalized_symbol = str(symbol).strip().upper()
    sector = sector_mapping.get(normalized_symbol)
    empty = _empty_relative_strength(benchmark_key="sector_return")
    empty.update({"sector": sector, "sector_universe": 0, "sector_eligible": 0})
    if not sector:
        return empty
    sector_symbols = get_sector_symbols(sector, sector_mapping)
    if normalized_symbol not in sector_symbols:
        sector_symbols = tuple(sorted(set(sector_symbols) | {normalized_symbol}))
    stock = _prepare_price_frame(load_price_data(normalized_symbol), "close_stock", as_of_date)
    if len(stock) < period + 1:
        return empty
    stock_rows = stock.tail(period + 1)
    stock_start, stock_end = float(stock_rows["close_stock"].iloc[0]), float(stock_rows["close_stock"].iloc[-1])
    if stock_start <= 0:
        return empty
    stock_return = (stock_end / stock_start - 1) * 100
    sector_returns: list[float] = []
    for member in sector_symbols:
        member_prices = _prepare_price_frame(load_price_data(member), "close_member", as_of_date)
        if len(member_prices) < period + 1:
            continue
        rows = member_prices.tail(period + 1)
        start, end = float(rows["close_member"].iloc[0]), float(rows["close_member"].iloc[-1])
        if start <= 0:
            continue
        sector_returns.append((end / start - 1) * 100)
    if not sector_returns:
        return empty
    sector_return = float(np.mean(sector_returns))
    return {"available": True, "stock_return": round(stock_return, 2), "sector_return": round(sector_return, 2), "relative_strength": round(stock_return - sector_return, 2), "sector": sector, "sector_universe": len(sector_symbols), "sector_eligible": len(sector_returns)}
