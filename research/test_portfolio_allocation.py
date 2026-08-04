from __future__ import annotations

from backtesting.portfolio_allocation import (
    AllocationCandidate,
    CompositeAllocator,
    EqualWeightAllocator,
    InverseATRAllocator,
    RiskBudgetAllocator,
    StopRiskAllocator,
    calculate_portfolio_risk_pct,
    VolatilityScalingAllocator,
    CompositeAllocator
)
from backtesting.allocation_factors import (
    ATRFactor,
    RegimeFactor,
    SignalScoreFactor,
    StopDistanceFactor,
    WeightedAllocationFactor,
)

def print_results(
    name: str,
    results,
    *,
    portfolio_equity: float,
) -> None:
    print()
    print("=" * 100)
    print(name.upper())
    print("=" * 100)

    print(
        f"{'Symbol':<10}"
        f"{'Weight':>12}"
        f"{'Allocation':>18}"
        f"{'Quantity':>12}"
        f"{'Risk %':>12}"
    )

    print("-" * 100)

    for result in results:
        risk_text = (
            "-"
            if result.reference_risk_pct
            is None
            else (
                f"{result.reference_risk_pct:.2f}%"
            )
        )

        print(
            f"{result.symbol:<10}"
            f"{result.normalized_weight:>11.2%}"
            f"{result.allocation_value:>18,.0f}"
            f"{result.quantity:>12,}"
            f"{risk_text:>12}"
        )

    portfolio_risk = (
        calculate_portfolio_risk_pct(
            results,
            portfolio_equity=(
                portfolio_equity
            ),
        )
    )

    print("-" * 100)

    print(
        f"Portfolio Risk: "
        f"{portfolio_risk:.2f}%"
    )


def main() -> None:
    portfolio_equity = (
        100_000_000
    )

    candidates = [
        AllocationCandidate(
            symbol="HPG",
            entry_price=25_000,
            stop_price=23_500,
            atr=800,
            signal_score=82,
            market_regime="BULL",
        ),
        AllocationCandidate(
            symbol="FPT",
            entry_price=120_000,
            stop_price=114_000,
            atr=3_000,
            signal_score=88,
            market_regime="SIDEWAY",
        ),
        AllocationCandidate(
            symbol="MWG",
            entry_price=65_000,
            stop_price=60_000,
            atr=2_600,
            signal_score=76,
            market_regime="BEAR",
        ),
        AllocationCandidate(
            symbol="SSI",
            entry_price=32_000,
            stop_price=29_800,
            atr=1_400,
            signal_score=80,
            market_regime="BULL",
        ),
    ]

    allocators = [
        EqualWeightAllocator(),
        InverseATRAllocator(),
        VolatilityScalingAllocator(
            target_volatility_pct=3.0,
            scaling_power=1.5,
            maximum_position_pct=40.0,
        ),
        StopRiskAllocator(
            maximum_position_pct=35.0
        ),
        RiskBudgetAllocator(
            target_risk_per_position_pct=0.80,
            maximum_position_pct=35.0,
            minimum_position_pct=2.0,
        ),
        CompositeAllocator(
            factors=[
                WeightedAllocationFactor(
                    factor=SignalScoreFactor(
                        minimum_score=40,
                        maximum_score=100,
                    ),
                    weight=0.35,
                ),
                WeightedAllocationFactor(
                    factor=ATRFactor(
                        target_atr_pct=3.0,
                    ),
                    weight=0.25,
                ),
                WeightedAllocationFactor(
                    factor=StopDistanceFactor(
                        target_stop_distance_pct=6.0,
                    ),
                    weight=0.25,
                ),
                WeightedAllocationFactor(
                    factor=RegimeFactor(),
                    weight=0.15,
                ),
            ],
            maximum_position_pct=40.0,
        ),
    ]

    for allocator in allocators:
        results = allocator.allocate(
            candidates,
            portfolio_equity=(
                portfolio_equity
            ),
            investable_pct=80.0,
        )

        print_results(
            allocator.name,
            results,
            portfolio_equity=(
                portfolio_equity
            ),
        )


if __name__ == "__main__":
    main()