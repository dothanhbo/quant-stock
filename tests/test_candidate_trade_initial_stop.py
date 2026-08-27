import pandas as pd

from backtesting.engine import BacktestConfig, generate_candidate_trades
from backtesting.exit_models import TrailingATRExitModel


class _AlwaysPassEntry:
    name = "always_pass"


def test_candidate_uses_initial_stop_for_risk_sizing(monkeypatch):
    prices = pd.DataFrame(
        [
            {
                "time": "2026-01-02",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "ATR14": 5.0,
                "ADX14": 25.0,
                "Stock_Return_20D": 1.0,
                "Index_Return_20D": 0.5,
                "Relative_Strength_20D": 0.5,
                "Market_Regime": "BULL",
            },
            {
                "time": "2026-01-05",
                "open": 100.0,
                "high": 120.0,
                "low": 99.0,
                "close": 118.0,
                "ATR14": 5.0,
                "ADX14": 25.0,
                "Stock_Return_20D": 1.0,
                "Index_Return_20D": 0.5,
                "Relative_Strength_20D": 0.5,
                "Market_Regime": "BULL",
            },
            {
                "time": "2026-01-06",
                "open": 118.0,
                "high": 119.0,
                "low": 111.0,
                "close": 112.0,
                "ATR14": 5.0,
                "ADX14": 25.0,
                "Stock_Return_20D": 1.0,
                "Index_Return_20D": 0.5,
                "Relative_Strength_20D": 0.5,
                "Market_Regime": "BULL",
            },
        ]
    )

    monkeypatch.setattr(
        "backtesting.engine.prepare_backtest_dataset",
        lambda *args, **kwargs: prices.assign(
            time=pd.to_datetime(prices["time"])
        ),
    )
    monkeypatch.setattr(
        "backtesting.engine.build_market_config",
        lambda regime: {"regime": regime},
    )
    def fake_evaluation(**kwargs):
        if pd.Timestamp(kwargs["latest"]["time"]) != pd.Timestamp(
            "2026-01-02"
        ):
            return {"status": "FAILED"}
        return {
            "status": "PASSED",
            "score": 90,
            "adx": 25,
            "atr": 5.0,
            "regime": "BULL",
        }

    monkeypatch.setattr(
        "backtesting.engine.evaluate_prepared_row",
        fake_evaluation,
    )

    config = BacktestConfig(
        max_holding_days=2,
    )
    model = TrailingATRExitModel(
        stop_atr_multiplier=2.0,
        target_atr_multiplier=10.0,
        trailing_atr_multiplier=1.0,
    )

    trades = generate_candidate_trades(
        symbol="TEST",
        config=config,
        warmup_bars=0,
        entry_model=_AlwaysPassEntry(),
        exit_model=model,
        start_date="2026-01-02",
        end_date="2026-01-06",
    )

    assert len(trades) == 1
    assert trades[0].entry_price == 100.0
    assert trades[0].stop_price == 90.0
    assert trades[0].exit_price == 115.0
    assert trades[0].stop_price != trades[0].exit_price


def test_candidate_scales_price_like_fields_to_vnd(monkeypatch):
    prices = pd.DataFrame(
        [
            {
                "time": "2026-01-02", "open": 10.0, "high": 10.1,
                "low": 9.9, "close": 10.0, "ATR14": 0.5,
                "ADX14": 25.0, "Stock_Return_20D": 1.0,
                "Index_Return_20D": 0.5, "Relative_Strength_20D": 0.5,
                "Market_Regime": "BULL",
            },
            {
                "time": "2026-01-05", "open": 10.0, "high": 10.2,
                "low": 9.9, "close": 10.0, "ATR14": 0.5,
                "ADX14": 25.0, "Stock_Return_20D": 1.0,
                "Index_Return_20D": 0.5, "Relative_Strength_20D": 0.5,
                "Market_Regime": "BULL",
            },
        ]
    )
    monkeypatch.setattr(
        "backtesting.engine.prepare_backtest_dataset",
        lambda *args, **kwargs: prices.assign(
            time=pd.to_datetime(prices["time"])
        ),
    )
    monkeypatch.setattr(
        "backtesting.engine.build_market_config",
        lambda regime: {"regime": regime},
    )
    monkeypatch.setattr(
        "backtesting.engine.evaluate_prepared_row",
        lambda **kwargs: {
            "status": "PASSED", "score": 90, "adx": 25,
            "atr": 0.5, "regime": "BULL",
        } if pd.Timestamp(kwargs["latest"]["time"]) == pd.Timestamp("2026-01-02")
        else {"status": "FAILED"},
    )
    trades = generate_candidate_trades(
        symbol="TEST",
        config=BacktestConfig(
            max_holding_days=1,
            market_price_scale=1000.0,
        ),
        warmup_bars=0,
        entry_model=_AlwaysPassEntry(),
        exit_model=TrailingATRExitModel(
            stop_atr_multiplier=2.0,
            target_atr_multiplier=10.0,
            trailing_atr_multiplier=1.0,
        ),
        start_date="2026-01-02",
        end_date="2026-01-05",
    )
    assert len(trades) == 1
    assert trades[0].entry_price == 10_000.0
    assert trades[0].stop_price == 9_000.0
    assert trades[0].atr == 500.0
    assert trades[0].exit_price == 10_000.0
