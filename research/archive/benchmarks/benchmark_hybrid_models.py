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
from research.universes import TOP10_SYMBOLS
from strategy.base_strategy import BaseStrategy
from strategy.donchian_breakout_entry import (
    DonchianBreakoutEntryModel,
)
from strategy.hybrid_trend_donchian_entry import (
    HybridTrendDonchianEntryModel,
)
from strategy.trend_strategy_v1 import (
    TrendStrategyV1,
)


DEFAULT_DETAIL_OUTPUT = (
    "research_results/"
    "hybrid_benchmark_detail.csv"
)

DEFAULT_SUMMARY_OUTPUT = (
    "research_results/"
    "hybrid_benchmark_summary.csv"
)

DEFAULT_WINNER_OUTPUT = (
    "research_results/"
    "hybrid_benchmark_symbol_winners.csv"
)

DEFAULT_WIN_COUNT_OUTPUT = (
    "research_results/"
    "hybrid_benchmark_win_summary.csv"
)


def build_model_registry(
) -> dict[str, BaseStrategy]:
    models: list[BaseStrategy] = [
        TrendStrategyV1(),

        DonchianBreakoutEntryModel(),

        HybridTrendDonchianEntryModel(
            mode="strict",
        ),

        HybridTrendDonchianEntryModel(
            mode="trend_context",
        ),

        HybridTrendDonchianEntryModel(
            mode="score_blend",
        ),
    ]

    registry: dict[
        str,
        BaseStrategy,
    ] = {}

    for model in models:
        model_name = str(
            getattr(
                model,
                "name",
                model.__class__.__name__,
            )
        )

        if model_name in registry:
            raise ValueError(
                "Model bị trùng tên: "
                f"{model_name}"
            )

        registry[
            model_name
        ] = model

    return registry


