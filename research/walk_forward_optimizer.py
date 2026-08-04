from __future__ import annotations

import argparse
from dataclasses import asdict
from itertools import product
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
from research.walk_forward import (
    WalkForwardConfig,
    WalkForwardWindow,
    build_walk_forward_windows,
)
from typing import (
    Any,
    Callable,
)

DEFAULT_OUTPUT = (
    "research_results/"
    "walk_forward_train_selection.csv"
)


ATR_STOP_VALUES = (
    2.0,
    2.5,
)

ATR_TARGET_VALUES = (
    4.0,
    5.0,
)

TRAILING_ATR_VALUES = (
    2.0,
    2.5,
    3.0,
)

TrainCandidateRunner = Callable[
    [
        str,
        WalkForwardWindow,
        dict[str, Any],
    ],
    dict[str, Any],
]


def generate_exit_parameter_sets() -> list[dict]:
    return [
        {
            "atr_stop_multiplier": atr_stop,
            "atr_target_multiplier": atr_target,
            "trailing_atr_multiplier": trailing_atr,
        }
        for (
            atr_stop,
            atr_target,
            trailing_atr,
        ) in product(
            ATR_STOP_VALUES,
            ATR_TARGET_VALUES,
            TRAILING_ATR_VALUES,
        )
    ]

