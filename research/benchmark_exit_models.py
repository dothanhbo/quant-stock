from __future__ import annotations

import argparse
from pathlib import Path
from statistics import mean

import pandas as pd

from backtesting.engine import (
    build_exit_model,
    run_backtest,
)
from research.universes import (
    TOP10_SYMBOLS,
)


DEFAULT_DETAIL_OUTPUT = (
    "research_results/"
    "exit_model_benchmark_detail.csv"
)

DEFAULT_SUMMARY_OUTPUT = (
    "research_results/"
    "exit_model_benchmark_summary.csv"
)

DEFAULT_WINNER_OUTPUT = (
    "research_results/"
    "exit_model_symbol_winners.csv"
)

DEFAULT_WINNER_COUNT_OUTPUT = (
    "research_results/"
    "exit_model_symbol_win_summary.csv"
)


MODEL_CONFIGS = [
    {
        "exit_model": "fixed",
        "break_even_trigger": 5.0,
        "atr_stop_multiplier": 2.0,
        "atr_target_multiplier": 4.0,
        "trailing_atr_multiplier": 2.0,
    },
    {
        "exit_model": "atr",
        "break_even_trigger": 5.0,
        "atr_stop_multiplier": 2.0,
        "atr_target_multiplier": 4.0,
        "trailing_atr_multiplier": 2.0,
    },
    {
        "exit_model": "break_even",
        "break_even_trigger": 7.0,
        "atr_stop_multiplier": 2.0,
        "atr_target_multiplier": 4.0,
        "trailing_atr_multiplier": 2.0,
    },
    {
        "exit_model": "trailing_atr",
        "break_even_trigger": 5.0,
        "atr_stop_multiplier": 2.5,
        "atr_target_multiplier": 5.0,
        "trailing_atr_multiplier": 2.5,
    },
]


