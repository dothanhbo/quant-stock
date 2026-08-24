from __future__ import annotations

import pandas as pd
import pytest

from research.monthly_momentum_baseline import (
    MonthlyMomentumConfig,
    apply_breadth_review_policy,
    breadth_exposure_pct,
    build_universe_snapshot,
    monthly_signal_execution_dates,
    prepare_market_regime,
    prepare_market_breadth,
    prepare_symbol_features,
    simulate_monthly_momentum,
)


def prices(symbol: str, *, periods: int = 320, slope: float = 0.2) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=periods)
    close = pd.Series(
        [100.0 + index * slope for index in range(periods)],
        dtype=float,
    )
    return pd.DataFrame({
        "symbol": symbol,
        "time": dates,
        "open": close - 0.1,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": [2_000_000.0] * periods,
    })


def test_momentum_features_have_no_future_lookahead() -> None:
    normal = prices("AAA")
    shocked = normal.copy()
    shocked.loc[319, "close"] = 10_000.0

    first = prepare_symbol_features(normal)
    second = prepare_symbol_features(shocked)

    columns = ["momentum_6_1_pct", "ema200", "adtv20", "volatility63_pct"]
    assert first.loc[318, columns].tolist() == pytest.approx(
        second.loc[318, columns].tolist()
    )


def test_price_scale_converts_thousand_vnd_before_adtv() -> None:
    features = prepare_symbol_features(
        prices("AAA"),
        price_scale=1000.0,
    )

    assert features.iloc[0]["close"] == pytest.approx(100_000.0)
    assert features.iloc[19]["adtv20"] > 10_000_000_000.0


def test_month_end_signal_executes_next_market_session() -> None:
    calendar = pd.bdate_range("2026-01-01", "2026-03-10")
    pairs = monthly_signal_execution_dates(
        calendar,
        start_date="2026-01-01",
        end_date="2026-03-10",
    )

    assert pairs[0] == (
        pd.Timestamp("2026-01-30"),
        pd.Timestamp("2026-02-02"),
    )


def test_month_end_signal_can_delay_one_additional_session() -> None:
    calendar = pd.bdate_range("2026-01-01", "2026-03-10")
    pairs = monthly_signal_execution_dates(
        calendar,
        start_date="2026-01-01",
        end_date="2026-03-10",
        execution_delay_sessions=1,
    )

    assert pairs[0] == (
        pd.Timestamp("2026-01-30"),
        pd.Timestamp("2026-02-03"),
    )


def test_dynamic_snapshot_selects_only_causal_eligible_leader() -> None:
    fast = prepare_symbol_features(prices("FAST", slope=0.3))
    slow = prepare_symbol_features(prices("SLOW", slope=0.1))
    config = MonthlyMomentumConfig(
        top_n=1,
        minimum_history_rows=252,
        minimum_adtv20=1.0,
        maximum_volatility63_pct=100.0,
    )
    signal_date = fast.iloc[-2]["time"]

    snapshot = build_universe_snapshot(
        {"FAST": fast, "SLOW": slow},
        signal_date=signal_date,
        config=config,
    )

    selected = snapshot.loc[snapshot["selected"], "symbol"].tolist()
    assert selected == ["FAST"]
    assert snapshot["eligible"].all()


def test_monthly_simulator_buys_after_signal_and_marks_equity() -> None:
    stock = prepare_symbol_features(prices("AAA"))
    benchmark = prices("VNINDEX")[["time", "close"]]
    config = MonthlyMomentumConfig(
        top_n=1,
        gross_exposure_pct=80.0,
        minimum_history_rows=252,
        minimum_adtv20=1.0,
        maximum_volatility63_pct=100.0,
        lot_size=1,
        commission_pct=0.0,
        sell_tax_pct=0.0,
        slippage_pct=0.0,
    )

    result = simulate_monthly_momentum(
        feature_cache={"AAA": stock},
        benchmark=benchmark,
        start_date=stock.iloc[250]["time"],
        end_date=stock.iloc[-1]["time"],
        initial_capital=100_000.0,
        config=config,
    )

    orders = result["orders"]
    assert not orders.empty
    first = orders.iloc[0]
    assert first["side"] == "BUY"
    assert pd.Timestamp(first["execution_date"]) > pd.Timestamp(first["signal_date"])
    assert float(result["final_equity"]) > 100_000.0


