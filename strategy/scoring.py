"""Pure scoring rules for Phase 1 stock evaluations."""

from __future__ import annotations

import pandas as pd

from config.strategy_loader import COMMON_CONFIG

RSI_MIN = float(COMMON_CONFIG["rsi_min"])
RSI_MAX = float(COMMON_CONFIG["rsi_max"])


def calculate_score(latest: pd.Series, relative_strength: float) -> tuple[int, list[str]]:
    """Return a score from 0 to 100 and human-readable positive reasons."""
    score = 0
    reasons: list[str] = []
    close = float(latest["close"])
    ema10 = float(latest["EMA10"])
    ema20 = float(latest["EMA20"])
    ema50 = float(latest["EMA50"])
    volume_ratio = float(latest["Vol_Ratio"])
    rsi = float(latest["RSI"])
    adx = float(latest["ADX14"])

    if close > ema10:
        score += 7
    if ema10 > ema20:
        score += 7
    if ema20 > ema50:
        score += 6
    if bool(latest["EMA20_Rising"]):
        score += 5
    if close > ema10 > ema20 > ema50:
        reasons.append("EMA xếp chồng tăng")

    if volume_ratio >= 2.0:
        score += 15
    elif volume_ratio >= 1.5:
        score += 12
    elif volume_ratio >= 1.2:
        score += 9
    elif volume_ratio >= 1.0:
        score += 5
    if volume_ratio >= 1.2:
        reasons.append(f"Volume {volume_ratio:.2f}x MA20")

    if 52 <= rsi <= 65:
        score += 12
    elif 48 <= rsi <= 70:
        score += 8
    elif RSI_MIN <= rsi <= RSI_MAX:
        score += 4

    if adx >= 30:
        score += 10
    elif adx >= 25:
        score += 8
    elif adx >= 20:
        score += 6
    elif adx >= 18:
        score += 3
    if adx >= 25:
        reasons.append(f"ADX mạnh {adx:.1f}")

    if bool(latest["Breakout_20D"]):
        score += 10
        reasons.append("Breakout đỉnh 20 phiên")
    if bool(latest["Volume_Breakout_5D"]):
        score += 4
        reasons.append("Volume cao nhất 5 phiên")
    if bool(latest["Green_Candle"]):
        score += 2
    if bool(latest["Close_Upper_Half"]):
        score += 1
    if float(latest["Body_Ratio"]) >= 0.35:
        score += 1

    if relative_strength >= 10:
        score += 20
    elif relative_strength >= 6:
        score += 16
    elif relative_strength >= 3:
        score += 12
    elif relative_strength >= 0:
        score += 8
    elif relative_strength >= -3:
        score += 3
    if relative_strength > 0:
        reasons.append(f"Mạnh hơn VNINDEX {relative_strength:.2f}%")

    return min(score, 100), reasons
