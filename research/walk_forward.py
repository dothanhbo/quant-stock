from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from backtesting.engine import (
    build_exit_model,
    run_backtest,
)
from research.universes import (
    TOP10_SYMBOLS,
)


DEFAULT_OUTPUT = (
    "research_results/"
    "walk_forward_results.csv"
)


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    window_number: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str


def build_walk_forward_windows(
    *,
    start_year: int,
    end_year: int,
    train_years: int,
    test_years: int = 1,
    step_years: int = 1,
) -> list[WalkForwardWindow]:
    if train_years <= 0:
        raise ValueError(
            "train_years phải lớn hơn 0"
        )

    if test_years <= 0:
        raise ValueError(
            "test_years phải lớn hơn 0"
        )

    if step_years <= 0:
        raise ValueError(
            "step_years phải lớn hơn 0"
        )

    windows: list[WalkForwardWindow] = []

    train_start_year = start_year
    window_number = 1

    while True:
        train_end_year = (
            train_start_year
            + train_years
            - 1
        )

        test_start_year = (
            train_end_year
            + 1
        )

        test_end_year = (
            test_start_year
            + test_years
            - 1
        )

        if test_end_year > end_year:
            break

        windows.append(
            WalkForwardWindow(
                window_number=window_number,
                train_start=(
                    f"{train_start_year}-01-01"
                ),
                train_end=(
                    f"{train_end_year}-12-31"
                ),
                test_start=(
                    f"{test_start_year}-01-01"
                ),
                test_end=(
                    f"{test_end_year}-12-31"
                ),
            )
        )

        train_start_year += step_years
        window_number += 1

    return windows


def run_walk_forward(
    *,
    symbols: list[str],
    windows: list[WalkForwardWindow],
    exit_model_name: str,
    stop_loss_pct: float,
    take_profit_pct: float,
    max_holding_days: int,
    min_adx: float,
    break_even_trigger: float,
    atr_stop_multiplier: float,
    atr_target_multiplier: float,
    trailing_atr_multiplier: float,
    output_path: str,
) -> pd.DataFrame:
    rows: list[dict] = []

    total_runs = (
        len(symbols)
        * len(windows)
    )

    completed = 0

    for window in windows:
        print()
        print("=" * 80)
        print(
            f"WINDOW {window.window_number} | "
            f"Train {window.train_start} "
            f"→ {window.train_end} | "
            f"Test {window.test_start} "
            f"→ {window.test_end}"
        )
        print("=" * 80)

        for symbol in symbols:
            completed += 1

            print(
                f"[{completed}/{total_runs}] "
                f"{symbol} | "
                f"{exit_model_name}"
            )

            exit_model = build_exit_model(
                name=exit_model_name,
                break_even_trigger=(
                    break_even_trigger
                ),
                stop_atr_multiplier=(
                    atr_stop_multiplier
                ),
                target_atr_multiplier=(
                    atr_target_multiplier
                ),
                trailing_atr_multiplier=(
                    trailing_atr_multiplier
                ),
            )

            _, metrics, _ = run_backtest(
                symbols=[symbol],
                stop_loss_pct=(
                    stop_loss_pct
                ),
                take_profit_pct=(
                    take_profit_pct
                ),
                max_holding_days=(
                    max_holding_days
                ),
                min_adx=min_adx,
                start_date=window.test_start,
                end_date=window.test_end,
                verbose=False,
                exit_model=exit_model,
            )

            row = {
                "window_number": (
                    window.window_number
                ),
                "train_start": (
                    window.train_start
                ),
                "train_end": (
                    window.train_end
                ),
                "test_start": (
                    window.test_start
                ),
                "test_end": (
                    window.test_end
                ),
                "symbol": symbol,
                "exit_model": (
                    exit_model_name
                ),
                "stop_loss_pct": (
                    stop_loss_pct
                ),
                "take_profit_pct": (
                    take_profit_pct
                ),
                "max_holding_days": (
                    max_holding_days
                ),
                "min_adx": min_adx,
                "break_even_trigger": (
                    break_even_trigger
                ),
                "atr_stop_multiplier": (
                    atr_stop_multiplier
                ),
                "atr_target_multiplier": (
                    atr_target_multiplier
                ),
                "trailing_atr_multiplier": (
                    trailing_atr_multiplier
                ),
                "total_trades": metrics.get(
                    "total_trades",
                    0,
                ),
                "total_return_pct": metrics.get(
                    "total_return_pct",
                    0.0,
                ),
                "cagr_pct": metrics.get(
                    "cagr_pct",
                    0.0,
                ),
                "max_drawdown_pct": metrics.get(
                    "max_drawdown_pct",
                    0.0,
                ),
                "sharpe_ratio": metrics.get(
                    "sharpe_ratio",
                    0.0,
                ),
                "profit_factor": metrics.get(
                    "profit_factor",
                    0.0,
                ),
                "win_rate_pct": metrics.get(
                    "win_rate_pct",
                    0.0,
                ),
                "expectancy_pct": metrics.get(
                    "expectancy_pct",
                    0.0,
                ),
            }

            rows.append(row)

            print(
                f"    Trades "
                f"{row['total_trades']} | "
                f"Return "
                f"{row['total_return_pct']:+.2f}% | "
                f"Sharpe "
                f"{row['sharpe_ratio']:.2f} | "
                f"DD "
                f"{row['max_drawdown_pct']:.2f}%"
            )

    result_df = pd.DataFrame(rows)

    output = Path(output_path)

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    result_df.to_csv(
        output,
        index=False,
        encoding="utf-8-sig",
    )

    print_walk_forward_summary(
        result_df
    )

    print()
    print(f"Đã xuất: {output}")

    return result_df


