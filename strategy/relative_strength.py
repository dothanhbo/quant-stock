"""Relative Strength của cổ phiếu so với VNINDEX."""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.database import load_price_data


def calculate_relative_strength(
    symbol: str,
    benchmark: str = "VNINDEX",
    period: int = 20,
    end_date=None,
) -> dict:
    stock_df = load_price_data(symbol)
    index_df = load_price_data(benchmark)

    def prepare(df: pd.DataFrame, name: str) -> pd.DataFrame:
        if df.empty or not {"time", "close"}.issubset(df.columns):
            return pd.DataFrame(columns=["time", name])
        data = df[["time", "close"]].copy()
        data["time"] = pd.to_datetime(data["time"], errors="coerce")
        data[name] = pd.to_numeric(data["close"], errors="coerce")
        data = data[["time", name]].dropna().drop_duplicates("time", keep="last")
        if end_date is not None:
            cutoff = pd.to_datetime(end_date, errors="coerce")
            if pd.isna(cutoff):
                raise ValueError(f"end_date không hợp lệ: {end_date}")
            data = data[data["time"] <= cutoff]
        return data.sort_values("time")

    stock = prepare(stock_df, "close_stock")
    index = prepare(index_df, "close_index")
    merged = stock.merge(index, on="time", how="inner")

    empty_result = {
        "available": False,
        "stock_return": np.nan,
        "index_return": np.nan,
        "relative_strength": np.nan,
    }
    if len(merged) < period + 1:
        return empty_result

    rows = merged.tail(period + 1)
    stock_start, stock_end = float(rows["close_stock"].iloc[0]), float(rows["close_stock"].iloc[-1])
    index_start, index_end = float(rows["close_index"].iloc[0]), float(rows["close_index"].iloc[-1])
    if stock_start <= 0 or index_start <= 0:
        return empty_result

    stock_return = (stock_end / stock_start - 1) * 100
    index_return = (index_end / index_start - 1) * 100
    return {
        "available": True,
        "stock_return": round(stock_return, 2),
        "index_return": round(index_return, 2),
        "relative_strength": round(stock_return - index_return, 2),
    }
