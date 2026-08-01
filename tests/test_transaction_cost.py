import pytest

from backtesting.transaction_cost import (
    TransactionCostConfig,
    apply_buy_slippage,
    apply_sell_slippage,
    calculate_buy_cost,
    calculate_sell_cost,
)


def test_calculate_buy_cost():
    config = TransactionCostConfig(
        buy_commission_pct=0.15,
        sell_commission_pct=0.15,
        sell_tax_pct=0.10,
    )

    result = calculate_buy_cost(
        price=20_000,
        quantity=100,
        config=config,
    )

    assert result.gross_value == 2_000_000
    assert result.commission == 3_000
    assert result.tax == 0
    assert result.total_cost == 3_000
    assert result.net_value == 2_003_000


def test_calculate_sell_cost():
    config = TransactionCostConfig(
        buy_commission_pct=0.15,
        sell_commission_pct=0.15,
        sell_tax_pct=0.10,
    )

    result = calculate_sell_cost(
        price=22_000,
        quantity=100,
        config=config,
    )

    assert result.gross_value == 2_200_000
    assert result.commission == 3_300
    assert result.tax == 2_200
    assert result.total_cost == 5_500
    assert result.net_value == 2_194_500


def test_rejects_negative_cost_config():
    config = TransactionCostConfig(
        buy_commission_pct=-0.1,
    )

    with pytest.raises(ValueError):
        config.validate()


def test_rejects_invalid_quantity():
    config = TransactionCostConfig()

    with pytest.raises(ValueError):
        calculate_buy_cost(
            price=20_000,
            quantity=0,
            config=config,
        )

def test_apply_buy_slippage():
    config = TransactionCostConfig(
        buy_slippage_pct=0.05,
    )

    adjusted = apply_buy_slippage(
        price=20_000,
        config=config,
    )

    assert adjusted == pytest.approx(20_010)


def test_apply_sell_slippage():
    config = TransactionCostConfig(
        sell_slippage_pct=0.05,
    )

    adjusted = apply_sell_slippage(
        price=22_000,
        config=config,
    )

    assert adjusted == pytest.approx(21_989)