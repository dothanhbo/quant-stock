from __future__ import annotations

import argparse
from itertools import product
from pathlib import Path

from research.universes import (
    TOP10_SYMBOLS,
)
from research.walk_forward import (
    WalkForwardConfig,
    WalkForwardWindow,
    build_walk_forward_windows,
)
from typing import Any

import pandas as pd

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
from research.benchmark_composite_weights import (
    build_composite_allocator,
)
from research.benchmark_portfolio_models import (
    build_portfolio_model_registry,
)

DEFAULT_OUTPUT_DIR = Path(
    "research_results/"
    "composite_walk_forward"
)


SIGNAL_WEIGHT_VALUES = (
    0.10,
    0.15,
    0.20,
)

ATR_WEIGHT_VALUES = (
    0.30,
    0.35,
    0.40,
)

STOP_WEIGHT_VALUES = (
    0.30,
    0.35,
    0.40,
)

REGIME_WEIGHT_VALUES = (
    0.10,
    0.15,
    0.20,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Composite Allocator Walk-Forward "
            "Optimization."
        )
    )

    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
    )

    parser.add_argument(
        "--start",
        default="2018-01-01",
    )

    parser.add_argument(
        "--end",
        default="2026-07-31",
    )

    parser.add_argument(
        "--train-years",
        type=int,
        default=4,
    )

    parser.add_argument(
        "--test-months",
        type=int,
        default=12,
    )

    parser.add_argument(
        "--step-months",
        type=int,
        default=12,
    )

    parser.add_argument(
        "--anchored",
        action="store_true",
    )

    parser.add_argument(
        "--aggregation",
        choices=[
            "sum",
            "product",
        ],
        default="sum",
    )

    parser.add_argument(
        "--output",
        default=str(
            DEFAULT_OUTPUT_DIR
        ),
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
        "--maximum-position",
        type=float,
        default=40.0,
    )

    parser.add_argument(
        "--entry-model",
        default=(
            "hybrid_trend_donchian_v1"
            "__trend_context"
        ),
    )

    return parser.parse_args()


def generate_composite_weight_sets(
    *,
    tolerance: float = 1e-9,
) -> list[dict[str, float]]:
    weight_sets: list[
        dict[str, float]
    ] = []

    for (
        signal_weight,
        atr_weight,
        stop_weight,
        regime_weight,
    ) in product(
        SIGNAL_WEIGHT_VALUES,
        ATR_WEIGHT_VALUES,
        STOP_WEIGHT_VALUES,
        REGIME_WEIGHT_VALUES,
    ):
        total_weight = (
            signal_weight
            + atr_weight
            + stop_weight
            + regime_weight
        )

        if abs(
            total_weight - 1.0
        ) > tolerance:
            continue

        weight_sets.append(
            {
                "signal_weight": float(
                    signal_weight
                ),
                "atr_weight": float(
                    atr_weight
                ),
                "stop_weight": float(
                    stop_weight
                ),
                "regime_weight": float(
                    regime_weight
                ),
                "weight_sum": float(
                    total_weight
                ),
            }
        )

    if not weight_sets:
        raise ValueError(
            "Không tạo được tổ hợp "
            "composite weight hợp lệ."
        )

    return weight_sets


def print_window_summary(
    windows: list[
        WalkForwardWindow
    ],
) -> None:
    print()
    print("=" * 110)
    print("COMPOSITE WALK-FORWARD WINDOWS")
    print("=" * 110)

    for window in windows:
        print(
            f"Window "
            f"{window.window_id:>2} | "
            f"Train "
            f"{window.train_start} "
            f"-> "
            f"{window.train_end} | "
            f"OOS "
            f"{window.test_start} "
            f"-> "
            f"{window.test_end}"
        )

