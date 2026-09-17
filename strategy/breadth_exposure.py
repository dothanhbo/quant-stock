from __future__ import annotations

"""Market-breadth exposure control for the V3 paper candidate.

This module contains only the deterministic policy.  Market-state calculation
lives in ``strategy.market_state`` so the exposure rule remains easy to test
and cannot accidentally fetch or infer future data.
"""

from math import isfinite

V3_BREADTH_VERSION = "V3_BREADTH_40_60"
BREADTH_FULL_EXPOSURE_PCT = 60.0
BREADTH_NEUTRAL_EXPOSURE_PCT = 40.0


def breadth_exposure_multiplier(breadth_pct: float | int | None) -> float:
    """Return the V3 position-size multiplier from EMA50 breadth.

    >= 60%: full size
    40-60%: half size
    < 40%: no new exposure

    Missing/non-finite breadth is fail-closed for V3: no new position is
    opened rather than silently treating missing market state as healthy.
    """
    try:
        value = float(breadth_pct)
    except (TypeError, ValueError):
        return 0.0

    if not isfinite(value):
        return 0.0
    if value >= BREADTH_FULL_EXPOSURE_PCT:
        return 1.0
    if value >= BREADTH_NEUTRAL_EXPOSURE_PCT:
        return 0.5
    return 0.0
