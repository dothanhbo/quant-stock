from __future__ import annotations

from typing import Any

import pandas as pd


def calculate_buy_and_hold_benchmark(
    price_df: pd.DataFrame,
    *,
    initial_capital: float,
) -> dict[str, Any]:
    empty_result = {
        "benchmark_start_date": None,
        "benchmark_end_date": None,
        "benchmark_start_price": 0.0,
        "benchmark_end_price": 0.0,
        "benchmark_return_pct": 0.0,
        "benchmark_final_equity": initial_capital,
        "benchmark_cagr_pct": 0.0,
    }

    if price_df.empty:
        return empty_result

    required = {"time", "close"}

    if not required.issubset(price_df.columns):
        return empty_result

    data = price_df[["time", "close"]].copy()

    data["time"] = pd.to_datetime(
        data["time"],
        errors="coerce",
    )

    data["close"] = pd.to_numeric(
        data["close"],
        errors="coerce",
    )

    data = (
        data
        .dropna()
        .drop_duplicates("time", keep="last")
        .sort_values("time")
        .reset_index(drop=True)
    )

    if len(data) < 2:
        return empty_result

    start_date = pd.Timestamp(data.iloc[0]["time"])
    end_date = pd.Timestamp(data.iloc[-1]["time"])

    start_price = float(data.iloc[0]["close"])
    end_price = float(data.iloc[-1]["close"])

    if start_price <= 0 or end_price <= 0:
        return empty_result

    benchmark_return_pct = (
        end_price / start_price - 1
    ) * 100

    benchmark_final_equity = (
        initial_capital
        * end_price
        / start_price
    )

    elapsed_days = (
        end_date - start_date
    ).days

    years = elapsed_days / 365.25

    benchmark_cagr_pct = (
        (
            benchmark_final_equity
            / initial_capital
        ) ** (1 / years)
        - 1
    ) * 100 if years > 0 else 0.0

    return {
        "benchmark_start_date": (
            start_date.strftime("%Y-%m-%d")
        ),
        "benchmark_end_date": (
            end_date.strftime("%Y-%m-%d")
        ),
        "benchmark_start_price": start_price,
        "benchmark_end_price": end_price,
        "benchmark_return_pct": float(
            benchmark_return_pct
        ),
        "benchmark_final_equity": float(
            benchmark_final_equity
        ),
        "benchmark_cagr_pct": float(
            benchmark_cagr_pct
        ),
    }