def benchmark_exit_models(
    *,
    symbols: list[str],
    start_date: str | None,
    end_date: str | None,
    stop_loss_pct: float,
    take_profit_pct: float,
    max_holding_days: int,
    min_adx: float,
    detail_output_path: str,
    summary_output_path: str,
    winner_output_path: str,
    winner_count_output_path: str,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:

    total_runs = (
        len(MODEL_CONFIGS)
        * len(symbols)
    )

    completed = 0

    for model_config in MODEL_CONFIGS:
        exit_model_name = model_config[
            "exit_model"
        ]

        print()
        print("=" * 70)
        print(
            f"BENCHMARK EXIT MODEL: "
            f"{exit_model_name}"
        )
        print("=" * 70)

        for symbol in symbols:
            completed += 1

            print(
                f"[{completed}/{total_runs}] "
                f"{exit_model_name} | "
                f"{symbol}"
            )

            exit_model = build_exit_model(
                name=exit_model_name,
                break_even_trigger=model_config[
                    "break_even_trigger"
                ],
                stop_atr_multiplier=model_config[
                    "atr_stop_multiplier"
                ],
                target_atr_multiplier=model_config[
                    "atr_target_multiplier"
                ],
                trailing_atr_multiplier=model_config[
                    "trailing_atr_multiplier"
                ],
            )

            _, metrics, _ = run_backtest(
                symbols=[symbol],
                stop_loss_pct=stop_loss_pct,
                take_profit_pct=take_profit_pct,
                max_holding_days=max_holding_days,
                min_adx=min_adx,
                start_date=start_date,
                end_date=end_date,
                verbose=False,
                exit_model=exit_model,
            )

            row = {
                "symbol": symbol,
                "exit_model": exit_model_name,
                "stop_loss_pct": stop_loss_pct,
                "take_profit_pct": (
                    take_profit_pct
                ),
                "max_holding_days": (
                    max_holding_days
                ),
                "min_adx": min_adx,
                "break_even_trigger": (
                    model_config[
                        "break_even_trigger"
                    ]
                ),
                "atr_stop_multiplier": (
                    model_config[
                        "atr_stop_multiplier"
                    ]
                ),
                "atr_target_multiplier": (
                    model_config[
                        "atr_target_multiplier"
                    ]
                ),
                "trailing_atr_multiplier": (
                    model_config[
                        "trailing_atr_multiplier"
                    ]
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
                "sortino_ratio": metrics.get(
                    "sortino_ratio",
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

            detail_rows.append(row)

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

    detail_df = pd.DataFrame(
        detail_rows
    )

    summary_df = build_summary(
        detail_df
    )

    winner_df, winner_count_df = (
        build_symbol_winner_matrix(
            detail_df
        )
    )

    save_results(
        detail_df=detail_df,
        summary_df=summary_df,
        detail_output_path=detail_output_path,
        summary_output_path=summary_output_path,
    )

    save_winner_results(
        winner_df=winner_df,
        winner_count_df=winner_count_df,
        winner_output_path=winner_output_path,
        winner_count_output_path=(
            winner_count_output_path
        ),
    )

    print_summary(summary_df)

    print_winner_summary(
        winner_df,
        winner_count_df,
    )

    return (
        detail_df,
        summary_df,
        winner_df,
        winner_count_df,
    )

def build_summary(
    detail_df: pd.DataFrame,
) -> pd.DataFrame:
    summary_rows: list[dict] = []

    for exit_model, group in (
        detail_df.groupby(
            "exit_model",
            sort=False,
        )
    ):
        returns = group[
            "total_return_pct"
        ].astype(float)

        sharpes = group[
            "sharpe_ratio"
        ].astype(float)

        drawdowns = group[
            "max_drawdown_pct"
        ].astype(float)

        profit_factors = group[
            "profit_factor"
        ].astype(float)

        win_rates = group[
            "win_rate_pct"
        ].astype(float)

        expectancies = group[
            "expectancy_pct"
        ].astype(float)

        trades = group[
            "total_trades"
        ].astype(int)

        positive_symbols = int(
            (returns > 0).sum()
        )

        qualified_symbols = int(
            (trades >= 30).sum()
        )

        summary_rows.append(
            {
                "exit_model": exit_model,
                "symbols": len(group),
                "positive_symbols": (
                    positive_symbols
                ),
                "qualified_symbols": (
                    qualified_symbols
                ),
                "average_trades": mean(
                    trades
                ),
                "average_return_pct": mean(
                    returns
                ),
                "median_return_pct": (
                    returns.median()
                ),
                "average_cagr_pct": mean(
                    group[
                        "cagr_pct"
                    ].astype(float)
                ),
                "average_sharpe": mean(
                    sharpes
                ),
                "median_sharpe": (
                    sharpes.median()
                ),
                "average_drawdown_pct": (
                    mean(drawdowns)
                ),
                "worst_drawdown_pct": (
                    drawdowns.min()
                ),
                "average_profit_factor": (
                    mean(profit_factors)
                ),
                "average_win_rate_pct": (
                    mean(win_rates)
                ),
                "average_expectancy_pct": (
                    mean(expectancies)
                ),
            }
        )

    summary_df = pd.DataFrame(
        summary_rows
    )

    summary_df = summary_df.sort_values(
        by=[
            "average_sharpe",
            "average_return_pct",
            "average_profit_factor",
        ],
        ascending=[
            False,
            False,
            False,
        ],
    ).reset_index(drop=True)

    summary_df.insert(
        0,
        "rank",
        range(
            1,
            len(summary_df) + 1,
        ),
    )

    return summary_df

def build_symbol_winner_matrix(
    detail_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if detail_df.empty:
        return (
            pd.DataFrame(),
            pd.DataFrame(),
        )

    rows: list[dict] = []

    for symbol, group in detail_df.groupby(
        "symbol",
        sort=True,
    ):
        group = group.copy()

        group["sharpe_ratio"] = pd.to_numeric(
            group["sharpe_ratio"],
            errors="coerce",
        )

        group["total_return_pct"] = pd.to_numeric(
            group["total_return_pct"],
            errors="coerce",
        )

        group["profit_factor"] = pd.to_numeric(
            group["profit_factor"],
            errors="coerce",
        )

        group["max_drawdown_pct"] = pd.to_numeric(
            group["max_drawdown_pct"],
            errors="coerce",
        )

        group = group.sort_values(
            by=[
                "sharpe_ratio",
                "total_return_pct",
                "profit_factor",
                "max_drawdown_pct",
            ],
            ascending=[
                False,
                False,
                False,
                False,
            ],
        )

        winner = group.iloc[0]

        rows.append(
            {
                "symbol": symbol,
                "winner": winner["exit_model"],
                "winner_sharpe": winner[
                    "sharpe_ratio"
                ],
                "winner_return_pct": winner[
                    "total_return_pct"
                ],
                "winner_profit_factor": winner[
                    "profit_factor"
                ],
                "winner_drawdown_pct": winner[
                    "max_drawdown_pct"
                ],
            }
        )

    winner_df = pd.DataFrame(rows)

    count_df = (
        winner_df[
            "winner"
        ]
        .value_counts()
        .rename_axis(
            "exit_model"
        )
        .reset_index(
            name="symbol_wins"
        )
    )

    count_df["win_rate_pct"] = (
        count_df["symbol_wins"]
        / len(winner_df)
        * 100
    )

    count_df = count_df.sort_values(
        by=[
            "symbol_wins",
            "win_rate_pct",
        ],
        ascending=[
            False,
            False,
        ],
    ).reset_index(drop=True)

    count_df.insert(
        0,
        "rank",
        range(
            1,
            len(count_df) + 1,
        ),
    )

    return winner_df, count_df

def save_results(
    *,
    detail_df: pd.DataFrame,
    summary_df: pd.DataFrame,
    detail_output_path: str,
    summary_output_path: str,
) -> None:
    detail_output = Path(
        detail_output_path
    )

    summary_output = Path(
        summary_output_path
    )

    detail_output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    summary_output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    detail_df.to_csv(
        detail_output,
        index=False,
        encoding="utf-8-sig",
    )

    summary_df.to_csv(
        summary_output,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print(
        f"Đã xuất detail: "
        f"{detail_output}"
    )
    print(
        f"Đã xuất summary: "
        f"{summary_output}"
    )

def save_winner_results(
    *,
    winner_df: pd.DataFrame,
    winner_count_df: pd.DataFrame,
    winner_output_path: str,
    winner_count_output_path: str,
) -> None:
    winner_output = Path(
        winner_output_path
    )

    winner_count_output = Path(
        winner_count_output_path
    )

    winner_output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    winner_count_output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    winner_df.to_csv(
        winner_output,
        index=False,
        encoding="utf-8-sig",
    )

    winner_count_df.to_csv(
        winner_count_output,
        index=False,
        encoding="utf-8-sig",
    )

    print(
        f"Đã xuất winner matrix: "
        f"{winner_output}"
    )

    print(
        f"Đã xuất winner summary: "
        f"{winner_count_output}"
    )

def print_summary(
    summary_df: pd.DataFrame,
) -> None:
    print()
    print("=" * 120)
    print("EXIT MODEL BENCHMARK SUMMARY")
    print("=" * 120)

    display_columns = [
        "rank",
        "exit_model",
        "symbols",
        "positive_symbols",
        "qualified_symbols",
        "average_trades",
        "average_return_pct",
        "median_return_pct",
        "average_sharpe",
        "average_drawdown_pct",
        "average_profit_factor",
        "average_expectancy_pct",
    ]

    print(
        summary_df[
            display_columns
        ].to_string(
            index=False,
            float_format=lambda value: (
                f"{value:.2f}"
            ),
        )
    )

    if not summary_df.empty:
        best = summary_df.iloc[0]

        print()
        print(
            f"Best model: "
            f"{best['exit_model']} | "
            f"Avg Sharpe "
            f"{best['average_sharpe']:.2f} | "
            f"Avg Return "
            f"{best['average_return_pct']:+.2f}%"
        )

def print_winner_summary(
    winner_df: pd.DataFrame,
    winner_count_df: pd.DataFrame,
) -> None:
    print()
    print("=" * 90)
    print("SYMBOL WINNER MATRIX")
    print("=" * 90)

    if winner_df.empty:
        print("Không có dữ liệu winner.")
        return

    print(
        winner_df.to_string(
            index=False,
            float_format=lambda value: (
                f"{value:.2f}"
            ),
        )
    )

    print()
    print("=" * 60)
    print("MODEL WINS BY SYMBOL")
    print("=" * 60)

    print(
        winner_count_df.to_string(
            index=False,
            float_format=lambda value: (
                f"{value:.2f}"
            ),
        )
    )

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark nhiều Exit Model "
            "trên cùng tập cổ phiếu."
        )
    )

    parser.add_argument(
        "--symbol",
        nargs="+",
        default=None,
        help=(
            "Danh sách mã. "
            "Mặc định dùng TOP10_SYMBOLS."
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
        "--detail-output",
        default=DEFAULT_DETAIL_OUTPUT,
    )

    parser.add_argument(
        "--summary-output",
        default=DEFAULT_SUMMARY_OUTPUT,
    )

    parser.add_argument(
        "--winner-output",
        default=DEFAULT_WINNER_OUTPUT,
    )

    parser.add_argument(
        "--winner-count-output",
        default=DEFAULT_WINNER_COUNT_OUTPUT,
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

    benchmark_exit_models(
        symbols=symbols,
        start_date=args.start,
        end_date=args.end,
        stop_loss_pct=args.sl,
        take_profit_pct=args.tp,
        max_holding_days=args.hold,
        min_adx=args.min_adx,
        detail_output_path=(
            args.detail_output
        ),
        summary_output_path=(
            args.summary_output
        ),
        winner_output_path=(
            args.winner_output
        ),
        winner_count_output_path=(
            args.winner_count_output
        ),
    )


if __name__ == "__main__":
    main()