from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence


@dataclass(slots=True, frozen=True)
class AllocationCandidate:
    symbol: str
    entry_price: float
    stop_price: float | None = None
    atr: float | None = None
    signal_score: float | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()

        if not symbol:
            raise ValueError(
                "symbol không được rỗng."
            )

        if (
            not math.isfinite(self.entry_price)
            or self.entry_price <= 0
        ):
            raise ValueError(
                f"entry_price không hợp lệ: "
                f"{self.entry_price}"
            )

        object.__setattr__(
            self,
            "symbol",
            symbol,
        )

    @property
    def stop_distance_pct(
        self,
    ) -> float | None:
        if self.stop_price is None:
            return None

        stop_price = float(
            self.stop_price
        )

        if (
            not math.isfinite(stop_price)
            or stop_price <= 0
            or stop_price >= self.entry_price
        ):
            return None

        return (
            self.entry_price
            - stop_price
        ) / self.entry_price * 100

    @property
    def atr_pct(
        self,
    ) -> float | None:
        if self.atr is None:
            return None

        atr = float(
            self.atr
        )

        if (
            not math.isfinite(atr)
            or atr <= 0
        ):
            return None

        return (
            atr / self.entry_price
        ) * 100


@dataclass(slots=True, frozen=True)
class AllocationResult:
    symbol: str
    raw_weight: float
    normalized_weight: float
    allocation_value: float
    quantity: int
    reference_risk_pct: float | None
    reason: str


class PortfolioAllocator(Protocol):
    name: str

    def allocate(
        self,
        candidates: Sequence[
            AllocationCandidate
        ],
        *,
        portfolio_equity: float,
        investable_pct: float = 100.0,
    ) -> list[AllocationResult]:
        ...


def _validate_common_inputs(
    candidates: Sequence[
        AllocationCandidate
    ],
    *,
    portfolio_equity: float,
    investable_pct: float,
) -> None:
    if not candidates:
        raise ValueError(
            "candidates không được rỗng."
        )

    if (
        not math.isfinite(portfolio_equity)
        or portfolio_equity <= 0
    ):
        raise ValueError(
            "portfolio_equity phải lớn hơn 0."
        )

    if not 0 < investable_pct <= 100:
        raise ValueError(
            "investable_pct phải nằm trong "
            "khoảng (0, 100]."
        )

    symbols = [
        candidate.symbol
        for candidate in candidates
    ]

    if len(symbols) != len(set(symbols)):
        raise ValueError(
            "Danh sách candidate bị trùng symbol."
        )


def _normalize_weights(
    raw_weights: Mapping[str, float],
) -> dict[str, float]:
    valid_weights = {
        symbol: float(weight)
        for symbol, weight
        in raw_weights.items()
        if (
            math.isfinite(float(weight))
            and float(weight) > 0
        )
    }

    total_weight = sum(
        valid_weights.values()
    )

    if total_weight <= 0:
        raise ValueError(
            "Không có raw weight hợp lệ."
        )

    return {
        symbol: weight / total_weight
        for symbol, weight
        in valid_weights.items()
    }