def evaluate_train_weight_set(
    *,
    window: WalkForwardWindow,
    symbols: list[str],
    weights: dict[str, float],
    args: argparse.Namespace,
    entry_model: Any,
) -> dict[str, Any]:
    allocator = build_composite_allocator(
        signal_weight=weights[
            "signal_weight"
        ],
        atr_weight=weights[
            "atr_weight"
        ],
        stop_weight=weights[
            "stop_weight"
        ],
        regime_weight=weights[
            "regime_weight"
        ],
        aggregation=args.aggregation,
        maximum_position_pct=(
            args.maximum_position
        ),
    )

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

    trades, metrics, _ = run_backtest(
        symbols=symbols,
        start_date=window.train_start,
        end_date=window.train_end,
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
            RegimePortfolioPolicy()
        ),
        portfolio_allocator=(
            allocator
        ),
        verbose=False,
    )

    return {
        "window_id": window.window_id,
        "train_start": window.train_start,
        "train_end": window.train_end,
        "test_start": window.test_start,
        "test_end": window.test_end,
        **weights,
        "aggregation": args.aggregation,
        "total_trades": len(trades),
        "total_return_pct": float(
            metrics.get(
                "total_return_pct",
                0.0,
            )
        ),
        "sharpe_ratio": float(
            metrics.get(
                "sharpe_ratio",
                0.0,
            )
        ),
        "sortino_ratio": float(
            metrics.get(
                "sortino_ratio",
                0.0,
            )
        ),
        "max_drawdown_pct": float(
            metrics.get(
                "max_drawdown_pct",
                0.0,
            )
        ),
        "profit_factor": float(
            metrics.get(
                "profit_factor",
                0.0,
            )
        ),
        "win_rate_pct": float(
            metrics.get(
                "win_rate_pct",
                0.0,
            )
        ),
        "expectancy_pct": float(
            metrics.get(
                "expectancy_pct",
                0.0,
            )
        ),
    }

def select_best_train_weight(
    window_results: pd.DataFrame,
) -> pd.Series:
    if window_results.empty:
        raise ValueError(
            "Không có kết quả train."
        )

    ranked = window_results.sort_values(
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
    ).reset_index(
        drop=True
    )

    return ranked.iloc[0]

def evaluate_oos_weight_set(
    *,
    selected_row: pd.Series,
    symbols: list[str],
    args: argparse.Namespace,
    entry_model: Any,
) -> dict[str, Any]:
    allocator = build_composite_allocator(
        signal_weight=float(
            selected_row["signal_weight"]
        ),
        atr_weight=float(
            selected_row["atr_weight"]
        ),
        stop_weight=float(
            selected_row["stop_weight"]
        ),
        regime_weight=float(
            selected_row["regime_weight"]
        ),
        aggregation=args.aggregation,
        maximum_position_pct=(
            args.maximum_position
        ),
    )

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
        start_date=str(
            selected_row["test_start"]
        ),
        end_date=str(
            selected_row["test_end"]
        ),
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
            RegimePortfolioPolicy()
        ),
        portfolio_allocator=(
            allocator
        ),
        verbose=False,
    )

    rejected_reasons = (
        metrics.get(
            "rejected_trade_reasons",
            {},
        )
        or {}
    )

    average_heat = 0.0
    maximum_heat = 0.0

    if (
        not equity.empty
        and "portfolio_heat_pct"
        in equity.columns
    ):
        heat_series = pd.to_numeric(
            equity["portfolio_heat_pct"],
            errors="coerce",
        ).dropna()

        if not heat_series.empty:
            average_heat = float(
                heat_series.mean()
            )

            maximum_heat = float(
                heat_series.max()
            )

    return {
        "window_id": int(
            selected_row["window_id"]
        ),
        "train_start": (
            selected_row["train_start"]
        ),
        "train_end": (
            selected_row["train_end"]
        ),
        "test_start": (
            selected_row["test_start"]
        ),
        "test_end": (
            selected_row["test_end"]
        ),
        "aggregation": args.aggregation,
        "signal_weight": float(
            selected_row["signal_weight"]
        ),
        "atr_weight": float(
            selected_row["atr_weight"]
        ),
        "stop_weight": float(
            selected_row["stop_weight"]
        ),
        "regime_weight": float(
            selected_row["regime_weight"]
        ),
        "train_return_pct": float(
            selected_row[
                "total_return_pct"
            ]
        ),
        "train_sharpe_ratio": float(
            selected_row[
                "sharpe_ratio"
            ]
        ),
        "total_trades": len(
            trades
        ),
        "rejected_trades": int(
            sum(
                rejected_reasons.values()
            )
        ),
        "final_equity": float(
            metrics.get(
                "final_equity",
                args.capital,
            )
        ),
        "total_return_pct": float(
            metrics.get(
                "total_return_pct",
                0.0,
            )
        ),
        "cagr_pct": float(
            metrics.get(
                "cagr_pct",
                0.0,
            )
        ),
        "sharpe_ratio": float(
            metrics.get(
                "sharpe_ratio",
                0.0,
            )
        ),
        "sortino_ratio": float(
            metrics.get(
                "sortino_ratio",
                0.0,
            )
        ),
        "max_drawdown_pct": float(
            metrics.get(
                "max_drawdown_pct",
                0.0,
            )
        ),
        "profit_factor": float(
            metrics.get(
                "profit_factor",
                0.0,
            )
        ),
        "win_rate_pct": float(
            metrics.get(
                "win_rate_pct",
                0.0,
            )
        ),
        "expectancy_pct": float(
            metrics.get(
                "expectancy_pct",
                0.0,
            )
        ),
        "average_portfolio_heat_pct": (
            average_heat
        ),
        "maximum_portfolio_heat_pct": (
            maximum_heat
        ),
        "total_transaction_cost": float(
            metrics.get(
                "total_transaction_cost",
                0.0,
            )
        ),
    }

