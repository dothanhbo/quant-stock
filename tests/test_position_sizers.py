from datetime import datetime

from backtesting.position_sizers import (
    FixedFractionSizer,
    PositionSizingContext,
)
from backtesting.trade import Trade
from backtesting.transaction_cost import (
    TransactionCostConfig,
)


def create_candidate(
    entry_price: float,
) -> Trade:
    return Trade(
        symbol="HPG",
        entry_date=datetime(2026, 1, 2),
        entry_price=entry_price,
        quantity=1,
    )


def test_fixed_fraction_sizer_reproduces_equal_weight_quantity():
    sizer = FixedFractionSizer(
        position_size_pct=20.0
    )

    context = PositionSizingContext(
        candidate=create_candidate(20.0),
        cash=100_000_000,
        equity=100_000_000,
        lot_size=100,
        transaction_cost_config=(
            TransactionCostConfig(
                buy_commission_pct=0.0,
                sell_commission_pct=0.0,
                sell_tax_pct=0.0,
                buy_slippage_pct=0.0,
                sell_slippage_pct=0.0,
            )
        ),
    )

    assert (
        sizer.calculate_quantity(context)
        == 1_000_000
    )


def test_fixed_fraction_sizer_respects_available_cash():
    sizer = FixedFractionSizer(
        position_size_pct=50.0
    )

    context = PositionSizingContext(
        candidate=create_candidate(100.0),
        cash=10_000,
        equity=100_000,
        lot_size=100,
        transaction_cost_config=(
            TransactionCostConfig(
                buy_commission_pct=0.0,
                sell_commission_pct=0.0,
                sell_tax_pct=0.0,
                buy_slippage_pct=0.0,
                sell_slippage_pct=0.0,
            )
        ),
    )

    assert (
        sizer.calculate_quantity(context)
        == 100
    )


def test_fixed_fraction_sizer_returns_zero_below_one_lot():
    sizer = FixedFractionSizer(
        position_size_pct=20.0
    )

    context = PositionSizingContext(
        candidate=create_candidate(1_000.0),
        cash=50_000,
        equity=50_000,
        lot_size=100,
        transaction_cost_config=(
            TransactionCostConfig(
                buy_commission_pct=0.15,
                sell_commission_pct=0.15,
                sell_tax_pct=0.10,
                buy_slippage_pct=0.05,
                sell_slippage_pct=0.05,
            )
        ),
    )

    assert (
        sizer.calculate_quantity(context)
        == 0
    )
