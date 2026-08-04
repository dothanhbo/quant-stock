from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from backtesting.portfolio_allocation import (
    AllocationCandidate,
)


class AllocationFactor(Protocol):
    """
    Một AllocationFactor nhận một candidate
    và trả về score lớn hơn hoặc bằng 0.

    Score càng cao:
    candidate càng được ưu tiên phân bổ vốn.
    """

    name: str

    def score(
        self,
        candidate: AllocationCandidate,
    ) -> float:
        ...


def _clamp(
    value: float,
    *,
    minimum: float = 0.0,
    maximum: float = 1.0,
) -> float:
    return max(
        minimum,
        min(
            maximum,
            value,
        ),
    )


@dataclass(slots=True, frozen=True)
class ConstantFactor:
    """
    Trả cùng một score cho mọi candidate.

    Dùng để:
    - test CompositeAllocator;
    - làm neutral factor;
    - tạo fallback.
    """

    value: float = 1.0
    name: str = "constant"

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.value)
            or self.value < 0
        ):
            raise ValueError(
                "ConstantFactor.value phải "
                "là số hữu hạn và không âm."
            )

    def score(
        self,
        candidate: AllocationCandidate,
    ) -> float:
        del candidate

        return float(
            self.value
        )


@dataclass(slots=True, frozen=True)
class SignalScoreFactor:
    """
    Chuẩn hóa signal_score về khoảng 0–1.

    Ví dụ:
    signal_score = 80
    minimum_score = 40
    maximum_score = 100

    score chuẩn hóa:
    (80 - 40) / (100 - 40) = 0.667
    """

    minimum_score: float = 0.0
    maximum_score: float = 100.0
    missing_score: float = 0.0
    power: float = 1.0
    name: str = "signal_score"

    def __post_init__(self) -> None:
        if (
            not math.isfinite(
                self.minimum_score
            )
            or not math.isfinite(
                self.maximum_score
            )
        ):
            raise ValueError(
                "Signal score bounds phải hữu hạn."
            )

        if (
            self.maximum_score
            <= self.minimum_score
        ):
            raise ValueError(
                "maximum_score phải lớn hơn "
                "minimum_score."
            )

        if not 0 <= self.missing_score <= 1:
            raise ValueError(
                "missing_score phải nằm trong [0, 1]."
            )

        if self.power <= 0:
            raise ValueError(
                "power phải lớn hơn 0."
            )

    def score(
        self,
        candidate: AllocationCandidate,
    ) -> float:
        raw_score = candidate.signal_score

        if raw_score is None:
            return float(
                self.missing_score
            )

        numeric_score = float(
            raw_score
        )

        if not math.isfinite(
            numeric_score
        ):
            return float(
                self.missing_score
            )

        normalized = (
            numeric_score
            - self.minimum_score
        ) / (
            self.maximum_score
            - self.minimum_score
        )

        normalized = _clamp(
            normalized
        )

        return normalized ** self.power


@dataclass(slots=True, frozen=True)
class ATRFactor:
    """
    Candidate có ATR% thấp hơn sẽ nhận score cao hơn.

    Công thức:

        score = (
            target_atr_pct
            / effective_atr_pct
        ) ** power

    Sau đó giới hạn score trong khoảng:
    [minimum_score, maximum_score].
    """

    target_atr_pct: float = 3.0
    minimum_atr_pct: float = 0.50
    power: float = 1.0
    minimum_score: float = 0.10
    maximum_score: float = 2.00
    missing_score: float = 0.0
    name: str = "atr"

    def __post_init__(self) -> None:
        if self.target_atr_pct <= 0:
            raise ValueError(
                "target_atr_pct phải lớn hơn 0."
            )

        if self.minimum_atr_pct <= 0:
            raise ValueError(
                "minimum_atr_pct phải lớn hơn 0."
            )

        if self.power <= 0:
            raise ValueError(
                "power phải lớn hơn 0."
            )

        if self.minimum_score < 0:
            raise ValueError(
                "minimum_score không được âm."
            )

        if (
            self.maximum_score
            < self.minimum_score
        ):
            raise ValueError(
                "maximum_score không được nhỏ hơn "
                "minimum_score."
            )

        if self.missing_score < 0:
            raise ValueError(
                "missing_score không được âm."
            )

    def score(
        self,
        candidate: AllocationCandidate,
    ) -> float:
        atr_pct = candidate.atr_pct

        if atr_pct is None:
            return float(
                self.missing_score
            )

        effective_atr_pct = max(
            float(atr_pct),
            self.minimum_atr_pct,
        )

        raw_score = (
            self.target_atr_pct
            / effective_atr_pct
        ) ** self.power

        return _clamp(
            raw_score,
            minimum=(
                self.minimum_score
            ),
            maximum=(
                self.maximum_score
            ),
        )


