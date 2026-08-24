from __future__ import annotations

from collections.abc import Collection

import pandas as pd

from risk.levels import calculate_risk_levels
from strategy.base_strategy import BaseStrategy


REGIME_THRESHOLD_FIELDS = frozenset({
    "min_volume_ratio",
    "min_adx",
    "min_relative_strength",
    "max_distance_ema20",
    "max_return_3d",
})


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
        use_adx: bool = True,
        use_volume: bool = True,
        use_relative_strength: bool = True,
        use_ema_filter: bool = True,
        use_distance_filter: bool = True,
        use_overheated_filter: bool = True,
        use_volume_breakout_score: bool = True,
        use_regime_thresholds: bool = False,
        regime_threshold_fields: Collection[str] | None = None,
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

        self.use_adx = bool(
            use_adx
        )

        self.use_volume = bool(
            use_volume
        )

        self.use_relative_strength = bool(
            use_relative_strength
        )

        self.use_ema_filter = bool(
            use_ema_filter
        )

        self.use_distance_filter = bool(
            use_distance_filter
        )

        self.use_overheated_filter = bool(
            use_overheated_filter
        )

        self.use_volume_breakout_score = bool(
            use_volume_breakout_score
        )
        self.use_regime_thresholds = bool(use_regime_thresholds)

        if regime_threshold_fields is None:
            selected_thresholds = REGIME_THRESHOLD_FIELDS
        else:
            selected_thresholds = frozenset(regime_threshold_fields)

        unknown_thresholds = (
            selected_thresholds - REGIME_THRESHOLD_FIELDS
        )
        if unknown_thresholds:
            raise ValueError(
                "Regime threshold không hợp lệ: "
                + ", ".join(sorted(unknown_thresholds))
            )

        if (
            not self.use_regime_thresholds
            and regime_threshold_fields is not None
        ):
            raise ValueError(
                "regime_threshold_fields yêu cầu "
                "use_regime_thresholds=True."
            )

        self.regime_threshold_fields = (
            frozenset(selected_thresholds)
            if self.use_regime_thresholds
            else frozenset()
        )

    def uses_regime_threshold(self, name: str) -> bool:
        return name in self.regime_threshold_fields

    @property
    def name(
        self,
    ) -> str:
        disabled: list[str] = []

        if not self.use_adx:
            disabled.append(
                "no_adx"
            )

        if not self.use_volume:
            disabled.append(
                "no_volume"
            )

        if not self.use_relative_strength:
            disabled.append(
                "no_rs"
            )

        if not self.use_ema_filter:
            disabled.append(
                "no_ema"
            )

        if not self.use_distance_filter:
            disabled.append(
                "no_distance"
            )

        if not self.use_overheated_filter:
            disabled.append(
                "no_overheated"
            )

        if (
            not self.use_volume_breakout_score
        ):
            disabled.append(
                "no_volume_breakout_score"
            )

        if self.require_volume_breakout:
            disabled.append(
                "require_volume_breakout"
            )

        if not disabled:
            return (
                "donchian_breakout_v1"
            )

        return (
            "donchian_breakout_v1__"
            + "_".join(
                disabled
            )
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

        min_volume_ratio = (
            float(market_config.get("min_volume_ratio", self.min_volume_ratio))
            if self.uses_regime_threshold("min_volume_ratio")
            else self.min_volume_ratio
        )
        min_adx = (
            float(market_config.get("min_adx", self.min_adx))
            if self.uses_regime_threshold("min_adx")
            else self.min_adx
        )
        min_relative_strength = (
            float(market_config.get("min_relative_strength", self.min_relative_strength))
            if self.uses_regime_threshold("min_relative_strength")
            else self.min_relative_strength
        )
        max_distance_ema20 = (
            float(market_config.get("max_distance_ema20", self.max_distance_ema20))
            if self.uses_regime_threshold("max_distance_ema20")
            else self.max_distance_ema20
        )
        max_return_3d = (
            float(market_config.get("max_return_3d", self.max_return_3d))
            if self.uses_regime_threshold("max_return_3d")
            else self.max_return_3d
        )

        conditions = {
            "breakout_20d": (
                breakout_20d
            ),
            "volume": (
                volume_ratio
                >= min_volume_ratio
                if self.use_volume
                else True
            ),
            "adx": (
                adx
                >= min_adx
                if self.use_adx
                else True
            ),
            "relative_strength": (
                relative_strength
                >= min_relative_strength
                if self.use_relative_strength
                else True
            ),
            "above_ema20": (
                close > ema20
                if self.use_ema_filter
                else True
            ),
            "distance": (
                (
                    0
                    <= distance_ema20
                    <= max_distance_ema20
                )
                if self.use_distance_filter
                else True
            ),
            "overheated": (
                return_3d
                <= max_return_3d
                if self.use_overheated_filter
                else True
            ),
        }

        if self.require_volume_breakout:
            conditions[
                "volume_breakout_5d"
            ] = volume_breakout_5d

        failed_conditions = [
            name
            for (
                name,
                passed,
            ) in conditions.items()
            if not passed
        ]

        score = self._calculate_score(
            breakout_20d=(
                breakout_20d
            ),
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
            >= (
                min_score
                - int(
                    market_config.get(
                        "watchlist_margin",
                        10,
                    )
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

        reasons: list[str] = []

        if breakout_20d:
            reasons.append(
                "Breakout đỉnh 20 phiên"
            )

        if (
            self.use_volume
            and volume_ratio >= 1.2
        ):
            reasons.append(
                f"Volume "
                f"{volume_ratio:.2f}x MA20"
            )

        if (
            self.use_volume_breakout_score
            and volume_breakout_5d
        ):
            reasons.append(
                "Volume cao nhất 5 phiên"
            )

        if (
            self.use_adx
            and adx >= 25
        ):
            reasons.append(
                f"ADX mạnh {adx:.1f}"
            )

        if (
            self.use_relative_strength
            and relative_strength > 0
        ):
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
            "entry_model": self.name,
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

    def _calculate_score(
        self,
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
        max_score = 0

        # Breakout là identity cốt lõi,
        # nên không được tắt.
        max_score += 30

        if breakout_20d:
            score += 30

        if self.use_volume_breakout_score:
            max_score += 10

            if volume_breakout_5d:
                score += 10

        if self.use_volume:
            max_score += 15

            if volume_ratio >= 2.0:
                score += 15
            elif volume_ratio >= 1.5:
                score += 12
            elif volume_ratio >= 1.2:
                score += 8

        if self.use_adx:
            max_score += 15

            if adx >= 30:
                score += 15
            elif adx >= 25:
                score += 12
            elif adx >= 20:
                score += 8

        if self.use_relative_strength:
            max_score += 15

            if relative_strength >= 10:
                score += 15
            elif relative_strength >= 5:
                score += 12
            elif relative_strength >= 0:
                score += 8

        if self.use_distance_filter:
            max_score += 8

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

        if self.use_overheated_filter:
            max_score += 7

            if return_3d <= 8:
                score += 7

            elif return_3d <= 15:
                score += 3

        if max_score <= 0:
            raise ValueError(
                "max_score phải lớn hơn 0."
            )

        normalized_score = round(
            score
            / max_score
            * 100
        )

        return min(
            normalized_score,
            100,
        )
