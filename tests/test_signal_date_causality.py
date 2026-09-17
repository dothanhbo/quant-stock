from datetime import datetime

from backtesting.trade import Trade


def test_trade_keeps_signal_date_separate_from_execution_date():
    signal = datetime(2026, 9, 1)
    entry = datetime(2026, 9, 2)
    trade = Trade(
        symbol="TEST",
        entry_date=entry,
        signal_date=signal,
        entry_price=100.0,
        quantity=100,
    )
    assert trade.signal_date == signal
    assert trade.entry_date == entry
    assert trade.signal_date != trade.entry_date


def test_trade_serializes_signal_date():
    signal = datetime(2026, 9, 1)
    trade = Trade(
        symbol="TEST",
        entry_date=datetime(2026, 9, 2),
        signal_date=signal,
        entry_price=100.0,
        quantity=100,
    )
    assert trade.to_dict()["signal_date"] == signal