def test_execution_delay_defers_monthly_fill_one_session() -> None:
    stock = prepare_symbol_features(prices("AAA"))
    benchmark = prices("VNINDEX")[["time", "close"]]
    config = MonthlyMomentumConfig(
        top_n=1,
        gross_exposure_pct=80.0,
        minimum_history_rows=252,
        minimum_adtv20=1.0,
        maximum_volatility63_pct=100.0,
        lot_size=1,
        commission_pct=0.0,
        sell_tax_pct=0.0,
        slippage_pct=0.0,
        execution_delay_sessions=1,
    )

    result = simulate_monthly_momentum(
        feature_cache={"AAA": stock},
        benchmark=benchmark,
        start_date=stock.iloc[250]["time"],
        end_date=stock.iloc[-1]["time"],
        initial_capital=100_000.0,
        config=config,
    )

    first = result["orders"].iloc[0]
    calendar = pd.DatetimeIndex(benchmark["time"])
    signal_position = calendar.get_loc(pd.Timestamp(first["signal_date"]))
    expected_execution = calendar[signal_position + 2]
    assert pd.Timestamp(first["execution_date"]) == expected_execution


def test_hold_buffer_retains_existing_name_inside_hold_rank() -> None:
    feature_cache = {
        "FAST": prepare_symbol_features(prices("FAST", slope=0.30)),
        "MID": prepare_symbol_features(prices("MID", slope=0.20)),
        "HELD": prepare_symbol_features(prices("HELD", slope=0.10)),
    }
    config = MonthlyMomentumConfig(
        top_n=2,
        entry_rank_max=2,
        hold_rank_max=3,
        minimum_history_rows=252,
        minimum_adtv20=1.0,
        maximum_volatility63_pct=100.0,
    )

    snapshot = build_universe_snapshot(
        feature_cache,
        signal_date=feature_cache["FAST"].iloc[-1]["time"],
        config=config,
        current_symbols={"HELD"},
    )

    assert set(snapshot.loc[snapshot["selected"], "symbol"]) == {"FAST", "HELD"}
    held = snapshot.loc[snapshot["symbol"] == "HELD"].iloc[0]
    assert held["selection_reason"] == "HOLD_BUFFER"


def test_market_regime_is_causal_before_future_shock() -> None:
    benchmark = prices("VNINDEX")[["time", "close"]]
    shocked = benchmark.copy()
    shocked.loc[319, "close"] = 1.0

    first = prepare_market_regime(benchmark)
    second = prepare_market_regime(shocked)

    assert first.loc[318, ["ema50", "ema200"]].tolist() == pytest.approx(
        second.loc[318, ["ema50", "ema200"]].tolist()
    )
    assert first.loc[318, "regime"] == second.loc[318, "regime"]


def test_daily_exit_signal_executes_at_next_open() -> None:
    raw = prices("AAA", periods=340)
    raw.loc[280, "close"] = 20.0
    stock = prepare_symbol_features(raw)
    benchmark = prices("VNINDEX", periods=340)[["time", "close"]]
    config = MonthlyMomentumConfig(
        top_n=1,
        minimum_history_rows=252,
        minimum_adtv20=1.0,
        maximum_volatility63_pct=100.0,
        lot_size=1,
        commission_pct=0.0,
        sell_tax_pct=0.0,
        slippage_pct=0.0,
        daily_exit_enabled=True,
    )

    result = simulate_monthly_momentum(
        feature_cache={"AAA": stock},
        benchmark=benchmark,
        start_date=stock.iloc[250]["time"],
        end_date=stock.iloc[-1]["time"],
        initial_capital=100_000.0,
        config=config,
    )

    exits = result["orders"].loc[
        result["orders"]["reason"].str.startswith("DAILY_EXIT")
    ]
    assert not exits.empty
    first_exit = exits.iloc[0]
    assert pd.Timestamp(first_exit["execution_date"]) > pd.Timestamp(
        first_exit["signal_date"]
    )


def test_bear_regime_holds_cash() -> None:
    stock = prepare_symbol_features(prices("AAA"))
    benchmark = prices("VNINDEX", slope=-0.15)[["time", "close"]]
    config = MonthlyMomentumConfig(
        top_n=1,
        minimum_history_rows=252,
        minimum_adtv20=1.0,
        maximum_volatility63_pct=100.0,
        lot_size=1,
        regime_exposure_enabled=True,
        bull_exposure_pct=80.0,
        sideway_exposure_pct=40.0,
        bear_exposure_pct=0.0,
    )

    result = simulate_monthly_momentum(
        feature_cache={"AAA": stock},
        benchmark=benchmark,
        start_date=stock.iloc[250]["time"],
        end_date=stock.iloc[-1]["time"],
        initial_capital=100_000.0,
        config=config,
    )

    assert result["orders"].empty
    assert (result["equity"]["target_gross_exposure_pct"] == 0.0).all()


