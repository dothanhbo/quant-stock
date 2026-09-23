from dataclasses import replace
from datetime import datetime

import pytest

from backtesting.portfolio import Portfolio
from backtesting.portfolio_simulator import CandidatePriorityEvidence, PortfolioSimulator
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


def _priority_trade(
    symbol: str,
    *,
    signal_day: int,
    score: float,
    volume: float | None,
    entry_day: int = 10,
) -> Trade:
    signal_date = datetime(2026, 1, signal_day)
    trade = Trade(
        symbol=symbol, signal_date=signal_date,
        entry_date=datetime(2026, 1, entry_day), entry_price=100,
        quantity=1, signal_score=score, volume_ratio=volume,
    )
    trade.close(datetime(2026, 1, 20), 105, ExitReason.TAKE_PROFIT)
    return trade


def _priority_evidence(trades: list[Trade], fingerprint: str = "volume-policy"):
    # Baseline score order is A, B, C, D. Signal-day slot sets are {1,3}/{2,4}.
    final = {"A": 3, "B": 4, "C": 1, "D": 2}
    baseline = {"A": 1, "B": 2, "C": 3, "D": 4}
    variant = {"A": 2, "B": 2, "C": 1, "D": 1}
    slots = {"A": (1, 3), "C": (1, 3), "B": (2, 4), "D": (2, 4)}
    qualities = {"A": .8, "B": .8, "C": .9, "D": .9}
    return tuple(CandidatePriorityEvidence(
        candidate_key=f"{trade.symbol}|{trade.signal_date.isoformat()}",
        symbol=trade.symbol, signal_date=trade.signal_date, entry_date=trade.entry_date,
        volume_ratio=trade.volume_ratio,
        volume_ratio_finite=trade.volume_ratio is not None,
        q70_quality_score=qualities[trade.symbol],
        baseline_signal_score=trade.signal_score,
        baseline_within_entry_date_ordinal=baseline[trade.symbol],
        signal_date_group_key=trade.signal_date.date().isoformat(),
        signal_date_group_size=2,
        signal_date_group_slot_ordinals=slots[trade.symbol],
        variant_within_signal_date_ordinal=variant[trade.symbol],
        final_simulator_priority_ordinal=final[trade.symbol],
        ranking_policy_fingerprint=fingerprint,
    ) for trade in trades)


def test_opt_in_priority_is_slot_preserving_and_daily_cap_uses_reordered_top_three():
    trades = [
        _priority_trade("A", signal_day=8, score=100, volume=1.0),
        _priority_trade("B", signal_day=9, score=90, volume=1.0),
        _priority_trade("C", signal_day=8, score=80, volume=3.0),
        _priority_trade("D", signal_day=9, score=70, volume=3.0),
    ]
    baseline = PortfolioSimulator(
        initial_cash=1_000_000, position_size_pct=10, max_positions=10,
        lot_size=1, ranking_method="signal_score", max_new_positions_per_day=3,
    ).simulate(trades)
    variant = PortfolioSimulator(
        initial_cash=1_000_000, position_size_pct=10, max_positions=10,
        lot_size=1, ranking_method="signal_score", max_new_positions_per_day=3,
        candidate_priority_evidence=_priority_evidence(trades),
        candidate_priority_policy_fingerprint="volume-policy",
    ).simulate(trades)
    assert {item.symbol for item in baseline.executed_trades} == {"A", "B", "C"}
    assert {item.symbol for item in variant.executed_trades} == {"A", "C", "D"}
    assert [(item.trade.symbol, item.reason) for item in baseline.rejected_trades] == [("D", "maximum_orders_per_scan")]
    assert [(item.trade.symbol, item.reason) for item in variant.rejected_trades] == [("B", "maximum_orders_per_scan")]
    evidence = _priority_evidence(trades)
    for signal_day in (8, 9):
        group = [item for item in evidence if item.signal_date.day == signal_day]
        assert {item.baseline_within_entry_date_ordinal for item in group} == {
            item.final_simulator_priority_ordinal for item in group
        }


def test_priority_never_interacts_across_entry_dates_and_default_is_unchanged():
    first = _priority_trade("A", signal_day=8, score=1, volume=1, entry_day=10)
    second = _priority_trade("B", signal_day=8, score=100, volume=100, entry_day=11)
    omitted = PortfolioSimulator(
        initial_cash=1_000_000, position_size_pct=10, max_positions=10, lot_size=1,
        ranking_method="signal_score",
    ).simulate([first, second])
    explicit = PortfolioSimulator(
        initial_cash=1_000_000, position_size_pct=10, max_positions=10, lot_size=1,
        ranking_method="signal_score", candidate_priority_evidence=None,
    ).simulate([first, second])
    assert [item.symbol for item in omitted.executed_trades] == [item.symbol for item in explicit.executed_trades]
    assert omitted.final_equity == explicit.final_equity


def test_invalid_priority_sidecars_fail_clearly():
    trades = [
        _priority_trade("A", signal_day=8, score=100, volume=1.0),
        _priority_trade("B", signal_day=9, score=90, volume=1.0),
        _priority_trade("C", signal_day=8, score=80, volume=3.0),
        _priority_trade("D", signal_day=9, score=70, volume=3.0),
    ]
    evidence = _priority_evidence(trades)
    with pytest.raises(ValueError, match="missing candidate priority evidence"):
        PortfolioSimulator(
            initial_cash=1_000_000, lot_size=1,
            candidate_priority_evidence=evidence[:-1],
            candidate_priority_policy_fingerprint="volume-policy",
        ).simulate(trades)
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        PortfolioSimulator(
            initial_cash=1_000_000, lot_size=1,
            candidate_priority_evidence=evidence,
            candidate_priority_policy_fingerprint="wrong",
        )
    with pytest.raises(ValueError, match="slot set"):
        replace(evidence[0], final_simulator_priority_ordinal=2)


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
