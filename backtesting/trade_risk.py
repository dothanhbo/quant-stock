from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any


@dataclass(slots=True, frozen=True)
class TradeRiskMetadata:
    """
    Risk metadata stored with a candidate or open trade.

    The values are snapshots calculated at entry time.
    """

    stop_price: float
    risk_per_share: float
    risk_amount: float
    risk_pct: float

    @classmethod
    def build(
        cls,
        *,
        entry_price: float,
        stop_price: float,
        quantity: int,
        portfolio_equity: float,
    ) -> "TradeRiskMetadata":
        entry = _positive_float(
            entry_price,
            field_name="entry_price",
        )
        stop = _positive_float(
            stop_price,
            field_name="stop_price",
        )
        equity = _positive_float(
            portfolio_equity,
            field_name="portfolio_equity",
        )
        normalized_quantity = int(quantity)

        if normalized_quantity < 1:
            raise ValueError(
                "quantity phải từ 1 trở lên."
            )

        risk_per_share = abs(
            entry - stop
        )
        risk_amount = (
            risk_per_share
            * normalized_quantity
        )
        risk_pct = (
            risk_amount
            / equity
            * 100
        )

        return cls(
            stop_price=stop,
            risk_per_share=risk_per_share,
            risk_amount=risk_amount,
            risk_pct=risk_pct,
        )

    @classmethod
    def from_trade(
        cls,
        trade: Any,
        *,
        portfolio_equity: float,
        stop_price: float | None = None,
    ) -> "TradeRiskMetadata":
        resolved_stop = (
            stop_price
            if stop_price is not None
            else getattr(
                trade,
                "stop_price",
                None,
            )
        )

        if resolved_stop is None:
            raise ValueError(
                "Trade chưa có stop_price."
            )

        return cls.build(
            entry_price=getattr(
                trade,
                "entry_price",
            ),
            stop_price=resolved_stop,
            quantity=getattr(
                trade,
                "quantity",
            ),
            portfolio_equity=(
                portfolio_equity
            ),
        )


def resolve_candidate_stop_price(
    candidate: Any,
    *,
    atr_stop_multiplier: float | None = None,
) -> float | None:
    """
    Resolve the protective stop already stored on a candidate.

    Priority:
    1. candidate.stop_price
    2. entry_price - ATR * multiplier

    Phase 1.2 does not invent a percentage stop because the active
    exit model should remain the source of truth.
    """

    direct_stop = getattr(
        candidate,
        "stop_price",
        None,
    )

    direct_stop = _optional_positive_float(
        direct_stop
    )

    if direct_stop is not None:
        return direct_stop

    if atr_stop_multiplier is None:
        return None

    multiplier = float(
        atr_stop_multiplier
    )

    if (
        not isfinite(multiplier)
        or multiplier <= 0
    ):
        raise ValueError(
            "atr_stop_multiplier phải lớn hơn 0."
        )

    entry_price = _optional_positive_float(
        getattr(
            candidate,
            "entry_price",
            None,
        )
    )
    atr = _optional_positive_float(
        getattr(
            candidate,
            "atr",
            None,
        )
    )

    if (
        entry_price is None
        or atr is None
    ):
        return None

    stop_price = (
        entry_price
        - atr * multiplier
    )

    return (
        stop_price
        if stop_price > 0
        else None
    )


def _positive_float(
    value: Any,
    *,
    field_name: str,
) -> float:
    normalized = float(value)

    if (
        not isfinite(normalized)
        or normalized <= 0
    ):
        raise ValueError(
            f"{field_name} phải lớn hơn 0."
        )

    return normalized


def _optional_positive_float(
    value: Any,
) -> float | None:
    if value is None:
        return None

    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None

    if (
        not isfinite(normalized)
        or normalized <= 0
    ):
        return None

    return normalized
