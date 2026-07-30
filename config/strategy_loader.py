"""Đọc và kiểm tra cấu hình strategy từ YAML."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).with_name("strategy.yaml")
_REQUIRED_REGIME_KEYS = {
    "min_score", "min_adx", "min_volume_ratio", "min_relative_strength",
    "rr_ratio", "atr_stop_multiplier", "max_distance_ema20",
    "max_return_3d", "watchlist_margin",
}


def load_strategy_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"Không tìm thấy cấu hình strategy: {config_path}")

    with config_path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}

    common = config.get("common")
    regimes = config.get("regimes")
    if not isinstance(common, dict) or not isinstance(regimes, dict):
        raise ValueError("strategy.yaml phải có hai phần 'common' và 'regimes'")

    for regime in ("BULL", "SIDEWAY", "BEAR", "UNKNOWN"):
        values = regimes.get(regime)
        if not isinstance(values, dict):
            raise ValueError(f"Thiếu cấu hình regime {regime}")
        missing = _REQUIRED_REGIME_KEYS - values.keys()
        if missing:
            raise ValueError(f"Regime {regime} thiếu khóa: {sorted(missing)}")

    return deepcopy(config)


STRATEGY_CONFIG = load_strategy_config()
COMMON_CONFIG = STRATEGY_CONFIG["common"]
REGIME_CONFIGS = STRATEGY_CONFIG["regimes"]
