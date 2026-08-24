from __future__ import annotations

import pandas as pd

from risk.levels import calculate_risk_levels
from strategy.base_strategy import BaseStrategy


class TrendPullbackRetestEntryModel(BaseStrategy):
    """Entry V2: buy a confirmed retest after an earlier 20-day breakout."""

    def __init__(
        self,
        *,
        min_adx: float = 18.0,
        min_relative_strength: float = 0.0,
        min_rsi: float = 45.0,
        max_rsi: float = 70.0,
        max_distance_ema20: float = 6.0,
        max_return_3d: float = 8.0,
    ) -> None:
        if min_adx < 0:
            raise ValueError("min_adx không được âm.")
        if min_rsi < 0 or max_rsi > 100 or min_rsi >= max_rsi:
            raise ValueError("Khoảng RSI không hợp lệ.")
        if max_distance_ema20 <= 0 or max_return_3d <= 0:
            raise ValueError("Distance/return limits phải lớn hơn 0.")

        self.min_adx = float(min_adx)
        self.min_relative_strength = float(min_relative_strength)
        self.min_rsi = float(min_rsi)
        self.max_rsi = float(max_rsi)
        self.max_distance_ema20 = float(max_distance_ema20)
        self.max_return_3d = float(max_return_3d)

    @property
    def name(self) -> str:
        return "trend_pullback_retest_v2"

    def evaluate(
        self,
        latest: pd.Series,
        relative_strength: float,
        market_config: dict,
    ) -> dict:
        close = float(latest["close"])
        ema10 = float(latest["EMA10"])
        ema20 = float(latest["EMA20"])
        ema50 = float(latest["EMA50"])
        adx = float(latest["ADX14"])
        rsi = float(latest["RSI"])
        distance = float(latest["Distance_EMA20_Pct"])
        return_3d = float(latest["Return_3D_Pct"])

        conditions = {
            "trend_structure": (
                close > ema20 > ema50
                and bool(latest["EMA20_Rising"])
            ),
            "recent_breakout": bool(latest["Recent_Breakout_10D"]),
            "pullback_reclaim": (
                bool(latest["Touched_EMA10"])
                and bool(latest["Reclaimed_EMA10"])
            ),
            "confirmation_candle": (
                bool(latest["Green_Candle"])
                and bool(latest["Close_Upper_Half"])
            ),
            "adx": adx >= self.min_adx,
            "relative_strength": (
                relative_strength >= self.min_relative_strength
            ),
            "rsi": self.min_rsi <= rsi <= self.max_rsi,
            "distance": 0.0 <= distance <= self.max_distance_ema20,
            "not_overheated": return_3d <= self.max_return_3d,
        }
        failed = [name for name, passed in conditions.items() if not passed]

        score_parts = {
            "trend_structure": 20,
            "recent_breakout": 20,
            "pullback_reclaim": 20,
            "confirmation_candle": 15,
            "adx": 5,
            "relative_strength": 10,
            "rsi": 4,
            "distance": 3,
            "not_overheated": 3,
        }
        score = sum(
            points
            for condition, points in score_parts.items()
            if conditions[condition]
        )
        status = "PASSED" if not failed else "REJECTED"
        reasons = []
        if conditions["recent_breakout"]:
            reasons.append("Breakout 20D trong 10 phiên trước")
        if conditions["pullback_reclaim"]:
            reasons.append("Pullback và reclaim EMA10")
        if conditions["relative_strength"]:
            reasons.append(f"RS {relative_strength:+.2f}%")

        risk = calculate_risk_levels(
            latest=latest,
            market_config=market_config,
        )
        return {
            "status": status,
            "reason": "passed" if status == "PASSED" else failed[0],
            "score": int(score),
            "min_score": 100,
            "regime": market_config.get("regime", "UNKNOWN"),
            "conditions": conditions,
            "failed_conditions": failed,
            "reasons": reasons,
            "entry_model": self.name,
            **risk,
        }
