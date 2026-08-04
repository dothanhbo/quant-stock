from datetime import datetime, timedelta

from backtesting.portfolio_simulator import (
    PortfolioSimulator,
)
from backtesting.trade import (
    ExitReason,
    Trade,
)
from backtesting.transaction_cost import (
    TransactionCostConfig,
)


def make_closed_candidate(
    *,
    symbol: str,
    entry_price: float,
    stop_price: float | None,
    quantity: int = 1,
) -> Trade:
    entry_date = datetime(
        2026,
        1,
        2,
    )

    trade = Trade(
        symbol=symbol,
        entry_date=entry_date,
        entry_price=entry_price,
        quantity=quantity,
        stop_price=stop_price,
    )

    trade.close(
        exit_date=(
            entry_date
            + timedelta(days=10)
        ),
        exit_price=(
            entry_price * 1.05
        ),
        reason=ExitReason.TAKE_PROFIT,
    )

    return trade


def zero_cost_config():
    return TransactionCostConfig(
        buy_commission_pct=0.0,
        sell_commission_pct=0.0,
        sell_tax_pct=0.0,
        buy_slippage_pct=0.0,
        sell_slippage_pct=0.0,
    )


def test_heat_disabled_preserves_old_behavior():
    simulator = PortfolioSimulator(
        initial_cash=100_000,
        position_size_pct=50.0,
        max_positions=2,
        lot_size=1,
        max_portfolio_heat_pct=None,
        transaction_cost_config=(
            zero_cost_config()
        ),
    )

    candidate = make_closed_candidate(
        symbol="HPG",
        entry_price=100,
        stop_price=None,
    )

    result = simulator.simulate(
        [candidate]
    )

    assert len(
        result.executed_trades
    ) == 1


def test_heat_rejects_candidate_above_limit():
    simulator = PortfolioSimulator(
        initial_cash=100_000,
        position_size_pct=50.0,
        max_positions=2,
        lot_size=1,
        max_portfolio_heat_pct=2.0,
        transaction_cost_config=(
            zero_cost_config()
        ),
    )

    # 50% allocation -> 500 shares.
    # Risk/share = 10, risk amount = 5,000,
    # so proposed portfolio heat is 5%.
    candidate = make_closed_candidate(
        symbol="HPG",
        entry_price=100,
        stop_price=90,
    )

    result = simulator.simulate(
        [candidate]
    )

    assert len(
        result.executed_trades
    ) == 0

    assert any(
        rejected.reason
        == "portfolio_heat_limit"
        for rejected
        in result.rejected_trades
    )


def test_heat_allows_candidate_within_limit():
    simulator = PortfolioSimulator(
        initial_cash=100_000,
        position_size_pct=20.0,
        max_positions=2,
        lot_size=1,
        max_portfolio_heat_pct=3.0,
        transaction_cost_config=(
            zero_cost_config()
        ),
    )

    # 20% allocation -> 200 shares.
    # Risk/share = 10, risk amount = 2,000,
    # projected heat = 2%.
    candidate = make_closed_candidate(
        symbol="HPG",
        entry_price=100,
        stop_price=90,
    )

    result = simulator.simulate(
        [candidate]
    )

    assert len(
        result.executed_trades
    ) == 1

    assert (
        result.executed_trades[0]
        .risk_amount
        == 2_000
    )


def test_heat_limit_rejects_missing_stop():
    simulator = PortfolioSimulator(
        initial_cash=100_000,
        position_size_pct=20.0,
        max_positions=2,
        lot_size=1,
        max_portfolio_heat_pct=3.0,
        transaction_cost_config=(
            zero_cost_config()
        ),
    )

    candidate = make_closed_candidate(
        symbol="HPG",
        entry_price=100,
        stop_price=None,
    )

    result = simulator.simulate(
        [candidate]
    )

    assert len(
        result.executed_trades
    ) == 0

    assert any(
        rejected.reason
        == "missing_stop_price"
        for rejected
        in result.rejected_trades
    )