def test_rebalance_tolerance_avoids_small_monthly_topups() -> None:
    stock = prepare_symbol_features(prices("AAA"))
    benchmark = prices("VNINDEX")[["time", "close"]]
    config = MonthlyMomentumConfig(
        top_n=1,
        minimum_history_rows=252,
        minimum_adtv20=1.0,
        maximum_volatility63_pct=100.0,
        lot_size=1,
        commission_pct=0.0,
        sell_tax_pct=0.0,
        slippage_pct=0.0,
        rebalance_tolerance_pct=100.0,
    )

    result = simulate_monthly_momentum(
        feature_cache={"AAA": stock},
        benchmark=benchmark,
        start_date=stock.iloc[250]["time"],
        end_date=stock.iloc[-1]["time"],
        initial_capital=100_000.0,
        config=config,
    )

    assert result["orders"]["side"].tolist() == ["BUY"]


def test_momentum_only_exit_does_not_require_ema_break() -> None:
    stock = prepare_symbol_features(prices("AAA", periods=340))
    stock.loc[280, "momentum_6_1_pct"] = -1.0
    benchmark = prices("VNINDEX", periods=340)[["time", "close"]]
    config = MonthlyMomentumConfig(
        top_n=1,
        minimum_history_rows=252,
        minimum_adtv20=1.0,
        maximum_volatility63_pct=100.0,
        lot_size=1,
        commission_pct=0.0,
        sell_tax_pct=0.0,
        slippage_pct=0.0,
        daily_exit_momentum_enabled=True,
    )

    result = simulate_monthly_momentum(
        feature_cache={"AAA": stock},
        benchmark=benchmark,
        start_date=stock.iloc[250]["time"],
        end_date=stock.iloc[-1]["time"],
        initial_capital=100_000.0,
        config=config,
    )

    assert "DAILY_EXIT_MOMENTUM_NON_POSITIVE" in set(result["orders"]["reason"])
    assert "DAILY_EXIT_BELOW_EMA200" not in set(result["orders"]["reason"])


def test_turnover_uses_average_equity_and_keeps_legacy_metric() -> None:
    stock = prepare_symbol_features(prices("AAA"))
    benchmark = prices("VNINDEX")[["time", "close"]]
    config = MonthlyMomentumConfig(
        top_n=1,
        minimum_history_rows=252,
        minimum_adtv20=1.0,
        maximum_volatility63_pct=100.0,
        lot_size=1,
        commission_pct=0.0,
        sell_tax_pct=0.0,
        slippage_pct=0.0,
    )

    result = simulate_monthly_momentum(
        feature_cache={"AAA": stock},
        benchmark=benchmark,
        start_date=stock.iloc[250]["time"],
        end_date=stock.iloc[-1]["time"],
        initial_capital=100_000.0,
        config=config,
    )

    assert result["average_equity"] > 100_000.0
    assert (
        result["annualized_turnover_pct"]
        < result["annualized_turnover_on_initial_pct"]
    )


def test_market_breadth_has_no_future_lookahead() -> None:
    normal = prepare_symbol_features(prices("AAA"))
    shocked_prices = prices("AAA")
    shocked_prices.loc[319, "close"] = 1.0
    shocked = prepare_symbol_features(shocked_prices)

    first = prepare_market_breadth({"AAA": normal})
    second = prepare_market_breadth({"AAA": shocked})
    comparison_date = pd.Timestamp(normal.iloc[318]["time"])
    first_value = first.loc[
        first["time"] == comparison_date,
        "breadth_above_ema50_pct",
    ].iloc[0]
    second_value = second.loc[
        second["time"] == comparison_date,
        "breadth_above_ema50_pct",
    ].iloc[0]

    assert first_value == pytest.approx(second_value)


def test_breadth_policy_uses_fixed_60_40_exposure() -> None:
    config = MonthlyMomentumConfig(
        breadth_exposure_enabled=True,
        bull_exposure_pct=80.0,
        sideway_exposure_pct=40.0,
        bear_exposure_pct=0.0,
        breadth_risk_on_pct=60.0,
        breadth_neutral_pct=40.0,
    )

    assert breadth_exposure_pct(
        market_regime="BULL", breadth_pct=65.0, config=config
    ) == 80.0
    assert breadth_exposure_pct(
        market_regime="BULL", breadth_pct=50.0, config=config
    ) == 40.0
    assert breadth_exposure_pct(
        market_regime="BULL", breadth_pct=35.0, config=config
    ) == 0.0
    assert breadth_exposure_pct(
        market_regime="BEAR", breadth_pct=80.0, config=config
    ) == 0.0