@dataclass(slots=True, frozen=True)
class StopDistanceFactor:
    """
    Candidate có stop gần hơn sẽ nhận score cao hơn.

    Dùng inverse stop distance:

        score = (
            target_stop_distance_pct
            / effective_stop_distance_pct
        ) ** power
    """

    target_stop_distance_pct: float = 6.0
    minimum_stop_distance_pct: float = 0.50
    power: float = 1.0
    minimum_score: float = 0.10
    maximum_score: float = 2.00
    missing_score: float = 0.0
    name: str = "stop_distance"

    def __post_init__(self) -> None:
        if self.target_stop_distance_pct <= 0:
            raise ValueError(
                "target_stop_distance_pct "
                "phải lớn hơn 0."
            )

        if self.minimum_stop_distance_pct <= 0:
            raise ValueError(
                "minimum_stop_distance_pct "
                "phải lớn hơn 0."
            )

        if self.power <= 0:
            raise ValueError(
                "power phải lớn hơn 0."
            )

        if self.minimum_score < 0:
            raise ValueError(
                "minimum_score không được âm."
            )

        if (
            self.maximum_score
            < self.minimum_score
        ):
            raise ValueError(
                "maximum_score không được nhỏ hơn "
                "minimum_score."
            )

        if self.missing_score < 0:
            raise ValueError(
                "missing_score không được âm."
            )

    def score(
        self,
        candidate: AllocationCandidate,
    ) -> float:
        stop_distance_pct = (
            candidate.stop_distance_pct
        )

        if stop_distance_pct is None:
            return float(
                self.missing_score
            )

        effective_stop_distance = max(
            float(stop_distance_pct),
            self.minimum_stop_distance_pct,
        )

        raw_score = (
            self.target_stop_distance_pct
            / effective_stop_distance
        ) ** self.power

        return _clamp(
            raw_score,
            minimum=(
                self.minimum_score
            ),
            maximum=(
                self.maximum_score
            ),
        )


@dataclass(slots=True, frozen=True)
class RegimeFactor:
    """
    Điều chỉnh score theo market regime.

    AllocationCandidate hiện chưa bắt buộc có
    market_regime field, nên factor dùng getattr()
    để giữ backward compatibility.
    """

    bull_score: float = 1.00
    sideway_score: float = 0.70
    bear_score: float = 0.00
    unknown_score: float = 0.25
    name: str = "regime"

    def __post_init__(self) -> None:
        values = (
            self.bull_score,
            self.sideway_score,
            self.bear_score,
            self.unknown_score,
        )

        if any(
            (
                not math.isfinite(value)
                or value < 0
            )
            for value in values
        ):
            raise ValueError(
                "Regime scores phải hữu hạn "
                "và không âm."
            )

    def score(
        self,
        candidate: AllocationCandidate,
    ) -> float:
        raw_regime = getattr(
            candidate,
            "market_regime",
            None,
        )

        if raw_regime is None:
            return float(
                self.unknown_score
            )

        normalized = (
            str(raw_regime)
            .strip()
            .upper()
        )

        if normalized == "BULL":
            return float(
                self.bull_score
            )

        if normalized == "SIDEWAY":
            return float(
                self.sideway_score
            )

        if normalized == "BEAR":
            return float(
                self.bear_score
            )

        return float(
            self.unknown_score
        )


@dataclass(slots=True, frozen=True)
class WeightedAllocationFactor:
    """
    Ghép một factor với trọng số của nó.

    weighted_score =
        factor.score(candidate) × weight
    """

    factor: AllocationFactor
    weight: float

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.weight)
            or self.weight < 0
        ):
            raise ValueError(
                "Factor weight phải hữu hạn "
                "và không âm."
            )

    @property
    def name(self) -> str:
        return self.factor.name

    def score(
        self,
        candidate: AllocationCandidate,
    ) -> float:
        factor_score = float(
            self.factor.score(
                candidate
            )
        )

        if (
            not math.isfinite(
                factor_score
            )
            or factor_score < 0
        ):
            return 0.0

        return (
            factor_score
            * self.weight
        )