from datetime import datetime

import pytest

from backtesting.portfolio import (
    DuplicatePositionError,
    InsufficientCashError,
    Portfolio,
    PositionNotFoundError,
)
from backtesting.trade import ExitReason
from backtesting.transaction_cost import TransactionCostConfig

def test_open_position_reduces_cash():
    portfolio = Portfolio(initial_cash=100_000)

    trade = portfolio.open_position(
        symbol="HPG",
        entry_date=datetime(2026, 1, 1),
        entry_price=20,
        quantity=1_000,
    )

    assert trade.symbol == "HPG"
    assert portfolio.cash == 80_000
    assert len(portfolio.open_positions) == 1
    assert len(portfolio.closed_positions) == 0


def test_close_winning_position_updates_cash_and_pnl():
    portfolio = Portfolio(initial_cash=100_000)

    portfolio.open_position(
        symbol="HPG",
        entry_date=datetime(2026, 1, 1),
        entry_price=20,
        quantity=1_000,
    )

    trade = portfolio.close_position(
        symbol="HPG",
        exit_date=datetime(2026, 1, 10),
        exit_price=22,
        reason=ExitReason.TAKE_PROFIT,
    )

    assert trade.is_closed
    assert trade.pnl == 2_000

    assert portfolio.cash == 102_000
    assert portfolio.realized_pnl == 2_000

    assert len(portfolio.open_positions) == 0
    assert len(portfolio.closed_positions) == 1


def test_close_losing_position_updates_cash_and_pnl():
    portfolio = Portfolio(initial_cash=100_000)

    portfolio.open_position(
        symbol="HPG",
        entry_date=datetime(2026, 1, 1),
        entry_price=20,
        quantity=1_000,
    )

    trade = portfolio.close_position(
        symbol="HPG",
        exit_date=datetime(2026, 1, 10),
        exit_price=19,
        reason=ExitReason.STOP_LOSS,
    )

    assert trade.pnl == -1_000
    assert portfolio.cash == 99_000
    assert portfolio.realized_pnl == -1_000


def test_equity_uses_current_market_prices():
    portfolio = Portfolio(initial_cash=100_000)

    portfolio.open_position(
        symbol="HPG",
        entry_date=datetime(2026, 1, 1),
        entry_price=20,
        quantity=1_000,
    )

    assert portfolio.cash == 80_000

    equity = portfolio.equity(
        current_prices={"HPG": 21}
    )

    assert equity == 101_000
    assert portfolio.unrealized_pnl(
        {"HPG": 21}
    ) == 1_000


def test_rejects_position_when_cash_is_insufficient():
    portfolio = Portfolio(initial_cash=10_000)

    with pytest.raises(InsufficientCashError):
        portfolio.open_position(
            symbol="HPG",
            entry_date=datetime(2026, 1, 1),
            entry_price=20,
            quantity=1_000,
        )


def test_rejects_duplicate_open_position():
    portfolio = Portfolio(initial_cash=100_000)

    portfolio.open_position(
        symbol="HPG",
        entry_date=datetime(2026, 1, 1),
        entry_price=20,
        quantity=1_000,
    )

    with pytest.raises(DuplicatePositionError):
        portfolio.open_position(
            symbol="HPG",
            entry_date=datetime(2026, 1, 2),
            entry_price=21,
            quantity=1_000,
        )


def test_close_unknown_position_raises_error():
    portfolio = Portfolio(initial_cash=100_000)

    with pytest.raises(PositionNotFoundError):
        portfolio.close_position(
            symbol="HPG",
            exit_date=datetime(2026, 1, 10),
            exit_price=22,
            reason=ExitReason.TAKE_PROFIT,
        )


def test_summary():
    portfolio = Portfolio(initial_cash=100_000)

    portfolio.open_position(
        symbol="HPG",
        entry_date=datetime(2026, 1, 1),
        entry_price=20,
        quantity=1_000,
    )

    summary = portfolio.summary(
        current_prices={"HPG": 21}
    )

    assert summary["initial_cash"] == 100_000
    assert summary["cash"] == 80_000
    assert summary["market_value"] == 21_000
    assert summary["equity"] == 101_000
    assert summary["unrealized_pnl"] == 1_000
    assert summary["open_positions"] == 1

def test_portfolio_applies_transaction_costs():
    portfolio = Portfolio(
        initial_cash=10_000_000,
        transaction_cost_config=TransactionCostConfig(
            buy_commission_pct=0.15,
            sell_commission_pct=0.15,
            sell_tax_pct=0.10,
            buy_slippage_pct=0.0,
            sell_slippage_pct=0.0,
        ),
    )

    trade = portfolio.open_position(
        symbol="HPG",
        entry_date=datetime(2026, 1, 2),
        entry_price=20_000,
        quantity=100,
    )

    assert trade.buy_commission == 3_000
    assert portfolio.cash == 7_997_000

    closed = portfolio.close_position(
        symbol="HPG",
        exit_date=datetime(2026, 1, 10),
        exit_price=22_000,
        reason=ExitReason.TAKE_PROFIT,
    )

    assert closed.sell_commission == 3_300
    assert closed.sell_tax == 2_200
    assert closed.net_pnl == 191_500
    assert portfolio.cash == 10_191_500