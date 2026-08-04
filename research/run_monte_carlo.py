from __future__ import annotations

import argparse
from pathlib import Path

from backtesting.engine import (
    build_exit_model,
    run_backtest,
)
from backtesting.monte_carlo import (
    print_monte_carlo_report,
    run_monte_carlo,
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
from research.universes import TOP10_SYMBOLS


DEFAULT_OUTPUT_DIR = Path(
    "research_results/monte_carlo"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Monte Carlo analysis "
            "for the backtest strategy."
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
        "--simulations",
        type=int,
        default=5_000,
    )

    parser.add_argument(
        "--confidence",
        type=float,
        default=0.95,
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
            f"Entry model không hợp lệ: "
            f"{args.entry_model}"
        )

    entry_model = registry[
        args.entry_model
    ]

    exit_model = build_exit_model(
        name="atr",
        stop_atr_multiplier=2.0,
        target_atr_multiplier=5.0,
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
        ranking_method="relative_strength",
        position_sizer=position_sizer,
        regime_policy=regime_policy,
        verbose=False,
    )

    print()
    print("BACKTEST SOURCE")
    print("=" * 70)
    print(f"Symbols          : {len(symbols)}")
    print(f"Executed trades  : {len(trades)}")
    print(
        f"Backtest return  : "
        f"{metrics.get('total_return_pct', 0.0):+.2f}%"
    )
    print(
        f"Backtest drawdown: "
        f"{metrics.get('max_drawdown_pct', 0.0):.2f}%"
    )

    result = run_monte_carlo(
        trades,
        initial_capital=args.capital,
        position_size_pct=args.position_size,
        simulations=args.simulations,
        confidence_level=args.confidence,
        random_seed=args.seed,
    )

    print_monte_carlo_report(
        result
    )

    output_dir = Path(
        args.output
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    simulation_path = (
        output_dir
        / "simulations.csv"
    )

    summary_path = (
        output_dir
        / "summary.csv"
    )

    result.save(
        simulation_path=str(
            simulation_path
        ),
        summary_path=str(
            summary_path
        ),
    )

    print()
    print(f"Đã xuất: {simulation_path}")
    print(f"Đã xuất: {summary_path}")


if __name__ == "__main__":
    main()