def benchmark_hybrid_models(
    *,
    symbols: list[str],
    start_date: str | None,
    end_date: str | None,
    stop_loss_pct: float,
    take_profit_pct: float,
    max_holding_days: int,
    min_adx: float,
    atr_stop_multiplier: float,
    atr_target_multiplier: float,
    detail_output_path: str,
    summary_output_path: str,
    winner_output_path: str,
    win_count_output_path: str,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    registry = build_model_registry()

    total_runs = (
        len(registry)
        * len(symbols)
    )

    completed = 0
    started_at = perf_counter()

    rows: list[
        dict[str, Any]
    ] = []

    print(
        f"Chạy {len(registry)} model(s) × "
        f"{len(symbols)} symbol(s) "
        f"= {total_runs} backtests."
    )

    print(
        "Exit cố định: "
        f"ATR Stop={atr_stop_multiplier} | "
        f"ATR Target={atr_target_multiplier}"
    )

    for (
        entry_model_name,
        entry_model,
    ) in registry.items():
        print()
        print("=" * 100)
        print(
            "BENCHMARK ENTRY MODEL: "
            f"{entry_model_name}"
        )
        print("=" * 100)

        for symbol in symbols:
            completed += 1

            run_started_at = (
                perf_counter()
            )

            exit_model = build_exit_model(
                name="atr",
                break_even_trigger=5.0,
                stop_atr_multiplier=(
                    atr_stop_multiplier
                ),
                target_atr_multiplier=(
                    atr_target_multiplier
                ),
                trailing_atr_multiplier=2.0,
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

            run_seconds = (
                perf_counter()
                - run_started_at
            )

            row = {
                "symbol": symbol,
                "entry_model": (
                    entry_model_name
                ),
                "exit_model": "atr",
                "atr_stop_multiplier": (
                    atr_stop_multiplier
                ),
                "atr_target_multiplier": (
                    atr_target_multiplier
                ),
                "elapsed_seconds": round(
                    run_seconds,
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
            }

            rows.append(row)

            elapsed = (
                perf_counter()
                - started_at
            )

            average_seconds = (
                elapsed / completed
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
                f"DD "
                f"{row['max_drawdown_pct']:.2f}% | "
                f"ETA "
                f"{format_duration(eta_seconds)}"
            )

    detail_df = pd.DataFrame(
        rows
    )

    summary_df = build_summary(
        detail_df
    )

    (
        winner_df,
        win_count_df,
    ) = build_winner_matrix(
        detail_df
    )

    save_outputs(
        detail_df=detail_df,
        summary_df=summary_df,
        winner_df=winner_df,
        win_count_df=win_count_df,
        detail_output_path=(
            detail_output_path
        ),
        summary_output_path=(
            summary_output_path
        ),
        winner_output_path=(
            winner_output_path
        ),
        win_count_output_path=(
            win_count_output_path
        ),
    )

    print_results(
        summary_df=summary_df,
        winner_df=winner_df,
        win_count_df=win_count_df,
    )

    return (
        detail_df,
        summary_df,
        winner_df,
        win_count_df,
    )


def build_summary(
    detail_df: pd.DataFrame,
) -> pd.DataFrame:
    if detail_df.empty:
        return pd.DataFrame()

    rows: list[
        dict[str, Any]
    ] = []

    for (
        entry_model,
        group,
    ) in detail_df.groupby(
        "entry_model",
        sort=False,
    ):
        returns = numeric_series(
            group,
            "total_return_pct",
        )

        sharpes = numeric_series(
            group,
            "sharpe_ratio",
        )

        drawdowns = numeric_series(
            group,
            "max_drawdown_pct",
        )

        profit_factors = numeric_series(
            group,
            "profit_factor",
        )

        expectancies = numeric_series(
            group,
            "expectancy_pct",
        )

        win_rates = numeric_series(
            group,
            "win_rate_pct",
        )

        trades = (
            pd.to_numeric(
                group["total_trades"],
                errors="coerce",
            )
            .fillna(0)
            .astype(int)
        )

        qualified_mask = (
            trades >= 20
        )

        rows.append(
            {
                "entry_model": (
                    entry_model
                ),
                "exit_model": "atr",
                "symbols": len(group),
                "positive_symbols": int(
                    (returns > 0).sum()
                ),
                "qualified_symbols": int(
                    qualified_mask.sum()
                ),
                "zero_trade_symbols": int(
                    (trades == 0).sum()
                ),
                "total_trades": int(
                    trades.sum()
                ),
                "minimum_trades": int(
                    trades.min()
                ),
                "median_trades": float(
                    trades.median()
                ),
                "average_trades": float(
                    mean(trades)
                ),
                "maximum_trades": int(
                    trades.max()
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


def build_winner_matrix(
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

    rows: list[
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

        numeric_columns = [
            "sharpe_ratio",
            "total_return_pct",
            "profit_factor",
            "expectancy_pct",
            "max_drawdown_pct",
            "total_trades",
        ]

        for column in numeric_columns:
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

        rows.append(
            {
                "symbol": symbol,
                "winner_entry_model": (
                    winner[
                        "entry_model"
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
                "winner_profitable": bool(
                    winner[
                        "total_return_pct"
                    ] > 0
                ),
                "winner_qualified": bool(
                    winner[
                        "total_trades"
                    ] >= 20
                ),
            }
        )

    winner_df = pd.DataFrame(
        rows
    )

    win_count_df = (
        winner_df[
            "winner_entry_model"
        ]
        .value_counts()
        .rename_axis(
            "entry_model"
        )
        .reset_index(
            name="symbol_wins"
        )
    )

    win_count_df[
        "win_rate_pct"
    ] = (
        win_count_df[
            "symbol_wins"
        ]
        / len(winner_df)
        * 100
    )

    profitable_counts = (
        winner_df.loc[
            winner_df[
                "winner_profitable"
            ],
            "winner_entry_model",
        ]
        .value_counts()
    )

    qualified_counts = (
        winner_df.loc[
            winner_df[
                "winner_qualified"
            ],
            "winner_entry_model",
        ]
        .value_counts()
    )

    win_count_df[
        "profitable_wins"
    ] = (
        win_count_df[
            "entry_model"
        ]
        .map(
            profitable_counts
        )
        .fillna(0)
        .astype(int)
    )

    win_count_df[
        "qualified_wins"
    ] = (
        win_count_df[
            "entry_model"
        ]
        .map(
            qualified_counts
        )
        .fillna(0)
        .astype(int)
    )

    win_count_df = (
        win_count_df
        .sort_values(
            by=[
                "symbol_wins",
                "profitable_wins",
                "qualified_wins",
            ],
            ascending=[
                False,
                False,
                False,
            ],
        )
        .reset_index(drop=True)
    )

    win_count_df.insert(
        0,
        "rank",
        range(
            1,
            len(win_count_df) + 1,
        ),
    )

    return (
        winner_df,
        win_count_df,
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
        .replace(
            [
                float("inf"),
                float("-inf"),
            ],
            pd.NA,
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
    detail_df: pd.DataFrame,
    summary_df: pd.DataFrame,
    winner_df: pd.DataFrame,
    win_count_df: pd.DataFrame,
    detail_output_path: str,
    summary_output_path: str,
    winner_output_path: str,
    win_count_output_path: str,
) -> None:
    save_dataframe(
        detail_df,
        detail_output_path,
    )

    save_dataframe(
        summary_df,
        summary_output_path,
    )

    save_dataframe(
        winner_df,
        winner_output_path,
    )

    save_dataframe(
        win_count_df,
        win_count_output_path,
    )


def print_results(
    *,
    summary_df: pd.DataFrame,
    winner_df: pd.DataFrame,
    win_count_df: pd.DataFrame,
) -> None:
    print()
    print("=" * 210)
    print(
        "HYBRID ENTRY MODEL BENCHMARK SUMMARY"
    )
    print("=" * 210)

    if summary_df.empty:
        print(
            "Không có summary data."
        )
        return

    display_columns = [
        "rank",
        "entry_model",
        "symbols",
        "positive_symbols",
        "qualified_symbols",
        "zero_trade_symbols",
        "total_trades",
        "minimum_trades",
        "median_trades",
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
            display_columns
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
        f"Best model: "
        f"{best['entry_model']} | "
        f"Avg Sharpe "
        f"{best['average_sharpe']:.2f} | "
        f"Avg Return "
        f"{best['average_return_pct']:+.2f}% | "
        f"Median Return "
        f"{best['median_return_pct']:+.2f}% | "
        f"Qualified "
        f"{int(best['qualified_symbols'])}/"
        f"{int(best['symbols'])}"
    )

    print()
    print("=" * 150)
    print(
        "HYBRID WINNER BY SYMBOL"
    )
    print("=" * 150)

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
    print("=" * 120)
    print(
        "HYBRID MODEL WINS"
    )
    print("=" * 120)

    print(
        win_count_df.to_string(
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
            "Benchmark Trend, Donchian "
            "và Hybrid Entry Models."
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
        "--atr-stop-multiplier",
        type=float,
        default=2.0,
    )

    parser.add_argument(
        "--atr-target-multiplier",
        type=float,
        default=4.0,
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
        "--win-count-output",
        default=(
            DEFAULT_WIN_COUNT_OUTPUT
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

    benchmark_hybrid_models(
        symbols=symbols,
        start_date=args.start,
        end_date=args.end,
        stop_loss_pct=args.sl,
        take_profit_pct=args.tp,
        max_holding_days=args.hold,
        min_adx=args.min_adx,
        atr_stop_multiplier=(
            args.atr_stop_multiplier
        ),
        atr_target_multiplier=(
            args.atr_target_multiplier
        ),
        detail_output_path=(
            args.detail_output
        ),
        summary_output_path=(
            args.summary_output
        ),
        winner_output_path=(
            args.winner_output
        ),
        win_count_output_path=(
            args.win_count_output
        ),
    )


if __name__ == "__main__":
    main()