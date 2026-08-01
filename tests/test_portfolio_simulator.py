from datetime import datetime

import pytest

from backtesting.portfolio import Portfolio
from backtesting.portfolio_simulator import PortfolioSimulator
from backtesting.trade import ExitReason, Trade
from backtesting.transaction_cost import TransactionCostConfig

def create_closed_trade(
    symbol: str,
    entry_date: datetime,
    exit_date: datetime,
    entry_price: float,
    exit_price: float,
) -> Trade:
    trade = Trade(
        symbol=symbol,
        entry_date=entry_date,
        entry_price=entry_price,
        quantity=1,
    )

    trade.close(
        exit_date=exit_date,
        exit_price=exit_price,
        reason=(
            ExitReason.TAKE_PROFIT
            if exit_price > entry_price
            else ExitReason.STOP_LOSS
        ),
    )

    return trade


def test_simulator_uses_one_shared_cash_balance():
    simulator = PortfolioSimulator(
        initial_cash=100_000,
        position_size_pct=50,
        max_positions=2,
        lot_size=100,
    )

    trades = [
        create_closed_trade(
            symbol="HPG",
            entry_date=datetime(2026, 1, 2),
            exit_date=datetime(2026, 1, 10),
            entry_price=20,
            exit_price=22,
        ),
        create_closed_trade(
            symbol="FPT",
            entry_date=datetime(2026, 1, 3),
            exit_date=datetime(2026, 1, 12),
            entry_price=100,
            exit_price=110,
        ),
    ]

    result = simulator.simulate(trades)

    assert len(result.executed_trades) == 2
    assert result.final_equity > 100_000


def test_simulator_respects_max_positions():
    simulator = PortfolioSimulator(
        initial_cash=100_000,
        position_size_pct=30,
        max_positions=1,
        lot_size=100,
    )

    trades = [
        create_closed_trade(
            "HPG",
            datetime(2026, 1, 2),
            datetime(2026, 1, 10),
            20,
            22,
        ),
        create_closed_trade(
            "FPT",
            datetime(2026, 1, 3),
            datetime(2026, 1, 12),
            100,
            110,
        ),
    ]

    result = simulator.simulate(trades)

    assert len(result.executed_trades) == 1
    assert len(result.rejected_trades) == 1


def test_simulator_quantity_uses_lot_size():
    simulator = PortfolioSimulator(
        initial_cash=100_000,
        position_size_pct=50,
        max_positions=2,
        lot_size=100,
    )

    trade = create_closed_trade(
        "HPG",
        datetime(2026, 1, 2),
        datetime(2026, 1, 10),
        20,
        22,
    )

    result = simulator.simulate([trade])

    executed = result.executed_trades[0]

    assert executed.quantity % 100 == 0
    assert executed.quantity == 2_500


def test_simulator_builds_equity_curve():
    simulator = PortfolioSimulator(
        initial_cash=100_000,
        position_size_pct=50,
        max_positions=2,
        lot_size=100,
    )

    trade = create_closed_trade(
        "HPG",
        datetime(2026, 1, 2),
        datetime(2026, 1, 10),
        20,
        22,
    )

    result = simulator.simulate([trade])

    assert not result.equity_curve.empty
    assert "cash" in result.equity_curve.columns
    assert "equity" in result.equity_curve.columns
    assert "drawdown_pct" in result.equity_curve.columns

def test_same_day_trade_is_closed():
    simulator = PortfolioSimulator(
        initial_cash=100_000,
        position_size_pct=20,
        max_positions=5,
        lot_size=100,
    )

    trade = create_closed_trade(
        symbol="HPG",
        entry_date=datetime(2026, 1, 2),
        exit_date=datetime(2026, 1, 2),
        entry_price=20,
        exit_price=21,
    )

    result = simulator.simulate([trade])

    assert len(result.executed_trades) == 1
    assert result.final_open_positions == 0
    assert result.final_market_value == 0


def test_simulator_applies_transaction_costs():
    simulator = PortfolioSimulator(
        initial_cash=10_000_000,
        position_size_pct=20,
        max_positions=5,
        lot_size=100,
        transaction_cost_config=TransactionCostConfig(
            buy_commission_pct=0.15,
            sell_commission_pct=0.15,
            sell_tax_pct=0.10,
        ),
    )

    trade = create_closed_trade(
        symbol="HPG",
        entry_date=datetime(2026, 1, 2),
        exit_date=datetime(2026, 1, 10),
        entry_price=20_000,
        exit_price=22_000,
    )

    result = simulator.simulate([trade])

    assert len(result.executed_trades) == 1

    executed = result.executed_trades[0]

    assert executed.buy_commission == 3_000
    assert executed.sell_commission == 3_300
    assert executed.sell_tax == 2_200
    assert executed.net_pnl == 191_500
    assert result.final_cash == 10_191_500

def test_simulator_applies_transaction_costs():
    simulator = PortfolioSimulator(
        initial_cash=10_000_000,
        position_size_pct=25,
        max_positions=5,
        lot_size=100,
        transaction_cost_config=TransactionCostConfig(
            buy_commission_pct=0.15,
            sell_commission_pct=0.15,
            sell_tax_pct=0.10,
        ),
    )

    trade = create_closed_trade(
        symbol="HPG",
        entry_date=datetime(2026, 1, 2),
        exit_date=datetime(2026, 1, 10),
        entry_price=20_000,
        exit_price=22_000,
    )

    result = simulator.simulate([trade])
    print(result.rejected_trades)
    assert len(result.executed_trades) == 1

    executed = result.executed_trades[0]

    assert executed.buy_commission == 3_000
    assert executed.sell_commission == 3_300
    assert executed.sell_tax == 2_200
    assert executed.net_pnl == 191_500
    assert result.final_cash == 10_191_500

def test_portfolio_applies_slippage():
    portfolio = Portfolio(
        initial_cash=10_000_000,
        transaction_cost_config=TransactionCostConfig(
            buy_commission_pct=0.0,
            sell_commission_pct=0.0,
            sell_tax_pct=0.0,
            buy_slippage_pct=0.05,
            sell_slippage_pct=0.05,
        ),
    )

    trade = portfolio.open_position(
        symbol="HPG",
        entry_date=datetime(2026, 1, 2),
        entry_price=20_000,
        quantity=100,
    )

    assert trade.entry_price == pytest.approx(20_010)

    closed = portfolio.close_position(
        symbol="HPG",
        exit_date=datetime(2026, 1, 10),
        exit_price=22_000,
        reason=ExitReason.TAKE_PROFIT,
    )

    assert closed.exit_price == pytest.approx(21_989)