"""Mandatory strategy filters, kept separate from scoring."""

from __future__ import annotations

import pandas as pd

from config.strategy_loader import COMMON_CONFIG


RSI_MIN = float(COMMON_CONFIG["rsi_min"])
RSI_MAX = float(COMMON_CONFIG["rsi_max"])


REQUIRED_INDICATORS = [
    "close",
    "EMA10",
    "EMA20",
    "EMA50",
    "EMA20_Rising",
    "RSI",
    "Vol_Ratio",
    "ATR14",
    "ATR_Percent",
    "ADX14",
    "Distance_EMA20_Pct",
    "Return_3D_Pct",
    "Green_Candle",
    "Close_Upper_Half",
    "Body_Ratio",
    "Breakout_20D",
    "Recent_Breakout_10D",
    "Touched_EMA10",
    "Reclaimed_EMA10",
    "Volume_Breakout_5D",
]


def trend_passes(
    latest: pd.Series,
    regime: str,
) -> bool:
    close = float(latest["close"])
    ema10 = float(latest["EMA10"])
    ema20 = float(latest["EMA20"])
    ema50 = float(latest["EMA50"])
    rising = bool(
        latest["EMA20_Rising"]
    )

    if regime == "SIDEWAY":
        return (
            close > ema20 > ema50
            and rising
        )

    return (
        close
        > ema10
        > ema20
        > ema50
        and rising
    )


def evaluate_conditions(
    latest: pd.Series,
    relative_strength: float,
    market_config: dict,
    *,
    use_trend_filter: bool = True,
    use_adx: bool = True,
    use_volume: bool = True,
    use_relative_strength: bool = True,
) -> dict[str, bool]:
    """
    Evaluate mandatory filters.

    Disabled filters are returned as True so classification ignores
    them without changing the decision schema.
    """
    return {
        "trend": (
            trend_passes(
                latest,
                str(
                    market_config[
                        "regime"
                    ]
                ),
            )
            if use_trend_filter
            else True
        ),
        "volume": (
            float(
                latest["Vol_Ratio"]
            )
            >= float(
                market_config[
                    "min_volume_ratio"
                ]
            )
            if use_volume
            else True
        ),
        "adx": (
            float(
                latest["ADX14"]
            )
            >= float(
                market_config[
                    "min_adx"
                ]
            )
            if use_adx
            else True
        ),
        "rsi": (
            RSI_MIN
            <= float(latest["RSI"])
            <= RSI_MAX
        ),
        "distance": (
            0
            <= float(
                latest[
                    "Distance_EMA20_Pct"
                ]
            )
            <= float(
                market_config[
                    "max_distance_ema20"
                ]
            )
        ),
        "overheated": (
            float(
                latest[
                    "Return_3D_Pct"
                ]
            )
            <= float(
                market_config[
                    "max_return_3d"
                ]
            )
        ),
        "relative_strength": (
            relative_strength
            >= float(
                market_config[
                    "min_relative_strength"
                ]
            )
            if use_relative_strength
            else True
        ),
    }
