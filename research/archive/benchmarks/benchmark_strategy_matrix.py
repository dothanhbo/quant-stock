from __future__ import annotations

import argparse
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

import pandas as pd

from backtesting.engine import (
    build_exit_model,
    run_backtest,
)
from research.universes import (
    TOP10_SYMBOLS,
)
from strategy.base_strategy import (
    BaseStrategy,
)
from strategy.donchian_breakout_entry import (
    DonchianBreakoutEntryModel,
)
from strategy.trend_strategy_v1 import (
    TrendStrategyV1,
)


DEFAULT_DETAIL_OUTPUT = (
    "research_results/"
    "strategy_matrix_detail.csv"
)

DEFAULT_SUMMARY_OUTPUT = (
    "research_results/"
    "strategy_matrix_summary.csv"
)

DEFAULT_WINNER_OUTPUT = (
    "research_results/"
    "strategy_matrix_symbol_winners.csv"
)

DEFAULT_WINNER_COUNT_OUTPUT = (
    "research_results/"
    "strategy_matrix_win_summary.csv"
)


def build_entry_model_registry(
) -> dict[str, BaseStrategy]:
    return {
        "trend_v1": (
            TrendStrategyV1()
        ),
        "donchian_breakout_v1": (
            DonchianBreakoutEntryModel()
        ),
    }


