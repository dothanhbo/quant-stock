from datetime import date

from backtesting.paper_parity import (
    BacktestPaperParityConfig,
    audit_entry_parity,
    audit_exit_parity,
    calculate_parity_quantity,
)
from execution.exit_models import (
    ExitBar,
    PositionExitState,
)


def config(
) -> BacktestPaperParityConfig:
    return BacktestPaperParityConfig(
        initial_cash=100_000_000,
        position_sizer="fixed_fraction",
        risk_per_trade_pct=1.0,
        atr_stop_multiplier=2.0,
        fixed_fraction_pct=10.0,
        lot_size=100,
        commission_rate=0.0015,
        slippage_bps=5.0,
        maximum_position_pct=20.0,
        maximum_gross_exposure_pct=80.0,
        maximum_open_positions=10,
        maximum_daily_loss_pct=3.0,
        minimum_cash_buffer_pct=5.0,
    )


def test_transaction_cost_conversion() -> None:
    value = config()

    costs = (
        value.transaction_cost_config()
    )

    assert costs.buy_commission_pct == 0.15
    assert costs.sell_commission_pct == 0.15
    assert costs.buy_slippage_pct == 0.05
    assert costs.sell_slippage_pct == 0.05
    assert costs.sell_tax_pct == 0.0


def test_quantity_uses_same_shared_sizer() -> None:
    quantity = calculate_parity_quantity(
        config=config(),
        symbol="VNM",
        entry_price=60_000,
        stop_price=58_000,
        atr=1_000,
    )

    assert quantity == 100


def test_entry_fill_and_cash_parity() -> None:
    result = audit_entry_parity(
        config=config(),
        reference_price=60_000,
        stop_price=58_000,
    )

    assert result.passed


def test_exit_parity() -> None:
    result = audit_exit_parity(
        state=PositionExitState(
            symbol="VNM",
            entry_date=date(
                2026,
                8,
                5,
            ),
            entry_price=60_000,
            quantity=100,
            stop_price=58_000,
            take_profit_price=64_000,
            highest_price=62_000,
            trailing_stop_price=59_000,
        ),
        bar=ExitBar(
            symbol="VNM",
            valuation_date=date(
                2026,
                8,
                6,
            ),
            open_price=61_000,
            high_price=64_500,
            low_price=60_500,
            close_price=64_000,
        ),
    )

    assert result.passed
    assert result.paper_decision.should_exit