def _cap_and_redistribute_weights(
    weights: Mapping[str, float],
    *,
    maximum_weight: float,
) -> dict[str, float]:
    if not 0 < maximum_weight <= 1:
        raise ValueError(
            "maximum_weight phải nằm trong khoảng (0, 1]."
        )

    normalized = _normalize_weights(
        weights
    )

    if (
        maximum_weight
        * len(normalized)
        < 1 - 1e-12
    ):
        raise ValueError(
            "maximum_position_pct quá thấp "
            "so với số lượng candidate."
        )

    remaining_symbols = set(
        normalized
    )

    final_weights: dict[str, float] = {
        symbol: 0.0
        for symbol in normalized
    }

    remaining_weight = 1.0

    while remaining_symbols:
        remaining_raw_total = sum(
            normalized[symbol]
            for symbol in remaining_symbols
        )

        if remaining_raw_total <= 0:
            equal_weight = (
                remaining_weight
                / len(remaining_symbols)
            )

            for symbol in remaining_symbols:
                final_weights[
                    symbol
                ] = equal_weight

            break

        capped_symbols: list[str] = []

        for symbol in remaining_symbols:
            proposed_weight = (
                remaining_weight
                * normalized[symbol]
                / remaining_raw_total
            )

            if (
                proposed_weight
                > maximum_weight
                + 1e-12
            ):
                final_weights[
                    symbol
                ] = maximum_weight

                remaining_weight -= (
                    maximum_weight
                )

                capped_symbols.append(
                    symbol
                )

        if not capped_symbols:
            for symbol in remaining_symbols:
                final_weights[
                    symbol
                ] = (
                    remaining_weight
                    * normalized[symbol]
                    / remaining_raw_total
                )

            break

        for symbol in capped_symbols:
            remaining_symbols.remove(
                symbol
            )

    return final_weights

def _build_results(
    candidates: Sequence[
        AllocationCandidate
    ],
    *,
    raw_weights: Mapping[str, float],
    portfolio_equity: float,
    investable_pct: float,
    reference_risks: Mapping[
        str,
        float | None,
    ],
    reason: str,
) -> list[AllocationResult]:
    normalized = _normalize_weights(
        raw_weights
    )

    investable_capital = (
        portfolio_equity
        * investable_pct
        / 100
    )

    results: list[
        AllocationResult
    ] = []

    for candidate in candidates:
        weight = normalized.get(
            candidate.symbol
        )

        if weight is None:
            continue

        allocation_value = (
            investable_capital
            * weight
        )

        quantity = int(
            allocation_value
            // candidate.entry_price
        )

        if quantity < 1:
            continue

        actual_allocation = (
            quantity
            * candidate.entry_price
        )

        results.append(
            AllocationResult(
                symbol=candidate.symbol,
                raw_weight=float(
                    raw_weights[
                        candidate.symbol
                    ]
                ),
                normalized_weight=weight,
                allocation_value=(
                    actual_allocation
                ),
                quantity=quantity,
                reference_risk_pct=(
                    reference_risks.get(
                        candidate.symbol
                    )
                ),
                reason=reason,
            )
        )

    return results


@dataclass(slots=True, frozen=True)
class EqualWeightAllocator:
    name: str = "equal_weight"

    def allocate(
        self,
        candidates: Sequence[
            AllocationCandidate
        ],
        *,
        portfolio_equity: float,
        investable_pct: float = 100.0,
    ) -> list[AllocationResult]:
        _validate_common_inputs(
            candidates,
            portfolio_equity=(
                portfolio_equity
            ),
            investable_pct=(
                investable_pct
            ),
        )

        raw_weights = {
            candidate.symbol: 1.0
            for candidate in candidates
        }

        reference_risks = {
            candidate.symbol: (
                candidate.stop_distance_pct
            )
            for candidate in candidates
        }

        return _build_results(
            candidates,
            raw_weights=raw_weights,
            portfolio_equity=(
                portfolio_equity
            ),
            investable_pct=(
                investable_pct
            ),
            reference_risks=(
                reference_risks
            ),
            reason=(
                "Equal capital allocation"
            ),
        )


