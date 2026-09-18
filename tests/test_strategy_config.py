from config.strategy_config import StrategyConfig, V1_BASELINE
from config.strategy_config import Q70_FROZEN, V3_BREADTH_PAPER
import pytest
import os
from config import trading_policy


@pytest.fixture(autouse=True)
def restore_trading_environment(monkeypatch):
    original_stop = os.environ.get("TRADING_STOP_ATR_MULTIPLIER")
    original_target = os.environ.get("TRADING_TARGET_ATR_MULTIPLIER")

    yield

    if original_stop is None:
        os.environ.pop("TRADING_STOP_ATR_MULTIPLIER", None)
    else:
        os.environ["TRADING_STOP_ATR_MULTIPLIER"] = original_stop

    if original_target is None:
        os.environ.pop("TRADING_TARGET_ATR_MULTIPLIER", None)
    else:
        os.environ["TRADING_TARGET_ATR_MULTIPLIER"] = original_target

def test_v1_baseline_config():
    assert V1_BASELINE.name == "V1_BASELINE"
    assert V1_BASELINE.entry_model == "hybrid_trend_donchian"
    assert V1_BASELINE.quality_enabled is False
    assert V1_BASELINE.stop_atr_multiplier == 2.0
    assert V1_BASELINE.target_atr_multiplier == 5.0


def test_strategy_config_is_immutable():
    config = StrategyConfig(
        name="TEST",
        entry_model="test",
    )

    try:
        config.name = "CHANGED"
        assert False, "StrategyConfig should be immutable"
    except AttributeError:
        pass

def test_apply_strategy_config(monkeypatch):
    config = StrategyConfig(
        name="TEST",
        entry_model="test",
        stop_atr_multiplier=4.5,
        target_atr_multiplier=9.0,
    )

    trading_policy.apply_strategy_config(config)

    assert os.environ["TRADING_STOP_ATR_MULTIPLIER"] == "4.5"
    assert os.environ["TRADING_TARGET_ATR_MULTIPLIER"] == "9.0"


def test_q70_frozen_config():
    assert Q70_FROZEN.name == "Q70_FROZEN"
    assert Q70_FROZEN.entry_model == "hybrid_trend_donchian"

    assert Q70_FROZEN.quality_enabled is True
    assert Q70_FROZEN.quality_threshold == 0.70

    assert Q70_FROZEN.stop_atr_multiplier == 2.0
    assert Q70_FROZEN.target_atr_multiplier == 5.0

def test_v3_breadth_paper_config():
    assert V3_BREADTH_PAPER.name == "V3_BREADTH_40_60"
    assert V3_BREADTH_PAPER.quality_enabled is True
    assert V3_BREADTH_PAPER.quality_threshold == 0.70
    assert V3_BREADTH_PAPER.stop_atr_multiplier == 2.0
    assert V3_BREADTH_PAPER.target_atr_multiplier == 5.0
