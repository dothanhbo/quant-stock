from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TransactionCostConfig:
    buy_commission_pct: float = 0.0
    sell_commission_pct: float = 0.0
    sell_tax_pct: float = 0.0
    buy_slippage_pct: float = 0.0
    sell_slippage_pct: float = 0.0

    def validate(self) -> None:
        values = {
            "buy_commission_pct": self.buy_commission_pct,
            "sell_commission_pct": self.sell_commission_pct,
            "sell_tax_pct": self.sell_tax_pct,
             "buy_slippage_pct": self.buy_slippage_pct,
             "sell_slippage_pct": self.sell_slippage_pct,
        }

        for name, value in values.items():
            if value < 0:
                raise ValueError(
                    f"{name} must be greater than or equal to 0"
                )


@dataclass(frozen=True, slots=True)
class TransactionCost:
    gross_value: float
    commission: float
    tax: float
    total_cost: float
    net_value: float


def calculate_buy_cost(
    *,
    price: float,
    quantity: int,
    config: TransactionCostConfig,
) -> TransactionCost:
    config.validate()

    if price <= 0:
        raise ValueError("price must be greater than 0")

    if quantity <= 0:
        raise ValueError("quantity must be greater than 0")

    gross_value = price * quantity
    commission = (
        gross_value
        * config.buy_commission_pct
        / 100
    )

    total_cost = commission
    net_value = gross_value + total_cost

    return TransactionCost(
        gross_value=gross_value,
        commission=commission,
        tax=0.0,
        total_cost=total_cost,
        net_value=net_value,
    )


def calculate_sell_cost(
    *,
    price: float,
    quantity: int,
    config: TransactionCostConfig,
) -> TransactionCost:
    config.validate()

    if price <= 0:
        raise ValueError("price must be greater than 0")

    if quantity <= 0:
        raise ValueError("quantity must be greater than 0")

    gross_value = price * quantity

    commission = (
        gross_value
        * config.sell_commission_pct
        / 100
    )

    tax = (
        gross_value
        * config.sell_tax_pct
        / 100
    )

    total_cost = commission + tax
    net_value = gross_value - total_cost

    return TransactionCost(
        gross_value=gross_value,
        commission=commission,
        tax=tax,
        total_cost=total_cost,
        net_value=net_value,
    )

def apply_buy_slippage(
    *,
    price: float,
    config: TransactionCostConfig,
) -> float:
    config.validate()

    if price <= 0:
        raise ValueError("price must be greater than 0")

    return price * (
        1 + config.buy_slippage_pct / 100
    )


def apply_sell_slippage(
    *,
    price: float,
    config: TransactionCostConfig,
) -> float:
    config.validate()

    if price <= 0:
        raise ValueError("price must be greater than 0")

    return price * (
        1 - config.sell_slippage_pct / 100
    )