def build_exit_model_configs(
) -> list[dict[str, Any]]:
    return [
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
            "break_even_trigger": 5.0,
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


def benchmark_strategy_matrix(
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
    entry_models = (
        build_entry_model_registry()
    )

    exit_configs = (
        build_exit_model_configs()
    )

    total_runs = (
        len(entry_models)
        * len(exit_configs)
        * len(symbols)
    )

    completed = 0
    started_at = perf_counter()

    rows: list[dict[str, Any]] = []

    print(
        f"Chạy {len(entry_models)} entry model(s) × "
        f"{len(exit_configs)} exit model(s) × "
        f"{len(symbols)} symbol(s)"
    )

    print(
        f"Tổng cộng: "
        f"{total_runs} backtests."
    )

    for (
        entry_model_name,
        entry_model,
    ) in entry_models.items():
        for exit_config in exit_configs:
            exit_model_name = str(
                exit_config[
                    "exit_model"
                ]
            )

            strategy_name = (
                f"{entry_model_name}"
                f"__"
                f"{exit_model_name}"
            )

            print()
            print("=" * 90)
            print(
                f"STRATEGY: "
                f"{entry_model_name} "
                f"× "
                f"{exit_model_name}"
            )
            print("=" * 90)

            for symbol in symbols:
                completed += 1

                run_started_at = (
                    perf_counter()
                )

                exit_model = build_exit_model(
                    name=exit_model_name,
                    break_even_trigger=float(
                        exit_config[
                            "break_even_trigger"
                        ]
                    ),
                    stop_atr_multiplier=float(
                        exit_config[
                            "atr_stop_multiplier"
                        ]
                    ),
                    target_atr_multiplier=float(
                        exit_config[
                            "atr_target_multiplier"
                        ]
                    ),
                    trailing_atr_multiplier=float(
                        exit_config[
                            "trailing_atr_multiplier"
                        ]
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
                    start_date=start_date,
                    end_date=end_date,
                    verbose=False,
                    entry_model=entry_model,
                    exit_model=exit_model,
                )

                elapsed_seconds = (
                    perf_counter()
                    - run_started_at
                )

                row = {
                    "strategy": (
                        strategy_name
                    ),
                    "symbol": symbol,
                    "entry_model": (
                        entry_model_name
                    ),
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
                        exit_config[
                            "break_even_trigger"
                        ]
                    ),
                    "atr_stop_multiplier": (
                        exit_config[
                            "atr_stop_multiplier"
                        ]
                    ),
                    "atr_target_multiplier": (
                        exit_config[
                            "atr_target_multiplier"
                        ]
                    ),
                    "trailing_atr_multiplier": (
                        exit_config[
                            "trailing_atr_multiplier"
                        ]
                    ),
                    "elapsed_seconds": round(
                        elapsed_seconds,
                        2,
                    ),
                    "total_trades": (
                        metrics.get(
                            "total_trades",
                            0,
                        )
                    ),
                    "total_return_pct": (
                        metrics.get(
                            "total_return_pct",
                            0.0,
                        )
                    ),
                    "cagr_pct": (
                        metrics.get(
                            "cagr_pct",
                            0.0,
                        )
                    ),
                    "max_drawdown_pct": (
                        metrics.get(
                            "max_drawdown_pct",
                            0.0,
                        )
                    ),
                    "sharpe_ratio": (
                        metrics.get(
                            "sharpe_ratio",
                            0.0,
                        )
                    ),
                    "sortino_ratio": (
                        metrics.get(
                            "sortino_ratio",
                            0.0,
                        )
                    ),
                    "profit_factor": (
                        metrics.get(
                            "profit_factor",
                            0.0,
                        )
                    ),
                    "win_rate_pct": (
                        metrics.get(
                            "win_rate_pct",
                            0.0,
                        )
                    ),
                    "expectancy_pct": (
                        metrics.get(
                            "expectancy_pct",
                            0.0,
                        )
                    ),
                    "benchmark_return_pct": (
                        metrics.get(
                            "benchmark_return_pct",
                            0.0,
                        )
                    ),
                    "strategy_vs_benchmark_pct": (
                        metrics.get(
                            "strategy_vs_benchmark_pct",
                            0.0,
                        )
                    ),
                }

                rows.append(row)

                total_elapsed = (
                    perf_counter()
                    - started_at
                )

                average_seconds = (
                    total_elapsed
                    / completed
                )

                remaining = (
                    total_runs
                    - completed
                )

                eta_seconds = (
                    average_seconds
                    * remaining
                )

                print(
                    f"[{completed}/{total_runs}] "
                    f"{symbol} | "
                    f"Trades "
                    f"{row['total_trades']} | "
                    f"Return "
                    f"{row['total_return_pct']:+.2f}% | "
                    f"Sharpe "
                    f"{row['sharpe_ratio']:.2f} | "
                    f"ETA "
                    f"{format_duration(eta_seconds)}"
                )

    detail_df = pd.DataFrame(
        rows
    )

    summary_df = (
        build_strategy_summary(
            detail_df
        )
    )

    (
        winner_df,
        winner_count_df,
    ) = build_strategy_winner_matrix(
        detail_df
    )

    save_results(
        detail_df=detail_df,
        summary_df=summary_df,
        winner_df=winner_df,
        winner_count_df=(
            winner_count_df
        ),
        detail_output_path=(
            detail_output_path
        ),
        summary_output_path=(
            summary_output_path
        ),
        winner_output_path=(
            winner_output_path
        ),
        winner_count_output_path=(
            winner_count_output_path
        ),
    )

    print_strategy_summary(
        summary_df
    )

    print_strategy_winners(
        winner_df=winner_df,
        winner_count_df=(
            winner_count_df
        ),
    )

    return (
        detail_df,
        summary_df,
        winner_df,
        winner_count_df,
    )


def build_strategy_summary(
    detail_df: pd.DataFrame,
) -> pd.DataFrame:
    if detail_df.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []

    for (
        strategy_name,
        group,
    ) in detail_df.groupby(
        "strategy",
        sort=False,
    ):
        returns = numeric_series(
            group,
            "total_return_pct",
        )

        cagr_values = numeric_series(
            group,
            "cagr_pct",
        )

        sharpes = numeric_series(
            group,
            "sharpe_ratio",
        )

        sortinos = numeric_series(
            group,
            "sortino_ratio",
        )

        drawdowns = numeric_series(
            group,
            "max_drawdown_pct",
        )

        profit_factors = numeric_series(
            group,
            "profit_factor",
        )

        win_rates = numeric_series(
            group,
            "win_rate_pct",
        )

        expectancies = numeric_series(
            group,
            "expectancy_pct",
        )

        trades = (
            pd.to_numeric(
                group[
                    "total_trades"
                ],
                errors="coerce",
            )
            .fillna(0)
            .astype(int)
        )

        rows.append(
            {
                "strategy": (
                    strategy_name
                ),
                "entry_model": (
                    group[
                        "entry_model"
                    ].iloc[0]
                ),
                "exit_model": (
                    group[
                        "exit_model"
                    ].iloc[0]
                ),
                "symbols": len(group),
                "positive_symbols": int(
                    (returns > 0).sum()
                ),
                "qualified_symbols": int(
                    (trades >= 20).sum()
                ),
                "total_trades": int(
                    trades.sum()
                ),
                "average_trades": float(
                    mean(trades)
                ),
                "average_return_pct": float(
                    mean(returns)
                ),
                "median_return_pct": float(
                    returns.median()
                ),
                "average_cagr_pct": float(
                    mean(cagr_values)
                ),
                "average_sharpe": float(
                    mean(sharpes)
                ),
                "median_sharpe": float(
                    sharpes.median()
                ),
                "average_sortino": float(
                    mean(sortinos)
                ),
                "average_drawdown_pct": float(
                    mean(drawdowns)
                ),
                "worst_drawdown_pct": float(
                    drawdowns.min()
                ),
                "average_profit_factor": float(
                    mean(
                        profit_factors
                    )
                ),
                "average_win_rate_pct": float(
                    mean(win_rates)
                ),
                "average_expectancy_pct": float(
                    mean(expectancies)
                ),
            }
        )

    summary_df = pd.DataFrame(
        rows
    )

    summary_df = (
        summary_df
        .sort_values(
            by=[
                "average_sharpe",
                "median_return_pct",
                "average_return_pct",
                "average_profit_factor",
                "average_drawdown_pct",
            ],
            ascending=[
                False,
                False,
                False,
                False,
                False,
            ],
        )
        .reset_index(drop=True)
    )

    summary_df.insert(
        0,
        "rank",
        range(
            1,
            len(summary_df) + 1,
        ),
    )

    return summary_df


def build_strategy_winner_matrix(
    detail_df: pd.DataFrame,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
]:
    if detail_df.empty:
        return (
            pd.DataFrame(),
            pd.DataFrame(),
        )

    winner_rows: list[
        dict[str, Any]
    ] = []

    for (
        symbol,
        group,
    ) in detail_df.groupby(
        "symbol",
        sort=True,
    ):
        ranked = group.copy()

        columns = [
            "sharpe_ratio",
            "total_return_pct",
            "profit_factor",
            "expectancy_pct",
            "max_drawdown_pct",
        ]

        for column in columns:
            ranked[column] = (
                pd.to_numeric(
                    ranked[column],
                    errors="coerce",
                )
                .fillna(0.0)
            )

        ranked = ranked.sort_values(
            by=[
                "sharpe_ratio",
                "total_return_pct",
                "profit_factor",
                "expectancy_pct",
                "max_drawdown_pct",
            ],
            ascending=[
                False,
                False,
                False,
                False,
                False,
            ],
        )

        winner = ranked.iloc[0]

        winner_rows.append(
            {
                "symbol": symbol,
                "winner_strategy": (
                    winner[
                        "strategy"
                    ]
                ),
                "winner_entry_model": (
                    winner[
                        "entry_model"
                    ]
                ),
                "winner_exit_model": (
                    winner[
                        "exit_model"
                    ]
                ),
                "winner_trades": int(
                    winner[
                        "total_trades"
                    ]
                ),
                "winner_return_pct": float(
                    winner[
                        "total_return_pct"
                    ]
                ),
                "winner_sharpe": float(
                    winner[
                        "sharpe_ratio"
                    ]
                ),
                "winner_profit_factor": float(
                    winner[
                        "profit_factor"
                    ]
                ),
                "winner_expectancy_pct": float(
                    winner[
                        "expectancy_pct"
                    ]
                ),
                "winner_drawdown_pct": float(
                    winner[
                        "max_drawdown_pct"
                    ]
                ),
            }
        )

    winner_df = pd.DataFrame(
        winner_rows
    )

    winner_count_df = (
        winner_df[
            "winner_strategy"
        ]
        .value_counts()
        .rename_axis(
            "strategy"
        )
        .reset_index(
            name="symbol_wins"
        )
    )

    winner_count_df[
        "win_rate_pct"
    ] = (
        winner_count_df[
            "symbol_wins"
        ]
        / len(winner_df)
        * 100
    )

    winner_count_df = (
        winner_count_df
        .sort_values(
            by=[
                "symbol_wins",
                "win_rate_pct",
            ],
            ascending=[
                False,
                False,
            ],
        )
        .reset_index(drop=True)
    )

    winner_count_df.insert(
        0,
        "rank",
        range(
            1,
            len(
                winner_count_df
            ) + 1,
        ),
    )

    return (
        winner_df,
        winner_count_df,
    )


def numeric_series(
    dataframe: pd.DataFrame,
    column: str,
) -> pd.Series:
    return (
        pd.to_numeric(
            dataframe[column],
            errors="coerce",
        )
        .fillna(0.0)
    )


def format_duration(
    seconds: float,
) -> str:
    seconds = max(
        0,
        int(seconds),
    )

    hours, remainder = divmod(
        seconds,
        3600,
    )

    minutes, seconds = divmod(
        remainder,
        60,
    )

    if hours:
        return (
            f"{hours:02d}h "
            f"{minutes:02d}m "
            f"{seconds:02d}s"
        )

    return (
        f"{minutes:02d}m "
        f"{seconds:02d}s"
    )


def save_results(
    *,
    detail_df: pd.DataFrame,
    summary_df: pd.DataFrame,
    winner_df: pd.DataFrame,
    winner_count_df: pd.DataFrame,
    detail_output_path: str,
    summary_output_path: str,
    winner_output_path: str,
    winner_count_output_path: str,
) -> None:
    outputs = {
        "detail": (
            detail_df,
            Path(
                detail_output_path
            ),
        ),
        "summary": (
            summary_df,
            Path(
                summary_output_path
            ),
        ),
        "winner matrix": (
            winner_df,
            Path(
                winner_output_path
            ),
        ),
        "winner summary": (
            winner_count_df,
            Path(
                winner_count_output_path
            ),
        ),
    }

    for (
        label,
        (
            dataframe,
            output,
        ),
    ) in outputs.items():
        output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        try:
            dataframe.to_csv(
                output,
                index=False,
                encoding="utf-8-sig",
            )

        except PermissionError as exc:
            raise PermissionError(
                f"Không thể ghi file "
                f"{output}. "
                "Hãy đóng file trong Excel."
            ) from exc

        print(
            f"Đã xuất {label}: "
            f"{output}"
        )


def print_strategy_summary(
    summary_df: pd.DataFrame,
) -> None:
    print()
    print("=" * 170)
    print(
        "ENTRY × EXIT STRATEGY MATRIX SUMMARY"
    )
    print("=" * 170)

    if summary_df.empty:
        print(
            "Không có dữ liệu summary."
        )
        return

    columns = [
        "rank",
        "strategy",
        "entry_model",
        "exit_model",
        "symbols",
        "positive_symbols",
        "qualified_symbols",
        "total_trades",
        "average_trades",
        "average_return_pct",
        "median_return_pct",
        "average_sharpe",
        "median_sharpe",
        "average_drawdown_pct",
        "average_profit_factor",
        "average_expectancy_pct",
    ]

    print(
        summary_df[
            columns
        ].to_string(
            index=False,
            float_format=(
                lambda value:
                f"{value:.2f}"
            ),
        )
    )

    best = summary_df.iloc[0]

    print()
    print(
        f"Best strategy: "
        f"{best['strategy']} | "
        f"Avg Sharpe "
        f"{best['average_sharpe']:.2f} | "
        f"Avg Return "
        f"{best['average_return_pct']:+.2f}% | "
        f"Median Return "
        f"{best['median_return_pct']:+.2f}% | "
        f"Positive "
        f"{int(best['positive_symbols'])}/"
        f"{int(best['symbols'])}"
    )


def print_strategy_winners(
    *,
    winner_df: pd.DataFrame,
    winner_count_df: pd.DataFrame,
) -> None:
    print()
    print("=" * 130)
    print(
        "STRATEGY WINNER MATRIX"
    )
    print("=" * 130)

    if winner_df.empty:
        print(
            "Không có winner data."
        )
        return

    print(
        winner_df.to_string(
            index=False,
            float_format=(
                lambda value:
                f"{value:.2f}"
            ),
        )
    )

    print()
    print("=" * 80)
    print(
        "STRATEGY WINS BY SYMBOL"
    )
    print("=" * 80)

    print(
        winner_count_df.to_string(
            index=False,
            float_format=(
                lambda value:
                f"{value:.2f}"
            ),
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark ma trận "
            "Entry Model × Exit Model."
        )
    )

    parser.add_argument(
        "--symbol",
        nargs="+",
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
        default=(
            DEFAULT_DETAIL_OUTPUT
        ),
    )

    parser.add_argument(
        "--summary-output",
        default=(
            DEFAULT_SUMMARY_OUTPUT
        ),
    )

    parser.add_argument(
        "--winner-output",
        default=(
            DEFAULT_WINNER_OUTPUT
        ),
    )

    parser.add_argument(
        "--winner-count-output",
        default=(
            DEFAULT_WINNER_COUNT_OUTPUT
        ),
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

    benchmark_strategy_matrix(
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