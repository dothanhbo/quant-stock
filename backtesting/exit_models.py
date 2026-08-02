from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseExitModel(ABC):
    @abstractmethod
    def calculate_levels(
        self,
        entry_price: float,
        entry_row: Any,
        config: Any,
    ) -> tuple[float, float]:
        raise NotImplementedError

    def update_levels(
        self,
        *,
        entry_price: float,
        current_row: Any,
        current_stop: float,
        current_target: float,
        highest_price: float,
        config: Any,
    ) -> tuple[float, float]:
        return current_stop, current_target


class FixedExitModel(BaseExitModel):
    def calculate_levels(
        self,
        entry_price: float,
        entry_row: Any,
        config: Any,
    ) -> tuple[float, float]:
        stop_price = entry_price * (
            1 - config.stop_loss_pct / 100
        )

        target_price = entry_price * (
            1 + config.take_profit_pct / 100
        )

        return stop_price, target_price


class ATRExitModel(BaseExitModel):
    def __init__(
        self,
        stop_atr_multiplier: float = 2.0,
        target_atr_multiplier: float = 4.0,
    ) -> None:
        if stop_atr_multiplier <= 0:
            raise ValueError(
                "stop_atr_multiplier phải lớn hơn 0"
            )

        if target_atr_multiplier <= 0:
            raise ValueError(
                "target_atr_multiplier phải lớn hơn 0"
            )

        self.stop_atr_multiplier = (
            stop_atr_multiplier
        )

        self.target_atr_multiplier = (
            target_atr_multiplier
        )

    def calculate_levels(
        self,
        entry_price: float,
        entry_row: Any,
        config: Any,
    ) -> tuple[float, float]:
        atr = self._get_atr(entry_row)

        stop_price = entry_price - (
            self.stop_atr_multiplier * atr
        )

        target_price = entry_price + (
            self.target_atr_multiplier * atr
        )

        if stop_price <= 0:
            raise ValueError(
                f"stop_price không hợp lệ: "
                f"{stop_price:.2f}"
            )

        return stop_price, target_price

    @staticmethod
    def _get_atr(
        entry_row: Any,
    ) -> float:
        atr_columns = (
            "ATR14",
            "atr",
            "ATR",
        )

        for column in atr_columns:
            try:
                value = entry_row[column]
            except (
                KeyError,
                TypeError,
                IndexError,
            ):
                continue

            if value is None:
                continue

            atr = float(value)

            if atr > 0:
                return atr

        raise ValueError(
            "Không tìm thấy ATR hợp lệ trong "
            "entry_row. Cần một trong các cột: "
            "ATR14, atr hoặc ATR."
        )


DEFAULT_EXIT_MODEL = FixedExitModel()