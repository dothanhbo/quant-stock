from __future__ import annotations

import argparse
from pathlib import Path

from backtesting.engine import (
    build_exit_model,
    run_backtest,
)
from backtesting.monte_carlo_v2 import (
    print_monte_carlo_v2_report,
    run_monte_carlo_v2,
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
    "research_results/monte_carlo_v2"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Monte Carlo V2 "
            "with block or regime bootstrap."
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
        "--simulations",
        type=int,
        default=10_000,
    )

    parser.add_argument(
        "--bootstrap-method",
        choices=[
            "trade",
            "block",
            "regime",
        ],
        default="block",
    )

    parser.add_argument(
        "--block-size",
        type=int,
        default=10,
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=42,
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

    regime_policy = (
        RegimePortfolioPolicy()
    )

    trades, metrics, _ = run_backtest(
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
        position_sizer=(
            position_sizer
        ),
        regime_policy=(
            regime_policy
        ),
        verbose=False,
    )

    print()
    print("=" * 80)
    print("BACKTEST SOURCE")
    print("=" * 80)
    print(f"Symbols          : {len(symbols)}")
    print(f"Executed Trades  : {len(trades)}")
    print(
        f"Backtest Return  : "
        f"{metrics.get('total_return_pct', 0.0):+.2f}%"
    )
    print(
        f"Backtest Drawdown: "
        f"{metrics.get('max_drawdown_pct', 0.0):.2f}%"
    )

    result = run_monte_carlo_v2(
        trades,
        initial_capital=args.capital,
        simulations=args.simulations,
        position_size_pct=(
            args.position_size
        ),
        bootstrap_method=(
            args.bootstrap_method
        ),
        block_size=args.block_size,
        random_seed=args.seed,
    )

    print_monte_carlo_v2_report(
        result
    )

    result.save(
        output_dir=args.output
    )

    output_dir = Path(
        args.output
    )

    print()
    print(
        "Đã xuất: "
        f"{output_dir / 'simulations_v2.csv'}"
    )
    print(
        "Đã xuất: "
        f"{output_dir / 'summary_v2.csv'}"
    )


if __name__ == "__main__":
    main()