def summarize_train_symbol_rows(
    *,
    symbol_rows: list[dict],
    window: WalkForwardWindow,
    candidate_parameters: dict,
) -> dict:
    """
    Tổng hợp kết quả train của nhiều symbol.

    Hàm này không phụ thuộc loại candidate được tối ưu:
    - Exit parameters
    - Composite weights
    - Position sizing
    - Regime policy
    """

    if not symbol_rows:
        raise ValueError(
            "symbol_rows không có dữ liệu."
        )

    symbol_df = pd.DataFrame(
        symbol_rows
    )

    required_columns = {
        "symbol",
        "total_trades",
        "total_return_pct",
        "sharpe_ratio",
        "profit_factor",
        "max_drawdown_pct",
    }

    missing_columns = (
        required_columns
        - set(symbol_df.columns)
    )

    if missing_columns:
        raise ValueError(
            "Thiếu cột train metrics: "
            + ", ".join(
                sorted(missing_columns)
            )
        )

    returns = pd.to_numeric(
        symbol_df[
            "total_return_pct"
        ],
        errors="coerce",
    ).fillna(0.0)

    sharpes = pd.to_numeric(
        symbol_df[
            "sharpe_ratio"
        ],
        errors="coerce",
    ).fillna(0.0)

    profit_factors = pd.to_numeric(
        symbol_df[
            "profit_factor"
        ],
        errors="coerce",
    ).fillna(0.0)

    drawdowns = pd.to_numeric(
        symbol_df[
            "max_drawdown_pct"
        ],
        errors="coerce",
    ).fillna(0.0)

    trades = pd.to_numeric(
        symbol_df[
            "total_trades"
        ],
        errors="coerce",
    ).fillna(0).astype(int)

    positive_symbols = int(
        (
            returns > 0
        ).sum()
    )

    qualified_symbols = int(
        (
            trades >= 10
        ).sum()
    )

    return {
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
        **candidate_parameters,
        "symbols": len(
            symbol_df
        ),
        "positive_symbols": (
            positive_symbols
        ),
        "qualified_symbols": (
            qualified_symbols
        ),
        "total_trades": int(
            trades.sum()
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
    }

def evaluate_train_candidate(
    *,
    symbols: list[str],
    window: WalkForwardWindow,
    candidate_parameters: dict[str, Any],
    candidate_runner: TrainCandidateRunner,
) -> dict[str, Any]:
    symbol_rows: list[
        dict[str, Any]
    ] = []

    for symbol in symbols:
        metrics_row = candidate_runner(
            symbol,
            window,
            candidate_parameters,
        )

        symbol_rows.append(
            {
                "symbol": symbol,
                "total_trades": (
                    metrics_row.get(
                        "total_trades",
                        0,
                    )
                ),
                "total_return_pct": (
                    metrics_row.get(
                        "total_return_pct",
                        0.0,
                    )
                ),
                "sharpe_ratio": (
                    metrics_row.get(
                        "sharpe_ratio",
                        0.0,
                    )
                ),
                "profit_factor": (
                    metrics_row.get(
                        "profit_factor",
                        0.0,
                    )
                ),
                "max_drawdown_pct": (
                    metrics_row.get(
                        "max_drawdown_pct",
                        0.0,
                    )
                ),
            }
        )

    return summarize_train_symbol_rows(
        symbol_rows=symbol_rows,
        window=window,
        candidate_parameters=(
            candidate_parameters
        ),
    )

def evaluate_train_parameter_set(
    *,
    symbols: list[str],
    window: WalkForwardWindow,
    parameters: dict,
    stop_loss_pct: float,
    take_profit_pct: float,
    max_holding_days: int,
    min_adx: float,
) -> dict:
    def run_exit_candidate(
        symbol: str,
        candidate_window: WalkForwardWindow,
        candidate_parameters: dict[str, Any],
    ) -> dict[str, Any]:
        exit_model = build_exit_model(
            name="trailing_atr",
            stop_atr_multiplier=(
                candidate_parameters[
                    "atr_stop_multiplier"
                ]
            ),
            target_atr_multiplier=(
                candidate_parameters[
                    "atr_target_multiplier"
                ]
            ),
            trailing_atr_multiplier=(
                candidate_parameters[
                    "trailing_atr_multiplier"
                ]
            ),
        )

        _, metrics, _ = run_backtest(
            symbols=[
                symbol
            ],
            stop_loss_pct=(
                stop_loss_pct
            ),
            take_profit_pct=(
                take_profit_pct
            ),
            max_holding_days=(
                max_holding_days
            ),
            min_adx=(
                min_adx
            ),
            start_date=(
                candidate_window.train_start
            ),
            end_date=(
                candidate_window.train_end
            ),
            verbose=False,
            exit_model=exit_model,
        )

        return metrics

    return evaluate_train_candidate(
        symbols=symbols,
        window=window,
        candidate_parameters=parameters,
        candidate_runner=(
            run_exit_candidate
        ),
    )

def select_best_parameter_set(
    train_results: pd.DataFrame,
) -> pd.Series:
    if train_results.empty:
        raise ValueError(
            "Không có kết quả train."
        )

    ranked = train_results.sort_values(
        by=[
            "median_sharpe",
            "median_return_pct",
            "average_sharpe",
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
    ).reset_index(drop=True)

    return ranked.iloc[0]


def run_train_selection(
    *,
    symbols: list[str],
    windows: list[WalkForwardWindow],
    stop_loss_pct: float,
    take_profit_pct: float,
    max_holding_days: int,
    min_adx: float,
    output_path: str,
) -> pd.DataFrame:
    parameter_sets = (
        generate_exit_parameter_sets()
    )

    all_rows: list[dict] = []

    total_runs = (
        len(windows)
        * len(parameter_sets)
    )

    completed = 0

    print(
        f"Chạy {len(windows)} window × "
        f"{len(parameter_sets)} bộ tham số "
        f"= {total_runs} train evaluations."
    )

    for window in windows:
        print()
        print("=" * 90)
        print(
            f"WINDOW {window.window_number} | "
            f"TRAIN {window.train_start} "
            f"→ {window.train_end}"
        )
        print("=" * 90)

        window_rows: list[dict] = []

        for parameters in parameter_sets:
            completed += 1

            row = evaluate_train_parameter_set(
                symbols=symbols,
                window=window,
                parameters=parameters,
                stop_loss_pct=stop_loss_pct,
                take_profit_pct=take_profit_pct,
                max_holding_days=max_holding_days,
                min_adx=min_adx,
            )

            window_rows.append(row)
            all_rows.append(row)

            print(
                f"[{completed}/{total_runs}] "
                f"ATR-S="
                f"{row['atr_stop_multiplier']} "
                f"ATR-T="
                f"{row['atr_target_multiplier']} "
                f"Trail="
                f"{row['trailing_atr_multiplier']} | "
                f"Median Sharpe "
                f"{row['median_sharpe']:.2f} | "
                f"Median Return "
                f"{row['median_return_pct']:+.2f}% | "
                f"Trades "
                f"{row['total_trades']}"
            )

        window_df = pd.DataFrame(
            window_rows
        )

        best = select_best_parameter_set(
            window_df
        )

        print()
        print(
            f"BEST WINDOW "
            f"{window.window_number}: "
            f"ATR-S="
            f"{best['atr_stop_multiplier']} | "
            f"ATR-T="
            f"{best['atr_target_multiplier']} | "
            f"Trail="
            f"{best['trailing_atr_multiplier']} | "
            f"Median Sharpe "
            f"{best['median_sharpe']:.2f} | "
            f"Median Return "
            f"{best['median_return_pct']:+.2f}%"
        )

    result_df = pd.DataFrame(
        all_rows
    )

    result_df["selected"] = False

    for window_number in (
        result_df["window_number"]
        .drop_duplicates()
        .tolist()
    ):
        mask = (
            result_df["window_number"]
            == window_number
        )

        best = select_best_parameter_set(
            result_df.loc[mask].copy()
        )

        selected_mask = (
            mask
            & (
                result_df[
                    "atr_stop_multiplier"
                ]
                == best[
                    "atr_stop_multiplier"
                ]
            )
            & (
                result_df[
                    "atr_target_multiplier"
                ]
                == best[
                    "atr_target_multiplier"
                ]
            )
            & (
                result_df[
                    "trailing_atr_multiplier"
                ]
                == best[
                    "trailing_atr_multiplier"
                ]
            )
        )

        result_df.loc[
            selected_mask,
            "selected",
        ] = True

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

    selected_df = result_df[
        result_df["selected"]
    ].copy()

    print()
    print("=" * 120)
    print("SELECTED TRAIN PARAMETERS")
    print("=" * 120)

    print(
        selected_df[
            [
                "window_number",
                "train_start",
                "train_end",
                "test_start",
                "test_end",
                "atr_stop_multiplier",
                "atr_target_multiplier",
                "trailing_atr_multiplier",
                "total_trades",
                "median_return_pct",
                "median_sharpe",
                "average_return_pct",
                "average_sharpe",
            ]
        ].to_string(
            index=False,
            float_format=lambda value: (
                f"{value:.2f}"
            ),
        )
    )

    print()
    print(f"Đã xuất: {output}")

    return result_df

def run_selected_test_windows(
    *,
    train_df: pd.DataFrame,
    symbols: list[str],
    stop_loss_pct: float,
    take_profit_pct: float,
    max_holding_days: int,
    min_adx: float,
) -> pd.DataFrame:
    selected_df = (
        train_df[
            train_df["selected"]
        ]
        .copy()
        .sort_values(
            "window_number"
        )
    )

    rows: list[dict] = []

    total = (
        len(selected_df)
        * len(symbols)
    )

    completed = 0

    print()
    print("=" * 90)
    print(
        "RUN SELECTED TEST WINDOWS"
    )
    print("=" * 90)

    for _, train_row in (
        selected_df.iterrows()
    ):
        print()
        print(
            f"WINDOW "
            f"{int(train_row['window_number'])}"
        )
        print(
            f"TEST "
            f"{train_row['test_start']} "
            f"→ "
            f"{train_row['test_end']}"
        )
        print(
            f"ATR-S="
            f"{train_row['atr_stop_multiplier']} | "
            f"ATR-T="
            f"{train_row['atr_target_multiplier']} | "
            f"Trail="
            f"{train_row['trailing_atr_multiplier']}"
        )

        exit_model = build_exit_model(
            name="trailing_atr",
            stop_atr_multiplier=(
                train_row[
                    "atr_stop_multiplier"
                ]
            ),
            target_atr_multiplier=(
                train_row[
                    "atr_target_multiplier"
                ]
            ),
            trailing_atr_multiplier=(
                train_row[
                    "trailing_atr_multiplier"
                ]
            ),
        )

        for symbol in symbols:
            completed += 1

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
                min_adx=(
                    min_adx
                ),
                start_date=(
                    train_row[
                        "test_start"
                    ]
                ),
                end_date=(
                    train_row[
                        "test_end"
                    ]
                ),
                verbose=False,
                exit_model=exit_model,
            )

            row = {
                "window_number":
                    int(
                        train_row[
                            "window_number"
                        ]
                    ),

                "train_start":
                    train_row[
                        "train_start"
                    ],

                "train_end":
                    train_row[
                        "train_end"
                    ],

                "test_start":
                    train_row[
                        "test_start"
                    ],

                "test_end":
                    train_row[
                        "test_end"
                    ],

                "symbol":
                    symbol,

                "atr_stop_multiplier":
                    train_row[
                        "atr_stop_multiplier"
                    ],

                "atr_target_multiplier":
                    train_row[
                        "atr_target_multiplier"
                    ],

                "trailing_atr_multiplier":
                    train_row[
                        "trailing_atr_multiplier"
                    ],

                "total_trades":
                    metrics.get(
                        "total_trades",
                        0,
                    ),

                "total_return_pct":
                    metrics.get(
                        "total_return_pct",
                        0.0,
                    ),

                "cagr_pct":
                    metrics.get(
                        "cagr_pct",
                        0.0,
                    ),

                "max_drawdown_pct":
                    metrics.get(
                        "max_drawdown_pct",
                        0.0,
                    ),

                "sharpe_ratio":
                    metrics.get(
                        "sharpe_ratio",
                        0.0,
                    ),

                "sortino_ratio":
                    metrics.get(
                        "sortino_ratio",
                        0.0,
                    ),

                "profit_factor":
                    metrics.get(
                        "profit_factor",
                        0.0,
                    ),

                "win_rate_pct":
                    metrics.get(
                        "win_rate_pct",
                        0.0,
                    ),

                "expectancy_pct":
                    metrics.get(
                        "expectancy_pct",
                        0.0,
                    ),
            }

            rows.append(
                row
            )

            print(
                f"[{completed}/{total}] "
                f"W"
                f"{row['window_number']} "
                f"{symbol} | "
                f"Trades "
                f"{row['total_trades']} | "
                f"Return "
                f"{row['total_return_pct']:+.2f}% | "
                f"Sharpe "
                f"{row['sharpe_ratio']:.2f}"
            )

    test_df = pd.DataFrame(
        rows
    )

    return test_df

def build_test_summary(
    test_df: pd.DataFrame,
) -> pd.DataFrame:
    if test_df.empty:
        raise ValueError(
            "Không có test results."
        )

    summary = (
        test_df
        .groupby(
            "window_number",
            as_index=False,
        )
        .agg(
            train_start=(
                "train_start",
                "first",
            ),
            train_end=(
                "train_end",
                "first",
            ),
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
            positive_symbols=(
                "total_return_pct",
                lambda values: int(
                    (
                        values > 0
                    ).sum()
                ),
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
            median_sharpe=(
                "sharpe_ratio",
                "median",
            ),
            average_profit_factor=(
                "profit_factor",
                "mean",
            ),
            average_drawdown_pct=(
                "max_drawdown_pct",
                "mean",
            ),
        )
    )

    return summary

def print_test_summary(
    summary_df: pd.DataFrame,
) -> None:
    print()

    print("=" * 120)

    print(
        "WALK FORWARD TEST SUMMARY"
    )

    print("=" * 120)

    print(
        summary_df.to_string(
            index=False,
            float_format=lambda value:
                f"{value:.2f}"
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
        f"Average Return   : "
        f"{summary_df['average_return_pct'].mean():+.2f}%"
    )

    print(
        f"Median Return    : "
        f"{summary_df['median_return_pct'].median():+.2f}%"
    )

    print(
        f"Average Sharpe   : "
        f"{summary_df['average_sharpe'].mean():.2f}"
    )

    print(
        f"Average PF       : "
        f"{summary_df['average_profit_factor'].mean():.2f}"
    )

    print(
        f"Average DD       : "
        f"{summary_df['average_drawdown_pct'].mean():.2f}%"
    )

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Walk-Forward Optimization "
            "Phase 1: chọn tham số trên train."
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

    walk_forward_config = WalkForwardConfig(
        start_date=(
            f"{args.start_year}-01-01"
        ),
        end_date=(
            f"{args.end_year}-12-31"
        ),
        train_years=(
            args.train_years
        ),
        test_months=(
            args.test_years * 12
        ),
        step_months=(
            args.step_years * 12
        ),
        anchored=False,
    )

    windows = build_walk_forward_windows(
        walk_forward_config
    )

    if not windows:
        raise ValueError(
            "Không tạo được window."
        )

    train_df = run_train_selection(
        symbols=symbols,
        windows=windows,
        stop_loss_pct=args.sl,
        take_profit_pct=args.tp,
        max_holding_days=args.hold,
        min_adx=args.min_adx,
        output_path=args.output,
    )

    test_df = run_selected_test_windows(
        train_df=train_df,
        symbols=symbols,
        stop_loss_pct=args.sl,
        take_profit_pct=args.tp,
        max_holding_days=args.hold,
        min_adx=args.min_adx,
    )

    summary_df = build_test_summary(
        test_df
    )

    test_output = Path(
        "research_results/"
        "wfo_selected_test_results.csv"
    )

    summary_output = Path(
        "research_results/"
        "wfo_summary.csv"
    )

    test_output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    summary_output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    test_df.to_csv(
        test_output,
        index=False,
        encoding="utf-8-sig",
    )

    summary_df.to_csv(
        summary_output,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print("=" * 90)
    print("SELECTED TEST RESULTS")
    print("=" * 90)

    print(
        test_df.head(
            20
        ).to_string(
            index=False,
            float_format=lambda value: (
                f"{value:.2f}"
            ),
        )
    )

    print_test_summary(
        summary_df
    )

    print()
    print(
        f"Đã xuất test results: "
        f"{test_output}"
    )

    print(
        f"Đã xuất summary: "
        f"{summary_output}"
    )

if __name__ == "__main__":
    main()	