from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ExitReason(Enum):
    TAKE_PROFIT = "Take Profit"
    STOP_LOSS = "Stop Loss"
    TIME_EXIT = "Time Exit"
    SIGNAL_EXIT = "Signal Exit"
    MANUAL = "Manual"


class ExitExecution(Enum):
    NORMAL = "Normal"
    STOP_GAP = "Stop Gap"
    TARGET_GAP = "Target Gap"
    SAME_DAY_SL_FIRST = "Same Day SL First"


@dataclass(slots=True)
class Trade:
    symbol: str
    entry_date: datetime
    entry_price: float
    quantity: int
    quantity_override: int | None = None

    # ---------- Signal metadata ----------
    signal_score: float | None = None
    relative_strength: float | None = None
    adx: float | None = None
    volume_ratio: float | None = None
    atr: float | None = None
    market_regime: str | None = None
    entry_model: str | None = None

    # ---------- Risk metadata ----------
    stop_price: float | None = None
    risk_per_share: float | None = None
    risk_amount: float | None = None
    risk_pct: float | None = None

    exit_date: datetime | None = None
    exit_price: float | None = None
    exit_reason: ExitReason | None = None
    execution: ExitExecution = ExitExecution.NORMAL

    buy_commission: float = 0.0
    sell_commission: float = 0.0
    sell_tax: float = 0.0

    def close(
        self,
        exit_date: datetime,
        exit_price: float,
        reason: ExitReason,
        execution: ExitExecution = ExitExecution.NORMAL,
    ) -> None:
        self.exit_date = exit_date
        self.exit_price = exit_price
        self.exit_reason = reason
        self.execution = execution

    @property
    def is_closed(self) -> bool:
        return self.exit_date is not None and self.exit_price is not None

    @property
    def pnl(self) -> float:
        return self.net_pnl

    @property
    def return_pct(self) -> float:
        return self.net_return_pct

    @property
    def holding_days(self) -> int:
        if not self.is_closed:
            return 0

        return (self.exit_date - self.entry_date).days

    @property
    def is_win(self) -> bool:
        return self.is_closed and self.pnl > 0


    @property
    def cost(self) -> float:
        return self.entry_price * self.quantity

    @property
    def gross_proceeds(self) -> float:
        if not self.is_closed:
            return 0.0

        return self.exit_price * self.quantity


    @property
    def total_transaction_cost(self) -> float:
        return (
            self.buy_commission
            + self.sell_commission
            + self.sell_tax
        )


    @property
    def gross_pnl(self) -> float:
        if not self.is_closed:
            return 0.0

        return (
            self.exit_price - self.entry_price
        ) * self.quantity


    @property
    def net_pnl(self) -> float:
        return (
            self.gross_pnl
            - self.total_transaction_cost
        )


    @property
    def net_return_pct(self) -> float:
        if self.cost == 0:
            return 0.0

        return (
            self.net_pnl
            / self.cost
        ) * 100


    @property
    def market_value(self) -> float:
        if not self.is_closed:
            return self.cost

        return self.exit_price * self.quantity

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "entry_date": self.entry_date,
            "entry_price": self.entry_price,
            "quantity": self.quantity,
            "quantity_override": self.quantity_override,
            "signal_score": self.signal_score,
            "relative_strength": self.relative_strength,
            "adx": self.adx,
            "volume_ratio": self.volume_ratio,
            "atr": self.atr,
            "market_regime": self.market_regime,
            "entry_model": self.entry_model,

            "stop_price": self.stop_price,
            "risk_per_share": self.risk_per_share,
            "risk_amount": self.risk_amount,
            "risk_pct": self.risk_pct,

            "exit_date": self.exit_date,
            "exit_price": self.exit_price,
            "exit_reason": (
                self.exit_reason.value
                if self.exit_reason is not None
                else None
            ),
            "execution": self.execution.value,
            "is_closed": self.is_closed,
            "is_win": self.is_win,
            "pnl": self.pnl,
            "return_pct": self.return_pct,
            "holding_days": self.holding_days,
            "cost": self.cost,
            "market_value": self.market_value,
            "gross_pnl": self.gross_pnl,
            "net_pnl": self.net_pnl,
            "buy_commission": self.buy_commission,
            "sell_commission": self.sell_commission,
            "sell_tax": self.sell_tax,
            "transaction_cost": self.total_transaction_cost,
            "net_return_pct": self.net_return_pct,
        }

    def __repr__(self) -> str:
        status = "CLOSED" if self.is_closed else "OPEN"

        return (
            f"Trade("
            f"symbol={self.symbol!r}, "
            f"status={status}, "
            f"entry_price={self.entry_price:.2f}, "
            f"exit_price={self.exit_price}, "
            f"pnl={self.pnl:.2f}"
            f")"
        )