from datetime import datetime

from backtesting.trade import ExitReason, Trade


def create_trade() -> Trade:
    return Trade(
        symbol="HPG",
        entry_date=datetime(2025, 1, 1),
        entry_price=25.0,
        quantity=100,
    )


def test_new_trade_is_open():
    trade = create_trade()

    assert trade.is_closed is False
    assert trade.pnl == 0.0
    assert trade.return_pct == 0.0
    assert trade.holding_days == 0


def test_close_trade():
    trade = create_trade()

    trade.close(
        exit_date=datetime(2025, 1, 10),
        exit_price=27.0,
        reason=ExitReason.TAKE_PROFIT,
    )

    assert trade.is_closed is True
    assert trade.exit_date == datetime(2025, 1, 10)
    assert trade.exit_price == 27.0
    assert trade.exit_reason == ExitReason.TAKE_PROFIT


def test_trade_pnl():
    trade = create_trade()

    trade.close(
        exit_date=datetime(2025, 1, 10),
        exit_price=27.0,
        reason=ExitReason.TAKE_PROFIT,
    )

    assert trade.pnl == 200.0


def test_trade_return_pct():
    trade = create_trade()

    trade.close(
        exit_date=datetime(2025, 1, 10),
        exit_price=27.0,
        reason=ExitReason.TAKE_PROFIT,
    )

    assert trade.return_pct == 8.0


def test_trade_holding_days():
    trade = create_trade()

    trade.close(
        exit_date=datetime(2025, 1, 11),
        exit_price=27.0,
        reason=ExitReason.TAKE_PROFIT,
    )

    assert trade.holding_days == 10

def test_trade_is_win():
    trade = create_trade()

    trade.close(
        exit_date=datetime(2025, 1, 10),
        exit_price=27.0,
        reason=ExitReason.TAKE_PROFIT,
    )

    assert trade.is_win is True


def test_losing_trade_is_not_win():
    trade = create_trade()

    trade.close(
        exit_date=datetime(2025, 1, 10),
        exit_price=24.0,
        reason=ExitReason.STOP_LOSS,
    )

    assert trade.is_win is False


def test_trade_cost():
    trade = create_trade()

    assert trade.cost == 2500.0


def test_trade_market_value():
    trade = create_trade()

    trade.close(
        exit_date=datetime(2025, 1, 10),
        exit_price=27.0,
        reason=ExitReason.TAKE_PROFIT,
    )

    assert trade.market_value == 2700.0


def test_trade_to_dict():
    trade = create_trade()

    trade.close(
        exit_date=datetime(2025, 1, 10),
        exit_price=27.0,
        reason=ExitReason.TAKE_PROFIT,
    )

    result = trade.to_dict()

    assert result["symbol"] == "HPG"
    assert result["exit_reason"] == "Take Profit"
    assert result["execution"] == "Normal"
    assert result["pnl"] == 200.0
    assert result["return_pct"] == 8.0
    assert result["is_win"] is True