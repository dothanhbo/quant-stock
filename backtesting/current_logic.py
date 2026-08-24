from __future__ import annotations

from typing import Any

from backtesting.exit_models import BaseExitModel
from config.strategy_loader import REGIME_CONFIGS


class CurrentScannerExitModel(BaseExitModel):
    """Stop/target rules used by TrendStrategyV1 in the daily scanner."""

    def calculate_levels(
        self,
        entry_price: float,
        entry_row: Any,
        config: Any,
    ) -> tuple[float, float]:
        regime = str(
            entry_row.get("Market_Regime", "UNKNOWN")
        ).upper()
        market_config = REGIME_CONFIGS.get(
            regime,
            REGIME_CONFIGS["UNKNOWN"],
        )

        atr = float(entry_row["ATR14"])
        ema20 = float(entry_row["EMA20"])
        multiplier = float(
            market_config["atr_stop_multiplier"]
        )
        rr_ratio = float(
            market_config["rr_ratio"]
        )

        atr_stop = entry_price - multiplier * atr
        ema_stop = ema20 * 0.99
        valid_stops = [
            value
            for value in (atr_stop, ema_stop)
            if 0 < value < entry_price
        ]
        stop_price = (
            max(valid_stops)
            if valid_stops
            else entry_price * 0.95
        )
        risk_per_share = entry_price - stop_price
        target_price = (
            entry_price
            + risk_per_share * rr_ratio
        )

        return stop_price, target_price