@dataclass(slots=True, frozen=True)
class InverseATRAllocator:
    """
    Cổ phiếu có ATR% thấp hơn sẽ nhận tỷ trọng cao hơn.
    """

    minimum_atr_pct: float = 0.10
    name: str = "inverse_atr"

    def allocate(
        self,
        candidates: Sequence[
            AllocationCandidate
        ],
        *,
        portfolio_equity: float,
        investable_pct: float = 100.0,
    ) -> list[AllocationResult]:
        _validate_common_inputs(
            candidates,
            portfolio_equity=(
                portfolio_equity
            ),
            investable_pct=(
                investable_pct
            ),
        )

        if self.minimum_atr_pct <= 0:
            raise ValueError(
                "minimum_atr_pct phải lớn hơn 0."
            )

        raw_weights: dict[
            str,
            float,
        ] = {}

        reference_risks: dict[
            str,
            float | None,
        ] = {}

        for candidate in candidates:
            atr_pct = candidate.atr_pct

            if atr_pct is None:
                continue

            effective_atr_pct = max(
                atr_pct,
                self.minimum_atr_pct,
            )

            raw_weights[
                candidate.symbol
            ] = (
                1 / effective_atr_pct
            )

            reference_risks[
                candidate.symbol
            ] = atr_pct

        if not raw_weights:
            raise ValueError(
                "Không có candidate nào có ATR hợp lệ."
            )

        return _build_results(
            candidates,
            raw_weights=raw_weights,
            portfolio_equity=(
                portfolio_equity
            ),
            investable_pct=(
                investable_pct
            ),
            reference_risks=(
                reference_risks
            ),
            reason=(
                "Inverse ATR volatility allocation"
            ),
        )

@dataclass(slots=True, frozen=True)
class VolatilityScalingAllocator:
    """
    Phân bổ vốn dựa trên ATR% như một đại diện
    đơn giản cho volatility.

    Raw weight:

        (target_volatility_pct / ATR%) ** scaling_power

    scaling_power:
    - 1.0: gần giống inverse ATR.
    - > 1.0: ưu tiên rõ hơn cho mã volatility thấp.
    - < 1.0: phân bổ mềm hơn.
    """

    target_volatility_pct: float = 3.0
    scaling_power: float = 1.5
    minimum_atr_pct: float = 0.50
    maximum_position_pct: float = 40.0
    name: str = "volatility_scaling"

    def allocate(
        self,
        candidates: Sequence[
            AllocationCandidate
        ],
        *,
        portfolio_equity: float,
        investable_pct: float = 100.0,
    ) -> list[AllocationResult]:
        _validate_common_inputs(
            candidates,
            portfolio_equity=(
                portfolio_equity
            ),
            investable_pct=(
                investable_pct
            ),
        )

        if self.target_volatility_pct <= 0:
            raise ValueError(
                "target_volatility_pct "
                "phải lớn hơn 0."
            )

        if self.scaling_power <= 0:
            raise ValueError(
                "scaling_power phải lớn hơn 0."
            )

        if self.minimum_atr_pct <= 0:
            raise ValueError(
                "minimum_atr_pct phải lớn hơn 0."
            )

        if not 0 < self.maximum_position_pct <= 100:
            raise ValueError(
                "maximum_position_pct phải nằm "
                "trong khoảng (0, 100]."
            )

        raw_weights: dict[
            str,
            float,
        ] = {}

        reference_risks: dict[
            str,
            float | None,
        ] = {}

        for candidate in candidates:
            atr_pct = candidate.atr_pct

            if atr_pct is None:
                continue

            effective_atr_pct = max(
                atr_pct,
                self.minimum_atr_pct,
            )

            volatility_ratio = (
                self.target_volatility_pct
                / effective_atr_pct
            )

            raw_weights[
                candidate.symbol
            ] = (
                volatility_ratio
                ** self.scaling_power
            )

            reference_risks[
                candidate.symbol
            ] = atr_pct

        if not raw_weights:
            raise ValueError(
                "Không có candidate nào có "
                "ATR hợp lệ để volatility scaling."
            )

        maximum_weight = (
            self.maximum_position_pct
            / 100
        )

        capped_weights = (
            _cap_and_redistribute_weights(
                raw_weights,
                maximum_weight=(
                    maximum_weight
                ),
            )
        )

        return _build_results(
            candidates,
            raw_weights=capped_weights,
            portfolio_equity=(
                portfolio_equity
            ),
            investable_pct=(
                investable_pct
            ),
            reference_risks=(
                reference_risks
            ),
            reason=(
                "ATR volatility scaling allocation"
            ),
        )

