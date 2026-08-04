from datetime import datetime

from backtesting.position_sizers import (
    AtrRiskSizer,
    PositionSizingContext,
)
from backtesting.trade import Trade
from backtesting.transaction_cost import (
    TransactionCostConfig,
)


def create_candidate(
    *,
    entry_price: float,
    atr: float | None,
) -> Trade:
    trade = Trade(
        symbol="HPG",
        entry_date=datetime(2026, 1, 2),
        entry_price=entry_price,
        quantity=1,
    )

    trade.atr = atr

    return trade


def zero_cost_config() -> TransactionCostConfig:
    return TransactionCostConfig(
        buy_commission_pct=0.0,
        sell_commission_pct=0.0,
        sell_tax_pct=0.0,
        buy_slippage_pct=0.0,
        sell_slippage_pct=0.0,
    )


def test_atr_risk_sizer_uses_risk_budget():
    sizer = AtrRiskSizer(
        risk_per_trade_pct=1.0,
        atr_stop_multiplier=2.0,
        max_position_size_pct=100.0,
    )

    context = PositionSizingContext(
        candidate=create_candidate(
            entry_price=20.0,
            atr=1.0,
        ),
        cash=100_000_000,
        equity=100_000_000,
        lot_size=100,
        transaction_cost_config=(
            zero_cost_config()
        ),
    )

    # Risk budget = 1,000,000
    # Stop distance = 1 * 2 = 2
    # Quantity = 500,000
    assert (
        sizer.calculate_quantity(context)
        == 500_000
    )


def test_atr_risk_sizer_caps_position_value():
    sizer = AtrRiskSizer(
        risk_per_trade_pct=5.0,
        atr_stop_multiplier=1.0,
        max_position_size_pct=20.0,
    )

    context = PositionSizingContext(
        candidate=create_candidate(
            entry_price=100.0,
            atr=1.0,
        ),
        cash=100_000,
        equity=100_000,
        lot_size=100,
        transaction_cost_config=(
            zero_cost_config()
        ),
    )

    # Risk sizing suggests 5,000 shares,
    # but max 20% position value allows only 200 shares.
    assert (
        sizer.calculate_quantity(context)
        == 200
    )


def test_atr_risk_sizer_respects_available_cash():
    sizer = AtrRiskSizer(
        risk_per_trade_pct=5.0,
        atr_stop_multiplier=1.0,
        max_position_size_pct=100.0,
    )

    context = PositionSizingContext(
        candidate=create_candidate(
            entry_price=100.0,
            atr=1.0,
        ),
        cash=15_000,
        equity=100_000,
        lot_size=100,
        transaction_cost_config=(
            zero_cost_config()
        ),
    )

    assert (
        sizer.calculate_quantity(context)
        == 100
    )


def test_atr_risk_sizer_returns_zero_without_atr():
    sizer = AtrRiskSizer()

    context = PositionSizingContext(
        candidate=create_candidate(
            entry_price=20.0,
            atr=None,
        ),
        cash=100_000,
        equity=100_000,
        lot_size=100,
        transaction_cost_config=(
            zero_cost_config()
        ),
    )

    assert (
        sizer.calculate_quantity(context)
        == 0
    )


def test_atr_risk_sizer_rounds_down_to_lot_size():
    sizer = AtrRiskSizer(
        risk_per_trade_pct=1.0,
        atr_stop_multiplier=2.0,
        max_position_size_pct=100.0,
    )

    context = PositionSizingContext(
        candidate=create_candidate(
            entry_price=20.0,
            atr=3.0,
        ),
        cash=100_000,
        equity=100_000,
        lot_size=100,
        transaction_cost_config=(
            zero_cost_config()
        ),
    )

    # 1,000 / 6 = 166 shares -> 100-share lot.
    assert (
        sizer.calculate_quantity(context)
        == 100
    )
