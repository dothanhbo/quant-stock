from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyConfig:
    """Immutable configuration for a trading strategy."""

    name: str

    # Entry
    entry_model: str

    # Quality
    quality_enabled: bool = False
    quality_threshold: float = 0.0

    # Exit
    stop_atr_multiplier: float = 2.0
    target_atr_multiplier: float = 5.0

    # Portfolio
    max_open_positions: int = 10
    max_position_size_pct: float = 0.20

V1_BASELINE = StrategyConfig(
    name="V1_BASELINE",
    entry_model="hybrid_trend_donchian",
    quality_enabled=False,
    quality_threshold=0.0,
    stop_atr_multiplier=2.0,
    target_atr_multiplier=5.0,
    max_open_positions=10,
    max_position_size_pct=0.20,
)

Q70_FROZEN = StrategyConfig(
    name="Q70_FROZEN",
    entry_model="hybrid_trend_donchian",
    quality_enabled=True,
    quality_threshold=0.70,
    stop_atr_multiplier=2.0,
    target_atr_multiplier=5.0,
    max_open_positions=10,
    max_position_size_pct=0.20,
)

V3_BREADTH_PAPER = StrategyConfig(
    name="V3_BREADTH_40_60",
    entry_model="hybrid_trend_donchian",
    quality_enabled=True,
    quality_threshold=0.70,
    stop_atr_multiplier=2.0,
    target_atr_multiplier=5.0,
    max_open_positions=10,
    max_position_size_pct=0.20,
)