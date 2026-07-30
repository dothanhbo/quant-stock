from config.strategy_loader import REGIME_CONFIGS, load_strategy_config


def test_strategy_yaml_has_all_regimes():
    config = load_strategy_config()
    assert set(config["regimes"]) == {"BULL", "SIDEWAY", "BEAR", "UNKNOWN"}


def test_bear_is_stricter_than_bull():
    assert REGIME_CONFIGS["BEAR"]["min_score"] > REGIME_CONFIGS["BULL"]["min_score"]
    assert REGIME_CONFIGS["BEAR"]["min_adx"] > REGIME_CONFIGS["BULL"]["min_adx"]
    assert REGIME_CONFIGS["BEAR"]["min_volume_ratio"] > REGIME_CONFIGS["BULL"]["min_volume_ratio"]
