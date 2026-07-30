"""ATR/EMA based entry, stop-loss and take-profit calculations."""

from __future__ import annotations

import pandas as pd


def calculate_risk_levels(latest: pd.Series, market_config: dict) -> dict:
    entry = float(latest["close"])
    atr = float(latest["ATR14"])
    ema20 = float(latest["EMA20"])
    atr_multiplier = float(market_config["atr_stop_multiplier"])
    rr_ratio = float(market_config["rr_ratio"])

    atr_stop = entry - atr_multiplier * atr
    ema_stop = ema20 * 0.99
    valid_stops = [value for value in (atr_stop, ema_stop) if 0 < value < entry]
    stop_loss = max(valid_stops) if valid_stops else entry * 0.95
    risk = entry - stop_loss
    if risk <= 0:
        stop_loss = entry * 0.95
        risk = entry - stop_loss
    take_profit = entry + risk * rr_ratio

    return {
        "entry": round(entry, 2),
        "stop_loss": round(stop_loss, 2),
        "take_profit": round(take_profit, 2),
        "stop_loss_pct": round((stop_loss / entry - 1) * 100, 2),
        "take_profit_pct": round((take_profit / entry - 1) * 100, 2),
        "rr_ratio": rr_ratio,
    }
