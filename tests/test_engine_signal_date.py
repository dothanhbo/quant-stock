from datetime import datetime

from backtesting.trade import Trade


def test_signal_date_is_decision_time_and_entry_date_is_execution_time():
    t = Trade(
        symbol="AAA",
        signal_date=datetime(2026, 9, 1),
        entry_date=datetime(2026, 9, 2),
        entry_price=100.0,
        quantity=100,
    )
    assert t.signal_date < t.entry_date