def build_oos_summary(
    oos_df: pd.DataFrame,
) -> pd.DataFrame:
    if oos_df.empty:
        raise ValueError(
            "Không có OOS results."
        )

    summary = pd.DataFrame(
        [
            {
                "aggregation": (
                    oos_df[
                        "aggregation"
                    ].iloc[0]
                ),
                "windows": len(
                    oos_df
                ),
                "positive_windows": int(
                    (
                        oos_df[
                            "total_return_pct"
                        ]
                        > 0
                    ).sum()
                ),
                "positive_window_pct": float(
                    (
                        oos_df[
                            "total_return_pct"
                        ]
                        > 0
                    ).mean()
                    * 100
                ),
                "total_oos_trades": int(
                    oos_df[
                        "total_trades"
                    ].sum()
                ),
                "mean_oos_return_pct": float(
                    oos_df[
                        "total_return_pct"
                    ].mean()
                ),
                "median_oos_return_pct": float(
                    oos_df[
                        "total_return_pct"
                    ].median()
                ),
                "best_oos_return_pct": float(
                    oos_df[
                        "total_return_pct"
                    ].max()
                ),
                "worst_oos_return_pct": float(
                    oos_df[
                        "total_return_pct"
                    ].min()
                ),
                "mean_oos_sharpe": float(
                    oos_df[
                        "sharpe_ratio"
                    ].mean()
                ),
                "median_oos_sharpe": float(
                    oos_df[
                        "sharpe_ratio"
                    ].median()
                ),
                "mean_oos_drawdown_pct": float(
                    oos_df[
                        "max_drawdown_pct"
                    ].mean()
                ),
                "worst_oos_drawdown_pct": float(
                    oos_df[
                        "max_drawdown_pct"
                    ].min()
                ),
                "mean_profit_factor": float(
                    oos_df[
                        "profit_factor"
                    ].mean()
                ),
                "mean_win_rate_pct": float(
                    oos_df[
                        "win_rate_pct"
                    ].mean()
                ),
                "mean_expectancy_pct": float(
                    oos_df[
                        "expectancy_pct"
                    ].mean()
                ),
                "average_portfolio_heat_pct": float(
                    oos_df[
                        "average_portfolio_heat_pct"
                    ].mean()
                ),
                "maximum_portfolio_heat_pct": float(
                    oos_df[
                        "maximum_portfolio_heat_pct"
                    ].max()
                ),
                "total_transaction_cost": float(
                    oos_df[
                        "total_transaction_cost"
                    ].sum()
                ),
            }
        ]
    )

    return summary

def build_selected_weight_frequency(
    selected_df: pd.DataFrame,
) -> pd.DataFrame:
    weight_columns = [
        "signal_weight",
        "atr_weight",
        "stop_weight",
        "regime_weight",
    ]

    rows: list[
        dict[str, Any]
    ] = []

    for parameter in weight_columns:
        counts = (
            selected_df[
                parameter
            ]
            .value_counts()
            .sort_index()
        )

        for value, count in counts.items():
            rows.append(
                {
                    "parameter": parameter,
                    "value": float(
                        value
                    ),
                    "selected_count": int(
                        count
                    ),
                    "selected_pct": float(
                        count
                        / len(selected_df)
                        * 100
                    ),
                }
            )

    return (
        pd.DataFrame(rows)
        .sort_values(
            by=[
                "parameter",
                "selected_count",
                "value",
            ],
            ascending=[
                True,
                False,
                True,
            ],
        )
        .reset_index(
            drop=True
        )
    )

