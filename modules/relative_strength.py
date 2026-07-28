import numpy as np
import pandas as pd

from database import load_price_data


def calculate_relative_strength(
    symbol: str,
    benchmark: str = "VNINDEX",
    period: int = 20
) -> dict:
    """
    So sánh hiệu suất của cổ phiếu với VNINDEX.

    Ví dụ:
    - Cổ phiếu tăng 8% trong 20 phiên
    - VNINDEX tăng 3%
    - Relative Strength = 5%
    """

    stock_df = load_price_data(symbol)
    index_df = load_price_data(benchmark)

    if (
        stock_df.empty
        or index_df.empty
        or len(stock_df) < period + 1
        or len(index_df) < period + 1
    ):
        return {
            "available": False,
            "stock_return": np.nan,
            "index_return": np.nan,
            "relative_strength": np.nan
        }

    stock = stock_df[
        ["time", "close"]
    ].copy()

    index = index_df[
        ["time", "close"]
    ].copy()

    stock["time"] = pd.to_datetime(
        stock["time"],
        errors="coerce"
    )

    index["time"] = pd.to_datetime(
        index["time"],
        errors="coerce"
    )

    stock["close"] = pd.to_numeric(
        stock["close"],
        errors="coerce"
    )

    index["close"] = pd.to_numeric(
        index["close"],
        errors="coerce"
    )

    stock = (
        stock
        .dropna()
        .drop_duplicates("time", keep="last")
        .sort_values("time")
    )

    index = (
        index
        .dropna()
        .drop_duplicates("time", keep="last")
        .sort_values("time")
    )

    merged = stock.merge(
        index,
        on="time",
        how="inner",
        suffixes=("_stock", "_index")
    )

    if len(merged) < period + 1:
        return {
            "available": False,
            "stock_return": np.nan,
            "index_return": np.nan,
            "relative_strength": np.nan
        }

    latest_rows = merged.tail(period + 1)

    stock_start = float(
        latest_rows["close_stock"].iloc[0]
    )

    stock_end = float(
        latest_rows["close_stock"].iloc[-1]
    )

    index_start = float(
        latest_rows["close_index"].iloc[0]
    )

    index_end = float(
        latest_rows["close_index"].iloc[-1]
    )

    if stock_start <= 0 or index_start <= 0:
        return {
            "available": False,
            "stock_return": np.nan,
            "index_return": np.nan,
            "relative_strength": np.nan
        }

    stock_return = (
        stock_end / stock_start - 1
    ) * 100

    index_return = (
        index_end / index_start - 1
    ) * 100

    relative_strength = (
        stock_return - index_return
    )

    return {
        "available": True,
        "stock_return": round(
            stock_return,
            2
        ),
        "index_return": round(
            index_return,
            2
        ),
        "relative_strength": round(
            relative_strength,
            2
        )
    }