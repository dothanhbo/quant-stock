from __future__ import annotations

import argparse
from pathlib import Path

from backtesting.engine import (
    build_exit_model,
    run_backtest,
)
from backtesting.position_sizers import (
    FixedFractionSizer,
)
from backtesting.regime_policy import (
    RegimePortfolioPolicy,
)
from backtesting.walk_forward import (
    WalkForwardConfig,
)
from backtesting.walk_forward_optimizer import (
    OptimizationConfig,
    WalkForwardParameterGrid,
    print_walk_forward_optimization_report,
    run_walk_forward_optimization,
)
from research.benchmark_portfolio_models import (
    build_portfolio_model_registry,
)
from research.universes import (
    TOP10_SYMBOLS,
)


DEFAULT_OUTPUT_DIR = Path(
    "research_results/walk_forward_optimizer"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run walk-forward parameter optimization."
        )
    )

    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
    )

    parser.add_argument(
        "--entry-model",
        default=(
            "hybrid_trend_donchian_v1"
            "__trend_context"
        ),
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
        "--train-months",
        type=int,
        default=24,
    )

    parser.add_argument(
        "--test-months",
        type=int,
        default=6,
    )

    parser.add_argument(
        "--step-months",
        type=int,
        default=6,
    )

    parser.add_argument(
        "--objective",
        choices=[
            "sharpe_ratio",
            "total_return_pct",
            "profit_factor",
            "composite",
        ],
        default="composite",
    )

    parser.add_argument(
        "--minimum-train-trades",
        type=int,
        default=10,
    )

    parser.add_argument(
        "--output",
        default=str(
            DEFAULT_OUTPUT_DIR
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    symbols = (
        list(TOP10_SYMBOLS)
        if args.symbols is None
        else [
            symbol.upper().strip()
            for symbol in args.symbols
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

    position_sizer = FixedFractionSizer(
        position_size_pct=(
            args.position_size
        )
    )

    regime_policy = (
        RegimePortfolioPolicy()
    )

    walk_forward_config = (
        WalkForwardConfig(
            start_date=args.start,
            end_date=args.end,
            train_months=(
                args.train_months
            ),
            test_months=(
                args.test_months
            ),
            step_months=(
                args.step_months
            ),
        )
    )

    parameter_grid = (
        WalkForwardParameterGrid(
            atr_stop_multipliers=(
                1.5,
                2.0,
                2.5,
            ),
            atr_target_multipliers=(
                3.0,
                4.0,
                5.0,
            ),
            holding_days=(
                20,
                30,
                40,
            ),
            min_adx_values=(
                15.0,
                20.0,
                25.0,
            ),
        )
    )

    optimization_config = (
        OptimizationConfig(
            objective=args.objective,
            minimum_train_trades=(
                args.minimum_train_trades
            ),
            drawdown_penalty=0.05,
        )
    )

    base_backtest_kwargs = {
        "symbols": symbols,
        "position_size_pct": (
            args.position_size
        ),
        "entry_model": entry_model,
        "ranking_method": (
            "relative_strength"
        ),
        "position_sizer": (
            position_sizer
        ),
        "regime_policy": (
            regime_policy
        ),
    }

    result = (
        run_walk_forward_optimization(
            walk_forward_config=(
                walk_forward_config
            ),
            parameter_grid=(
                parameter_grid
            ),
            optimization_config=(
                optimization_config
            ),
            initial_capital=args.capital,
            run_backtest_fn=run_backtest,
            build_exit_model_fn=(
                build_exit_model
            ),
            base_backtest_kwargs=(
                base_backtest_kwargs
            ),
        )
    )

    print_walk_forward_optimization_report(
        result
    )

    output_dir = Path(
        args.output
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    folds_path = (
        output_dir
        / "folds.csv"
    )

    train_search_path = (
        output_dir
        / "train_search.csv"
    )

    summary_path = (
        output_dir
        / "summary.csv"
    )

    result.save(
        folds_path=str(
            folds_path
        ),
        train_search_path=str(
            train_search_path
        ),
        summary_path=str(
            summary_path
        ),
    )

    print()
    print(f"Đã xuất: {folds_path}")
    print(
        f"Đã xuất: {train_search_path}"
    )
    print(f"Đã xuất: {summary_path}")


if __name__ == "__main__":
    main()	