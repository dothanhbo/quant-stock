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
from research.walk_forward import (
    WalkForwardWindow,
    build_walk_forward_windows,
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


DEFAULT_TRAIN_OUTPUT = (
    "research_results/"
    "strategy_wfo_train_results.csv"
)

DEFAULT_SELECTED_OUTPUT = (
    "research_results/"
    "strategy_wfo_selected_strategies.csv"
)

DEFAULT_TEST_OUTPUT = (
    "research_results/"
    "strategy_wfo_test_results.csv"
)

DEFAULT_SUMMARY_OUTPUT = (
    "research_results/"
    "strategy_wfo_summary.csv"
)


def build_strategy_configs(
) -> list[dict[str, Any]]:
    return [
        {
            "strategy": (
                "trend_v1__atr"
            ),
            "entry_model": (
                "trend_v1"
            ),
            "exit_model": "atr",
            "break_even_trigger": 5.0,
            "atr_stop_multiplier": 2.0,
            "atr_target_multiplier": 4.0,
            "trailing_atr_multiplier": 2.0,
        },
        {
            "strategy": (
                "trend_v1__trailing_atr"
            ),
            "entry_model": (
                "trend_v1"
            ),
            "exit_model": (
                "trailing_atr"
            ),
            "break_even_trigger": 5.0,
            "atr_stop_multiplier": 2.5,
            "atr_target_multiplier": 5.0,
            "trailing_atr_multiplier": 2.5,
        },
        {
            "strategy": (
                "donchian_breakout_v1__atr"
            ),
            "entry_model": (
                "donchian_breakout_v1"
            ),
            "exit_model": "atr",
            "break_even_trigger": 5.0,
            "atr_stop_multiplier": 2.0,
            "atr_target_multiplier": 4.0,
            "trailing_atr_multiplier": 2.0,
        },
        {
            "strategy": (
                "donchian_breakout_v1__trailing_atr"
            ),
            "entry_model": (
                "donchian_breakout_v1"
            ),
            "exit_model": (
                "trailing_atr"
            ),
            "break_even_trigger": 5.0,
            "atr_stop_multiplier": 2.5,
            "atr_target_multiplier": 5.0,
            "trailing_atr_multiplier": 2.5,
        },
    ]


def build_entry_model(
    name: str,
) -> BaseStrategy:
    normalized = (
        name.strip().lower()
    )

    if normalized == "trend_v1":
        return TrendStrategyV1()

    if normalized == (
        "donchian_breakout_v1"
    ):
        return (
            DonchianBreakoutEntryModel()
        )

    raise ValueError(
        "Entry model không được hỗ trợ: "
        f"{name}"
    )


def evaluate_strategy_window(
    *,
    symbols: list[str],
    window_number: int,
    period_type: str,
    period_start: str,
    period_end: str,
    strategy_config: dict[str, Any],
    stop_loss_pct: float,
    take_profit_pct: float,
    max_holding_days: int,
    min_adx: float,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
]:
    entry_model_name = str(
        strategy_config[
            "entry_model"
        ]
    )

    exit_model_name = str(
        strategy_config[
            "exit_model"
        ]
    )

    symbol_rows: list[
        dict[str, Any]
    ] = []

    for symbol in symbols:
        entry_model = build_entry_model(
            entry_model_name
        )

        exit_model = build_exit_model(
            name=exit_model_name,
            break_even_trigger=float(
                strategy_config[
                    "break_even_trigger"
                ]
            ),
            stop_atr_multiplier=float(
                strategy_config[
                    "atr_stop_multiplier"
                ]
            ),
            target_atr_multiplier=float(
                strategy_config[
                    "atr_target_multiplier"
                ]
            ),
            trailing_atr_multiplier=float(
                strategy_config[
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
            start_date=period_start,
            end_date=period_end,
            verbose=False,
            entry_model=entry_model,
            exit_model=exit_model,
        )

        symbol_rows.append(
            {
                "window_number": (
                    window_number
                ),
                "period_type": (
                    period_type
                ),
                "period_start": (
                    period_start
                ),
                "period_end": period_end,
                "symbol": symbol,
                "strategy": (
                    strategy_config[
                        "strategy"
                    ]
                ),
                "entry_model": (
                    entry_model_name
                ),
                "exit_model": (
                    exit_model_name
                ),
                "break_even_trigger": (
                    strategy_config[
                        "break_even_trigger"
                    ]
                ),
                "atr_stop_multiplier": (
                    strategy_config[
                        "atr_stop_multiplier"
                    ]
                ),
                "atr_target_multiplier": (
                    strategy_config[
                        "atr_target_multiplier"
                    ]
                ),
                "trailing_atr_multiplier": (
                    strategy_config[
                        "trailing_atr_multiplier"
                    ]
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
            }
        )

    symbol_df = pd.DataFrame(
        symbol_rows
    )

    returns = numeric_series(
        symbol_df,
        "total_return_pct",
    )

    sharpes = numeric_series(
        symbol_df,
        "sharpe_ratio",
    )

    profit_factors = numeric_series(
        symbol_df,
        "profit_factor",
    )

    drawdowns = numeric_series(
        symbol_df,
        "max_drawdown_pct",
    )

    expectancies = numeric_series(
        symbol_df,
        "expectancy_pct",
    )

    trades = (
        pd.to_numeric(
            symbol_df[
                "total_trades"
            ],
            errors="coerce",
        )
        .fillna(0)
        .astype(int)
    )

    aggregate_row = {
        "window_number": (
            window_number
        ),
        "period_type": (
            period_type
        ),
        "period_start": (
            period_start
        ),
        "period_end": period_end,
        "strategy": (
            strategy_config[
                "strategy"
            ]
        ),
        "entry_model": (
            entry_model_name
        ),
        "exit_model": (
            exit_model_name
        ),
        "break_even_trigger": (
            strategy_config[
                "break_even_trigger"
            ]
        ),
        "atr_stop_multiplier": (
            strategy_config[
                "atr_stop_multiplier"
            ]
        ),
        "atr_target_multiplier": (
            strategy_config[
                "atr_target_multiplier"
            ]
        ),
        "trailing_atr_multiplier": (
            strategy_config[
                "trailing_atr_multiplier"
            ]
        ),
        "symbols": len(
            symbol_df
        ),
        "positive_symbols": int(
            (returns > 0).sum()
        ),
        "qualified_symbols": int(
            (trades >= 10).sum()
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
        "average_sharpe": float(
            mean(sharpes)
        ),
        "median_sharpe": float(
            sharpes.median()
        ),
        "average_profit_factor": float(
            mean(profit_factors)
        ),
        "average_drawdown_pct": float(
            mean(drawdowns)
        ),
        "average_expectancy_pct": float(
            mean(expectancies)
        ),
    }

    return (
        aggregate_row,
        symbol_rows,
    )


def select_best_strategy(
    window_train_df: pd.DataFrame,
) -> pd.Series:
    if window_train_df.empty:
        raise ValueError(
            "Không có train result."
        )

    ranked = (
        window_train_df
        .sort_values(
            by=[
                "median_sharpe",
                "median_return_pct",
                "average_sharpe",
                "average_profit_factor",
                "positive_symbols",
                "average_drawdown_pct",
            ],
            ascending=[
                False,
                False,
                False,
                False,
                False,
                False,
            ],
        )
        .reset_index(drop=True)
    )

    return ranked.iloc[0]


def run_strategy_walk_forward(
    *,
    symbols: list[str],
    windows: list[
        WalkForwardWindow
    ],
    stop_loss_pct: float,
    take_profit_pct: float,
    max_holding_days: int,
    min_adx: float,
    train_output_path: str,
    selected_output_path: str,
    test_output_path: str,
    summary_output_path: str,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    strategy_configs = (
        build_strategy_configs()
    )

    train_rows: list[
        dict[str, Any]
    ] = []

    selected_rows: list[
        dict[str, Any]
    ] = []

    test_symbol_rows: list[
        dict[str, Any]
    ] = []

    test_aggregate_rows: list[
        dict[str, Any]
    ] = []

    total_train_evaluations = (
        len(windows)
        * len(strategy_configs)
    )

    completed = 0
    started_at = perf_counter()

    print(
        f"Chạy {len(windows)} window(s) × "
        f"{len(strategy_configs)} strategy "
        f"= {total_train_evaluations} "
        f"train evaluations."
    )

    for window in windows:
        print()
        print("=" * 100)
        print(
            f"WINDOW "
            f"{window.window_number} | "
            f"TRAIN "
            f"{window.train_start} "
            f"→ {window.train_end}"
        )
        print("=" * 100)

        window_train_rows: list[
            dict[str, Any]
        ] = []

        for strategy_config in (
            strategy_configs
        ):
            completed += 1

            row, _ = (
                evaluate_strategy_window(
                    symbols=symbols,
                    window_number=(
                        window.window_number
                    ),
                    period_type="train",
                    period_start=(
                        window.train_start
                    ),
                    period_end=(
                        window.train_end
                    ),
                    strategy_config=(
                        strategy_config
                    ),
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
                )
            )

            train_rows.append(row)
            window_train_rows.append(
                row
            )

            elapsed = (
                perf_counter()
                - started_at
            )

            average_seconds = (
                elapsed / completed
            )

            remaining = (
                total_train_evaluations
                - completed
            )

            eta_seconds = (
                average_seconds
                * remaining
            )

            print(
                f"[{completed}/"
                f"{total_train_evaluations}] "
                f"{row['strategy']} | "
                f"Median Sharpe "
                f"{row['median_sharpe']:.2f} | "
                f"Median Return "
                f"{row['median_return_pct']:+.2f}% | "
                f"Positive "
                f"{row['positive_symbols']}/"
                f"{row['symbols']} | "
                f"Trades "
                f"{row['total_trades']} | "
                f"ETA "
                f"{format_duration(eta_seconds)}"
            )

        window_train_df = (
            pd.DataFrame(
                window_train_rows
            )
        )

        best = select_best_strategy(
            window_train_df
        )

        selected_config = (
            find_strategy_config(
                strategy_configs,
                str(
                    best[
                        "strategy"
                    ]
                ),
            )
        )

        selected_row = {
            **best.to_dict(),
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
        }

        selected_rows.append(
            selected_row
        )

        print()
        print(
            f"BEST TRAIN STRATEGY "
            f"WINDOW "
            f"{window.window_number}: "
            f"{best['strategy']} | "
            f"Median Sharpe "
            f"{best['median_sharpe']:.2f} | "
            f"Median Return "
            f"{best['median_return_pct']:+.2f}%"
        )

        print(
            f"RUN TEST "
            f"{window.test_start} "
            f"→ {window.test_end}"
        )

        (
            test_aggregate,
            symbol_rows,
        ) = evaluate_strategy_window(
            symbols=symbols,
            window_number=(
                window.window_number
            ),
            period_type="test",
            period_start=(
                window.test_start
            ),
            period_end=(
                window.test_end
            ),
            strategy_config=(
                selected_config
            ),
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
        )

        test_aggregate[
            "train_strategy"
        ] = best["strategy"]

        test_aggregate[
            "train_median_sharpe"
        ] = best[
            "median_sharpe"
        ]

        test_aggregate[
            "train_median_return_pct"
        ] = best[
            "median_return_pct"
        ]

        test_aggregate[
            "train_average_sharpe"
        ] = best[
            "average_sharpe"
        ]

        test_aggregate[
            "train_average_return_pct"
        ] = best[
            "average_return_pct"
        ]

        test_aggregate_rows.append(
            test_aggregate
        )

        for symbol_row in symbol_rows:
            symbol_row[
                "train_start"
            ] = window.train_start

            symbol_row[
                "train_end"
            ] = window.train_end

            symbol_row[
                "test_start"
            ] = window.test_start

            symbol_row[
                "test_end"
            ] = window.test_end

            test_symbol_rows.append(
                symbol_row
            )

        print(
            f"TEST RESULT | "
            f"{test_aggregate['strategy']} | "
            f"Avg Return "
            f"{test_aggregate['average_return_pct']:+.2f}% | "
            f"Median Return "
            f"{test_aggregate['median_return_pct']:+.2f}% | "
            f"Avg Sharpe "
            f"{test_aggregate['average_sharpe']:.2f} | "
            f"Positive "
            f"{test_aggregate['positive_symbols']}/"
            f"{test_aggregate['symbols']}"
        )

    train_df = pd.DataFrame(
        train_rows
    )

    selected_df = pd.DataFrame(
        selected_rows
    )

    test_df = pd.DataFrame(
        test_symbol_rows
    )

    summary_df = (
        build_walk_forward_summary(
            pd.DataFrame(
                test_aggregate_rows
            )
        )
    )

    save_outputs(
        train_df=train_df,
        selected_df=selected_df,
        test_df=test_df,
        summary_df=summary_df,
        train_output_path=(
            train_output_path
        ),
        selected_output_path=(
            selected_output_path
        ),
        test_output_path=(
            test_output_path
        ),
        summary_output_path=(
            summary_output_path
        ),
    )

    print_selected_strategies(
        selected_df
    )

    print_walk_forward_summary(
        summary_df
    )

    return (
        train_df,
        selected_df,
        test_df,
        summary_df,
    )


def build_walk_forward_summary(
    test_aggregate_df: pd.DataFrame,
) -> pd.DataFrame:
    if test_aggregate_df.empty:
        return pd.DataFrame()

    summary_df = (
        test_aggregate_df.copy()
    )

    summary_df[
        "sharpe_gap"
    ] = (
        summary_df[
            "average_sharpe"
        ]
        - summary_df[
            "train_average_sharpe"
        ]
    )

    summary_df[
        "median_sharpe_gap"
    ] = (
        summary_df[
            "median_sharpe"
        ]
        - summary_df[
            "train_median_sharpe"
        ]
    )

    summary_df[
        "return_gap_pct"
    ] = (
        summary_df[
            "average_return_pct"
        ]
        - summary_df[
            "train_average_return_pct"
        ]
    )

    columns = [
        "window_number",
        "strategy",
        "entry_model",
        "exit_model",
        "period_start",
        "period_end",
        "symbols",
        "positive_symbols",
        "qualified_symbols",
        "total_trades",
        "average_return_pct",
        "median_return_pct",
        "average_sharpe",
        "median_sharpe",
        "average_profit_factor",
        "average_drawdown_pct",
        "train_average_return_pct",
        "train_median_return_pct",
        "train_average_sharpe",
        "train_median_sharpe",
        "return_gap_pct",
        "sharpe_gap",
        "median_sharpe_gap",
    ]

    return (
        summary_df[
            columns
        ]
        .sort_values(
            "window_number"
        )
        .reset_index(drop=True)
    )


def find_strategy_config(
    configs: list[
        dict[str, Any]
    ],
    strategy_name: str,
) -> dict[str, Any]:
    for config in configs:
        if (
            config["strategy"]
            == strategy_name
        ):
            return config

    raise ValueError(
        "Không tìm thấy strategy config: "
        f"{strategy_name}"
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


def save_dataframe(
    dataframe: pd.DataFrame,
    output_path: str,
) -> None:
    output = Path(
        output_path
    )

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
        f"Đã xuất: {output}"
    )


def save_outputs(
    *,
    train_df: pd.DataFrame,
    selected_df: pd.DataFrame,
    test_df: pd.DataFrame,
    summary_df: pd.DataFrame,
    train_output_path: str,
    selected_output_path: str,
    test_output_path: str,
    summary_output_path: str,
) -> None:
    save_dataframe(
        train_df,
        train_output_path,
    )

    save_dataframe(
        selected_df,
        selected_output_path,
    )

    save_dataframe(
        test_df,
        test_output_path,
    )

    save_dataframe(
        summary_df,
        summary_output_path,
    )


def print_selected_strategies(
    selected_df: pd.DataFrame,
) -> None:
    print()
    print("=" * 140)
    print(
        "SELECTED TRAIN STRATEGIES"
    )
    print("=" * 140)

    if selected_df.empty:
        print(
            "Không có selected strategy."
        )
        return

    columns = [
        "window_number",
        "train_start",
        "train_end",
        "test_start",
        "test_end",
        "strategy",
        "entry_model",
        "exit_model",
        "total_trades",
        "positive_symbols",
        "median_return_pct",
        "median_sharpe",
        "average_return_pct",
        "average_sharpe",
    ]

    print(
        selected_df[
            columns
        ].to_string(
            index=False,
            float_format=(
                lambda value:
                f"{value:.2f}"
            ),
        )
    )


def print_walk_forward_summary(
    summary_df: pd.DataFrame,
) -> None:
    print()
    print("=" * 180)
    print(
        "STRATEGY WALK-FORWARD SUMMARY"
    )
    print("=" * 180)

    if summary_df.empty:
        print(
            "Không có WFO summary."
        )
        return

    print(
        summary_df.to_string(
            index=False,
            float_format=(
                lambda value:
                f"{value:.2f}"
            ),
        )
    )

    positive_windows = int(
        (
            summary_df[
                "average_return_pct"
            ] > 0
        ).sum()
    )

    print()
    print(
        f"Positive windows : "
        f"{positive_windows}/"
        f"{len(summary_df)}"
    )

    print(
        f"Average OOS Return: "
        f"{summary_df['average_return_pct'].mean():+.2f}%"
    )

    print(
        f"Median OOS Return : "
        f"{summary_df['median_return_pct'].median():+.2f}%"
    )

    print(
        f"Average OOS Sharpe: "
        f"{summary_df['average_sharpe'].mean():.2f}"
    )

    print(
        f"Average OOS PF    : "
        f"{summary_df['average_profit_factor'].mean():.2f}"
    )

    print(
        f"Average Sharpe Gap: "
        f"{summary_df['sharpe_gap'].mean():+.2f}"
    )

    print(
        f"Average Return Gap: "
        f"{summary_df['return_gap_pct'].mean():+.2f}%"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Walk-Forward Selection "
            "cho Entry × Exit Strategy."
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
        default=2025,
    )

    parser.add_argument(
        "--train-years",
        type=int,
        default=4,
    )

    parser.add_argument(
        "--test-years",
        type=int,
        default=2,
    )

    parser.add_argument(
        "--step-years",
        type=int,
        default=1,
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
        "--train-output",
        default=(
            DEFAULT_TRAIN_OUTPUT
        ),
    )

    parser.add_argument(
        "--selected-output",
        default=(
            DEFAULT_SELECTED_OUTPUT
        ),
    )

    parser.add_argument(
        "--test-output",
        default=(
            DEFAULT_TEST_OUTPUT
        ),
    )

    parser.add_argument(
        "--summary-output",
        default=(
            DEFAULT_SUMMARY_OUTPUT
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

    windows = (
        build_walk_forward_windows(
            start_year=(
                args.start_year
            ),
            end_year=args.end_year,
            train_years=(
                args.train_years
            ),
            test_years=(
                args.test_years
            ),
            step_years=(
                args.step_years
            ),
        )
    )

    if not windows:
        raise ValueError(
            "Không tạo được "
            "Walk-Forward Window."
        )

    run_strategy_walk_forward(
        symbols=symbols,
        windows=windows,
        stop_loss_pct=args.sl,
        take_profit_pct=args.tp,
        max_holding_days=args.hold,
        min_adx=args.min_adx,
        train_output_path=(
            args.train_output
        ),
        selected_output_path=(
            args.selected_output
        ),
        test_output_path=(
            args.test_output
        ),
        summary_output_path=(
            args.summary_output
        ),
    )


if __name__ == "__main__":
    main()