def test_breadth_recovery_requires_two_consecutive_reviews() -> None:
    first = apply_breadth_review_policy(
        active_exposure_pct=0.0,
        proposed_exposure_pct=40.0,
        recovery_confirmation_periods=2,
        recovery_allowed=True,
        pending_recovery_exposure_pct=None,
        pending_recovery_periods=0,
    )
    second = apply_breadth_review_policy(
        active_exposure_pct=0.0,
        proposed_exposure_pct=80.0,
        recovery_confirmation_periods=2,
        recovery_allowed=True,
        pending_recovery_exposure_pct=first[1],
        pending_recovery_periods=first[2],
    )

    assert first == (0.0, 40.0, 1)
    assert second == (40.0, None, 0)


def test_breadth_risk_reduction_is_immediate_despite_hysteresis() -> None:
    result = apply_breadth_review_policy(
        active_exposure_pct=80.0,
        proposed_exposure_pct=40.0,
        recovery_confirmation_periods=2,
        recovery_allowed=False,
        pending_recovery_exposure_pct=80.0,
        pending_recovery_periods=1,
    )

    assert result == (40.0, None, 0)


def test_monthly_only_recovery_blocks_weekly_risk_on() -> None:
    result = apply_breadth_review_policy(
        active_exposure_pct=0.0,
        proposed_exposure_pct=80.0,
        recovery_confirmation_periods=1,
        recovery_allowed=False,
        pending_recovery_exposure_pct=None,
        pending_recovery_periods=0,
    )

    assert result == (0.0, None, 0)


def test_two_week_recovery_is_not_bypassed_by_monthly_rebalance() -> None:
    stock = prepare_symbol_features(prices("AAA", periods=340))
    stock["ema50"] = stock["close"] * 2.0
    stock.loc[
        stock["time"] >= pd.Timestamp("2026-01-26"),
        "ema50",
    ] = stock["close"] * 0.5
    benchmark = prices("VNINDEX", periods=340)[["time", "close"]]
    config = MonthlyMomentumConfig(
        top_n=1,
        minimum_history_rows=252,
        minimum_adtv20=1.0,
        maximum_volatility63_pct=100.0,
        lot_size=1,
        commission_pct=0.0,
        sell_tax_pct=0.0,
        slippage_pct=0.0,
        regime_exposure_enabled=True,
        bull_exposure_pct=80.0,
        sideway_exposure_pct=40.0,
        bear_exposure_pct=0.0,
        breadth_exposure_enabled=True,
        breadth_review_frequency="WEEKLY",
        breadth_risk_on_pct=60.0,
        breadth_neutral_pct=40.0,
        breadth_recovery_confirmation_periods=2,
    )

    result = simulate_monthly_momentum(
        feature_cache={"AAA": stock},
        benchmark=benchmark,
        start_date=pd.Timestamp("2025-12-15"),
        end_date=pd.Timestamp("2026-02-13"),
        initial_capital=100_000.0,
        config=config,
    )

    buys = result["orders"].loc[result["orders"]["side"] == "BUY"]
    assert not buys.empty
    first = buys.iloc[0]
    assert first["reason"] == "BREADTH_EXPOSURE_REBALANCE"
    assert pd.Timestamp(first["signal_date"]) == pd.Timestamp("2026-02-06")
    assert pd.Timestamp(first["execution_date"]) == pd.Timestamp("2026-02-09")


def test_daily_breadth_risk_off_executes_next_session() -> None:
    raw = prices("AAA", periods=340)
    raw.loc[280, "close"] = 20.0
    stock = prepare_symbol_features(raw)
    benchmark = prices("VNINDEX", periods=340)[["time", "close"]]
    config = MonthlyMomentumConfig(
        top_n=1,
        minimum_history_rows=252,
        minimum_adtv20=1.0,
        maximum_volatility63_pct=100.0,
        lot_size=1,
        commission_pct=0.0,
        sell_tax_pct=0.0,
        slippage_pct=0.0,
        breadth_exposure_enabled=True,
        breadth_review_frequency="DAILY",
        bull_exposure_pct=80.0,
        sideway_exposure_pct=40.0,
        bear_exposure_pct=0.0,
        breadth_risk_on_pct=60.0,
        breadth_neutral_pct=40.0,
    )

    result = simulate_monthly_momentum(
        feature_cache={"AAA": stock},
        benchmark=benchmark,
        start_date=stock.iloc[250]["time"],
        end_date=stock.iloc[-1]["time"],
        initial_capital=100_000.0,
        config=config,
    )

    breadth_orders = result["orders"].loc[
        result["orders"]["reason"] == "BREADTH_EXPOSURE_REBALANCE"
    ]
    assert not breadth_orders.empty
    first = breadth_orders.iloc[0]
    assert pd.Timestamp(first["execution_date"]) > pd.Timestamp(first["signal_date"])
