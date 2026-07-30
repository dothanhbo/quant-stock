"""Phân loại trạng thái VNINDEX và trả về cấu hình strategy tương ứng."""

from __future__ import annotations

import numpy as np
import pandas as pd

from core.database import engine


from config.strategy_loader import REGIME_CONFIGS



def _config(regime: str, **metrics) -> dict:
    result = {"regime": regime, **REGIME_CONFIGS[regime]}
    result.update(metrics)
    return result


def get_market_regime(end_date=None) -> dict:
    """Phân loại BULL/SIDEWAY/BEAR từ VNINDEX, không nhìn vượt ``end_date``."""
    df = pd.read_sql(
        """
        SELECT time, close
        FROM prices
        WHERE symbol = 'VNINDEX'
        ORDER BY time
        """,
        engine,
    )

    if df.empty:
        return _config("UNKNOWN", reason="Không có dữ liệu VNINDEX")

    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna().drop_duplicates("time", keep="last").sort_values("time")

    if end_date is not None:
        cutoff = pd.to_datetime(end_date, errors="coerce")
        if pd.isna(cutoff):
            raise ValueError(f"end_date không hợp lệ: {end_date}")
        df = df[df["time"] <= cutoff]

    if len(df) < 200:
        return _config(
            "UNKNOWN",
            reason=f"VNINDEX chỉ có {len(df)} phiên, cần ít nhất 200",
        )

    close = df["close"]
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()

    latest_close = float(close.iloc[-1])
    latest_ema50 = float(ema50.iloc[-1])
    latest_ema200 = float(ema200.iloc[-1])
    ema50_slope_10d = float((latest_ema50 / ema50.iloc[-11] - 1) * 100)
    return_20d = float((latest_close / close.iloc[-21] - 1) * 100)
    distance_ema200 = float((latest_close / latest_ema200 - 1) * 100)

    # BULL cần nằm trên cả EMA50/EMA200 và EMA50 đang dốc lên.
    if (
        latest_close > latest_ema50 > latest_ema200
        and ema50_slope_10d > 0
        and return_20d > -2
    ):
        regime = "BULL"
    # BEAR cần xác nhận rõ ràng, tránh coi mọi phiên dưới EMA200 là thị trường giảm mạnh.
    elif (
        latest_close < latest_ema200
        and latest_ema50 < latest_ema200
        and ema50_slope_10d < 0
    ):
        regime = "BEAR"
    else:
        regime = "SIDEWAY"

    return _config(
        regime,
        date=df["time"].iloc[-1].strftime("%Y-%m-%d"),
        close=round(latest_close, 2),
        ema50=round(latest_ema50, 2),
        ema200=round(latest_ema200, 2),
        distance_ema200=round(distance_ema200, 2),
        ema50_slope_10d=round(ema50_slope_10d, 2),
        return_20d=round(return_20d, 2),
    )