def main() -> None:
    args = parse_args()

    symbols = (
        list(TOP10_SYMBOLS)
        if args.symbols is None
        else [
            symbol
            .upper()
            .strip()
            for symbol in args.symbols
            if symbol.strip()
        ]
    )

    if not symbols:
        raise ValueError(
            "Không có symbol hợp lệ."
        )

    config = WalkForwardConfig(
        start_date=args.start,
        end_date=args.end,
        train_years=(
            args.train_years
        ),
        test_months=(
            args.test_months
        ),
        step_months=(
            args.step_months
        ),
        anchored=args.anchored,
    )

    windows = build_walk_forward_windows(
        config
    )

    weight_sets = (
        generate_composite_weight_sets()
    )

    output_dir = Path(
        args.output
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    print("=" * 110)
    print(
        "COMPOSITE WALK-FORWARD OPTIMIZER"
    )
    print("=" * 110)
    print(
        f"Aggregation    : "
        f"{args.aggregation}"
    )
    print(
        f"Symbols        : "
        f"{len(symbols)}"
    )
    print(
        f"Period         : "
        f"{args.start} -> {args.end}"
    )
    print(
        f"Train window   : "
        f"{args.train_years} years"
    )
    print(
        f"Test window    : "
        f"{args.test_months} months"
    )
    print(
        f"Step           : "
        f"{args.step_months} months"
    )
    print(
        f"Windows        : "
        f"{len(windows)}"
    )
    print(
        f"Weight sets    : "
        f"{len(weight_sets)}"
    )
    print(
        f"Train runs     : "
        f"{len(windows) * len(weight_sets)}"
    )
    print(
        f"Output         : "
        f"{output_dir}"
    )
    print("=" * 110)

    print_window_summary(
        windows
    )

    print()
    print("=" * 110)
    print("COMPOSITE WEIGHT GRID")
    print("=" * 110)

    for index, weights in enumerate(
        weight_sets,
        start=1,
    ):
        print(
            f"[{index:>2}] "
            f"Signal="
            f"{weights['signal_weight']:.2f} | "
            f"ATR="
            f"{weights['atr_weight']:.2f} | "
            f"Stop="
            f"{weights['stop_weight']:.2f} | "
            f"Regime="
            f"{weights['regime_weight']:.2f}"
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

    train_rows: list[
        dict[str, Any]
    ] = []

    selected_rows: list[
        dict[str, Any]
    ] = []

    total_runs = (
        len(windows)
        * len(weight_sets)
    )

    completed = 0

    for window in windows:
        print()
        print("=" * 110)
        print(
            f"TRAIN WINDOW {window.window_id} | "
            f"{window.train_start} -> "
            f"{window.train_end}"
        )
        print("=" * 110)

        current_window_rows: list[
            dict[str, Any]
        ] = []

        for weights in weight_sets:
            completed += 1

            row = evaluate_train_weight_set(
                window=window,
                symbols=symbols,
                weights=weights,
                args=args,
                entry_model=entry_model,
            )

            train_rows.append(
                row
            )

            current_window_rows.append(
                row
            )

            print(
                f"[{completed}/{total_runs}] "
                f"S={row['signal_weight']:.2f} "
                f"A={row['atr_weight']:.2f} "
                f"T={row['stop_weight']:.2f} "
                f"R={row['regime_weight']:.2f} | "
                f"Return="
                f"{row['total_return_pct']:+.2f}% | "
                f"Sharpe="
                f"{row['sharpe_ratio']:.3f}"
            )

        current_df = pd.DataFrame(
            current_window_rows
        )

        best = select_best_train_weight(
            current_df
        )

        selected_rows.append(
            best.to_dict()
        )

        print()
        print(
            f"BEST WINDOW {window.window_id}: "
            f"S={best['signal_weight']:.2f} | "
            f"A={best['atr_weight']:.2f} | "
            f"T={best['stop_weight']:.2f} | "
            f"R={best['regime_weight']:.2f} | "
            f"Sharpe={best['sharpe_ratio']:.3f} | "
            f"Return="
            f"{best['total_return_pct']:+.2f}%"
        )

    train_df = pd.DataFrame(
        train_rows
    )

    selected_df = pd.DataFrame(
        selected_rows
    )

    train_path = (
        output_dir
        / f"train_results_{args.aggregation}.csv"
    )

    selected_path = (
        output_dir
        / f"selected_weights_{args.aggregation}.csv"
    )

    train_df.to_csv(
        train_path,
        index=False,
        encoding="utf-8-sig",
    )

    selected_df.to_csv(
        selected_path,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print("=" * 130)
    print("SELECTED COMPOSITE WEIGHTS")
    print("=" * 130)

    print(
        selected_df[
            [
                "window_id",
                "train_start",
                "train_end",
                "test_start",
                "test_end",
                "signal_weight",
                "atr_weight",
                "stop_weight",
                "regime_weight",
                "total_trades",
                "total_return_pct",
                "sharpe_ratio",
                "max_drawdown_pct",
            ]
        ].to_string(
            index=False,
            float_format=lambda value: (
                f"{value:.4f}"
            ),
        )
    )

    print()
    print(f"Đã xuất: {train_path}")
    print(f"Đã xuất: {selected_path}")

    oos_rows: list[
        dict[str, Any]
    ] = []

    print()
    print("=" * 130)
    print(
        "RUN SELECTED COMPOSITE OOS WINDOWS"
    )
    print("=" * 130)

    for _, selected_row in (
        selected_df
        .sort_values(
            "window_id"
        )
        .iterrows()
    ):
        print()
        print(
            f"WINDOW "
            f"{int(selected_row['window_id'])} | "
            f"OOS "
            f"{selected_row['test_start']} "
            f"-> "
            f"{selected_row['test_end']}"
        )

        print(
            f"Weights: "
            f"S={selected_row['signal_weight']:.2f} | "
            f"A={selected_row['atr_weight']:.2f} | "
            f"T={selected_row['stop_weight']:.2f} | "
            f"R={selected_row['regime_weight']:.2f}"
        )

        oos_row = evaluate_oos_weight_set(
            selected_row=selected_row,
            symbols=symbols,
            args=args,
            entry_model=entry_model,
        )

        oos_rows.append(
            oos_row
        )

        print(
            f"Trades={oos_row['total_trades']} | "
            f"Return="
            f"{oos_row['total_return_pct']:+.2f}% | "
            f"Sharpe="
            f"{oos_row['sharpe_ratio']:.3f} | "
            f"DD="
            f"{oos_row['max_drawdown_pct']:.2f}%"
        )

    oos_df = pd.DataFrame(
        oos_rows
    )

    oos_summary = build_oos_summary(
        oos_df
    )

    weight_frequency = (
        build_selected_weight_frequency(
            selected_df
        )
    )

    oos_path = (
        output_dir
        / f"oos_results_{args.aggregation}.csv"
    )

    oos_summary_path = (
        output_dir
        / f"oos_summary_{args.aggregation}.csv"
    )

    frequency_path = (
        output_dir
        / f"weight_frequency_{args.aggregation}.csv"
    )

    oos_df.to_csv(
        oos_path,
        index=False,
        encoding="utf-8-sig",
    )

    oos_summary.to_csv(
        oos_summary_path,
        index=False,
        encoding="utf-8-sig",
    )

    weight_frequency.to_csv(
        frequency_path,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print("=" * 160)
    print(
        "COMPOSITE WALK-FORWARD OOS RESULTS"
    )
    print("=" * 160)

    print(
        oos_df[
            [
                "window_id",
                "test_start",
                "test_end",
                "signal_weight",
                "atr_weight",
                "stop_weight",
                "regime_weight",
                "total_trades",
                "total_return_pct",
                "sharpe_ratio",
                "max_drawdown_pct",
                "profit_factor",
            ]
        ].to_string(
            index=False,
            float_format=lambda value: (
                f"{value:.4f}"
            ),
        )
    )

    print()
    print("=" * 160)
    print(
        "COMPOSITE WALK-FORWARD SUMMARY"
    )
    print("=" * 160)

    print(
        oos_summary.to_string(
            index=False,
            float_format=lambda value: (
                f"{value:.4f}"
            ),
        )
    )

    print()
    print(f"Đã xuất: {oos_path}")
    print(
        f"Đã xuất: {oos_summary_path}"
    )
    print(
        f"Đã xuất: {frequency_path}"
    )

if __name__ == "__main__":
    main()	