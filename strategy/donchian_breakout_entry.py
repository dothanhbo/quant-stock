from __future__ import annotations

import pandas as pd

from risk.levels import calculate_risk_levels
from strategy.base_strategy import BaseStrategy


class DonchianBreakoutEntryModel(
    BaseStrategy
):
    def __init__(
        self,
        *,
        min_adx: float = 20.0,
        min_volume_ratio: float = 1.2,
        min_relative_strength: float = 0.0,
        max_distance_ema20: float = 12.0,
        max_return_3d: float = 15.0,
        require_volume_breakout: bool = False,
    ) -> None:
        if min_adx < 0:
            raise ValueError(
                "min_adx không được âm."
            )

        if min_volume_ratio <= 0:
            raise ValueError(
                "min_volume_ratio phải lớn hơn 0."
            )

        if max_distance_ema20 <= 0:
            raise ValueError(
                "max_distance_ema20 phải lớn hơn 0."
            )

        if max_return_3d <= 0:
            raise ValueError(
                "max_return_3d phải lớn hơn 0."
            )

        self.min_adx = float(
            min_adx
        )

        self.min_volume_ratio = float(
            min_volume_ratio
        )

        self.min_relative_strength = float(
            min_relative_strength
        )

        self.max_distance_ema20 = float(
            max_distance_ema20
        )

        self.max_return_3d = float(
            max_return_3d
        )

        self.require_volume_breakout = bool(
            require_volume_breakout
        )

    @property
    def name(
        self,
    ) -> str:
        return (
            "donchian_breakout_v1"
        )

    def evaluate(
        self,
        latest: pd.Series,
        relative_strength: float,
        market_config: dict,
    ) -> dict:
        breakout_20d = bool(
            latest["Breakout_20D"]
        )

        volume_breakout_5d = bool(
            latest["Volume_Breakout_5D"]
        )

        volume_ratio = float(
            latest["Vol_Ratio"]
        )

        adx = float(
            latest["ADX14"]
        )

        distance_ema20 = float(
            latest["Distance_EMA20_Pct"]
        )

        return_3d = float(
            latest["Return_3D_Pct"]
        )

        close = float(
            latest["close"]
        )

        ema20 = float(
            latest["EMA20"]
        )

        conditions = {
            "breakout_20d": (
                breakout_20d
            ),
            "volume": (
                volume_ratio
                >= self.min_volume_ratio
            ),
            "adx": (
                adx
                >= self.min_adx
            ),
            "relative_strength": (
                relative_strength
                >= self.min_relative_strength
            ),
            "above_ema20": (
                close > ema20
            ),
            "distance": (
                0
                <= distance_ema20
                <= self.max_distance_ema20
            ),
            "overheated": (
                return_3d
                <= self.max_return_3d
            ),
        }

        if self.require_volume_breakout:
            conditions[
                "volume_breakout_5d"
            ] = volume_breakout_5d

        failed_conditions = [
            name
            for name, passed
            in conditions.items()
            if not passed
        ]

        score = self._calculate_score(
            breakout_20d=breakout_20d,
            volume_breakout_5d=(
                volume_breakout_5d
            ),
            volume_ratio=volume_ratio,
            adx=adx,
            relative_strength=(
                relative_strength
            ),
            distance_ema20=(
                distance_ema20
            ),
            return_3d=return_3d,
        )

        min_score = int(
            market_config.get(
                "min_score",
                60,
            )
        )

        score_passed = (
            score >= min_score
        )

        if (
            not failed_conditions
            and score_passed
        ):
            status = "PASSED"
            reason = "passed"

        elif (
            len(failed_conditions) <= 2
            and score
            >= min_score
            - int(
                market_config.get(
                    "watchlist_margin",
                    10,
                )
            )
        ):
            status = "WATCHLIST"
            reason = "watchlist"

        else:
            status = "REJECTED"

            reason = (
                failed_conditions[0]
                if failed_conditions
                else "score"
            )

        reasons = []

        if breakout_20d:
            reasons.append(
                "Breakout đỉnh 20 phiên"
            )

        if volume_ratio >= 1.2:
            reasons.append(
                f"Volume {volume_ratio:.2f}x MA20"
            )

        if volume_breakout_5d:
            reasons.append(
                "Volume cao nhất 5 phiên"
            )

        if adx >= 25:
            reasons.append(
                f"ADX mạnh {adx:.1f}"
            )

        if relative_strength > 0:
            reasons.append(
                "Mạnh hơn VNINDEX "
                f"{relative_strength:.2f}%"
            )

        risk = calculate_risk_levels(
            latest=latest,
            market_config=market_config,
        )

        decision = {
            "status": status,
            "reason": reason,
            "score": score,
            "min_score": min_score,
            "regime": market_config.get(
                "regime",
                "UNKNOWN",
            ),
            "conditions": conditions,
            "failed_conditions": (
                failed_conditions
            ),
            "reasons": reasons,
            "entry_model": (
                "donchian_breakout_v1"
            ),
            **risk,
        }

        if status == "WATCHLIST":
            missing = list(
                failed_conditions
            )

            if not score_passed:
                missing.append(
                    "score"
                )

            decision["missing"] = (
                missing
            )

        return decision

    @staticmethod
    def _calculate_score(
        *,
        breakout_20d: bool,
        volume_breakout_5d: bool,
        volume_ratio: float,
        adx: float,
        relative_strength: float,
        distance_ema20: float,
        return_3d: float,
    ) -> int:
        score = 0

        if breakout_20d:
            score += 30

        if volume_breakout_5d:
            score += 10

        if volume_ratio >= 2.0:
            score += 15
        elif volume_ratio >= 1.5:
            score += 12
        elif volume_ratio >= 1.2:
            score += 8

        if adx >= 30:
            score += 15
        elif adx >= 25:
            score += 12
        elif adx >= 20:
            score += 8

        if relative_strength >= 10:
            score += 15
        elif relative_strength >= 5:
            score += 12
        elif relative_strength >= 0:
            score += 8

        if (
            0
            <= distance_ema20
            <= 6
        ):
            score += 8
        elif (
            6
            < distance_ema20
            <= 12
        ):
            score += 4

        if return_3d <= 8:
            score += 7
        elif return_3d <= 15:
            score += 3

        return min(
            score,
            100,
        )