@dataclass(slots=True, frozen=True)
class StopRiskAllocator:
    """
    Cân bằng rủi ro dựa trên khoảng cách từ entry tới stop.

    Stop càng xa -> tỷ trọng càng nhỏ.
    Stop càng gần -> tỷ trọng càng lớn.
    """

    minimum_stop_distance_pct: float = 0.50
    maximum_position_pct: float = 35.0
    name: str = "stop_risk"

    def allocate(
        self,
        candidates: Sequence[
            AllocationCandidate
        ],
        *,
        portfolio_equity: float,
        investable_pct: float = 100.0,
    ) -> list[AllocationResult]:
        _validate_common_inputs(
            candidates,
            portfolio_equity=(
                portfolio_equity
            ),
            investable_pct=(
                investable_pct
            ),
        )

        if self.minimum_stop_distance_pct <= 0:
            raise ValueError(
                "minimum_stop_distance_pct "
                "phải lớn hơn 0."
            )

        if not 0 < self.maximum_position_pct <= 100:
            raise ValueError(
                "maximum_position_pct phải nằm "
                "trong khoảng (0, 100]."
            )

        raw_weights: dict[
            str,
            float,
        ] = {}

        reference_risks: dict[
            str,
            float | None,
        ] = {}

        for candidate in candidates:
            stop_distance = (
                candidate.stop_distance_pct
            )

            if stop_distance is None:
                continue

            effective_distance = max(
                stop_distance,
                self.minimum_stop_distance_pct,
            )

            raw_weights[
                candidate.symbol
            ] = (
                1 / effective_distance
            )

            reference_risks[
                candidate.symbol
            ] = stop_distance

        if not raw_weights:
            raise ValueError(
                "Không có candidate nào có "
                "stop_price hợp lệ."
            )

        maximum_weight = (
            self.maximum_position_pct
            / 100
        )

        capped_weights = (
            _cap_and_redistribute_weights(
                raw_weights,
                maximum_weight=(
                    maximum_weight
                ),
            )
        )

        return _build_results(
            candidates,
            raw_weights=capped_weights,
            portfolio_equity=(
                portfolio_equity
            ),
            investable_pct=(
                investable_pct
            ),
            reference_risks=(
                reference_risks
            ),
            reason=(
                "Inverse stop-distance risk allocation"
            ),
        )

