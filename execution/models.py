from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass(slots=True)
class Order:
    symbol: str
    side: OrderSide
    quantity: int
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    reference_price: float | None = None
    client_order_id: str = field(
        default_factory=lambda: uuid4().hex
    )
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: int = 0
    average_fill_price: float | None = None
    rejection_reason: str | None = None
    created_at: datetime = field(
        default_factory=utc_now
    )
    updated_at: datetime = field(
        default_factory=utc_now
    )

    def __post_init__(self) -> None:
        self.symbol = self.symbol.strip().upper()

        if not self.symbol:
            raise ValueError(
                "symbol không được để trống."
            )

        if self.quantity <= 0:
            raise ValueError(
                "quantity phải lớn hơn 0."
            )

        if (
            self.order_type == OrderType.LIMIT
            and (
                self.limit_price is None
                or self.limit_price <= 0
            )
        ):
            raise ValueError(
                "Lệnh LIMIT cần limit_price > 0."
            )


@dataclass(slots=True)
class Fill:
    order_id: str
    symbol: str
    side: OrderSide
    quantity: int
    price: float
    gross_value: float
    commission: float
    slippage_cost: float
    net_cash_flow: float
    created_at: datetime = field(
        default_factory=utc_now
    )


@dataclass(slots=True)
class Position:
    symbol: str
    quantity: int = 0
    average_price: float = 0.0
    market_price: float = 0.0
    realized_pnl: float = 0.0

    @property
    def market_value(self) -> float:
        return (
            self.quantity
            * self.market_price
        )

    @property
    def cost_basis(self) -> float:
        return (
            self.quantity
            * self.average_price
        )

    @property
    def unrealized_pnl(self) -> float:
        return (
            self.market_value
            - self.cost_basis
        )

    @property
    def unrealized_pnl_pct(self) -> float:
        if self.cost_basis <= 0:
            return 0.0

        return (
            self.unrealized_pnl
            / self.cost_basis
            * 100
        )


@dataclass(slots=True)
class PortfolioSnapshot:
    cash: float
    positions_value: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    gross_exposure_pct: float
    open_positions: int
    created_at: datetime = field(
        default_factory=utc_now
    )
