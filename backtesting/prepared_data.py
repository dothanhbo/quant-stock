from __future__ import annotations

import pandas as pd

from core.database import load_price_data
from strategy.cache import get_indicators_cached
from strategy.market_regime import (
    prepare_market_regime_history,
)

DEFAULT_BENCHMARK = "VNINDEX"
DEFAULT_RS_PERIOD = 20


def add_relative_strength_columns(
    stock_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    *,
    period: int = DEFAULT_RS_PERIOD,
) -> pd.DataFrame:
    """
    Tính Relative Strength cho toàn bộ lịch sử.

    Không sử dụng dữ liệu tương lai:
    return tại ngày T chỉ dùng giá T và T-period.
    """
    if period <= 0:
        raise ValueError(
            "period must be greater than 0"
        )

    required_columns = {"time", "close"}

    if (
        stock_df.empty
        or benchmark_df.empty
        or not required_columns.issubset(
            stock_df.columns
        )
        or not required_columns.issubset(
            benchmark_df.columns
        )
    ):
        result = stock_df.copy()

        result["Stock_Return_20D"] = pd.NA
        result["Index_Return_20D"] = pd.NA
        result["Relative_Strength_20D"] = pd.NA

        return result

    stock = stock_df.copy()

    stock["time"] = pd.to_datetime(
        stock["time"],
        errors="coerce",
    )

    stock["close"] = pd.to_numeric(
        stock["close"],
        errors="coerce",
    )

    stock = (
        stock
        .dropna(subset=["time", "close"])
        .drop_duplicates(
            subset=["time"],
            keep="last",
        )
        .sort_values("time")
        .reset_index(drop=True)
    )

    benchmark = benchmark_df[
        ["time", "close"]
    ].copy()

    benchmark["time"] = pd.to_datetime(
        benchmark["time"],
        errors="coerce",
    )

    benchmark["close"] = pd.to_numeric(
        benchmark["close"],
        errors="coerce",
    )

    benchmark = (
        benchmark
        .dropna(subset=["time", "close"])
        .drop_duplicates(
            subset=["time"],
            keep="last",
        )
        .sort_values("time")
        .rename(
            columns={
                "close": "benchmark_close",
            }
        )
    )

    result = stock.merge(
        benchmark,
        on="time",
        how="left",
    )

    result["Stock_Return_20D"] = (
        result["close"]
        / result["close"].shift(period)
        - 1
    ) * 100

    result["Index_Return_20D"] = (
        result["benchmark_close"]
        / result["benchmark_close"].shift(period)
        - 1
    ) * 100

    result["Relative_Strength_20D"] = (
        result["Stock_Return_20D"]
        - result["Index_Return_20D"]
    )

    return result


def prepare_backtest_dataset(
    symbol: str,
    *,
    benchmark: str = DEFAULT_BENCHMARK,
    rs_period: int = DEFAULT_RS_PERIOD,
    end_date=None,
) -> pd.DataFrame:
    """
    Chuẩn bị dữ liệu dùng chung cho toàn bộ backtest:

    - Load dữ liệu cổ phiếu một lần
    - Load benchmark một lần
    - Tính indicators một lần
    - Tính Relative Strength cho toàn lịch sử một lần
    """
    stock_df = load_price_data(symbol)

    if stock_df.empty:
        return stock_df

    benchmark_df = load_price_data(
        benchmark
    )
    market_history = (
        prepare_market_regime_history(
            benchmark_df
        )
    )

    if end_date is not None:
        cutoff = pd.to_datetime(
            end_date,
            errors="coerce",
        )

        if pd.isna(cutoff):
            raise ValueError(
                f"end_date không hợp lệ: {end_date}"
            )

        stock_df = stock_df[
            pd.to_datetime(
                stock_df["time"],
                errors="coerce",
            ) <= cutoff
        ].copy()

        benchmark_df = benchmark_df[
            pd.to_datetime(
                benchmark_df["time"],
                errors="coerce",
            ) <= cutoff
        ].copy()

    data = get_indicators_cached(
        symbol,
        stock_df,
        end_date=end_date,
    )
  
    if (
        data.empty
        or "time" not in data.columns
    ):
        return pd.DataFrame()

    data = add_relative_strength_columns(
        data,
        benchmark_df,
        period=rs_period,
    )

    if (
        data.empty
        or "time" not in data.columns
    ):
        return pd.DataFrame()

    market_columns = [
        "time",
        "Market_Regime",
    ]

    if (
        data.empty
        or "time" not in data.columns
    ):
        return pd.DataFrame()

    data["time"] = pd.to_datetime(
        data["time"],
        errors="coerce",
    )

    market_history["time"] = pd.to_datetime(
        market_history["time"],
        errors="coerce",
    )

    data = data.dropna(subset=["time"])
    market_history = market_history.dropna(
        subset=["time"]
    )

    data = data.merge(
        market_history[
            market_columns
        ],
        on="time",
        how="left",
    )

    return data