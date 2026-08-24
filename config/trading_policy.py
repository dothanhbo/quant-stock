"""Single source of truth for production and research trading rules."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from backtesting.position_sizers import AtrRiskSizer, FixedFractionSizer
from strategy.base_strategy import BaseStrategy
from strategy.hybrid_trend_donchian_entry import HybridTrendDonchianEntryModel
from strategy.trend_strategy_v1 import TrendStrategyV1


EntryModelName = Literal["trend", "hybrid"]
ExitModelName = Literal["atr"]
ExecutionTiming = Literal["next_open"]


def _float(name: str, default: float) -> float:
    value = os.getenv(name)
    return default if value is None else float(value)


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None else int(value)


@dataclass(frozen=True, slots=True)
class TradingPolicy:
    """Frozen rules used by scanner, paper execution and validation."""

    entry_model: EntryModelName = "hybrid"
    exit_model: ExitModelName = "atr"
    execution_timing: ExecutionTiming = "next_open"
    stop_atr_multiplier: float = 2.0
    target_atr_multiplier: float = 5.0
    maximum_holding_days: int = 30
    position_sizer: str = "atr_risk"
    risk_per_trade_pct: float = 1.0
    fixed_fraction_pct: float = 20.0
    sell_tax_rate: float = 0.001

    @classmethod
    def from_env(cls) -> "TradingPolicy":
        policy = cls(
            entry_model=os.getenv("TRADING_ENTRY_MODEL", "hybrid").strip().lower(),
            exit_model=os.getenv("TRADING_EXIT_MODEL", "atr").strip().lower(),
            execution_timing=os.getenv(
                "TRADING_EXECUTION_TIMING", "next_open"
            ).strip().lower(),
            stop_atr_multiplier=_float("TRADING_STOP_ATR_MULTIPLIER", 2.0),
            target_atr_multiplier=_float("TRADING_TARGET_ATR_MULTIPLIER", 5.0),
            maximum_holding_days=_int("TRADING_MAX_HOLDING_DAYS", 30),
            position_sizer=os.getenv(
                "PAPER_POSITION_SIZER", "atr_risk"
            ).strip().lower(),
            risk_per_trade_pct=_float("PAPER_RISK_PER_TRADE_PCT", 1.0),
            fixed_fraction_pct=_float("PAPER_FIXED_FRACTION_PCT", 20.0),
            sell_tax_rate=_float("PAPER_SELL_TAX_RATE", 0.001),
        )
        policy.validate()
        return policy

    def validate(self) -> None:
        if self.entry_model not in {"trend", "hybrid"}:
            raise ValueError("TRADING_ENTRY_MODEL chỉ nhận trend hoặc hybrid.")
        if self.exit_model != "atr":
            raise ValueError("TRADING_EXIT_MODEL hiện chỉ hỗ trợ atr.")
        if self.execution_timing != "next_open":
            raise ValueError("Production chỉ hỗ trợ execution next_open.")
        if self.stop_atr_multiplier <= 0 or self.target_atr_multiplier <= 0:
            raise ValueError("ATR multipliers phải lớn hơn 0.")
        if self.maximum_holding_days <= 0:
            raise ValueError("TRADING_MAX_HOLDING_DAYS phải lớn hơn 0.")
        if self.position_sizer not in {"atr_risk", "fixed_fraction"}:
            raise ValueError("Position sizer không hợp lệ.")
        if not 0 < self.risk_per_trade_pct <= 100:
            raise ValueError("Risk per trade phải nằm trong (0, 100].")
        if not 0 < self.fixed_fraction_pct <= 100:
            raise ValueError("Fixed fraction phải nằm trong (0, 100].")
        if not 0 <= self.sell_tax_rate < 1:
            raise ValueError("Sell tax rate phải nằm trong [0, 1).")

    def build_entry_model(self) -> BaseStrategy:
        if self.entry_model == "hybrid":
            return HybridTrendDonchianEntryModel(mode="trend_context")
        return TrendStrategyV1()

    def calculate_levels(self, *, entry_price: float, atr: float) -> tuple[float, float]:
        if entry_price <= 0 or atr <= 0:
            raise ValueError("entry_price và atr phải lớn hơn 0.")
        stop = entry_price - self.stop_atr_multiplier * atr
        target = entry_price + self.target_atr_multiplier * atr
        if stop <= 0:
            raise ValueError("ATR stop không hợp lệ.")
        return stop, target

    def build_position_sizer(self, *, maximum_position_pct: float):
        if self.position_sizer == "fixed_fraction":
            return FixedFractionSizer(position_size_pct=self.fixed_fraction_pct)
        return AtrRiskSizer(
            risk_per_trade_pct=self.risk_per_trade_pct,
            atr_stop_multiplier=self.stop_atr_multiplier,
            max_position_size_pct=maximum_position_pct,
            use_candidate_stop=True,
        )
