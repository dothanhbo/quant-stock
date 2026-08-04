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
    print_walk_forward_report,
    run_walk_forward,
)
from research.benchmark_portfolio_models import (
    build_portfolio_model_registry,
)
from research.universes import TOP10_SYMBOLS


DEFAULT_OUTPUT_DIR = Path(
    "research_results/walk_forward"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run rolling walk-forward analysis."
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
        "--hold",
        type=int,
        default=30,
    )

    parser.add_argument(
        "--min-adx",
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
        "--atr-stop-multiplier",
        type=float,
        default=2.0,
    )

    parser.add_argument(
        "--atr-target-multiplier",
        type=float,
        default=5.0,
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
            f"Entry model không hợp lệ: "
            f"{args.entry_model}"
        )

    entry_model = registry[
        args.entry_model
    ]

    exit_model = build_exit_model(
        name="atr",
        stop_atr_multiplier=(
            args.atr_stop_multiplier
        ),
        target_atr_multiplier=(
            args.atr_target_multiplier
        ),
        break_even_trigger=5.0,
        trailing_atr_multiplier=2.0,
    )

    position_sizer = FixedFractionSizer(
        position_size_pct=(
            args.position_size
        )
    )

    regime_policy = (
        RegimePortfolioPolicy()
    )

    config = WalkForwardConfig(
        start_date=args.start,
        end_date=args.end,
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
    )

    backtest_kwargs = {
        "symbols": symbols,
        "position_size_pct": (
            args.position_size
        ),
        "max_holding_days": (
            args.hold
        ),
        "min_adx": args.min_adx,
        "entry_model": entry_model,
        "exit_model": exit_model,
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

    result = run_walk_forward(
        config=config,
        initial_capital=args.capital,
        run_backtest_fn=run_backtest,
        backtest_kwargs=backtest_kwargs,
    )

    print_walk_forward_report(
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

    summary_path = (
        output_dir
        / "summary.csv"
    )

    result.save(
        folds_path=str(
            folds_path
        ),
        summary_path=str(
            summary_path
        ),
    )

    print()
    print(f"Đã xuất: {folds_path}")
    print(f"Đã xuất: {summary_path}")


if __name__ == "__main__":
    main()