def print_walk_forward_summary(
    result_df: pd.DataFrame,
) -> None:
    if result_df.empty:
        print("Không có kết quả.")
        return

    summary = (
        result_df
        .groupby(
            "window_number",
            as_index=False,
        )
        .agg(
            test_start=(
                "test_start",
                "first",
            ),
            test_end=(
                "test_end",
                "first",
            ),
            symbols=(
                "symbol",
                "count",
            ),
            total_trades=(
                "total_trades",
                "sum",
            ),
            average_return_pct=(
                "total_return_pct",
                "mean",
            ),
            median_return_pct=(
                "total_return_pct",
                "median",
            ),
            average_sharpe=(
                "sharpe_ratio",
                "mean",
            ),
            average_drawdown_pct=(
                "max_drawdown_pct",
                "mean",
            ),
            positive_symbols=(
                "total_return_pct",
                lambda values: int(
                    (values > 0).sum()
                ),
            ),
            qualified_symbols=(
                "total_trades",
                lambda values: int(
                    (values >= 10).sum()
                ),
            ),
            qualified_trades=(
                "total_trades",
                lambda values: int(
                    values[
                        values >= 10
                    ].sum()
                ),
            ),
        )
    )

    print()
    print("=" * 120)
    print("WALK-FORWARD TEST SUMMARY")
    print("=" * 120)

    print(
        summary.to_string(
            index=False,
            float_format=lambda value: (
                f"{value:.2f}"
            ),
        )
    )

    positive_windows = int(
        (
            summary[
                "average_return_pct"
            ]
            > 0
        ).sum()
    )

    print()
    print(
        f"Positive windows: "
        f"{positive_windows}/"
        f"{len(summary)}"
    )

    print(
        f"Overall average return: "
        f"{summary['average_return_pct'].mean():+.2f}%"
    )

    print(
        f"Overall average Sharpe: "
        f"{summary['average_sharpe'].mean():.2f}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Walk-Forward Test cho "
            "Exit Model."
        )
    )

    parser.add_argument(
        "--symbol",
        nargs="+",
        default=None,
    )

    parser.add_argument(
        "--start-year",
        type=int,
        default=2018,
    )

    parser.add_argument(
        "--end-year",
        type=int,
        default=2026,
    )

    parser.add_argument(
        "--train-years",
        type=int,
        default=4,
    )

    parser.add_argument(
        "--test-years",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--step-years",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--exit-model",
        choices=[
            "fixed",
            "atr",
            "break_even",
            "trailing_atr",
        ],
        default="trailing_atr",
    )

    parser.add_argument(
        "--sl",
        type=float,
        default=3.0,
    )

    parser.add_argument(
        "--tp",
        type=float,
        default=8.0,
    )

    parser.add_argument(
        "--hold",
        type=int,
        default=10,
    )

    parser.add_argument(
        "--min-adx",
        type=float,
        default=20.0,
    )

    parser.add_argument(
        "--break-even-trigger",
        type=float,
        default=7.0,
    )

    parser.add_argument(
        "--atr-stop-multiplier",
        type=float,
        default=2.5,
    )

    parser.add_argument(
        "--atr-target-multiplier",
        type=float,
        default=5.0,
    )

    parser.add_argument(
        "--trailing-atr-multiplier",
        type=float,
        default=2.5,
    )

    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    symbols = (
        TOP10_SYMBOLS
        if args.symbol is None
        else [
            symbol.upper().strip()
            for symbol in args.symbol
        ]
    )

    windows = build_walk_forward_windows(
        start_year=args.start_year,
        end_year=args.end_year,
        train_years=args.train_years,
        test_years=args.test_years,
        step_years=args.step_years,
    )

    if not windows:
        raise ValueError(
            "Không tạo được Walk-Forward Window."
        )

    run_walk_forward(
        symbols=symbols,
        windows=windows,
        exit_model_name=args.exit_model,
        stop_loss_pct=args.sl,
        take_profit_pct=args.tp,
        max_holding_days=args.hold,
        min_adx=args.min_adx,
        break_even_trigger=(
            args.break_even_trigger
        ),
        atr_stop_multiplier=(
            args.atr_stop_multiplier
        ),
        atr_target_multiplier=(
            args.atr_target_multiplier
        ),
        trailing_atr_multiplier=(
            args.trailing_atr_multiplier
        ),
        output_path=args.output,
    )


if __name__ == "__main__":
    main()