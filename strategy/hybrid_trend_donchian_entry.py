from __future__ import annotations

from typing import Literal

import pandas as pd

from risk.levels import calculate_risk_levels
from strategy.base_strategy import BaseStrategy
from strategy.donchian_breakout_entry import (
    DonchianBreakoutEntryModel,
)
from strategy.trend_strategy_v1 import (
    TrendStrategyV1,
)


HybridMode = Literal[
    "strict",
    "trend_context",
    "score_blend",
]


class HybridTrendDonchianEntryModel(
    BaseStrategy
):
    """
    Hybrid Entry Model:

    TrendStrategyV1:
        Xác nhận cấu trúc xu hướng.

    DonchianBreakoutEntryModel:
        Xác nhận breakout và timing vào lệnh.

    Modes
    -----
    strict:
        Trend phải PASSED
        và Donchian phải PASSED.

    trend_context:
        Trend được PASSED hoặc WATCHLIST,
        Donchian bắt buộc PASSED.

    score_blend:
        Không bắt buộc cả hai PASSED.
        Dùng điểm tổng hợp và các điều kiện cốt lõi.
    """

    def __init__(
        self,
        *,
        mode: HybridMode = (
            "trend_context"
        ),
        trend_model: (
            TrendStrategyV1 | None
        ) = None,
        donchian_model: (
            DonchianBreakoutEntryModel
            | None
        ) = None,
        trend_weight: float = 0.4,
        donchian_weight: float = 0.6,
        min_hybrid_score: int = 60,
        use_regime_thresholds: bool = True,
        require_hybrid_score: bool = True,
    ) -> None:
        supported_modes = {
            "strict",
            "trend_context",
            "score_blend",
        }

        if mode not in supported_modes:
            raise ValueError(
                "Hybrid mode không hợp lệ: "
                f"{mode}"
            )

        if trend_weight < 0:
            raise ValueError(
                "trend_weight không được âm."
            )

        if donchian_weight < 0:
            raise ValueError(
                "donchian_weight "
                "không được âm."
            )

        total_weight = (
            trend_weight
            + donchian_weight
        )

        if total_weight <= 0:
            raise ValueError(
                "Tổng trọng số "
                "phải lớn hơn 0."
            )

        if not (
            0
            <= min_hybrid_score
            <= 100
        ):
            raise ValueError(
                "min_hybrid_score phải "
                "nằm trong khoảng 0–100."
            )

        self.mode = mode

        self.trend_model = (
            trend_model
            or TrendStrategyV1()
        )

        self.donchian_model = (
            donchian_model
            or DonchianBreakoutEntryModel(
                use_regime_thresholds=use_regime_thresholds,
            )
        )

        self.use_regime_thresholds = bool(
            use_regime_thresholds
        )
        self.require_hybrid_score = bool(
            require_hybrid_score
        )

        self.trend_weight = (
            float(trend_weight)
            / total_weight
        )

        self.donchian_weight = (
            float(donchian_weight)
            / total_weight
        )

        self.min_hybrid_score = int(
            min_hybrid_score
        )

    @property
    def name(
        self,
    ) -> str:
        name = (
            "hybrid_trend_donchian_v1"
            f"__{self.mode}"
        )

        if not self.use_regime_thresholds:
            name += "__legacy_thresholds"

        if (
            self.mode == "trend_context"
            and not self.require_hybrid_score
        ):
            name += "__no_hard_score"

        return name

    def evaluate(
        self,
        latest: pd.Series,
        relative_strength: float,
        market_config: dict,
    ) -> dict:
        trend_decision = (
            self.trend_model.evaluate(
                latest=latest,
                relative_strength=(
                    relative_strength
                ),
                market_config=(
                    market_config
                ),
            )
        )

        donchian_decision = (
            self.donchian_model.evaluate(
                latest=latest,
                relative_strength=(
                    relative_strength
                ),
                market_config=(
                    market_config
                ),
            )
        )

        trend_status = str(
            trend_decision.get(
                "status",
                "REJECTED",
            )
        )

        donchian_status = str(
            donchian_decision.get(
                "status",
                "REJECTED",
            )
        )

        trend_score = float(
            trend_decision.get(
                "score",
                0,
            )
        )

        donchian_score = float(
            donchian_decision.get(
                "score",
                0,
            )
        )

        hybrid_score = round(
            (
                trend_score
                * self.trend_weight
            )
            + (
                donchian_score
                * self.donchian_weight
            )
        )

        trend_passed = (
            trend_status == "PASSED"
        )

        trend_context_passed = (
            trend_status
            in {
                "PASSED",
                "WATCHLIST",
            }
        )

        donchian_passed = (
            donchian_status == "PASSED"
        )

        breakout_passed = bool(
            donchian_decision.get(
                "conditions",
                {},
            ).get(
                "breakout_20d",
                False,
            )
        )

        conditions = {
            "trend_context": (
                trend_context_passed
            ),
            "trend_passed": (
                trend_passed
            ),
            "donchian_passed": (
                donchian_passed
            ),
            "breakout_20d": (
                breakout_passed
            ),
            "hybrid_score": (
                hybrid_score
                >= self.min_hybrid_score
            ),
        }

        if self.mode == "strict":
            entry_passed = (
                trend_passed
                and donchian_passed
            )

        elif (
            self.mode
            == "trend_context"
        ):
            entry_passed = (
                trend_context_passed
                and donchian_passed
                and (
                    not self.require_hybrid_score
                    or hybrid_score
                    >= self.min_hybrid_score
                )
            )

        else:
            entry_passed = (
                breakout_passed
                and trend_context_passed
                and hybrid_score
                >= self.min_hybrid_score
            )

        failed_conditions = (
            self._build_failed_conditions(
                conditions=conditions,
            )
        )

        watchlist_margin = int(
            market_config.get(
                "watchlist_margin",
                10,
            )
        )

        watchlist_threshold = (
            self.min_hybrid_score
            - watchlist_margin
        )

        if entry_passed:
            status = "PASSED"
            reason = "passed"

        elif (
            breakout_passed
            and hybrid_score
            >= watchlist_threshold
        ):
            status = "WATCHLIST"
            reason = "watchlist"

        else:
            status = "REJECTED"

            reason = (
                failed_conditions[0]
                if failed_conditions
                else "hybrid_score"
            )

        reasons = (
            self._build_reasons(
                trend_decision=(
                    trend_decision
                ),
                donchian_decision=(
                    donchian_decision
                ),
                hybrid_score=(
                    hybrid_score
                ),
            )
        )

        risk = calculate_risk_levels(
            latest=latest,
            market_config=(
                market_config
            ),
        )

        decision = {
            "status": status,
            "reason": reason,
            "score": hybrid_score,
            "min_score": (
                self.min_hybrid_score
            ),
            "regime": (
                market_config.get(
                    "regime",
                    "UNKNOWN",
                )
            ),
            "conditions": conditions,
            "failed_conditions": (
                failed_conditions
            ),
            "reasons": reasons,
            "entry_model": self.name,
            "hybrid_mode": self.mode,
            "trend_model": getattr(
                self.trend_model,
                "name",
                self.trend_model
                .__class__.__name__,
            ),
            "donchian_model": getattr(
                self.donchian_model,
                "name",
                self.donchian_model
                .__class__.__name__,
            ),
            "trend_status": (
                trend_status
            ),
            "donchian_status": (
                donchian_status
            ),
            "trend_score": (
                trend_score
            ),
            "donchian_score": (
                donchian_score
            ),
            "hybrid_score": (
                hybrid_score
            ),
            **risk,
        }

        if status == "WATCHLIST":
            decision["missing"] = (
                failed_conditions
            )

        return decision

    def _build_failed_conditions(
        self,
        *,
        conditions: dict[
            str,
            bool,
        ],
    ) -> list[str]:
        required_conditions: list[
            str
        ]

        if self.mode == "strict":
            required_conditions = [
                "trend_passed",
                "donchian_passed",
            ]

        elif (
            self.mode
            == "trend_context"
        ):
            required_conditions = [
                "trend_context",
                "donchian_passed",
            ]

            if self.require_hybrid_score:
                required_conditions.append(
                    "hybrid_score"
                )

        else:
            required_conditions = [
                "trend_context",
                "breakout_20d",
                "hybrid_score",
            ]

        return [
            name
            for name
            in required_conditions
            if not conditions.get(
                name,
                False,
            )
        ]

    @staticmethod
    def _build_reasons(
        *,
        trend_decision: dict,
        donchian_decision: dict,
        hybrid_score: int,
    ) -> list[str]:
        reasons: list[str] = []

        trend_reasons = (
            trend_decision.get(
                "reasons",
                [],
            )
        )

        donchian_reasons = (
            donchian_decision.get(
                "reasons",
                [],
            )
        )

        for reason in trend_reasons:
            reasons.append(
                f"Trend: {reason}"
            )

        for reason in (
            donchian_reasons
        ):
            reasons.append(
                f"Donchian: {reason}"
            )

        reasons.append(
            f"Hybrid score "
            f"{hybrid_score}/100"
        )

        return reasons
