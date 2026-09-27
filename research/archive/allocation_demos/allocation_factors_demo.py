from __future__ import annotations

from backtesting.allocation_factors import (
    ATRFactor,
    ConstantFactor,
    RegimeFactor,
    SignalScoreFactor,
    StopDistanceFactor,
    WeightedAllocationFactor,
)
from backtesting.portfolio_allocation import (
    AllocationCandidate,
)


def main() -> None:
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
            signal_score=90,
            market_regime="SIDEWAY",
        ),
        AllocationCandidate(
            symbol="MWG",
            entry_price=65_000,
            stop_price=60_000,
            atr=2_600,
            signal_score=74,
            market_regime="BEAR",
        ),
    ]

    factors = [
        ConstantFactor(
            value=1.0,
        ),
        SignalScoreFactor(
            minimum_score=40,
            maximum_score=100,
            power=1.0,
        ),
        ATRFactor(
            target_atr_pct=3.0,
            power=1.0,
        ),
        StopDistanceFactor(
            target_stop_distance_pct=6.0,
            power=1.0,
        ),
        RegimeFactor(),
    ]

    print()
    print("=" * 110)
    print("ALLOCATION FACTOR TEST")
    print("=" * 110)

    header = (
        f"{'Symbol':<10}"
        + "".join(
            f"{factor.name:>20}"
            for factor in factors
        )
    )

    print(header)
    print("-" * 110)

    for candidate in candidates:
        scores = [
            factor.score(
                candidate
            )
            for factor in factors
        ]

        print(
            f"{candidate.symbol:<10}"
            + "".join(
                f"{score:>20.4f}"
                for score in scores
            )
        )

    weighted_factor = (
        WeightedAllocationFactor(
            factor=SignalScoreFactor(
                minimum_score=40,
                maximum_score=100,
            ),
            weight=0.35,
        )
    )

    print()
    print(
        "Weighted Signal Factor "
        f"({weighted_factor.weight:.2f})"
    )
    print("-" * 110)

    for candidate in candidates:
        print(
            f"{candidate.symbol:<10}"
            f"{weighted_factor.score(candidate):>12.4f}"
        )


if __name__ == "__main__":
    main()