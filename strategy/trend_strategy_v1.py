from __future__ import annotations

import pandas as pd

from risk.levels import calculate_risk_levels
from strategy.base_strategy import BaseStrategy
from strategy.filters import evaluate_conditions
from strategy.scoring import calculate_score
from strategy.watchlist import classify


class TrendStrategyV1(BaseStrategy):
    """Logic quyết định của chiến lược trend-following V1."""

    def evaluate(
        self,
        latest: pd.Series,
        relative_strength: float,
        market_config: dict,
    ) -> dict:
        conditions = evaluate_conditions(
            latest=latest,
            relative_strength=relative_strength,
            market_config=market_config,
        )

        score, reasons = calculate_score(
            latest=latest,
            relative_strength=relative_strength,
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
            "min_score": int(market_config["min_score"]),
            "regime": market_config["regime"],
            "conditions": conditions,
            "failed_conditions": [
                name
                for name, passed in conditions.items()
                if not passed
            ],
            "reasons": reasons,
            **risk,
        }

        if status == "WATCHLIST":
            decision["missing"] = missing

        return decision