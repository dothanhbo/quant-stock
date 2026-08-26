from config.trading_policy import TradingPolicy


def test_default_policy_is_frozen_hybrid_atr() -> None:
    policy = TradingPolicy()
    assert policy.entry_model == "hybrid"
    assert policy.execution_timing == "next_open"
    assert policy.calculate_levels(entry_price=100.0, atr=2.0) == (96.0, 110.0)
    assert policy.trailing_atr_multiplier == 2.0
    assert policy.maximum_holding_days == 30
