from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd

from backtesting.allocation_diagnostics import (
    analyze_allocation_diagnostics,
    print_allocation_diagnostics,
)
from backtesting.engine import (
    build_exit_model,
    run_backtest,
)
from backtesting.portfolio_allocation import (
    EqualWeightAllocator,
    InverseATRAllocator,
    PortfolioAllocator,
    RiskBudgetAllocator,
    StopRiskAllocator,
    VolatilityScalingAllocator,
)
from backtesting.position_sizers import (
    FixedFractionSizer,
)
from backtesting.regime_policy import (
    RegimePortfolioPolicy,
)
from research.benchmark_portfolio_models import (
    build_portfolio_model_registry,
)
from research.universes import (
    TOP10_SYMBOLS,
)


DEFAULT_OUTPUT_DIR = Path(
    "research_results/"
    "portfolio_allocation/"
    "diagnostics"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run detailed portfolio allocator diagnostics."
        )
    )

    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
    )

    parser.add_argument(
        "--start",
        default="2018-08-04",
    )

    parser.add_argument(
        "--end",
        default="2026-07-31",
    )

    parser.add_argument(
        "--capital",
        type=float,
        default=100_000_000,
    )

    parser.add_argument(
        "--position-size",
        type=float,
        default=20.0,
    )

    parser.add_argument(
        "--hold",
        type=int,
        default=40,
    )

    parser.add_argument(
        "--min-adx",
        type=float,
        default=20.0,
    )

    parser.add_argument(
        "--atr-stop",
        type=float,
        default=2.5,
    )

    parser.add_argument(
        "--atr-target",
        type=float,
        default=5.0,
    )

    parser.add_argument(
        "--entry-model",
        default=(
            "hybrid_trend_donchian_v1"
            "__trend_context"
        ),
    )

    parser.add_argument(
        "--output",
        default=str(
            DEFAULT_OUTPUT_DIR
        ),
    )

    return parser.parse_args()


def build_allocator_registry(
) -> dict[str, PortfolioAllocator | None]:
    return {
        "fixed_fraction_baseline": None,
        "equal_weight": (
            EqualWeightAllocator()
        ),
        "inverse_atr": (
            InverseATRAllocator()
        ),
        "volatility_scaling": (
            VolatilityScalingAllocator(
                target_volatility_pct=3.0,
                scaling_power=1.5,
                maximum_position_pct=40.0,
            )
        ),
        "stop_risk": (
            StopRiskAllocator(
                maximum_position_pct=35.0,
            )
        ),
        "risk_budget": (
            RiskBudgetAllocator(
                target_risk_per_position_pct=0.80,
                maximum_position_pct=35.0,
                minimum_position_pct=2.0,
            )
        ),
    }


def run_allocator(
    *,
    allocator_name: str,
    allocator: PortfolioAllocator | None,
    symbols: list[str],
    args: argparse.Namespace,
    entry_model: Any,
):
    exit_model = build_exit_model(
        name="atr",
        stop_atr_multiplier=(
            args.atr_stop
        ),
        target_atr_multiplier=(
            args.atr_target
        ),
        break_even_trigger=5.0,
        trailing_atr_multiplier=2.0,
    )

    position_sizer = FixedFractionSizer(
        position_size_pct=(
            args.position_size
        )
    )

    trades, metrics, equity = run_backtest(
        symbols=symbols,
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
        position_size_pct=(
            args.position_size
        ),
        max_holding_days=args.hold,
        min_adx=args.min_adx,
        entry_model=entry_model,
        exit_model=exit_model,
        ranking_method=(
            "relative_strength"
        ),
        position_sizer=position_sizer,
        regime_policy=(
            RegimePortfolioPolicy()
        ),
        portfolio_allocator=allocator,
        verbose=False,
    )

    result = analyze_allocation_diagnostics(
        allocator_name=allocator_name,
        trades=trades,
        metrics=metrics,
        equity_curve=equity,
        initial_capital=args.capital,
    )

    return result


def main() -> None:
    args = parse_args()

    symbols = (
        list(TOP10_SYMBOLS)
        if args.symbols is None
        else [
            symbol.upper().strip()
            for symbol in args.symbols
            if symbol.strip()
        ]
    )

    registry = (
        build_portfolio_model_registry()
    )

    if args.entry_model not in registry:
        raise ValueError(
            "Entry model không hợp lệ: "
            f"{args.entry_model}"
        )

    entry_model = registry[
        args.entry_model
    ]

    output_dir = Path(
        args.output
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    summary_rows: list[
        dict[str, Any]
    ] = []

    all_timelines: list[
        pd.DataFrame
    ] = []

    for allocator_name, allocator in (
        build_allocator_registry().items()
    ):
        result = run_allocator(
            allocator_name=allocator_name,
            allocator=allocator,
            symbols=symbols,
            args=args,
            entry_model=entry_model,
        )

        print_allocation_diagnostics(
            result
        )

        result.save(
            output_dir=output_dir,
            prefix=allocator_name,
        )

        summary_rows.append(
            result.summary
        )

        timeline = (
            result.exposure_timeline.copy()
        )

        timeline.insert(
            0,
            "allocator",
            allocator_name,
        )

        all_timelines.append(
            timeline
        )

    comparison = pd.DataFrame(
        summary_rows
    )

    comparison = comparison.sort_values(
        by=[
            "sharpe_ratio",
            "total_return_pct",
            "max_drawdown_pct",
        ],
        ascending=[
            False,
            False,
            False,
        ],
    ).reset_index(
        drop=True
    )

    comparison.insert(
        0,
        "rank",
        range(
            1,
            len(comparison) + 1,
        ),
    )

    comparison_path = (
        output_dir
        / "diagnostics_comparison.csv"
    )

    comparison.to_csv(
        comparison_path,
        index=False,
        encoding="utf-8-sig",
    )

    combined_timeline = pd.concat(
        all_timelines,
        ignore_index=True,
    )

    timeline_path = (
        output_dir
        / "all_allocator_exposure_timeline.csv"
    )

    combined_timeline.to_csv(
        timeline_path,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print("=" * 160)
    print("ALLOCATOR DIAGNOSTICS COMPARISON")
    print("=" * 160)

    display_columns = [
        "rank",
        "allocator",
        "total_trades",
        "total_return_pct",
        "sharpe_ratio",
        "max_drawdown_pct",
        "average_trade_notional",
        "average_trade_notional_pct_initial",
        "average_exposure_pct",
        "maximum_exposure_pct",
        "average_cash_pct",
        "average_risk_pct",
        "average_portfolio_heat_pct",
        "maximum_portfolio_heat_pct",
        "total_transaction_cost",
    ]

    print(
        comparison[
            display_columns
        ].to_string(
            index=False,
            float_format=lambda value: (
                f"{value:.2f}"
            ),
        )
    )

    print()
    print(f"Đã xuất: {comparison_path}")
    print(f"Đã xuất: {timeline_path}")


if __name__ == "__main__":
    main()