@dataclass(slots=True, frozen=True)
class RiskBudgetAllocator:
    """
    Phân bổ vốn dựa trên ngân sách rủi ro tới stop.

    Mỗi vị thế được nhắm tới một mức risk contribution
    bằng target_risk_per_position_pct của portfolio equity.

    Allocation value x stop distance %
        ≈ target risk value
    """

    target_risk_per_position_pct: float = 0.80
    maximum_position_pct: float = 35.0
    minimum_position_pct: float = 2.0
    minimum_stop_distance_pct: float = 0.50
    name: str = "risk_budget"

    def allocate(
        self,
        candidates: Sequence[
            AllocationCandidate
        ],
        *,
        portfolio_equity: float,
        investable_pct: float = 100.0,
    ) -> list[AllocationResult]:
        _validate_common_inputs(
            candidates,
            portfolio_equity=portfolio_equity,
            investable_pct=investable_pct,
        )

        if self.target_risk_per_position_pct <= 0:
            raise ValueError(
                "target_risk_per_position_pct "
                "phải lớn hơn 0."
            )

        if not 0 < self.maximum_position_pct <= 100:
            raise ValueError(
                "maximum_position_pct phải nằm "
                "trong khoảng (0, 100]."
            )

        if not 0 <= self.minimum_position_pct <= 100:
            raise ValueError(
                "minimum_position_pct phải nằm "
                "trong khoảng [0, 100]."
            )

        if (
            self.minimum_position_pct
            > self.maximum_position_pct
        ):
            raise ValueError(
                "minimum_position_pct không được "
                "lớn hơn maximum_position_pct."
            )

        if self.minimum_stop_distance_pct <= 0:
            raise ValueError(
                "minimum_stop_distance_pct "
                "phải lớn hơn 0."
            )

        investable_capital = (
            portfolio_equity
            * investable_pct
            / 100
        )

        target_risk_value = (
            portfolio_equity
            * self.target_risk_per_position_pct
            / 100
        )

        minimum_position_value = (
            portfolio_equity
            * self.minimum_position_pct
            / 100
        )

        maximum_position_value = (
            portfolio_equity
            * self.maximum_position_pct
            / 100
        )

        desired_values: dict[
            str,
            float,
        ] = {}

        reference_risks: dict[
            str,
            float | None,
        ] = {}

        candidate_by_symbol = {
            candidate.symbol: candidate
            for candidate in candidates
        }

        for candidate in candidates:
            stop_distance_pct = (
                candidate.stop_distance_pct
            )

            if stop_distance_pct is None:
                continue

            effective_stop_distance_pct = max(
                stop_distance_pct,
                self.minimum_stop_distance_pct,
            )

            desired_value = (
                target_risk_value
                / (
                    effective_stop_distance_pct
                    / 100
                )
            )

            desired_value = max(
                desired_value,
                minimum_position_value,
            )

            desired_value = min(
                desired_value,
                maximum_position_value,
            )

            desired_values[
                candidate.symbol
            ] = desired_value

            reference_risks[
                candidate.symbol
            ] = stop_distance_pct

        if not desired_values:
            raise ValueError(
                "Không có candidate nào có "
                "stop_price hợp lệ."
            )

        total_desired_value = sum(
            desired_values.values()
        )

        scale_factor = min(
            1.0,
            investable_capital
            / total_desired_value,
        )

        results: list[
            AllocationResult
        ] = []

        for symbol, desired_value in (
            desired_values.items()
        ):
            candidate = candidate_by_symbol[
                symbol
            ]

            scaled_value = (
                desired_value
                * scale_factor
            )

            quantity = int(
                scaled_value
                // candidate.entry_price
            )

            if quantity < 1:
                continue

            actual_allocation_value = (
                quantity
                * candidate.entry_price
            )

            normalized_weight = (
                actual_allocation_value
                / investable_capital
                if investable_capital > 0
                else 0.0
            )

            stop_distance_pct = (
                reference_risks[symbol]
            )

            estimated_risk_value = (
                actual_allocation_value
                * stop_distance_pct
                / 100
                if stop_distance_pct is not None
                else 0.0
            )

            estimated_risk_pct = (
                estimated_risk_value
                / portfolio_equity
                * 100
            )

            results.append(
                AllocationResult(
                    symbol=symbol,
                    raw_weight=desired_value,
                    normalized_weight=(
                        normalized_weight
                    ),
                    allocation_value=(
                        actual_allocation_value
                    ),
                    quantity=quantity,
                    reference_risk_pct=(
                        stop_distance_pct
                    ),
                    reason=(
                        "Risk budget allocation; "
                        f"estimated portfolio risk "
                        f"{estimated_risk_pct:.2f}%"
                    ),
                )
            )

        return results

def calculate_portfolio_risk_pct(
    allocations: Sequence[
        AllocationResult
    ],
    *,
    portfolio_equity: float,
) -> float:
    if portfolio_equity <= 0:
        raise ValueError(
            "portfolio_equity phải lớn hơn 0."
        )

    total_risk_value = 0.0

    for allocation in allocations:
        if (
            allocation.reference_risk_pct
            is None
        ):
            continue

        total_risk_value += (
            allocation.allocation_value
            * allocation.reference_risk_pct
            / 100
        )

    return (
        total_risk_value
        / portfolio_equity
        * 100
    )