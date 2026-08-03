from __future__ import annotations

import pandas as pd

from risk.levels import calculate_risk_levels
from strategy.base_strategy import BaseStrategy
from strategy.filters import evaluate_conditions
from strategy.scoring import calculate_score
from strategy.watchlist import classify


class TrendStrategyV1(
    BaseStrategy
):
    def __init__(
        self,
        *,
        use_trend_filter: bool = True,
        use_adx: bool = True,
        use_volume: bool = True,
        use_relative_strength: bool = True,
        use_market_filter: bool | None = None,
    ) -> None:
        """
        Configurable Trend V1 strategy for ablation studies.

        use_market_filter is retained as a backwards-compatible alias
        for use_trend_filter. The current "trend" condition is an
        EMA-structure filter influenced by regime, not a separate
        broad-market permission filter.
        """
        if use_market_filter is not None:
            use_trend_filter = bool(
                use_market_filter
            )

        self.use_trend_filter = bool(
            use_trend_filter
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

    @property
    def name(
        self,
    ) -> str:
        disabled: list[str] = []

        if not self.use_trend_filter:
            disabled.append(
                "no_trend"
            )

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

        if not disabled:
            return "trend_v1"

        return (
            "trend_v1__"
            + "_".join(disabled)
        )

    def evaluate(
        self,
        latest: pd.Series,
        relative_strength: float,
        market_config: dict,
    ) -> dict:
        conditions = evaluate_conditions(
            latest=latest,
            relative_strength=(
                relative_strength
            ),
            market_config=market_config,
            use_trend_filter=(
                self.use_trend_filter
            ),
            use_adx=self.use_adx,
            use_volume=self.use_volume,
            use_relative_strength=(
                self.use_relative_strength
            ),
        )

        score, reasons = calculate_score(
            latest=latest,
            relative_strength=(
                relative_strength
            ),
            use_trend_score=(
                self.use_trend_filter
            ),
            use_adx=self.use_adx,
            use_volume=self.use_volume,
            use_relative_strength=(
                self.use_relative_strength
            ),
        )

        status, reason, missing = classify(
            score=score,
            conditions=conditions,
            market_config=market_config,
        )

        risk = calculate_risk_levels(
            latest=latest,
            market_config=market_config,
        )

        decision = {
            "status": status,
            "reason": reason,
            "score": score,
            "min_score": int(
                market_config[
                    "min_score"
                ]
            ),
            "regime": market_config[
                "regime"
            ],
            "conditions": conditions,
            "failed_conditions": [
                name
                for name, passed
                in conditions.items()
                if not passed
            ],
            "reasons": reasons,
            "entry_model": self.name,
            **risk,
        }

        if status == "WATCHLIST":
            decision["missing"] = missing

        return decision
