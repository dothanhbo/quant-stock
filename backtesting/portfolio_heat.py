from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable


@dataclass(slots=True, frozen=True)
class PositionRisk:
    """
    Risk snapshot for one open or proposed position.

    Risk is measured from entry price to protective stop:

        risk_amount = abs(entry_price - stop_price) * quantity
        risk_pct = risk_amount / portfolio_equity * 100
    """

    symbol: str
    entry_price: float
    stop_price: float
    quantity: int
    risk_per_share: float
    risk_amount: float
    risk_pct: float

    @classmethod
    def from_prices(
        cls,
        *,
        symbol: str,
        entry_price: float,
        stop_price: float,
        quantity: int,
        portfolio_equity: float,
    ) -> "PositionRisk":
        normalized_symbol = str(
            symbol
        ).upper().strip()

        if not normalized_symbol:
            raise ValueError(
                "symbol không được rỗng."
            )

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

        normalized_quantity = int(
            quantity
        )

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
            symbol=normalized_symbol,
            entry_price=entry,
            stop_price=stop,
            quantity=normalized_quantity,
            risk_per_share=risk_per_share,
            risk_amount=risk_amount,
            risk_pct=risk_pct,
        )


@dataclass(slots=True, frozen=True)
class PortfolioHeatSnapshot:
    """Immutable portfolio-level risk summary."""

    equity: float
    max_heat_pct: float | None
    current_heat_amount: float
    current_heat_pct: float
    available_heat_amount: float | None
    available_heat_pct: float | None
    positions: int
    risks: tuple[PositionRisk, ...]

    @property
    def is_limited(self) -> bool:
        return self.max_heat_pct is not None

    @property
    def is_at_or_above_limit(self) -> bool:
        return (
            self.max_heat_pct is not None
            and self.current_heat_pct
            >= self.max_heat_pct
        )


@dataclass(slots=True, frozen=True)
class HeatDecision:
    allowed: bool
    reason: str
    current_heat_pct: float
    proposed_risk_pct: float
    projected_heat_pct: float
    max_heat_pct: float | None
    available_heat_pct: float | None


class PortfolioHeat:
    """
    Calculate and validate total portfolio heat.

    The class is stateless. Supply current position risks to each method.
    This keeps it independent from Portfolio and PortfolioSimulator.
    """

    def __init__(
        self,
        max_heat_pct: float | None = None,
        *,
        tolerance: float = 1e-9,
    ) -> None:
        if max_heat_pct is not None:
            normalized_limit = float(
                max_heat_pct
            )

            if (
                not isfinite(
                    normalized_limit
                )
                or normalized_limit <= 0
            ):
                raise ValueError(
                    "max_heat_pct phải lớn hơn 0 "
                    "hoặc bằng None."
                )

            self.max_heat_pct = (
                normalized_limit
            )

        else:
            self.max_heat_pct = None

        normalized_tolerance = float(
            tolerance
        )

        if (
            not isfinite(
                normalized_tolerance
            )
            or normalized_tolerance < 0
        ):
            raise ValueError(
                "tolerance không được âm."
            )

        self.tolerance = (
            normalized_tolerance
        )

    def calculate_position_risk(
        self,
        *,
        symbol: str,
        entry_price: float,
        stop_price: float,
        quantity: int,
        portfolio_equity: float,
    ) -> PositionRisk:
        return PositionRisk.from_prices(
            symbol=symbol,
            entry_price=entry_price,
            stop_price=stop_price,
            quantity=quantity,
            portfolio_equity=portfolio_equity,
        )

    def snapshot(
        self,
        *,
        portfolio_equity: float,
        position_risks: Iterable[
            PositionRisk
        ],
    ) -> PortfolioHeatSnapshot:
        equity = _positive_float(
            portfolio_equity,
            field_name="portfolio_equity",
        )

        risks = tuple(
            position_risks
        )

        current_heat_amount = sum(
            float(risk.risk_amount)
            for risk in risks
        )

        current_heat_pct = (
            current_heat_amount
            / equity
            * 100
        )

        if self.max_heat_pct is None:
            available_heat_amount = None
            available_heat_pct = None

        else:
            available_heat_pct = max(
                self.max_heat_pct
                - current_heat_pct,
                0.0,
            )

            available_heat_amount = (
                equity
                * available_heat_pct
                / 100
            )

        return PortfolioHeatSnapshot(
            equity=equity,
            max_heat_pct=(
                self.max_heat_pct
            ),
            current_heat_amount=(
                current_heat_amount
            ),
            current_heat_pct=(
                current_heat_pct
            ),
            available_heat_amount=(
                available_heat_amount
            ),
            available_heat_pct=(
                available_heat_pct
            ),
            positions=len(risks),
            risks=risks,
        )

    def decide(
        self,
        *,
        portfolio_equity: float,
        position_risks: Iterable[
            PositionRisk
        ],
        proposed_risk: PositionRisk,
    ) -> HeatDecision:
        snapshot = self.snapshot(
            portfolio_equity=(
                portfolio_equity
            ),
            position_risks=(
                position_risks
            ),
        )

        proposed_risk_pct = (
            float(
                proposed_risk.risk_amount
            )
            / snapshot.equity
            * 100
        )

        projected_heat_pct = (
            snapshot.current_heat_pct
            + proposed_risk_pct
        )

        if self.max_heat_pct is None:
            return HeatDecision(
                allowed=True,
                reason="heat_limit_disabled",
                current_heat_pct=(
                    snapshot.current_heat_pct
                ),
                proposed_risk_pct=(
                    proposed_risk_pct
                ),
                projected_heat_pct=(
                    projected_heat_pct
                ),
                max_heat_pct=None,
                available_heat_pct=None,
            )

        allowed = (
            projected_heat_pct
            <= (
                self.max_heat_pct
                + self.tolerance
            )
        )

        return HeatDecision(
            allowed=allowed,
            reason=(
                "within_heat_limit"
                if allowed
                else "portfolio_heat_limit"
            ),
            current_heat_pct=(
                snapshot.current_heat_pct
            ),
            proposed_risk_pct=(
                proposed_risk_pct
            ),
            projected_heat_pct=(
                projected_heat_pct
            ),
            max_heat_pct=(
                self.max_heat_pct
            ),
            available_heat_pct=(
                snapshot.available_heat_pct
            ),
        )

    def can_open(
        self,
        *,
        portfolio_equity: float,
        position_risks: Iterable[
            PositionRisk
        ],
        proposed_risk: PositionRisk,
    ) -> bool:
        return self.decide(
            portfolio_equity=(
                portfolio_equity
            ),
            position_risks=(
                position_risks
            ),
            proposed_risk=(
                proposed_risk
            ),
        ).allowed


def _positive_float(
    value: float,
    *,
    field_name: str,
) -> float:
    normalized = float(
        value
    )

    if (
        not isfinite(
            normalized
        )
        or normalized <= 0
    ):
        raise ValueError(
            f"{field_name} phải lớn hơn 0."
        )

    return normalized
