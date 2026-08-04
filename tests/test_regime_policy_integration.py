from datetime import datetime, timedelta

from backtesting.portfolio_simulator import (
    PortfolioSimulator,
)
from backtesting.regime_policy import (
    RegimePortfolioPolicy,
)
from backtesting.trade import (
    ExitReason,
    Trade,
)
from backtesting.transaction_cost import (
    TransactionCostConfig,
)


def make_candidate(
    *,
    symbol: str,
    regime: str | None,
    entry_price: float = 100.0,
    stop_price: float = 90.0,
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
        quantity=1,
        stop_price=stop_price,
        market_regime=regime,
    )

    trade.close(
        exit_date=(
            entry_date
            + timedelta(days=10)
        ),
        exit_price=110.0,
        reason=ExitReason.TAKE_PROFIT,
    )

    return trade


def zero_costs():
    return TransactionCostConfig(
        buy_commission_pct=0.0,
        sell_commission_pct=0.0,
        sell_tax_pct=0.0,
        buy_slippage_pct=0.0,
        sell_slippage_pct=0.0,
    )


def test_policy_disabled_preserves_behavior():
    simulator = PortfolioSimulator(
        initial_cash=100_000,
        position_size_pct=20.0,
        max_positions=5,
        lot_size=1,
        regime_policy=None,
        transaction_cost_config=(
            zero_costs()
        ),
    )

    result = simulator.simulate(
        [
            make_candidate(
                symbol="HPG",
                regime="BEAR",
            )
        ]
    )

    assert len(
        result.executed_trades
    ) == 1


def test_bear_blocks_new_entries():
    simulator = PortfolioSimulator(
        initial_cash=100_000,
        position_size_pct=20.0,
        max_positions=5,
        lot_size=1,
        regime_policy=(
            RegimePortfolioPolicy()
        ),
        transaction_cost_config=(
            zero_costs()
        ),
    )

    result = simulator.simulate(
        [
            make_candidate(
                symbol="HPG",
                regime="BEAR",
            )
        ]
    )

    assert len(
        result.executed_trades
    ) == 0

    assert any(
        rejected.reason
        == "regime_entries_disabled"
        for rejected
        in result.rejected_trades
    )


def test_unknown_regime_is_rejected():
    simulator = PortfolioSimulator(
        initial_cash=100_000,
        position_size_pct=20.0,
        max_positions=5,
        lot_size=1,
        regime_policy=(
            RegimePortfolioPolicy()
        ),
        transaction_cost_config=(
            zero_costs()
        ),
    )

    result = simulator.simulate(
        [
            make_candidate(
                symbol="HPG",
                regime="NEW_STATE",
            )
        ]
    )

    assert len(
        result.executed_trades
    ) == 0

    assert any(
        rejected.reason
        == "unknown_market_regime"
        for rejected
        in result.rejected_trades
    )


def test_sideway_uses_lower_position_limit():
    simulator = PortfolioSimulator(
        initial_cash=1_000_000,
        position_size_pct=10.0,
        max_positions=5,
        lot_size=1,
        regime_policy=(
            RegimePortfolioPolicy()
        ),
        transaction_cost_config=(
            zero_costs()
        ),
    )

    entry_date = datetime(
        2026,
        1,
        2,
    )

    candidates = []

    for symbol in [
        "AAA",
        "BBB",
        "CCC",
        "DDD",
    ]:
        trade = Trade(
            symbol=symbol,
            entry_date=entry_date,
            entry_price=100.0,
            quantity=1,
            stop_price=99.0,
            market_regime="SIDEWAY",
        )

        trade.close(
            exit_date=(
                entry_date
                + timedelta(days=10)
            ),
            exit_price=110.0,
            reason=ExitReason.TAKE_PROFIT,
        )

        candidates.append(trade)

    result = simulator.simulate(
        candidates
    )

    assert len(
        result.executed_trades
    ) == 3


def test_bull_policy_applies_heat_limit():
    simulator = PortfolioSimulator(
        initial_cash=100_000,
        position_size_pct=100.0,
        max_positions=5,
        lot_size=1,
        regime_policy=(
            RegimePortfolioPolicy()
        ),
        max_portfolio_heat_pct=None,
        transaction_cost_config=(
            zero_costs()
        ),
    )

    # Bull rule allows 5% heat. This trade risks 10%.
    result = simulator.simulate(
        [
            make_candidate(
                symbol="HPG",
                regime="BULL",
                entry_price=100.0,
                stop_price=90.0,
            )
        ]
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


def test_static_heat_and_regime_use_stricter_limit():
    simulator = PortfolioSimulator(
        initial_cash=100_000,
        position_size_pct=40.0,
        max_positions=5,
        lot_size=1,
        regime_policy=(
            RegimePortfolioPolicy()
        ),
        max_portfolio_heat_pct=3.0,
        transaction_cost_config=(
            zero_costs()
        ),
    )

    # Bull allows 5%, static limit is 3%, projected heat is 4%.
    result = simulator.simulate(
        [
            make_candidate(
                symbol="HPG",
                regime="BULL",
                entry_price=100.0,
                stop_price=90.0,
            )
        ]
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
