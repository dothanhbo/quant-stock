from __future__ import annotations

import argparse
from pathlib import Path
from statistics import mean
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
    "entry_model_benchmark_detail.csv"
)

DEFAULT_SUMMARY_OUTPUT = (
    "research_results/"
    "entry_model_benchmark_summary.csv"
)

DEFAULT_WINNER_OUTPUT = (
    "research_results/"
    "entry_model_symbol_winners.csv"
)

DEFAULT_WINNER_COUNT_OUTPUT = (
    "research_results/"
    "entry_model_symbol_win_summary.csv"
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


def benchmark_entry_models(
    *,
    symbols: list[str],
    start_date: str | None,
    end_date: str | None,
    stop_loss_pct: float,
    take_profit_pct: float,
    max_holding_days: int,
    min_adx: float,
    exit_model_name: str,
    break_even_trigger: float,
    atr_stop_multiplier: float,
    atr_target_multiplier: float,
    trailing_atr_multiplier: float,
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

    detail_rows: list[
        dict[str, Any]
    ] = []

    total_runs = (
        len(entry_models)
        * len(symbols)
    )

    completed = 0

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

    print(
        f"Chạy {len(entry_models)} "
        f"entry model(s) × "
        f"{len(symbols)} symbol(s) "
        f"= {total_runs} backtests."
    )

    print(
        f"Exit model cố định: "
        f"{exit_model_name}"
    )

    for (
        entry_model_name,
        entry_model,
    ) in entry_models.items():
        print()
        print("=" * 80)
        print(
            "BENCHMARK ENTRY MODEL: "
            f"{entry_model_name}"
        )
        print("=" * 80)

        for symbol in symbols:
            completed += 1

            print(
                f"[{completed}/{total_runs}] "
                f"{entry_model_name} | "
                f"{symbol}"
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

            row = {
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

            detail_rows.append(
                row
            )

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

    summary_df = build_entry_summary(
        detail_df
    )

    (
        winner_df,
        winner_count_df,
    ) = build_entry_winner_matrix(
        detail_df
    )

    save_benchmark_results(
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

    print_entry_summary(
        summary_df
    )

    print_entry_winner_summary(
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


def build_entry_summary(
    detail_df: pd.DataFrame,
) -> pd.DataFrame:
    if detail_df.empty:
        return pd.DataFrame()

    summary_rows: list[
        dict[str, Any]
    ] = []

    for (
        entry_model,
        group,
    ) in detail_df.groupby(
        "entry_model",
        sort=False,
    ):
        returns = pd.to_numeric(
            group["total_return_pct"],
            errors="coerce",
        ).fillna(0.0)

        cagr_values = pd.to_numeric(
            group["cagr_pct"],
            errors="coerce",
        ).fillna(0.0)

        sharpes = pd.to_numeric(
            group["sharpe_ratio"],
            errors="coerce",
        ).fillna(0.0)

        sortinos = pd.to_numeric(
            group["sortino_ratio"],
            errors="coerce",
        ).fillna(0.0)

        drawdowns = pd.to_numeric(
            group["max_drawdown_pct"],
            errors="coerce",
        ).fillna(0.0)

        profit_factors = pd.to_numeric(
            group["profit_factor"],
            errors="coerce",
        ).fillna(0.0)

        win_rates = pd.to_numeric(
            group["win_rate_pct"],
            errors="coerce",
        ).fillna(0.0)

        expectancies = pd.to_numeric(
            group["expectancy_pct"],
            errors="coerce",
        ).fillna(0.0)

        trades = pd.to_numeric(
            group["total_trades"],
            errors="coerce",
        ).fillna(0).astype(int)

        positive_symbols = int(
            (returns > 0).sum()
        )

        qualified_symbols = int(
            (trades >= 30).sum()
        )

        summary_rows.append(
            {
                "entry_model": (
                    entry_model
                ),
                "exit_model": (
                    group[
                        "exit_model"
                    ].iloc[0]
                ),
                "symbols": len(group),
                "positive_symbols": (
                    positive_symbols
                ),
                "qualified_symbols": (
                    qualified_symbols
                ),
                "total_trades": int(
                    trades.sum()
                ),
                "average_trades": float(
                    mean(trades)
                ),
                "average_return_pct": (
                    float(
                        mean(returns)
                    )
                ),
                "median_return_pct": (
                    float(
                        returns.median()
                    )
                ),
                "average_cagr_pct": (
                    float(
                        mean(cagr_values)
                    )
                ),
                "average_sharpe": (
                    float(
                        mean(sharpes)
                    )
                ),
                "median_sharpe": (
                    float(
                        sharpes.median()
                    )
                ),
                "average_sortino": (
                    float(
                        mean(sortinos)
                    )
                ),
                "average_drawdown_pct": (
                    float(
                        mean(drawdowns)
                    )
                ),
                "worst_drawdown_pct": (
                    float(
                        drawdowns.min()
                    )
                ),
                "average_profit_factor": (
                    float(
                        mean(profit_factors)
                    )
                ),
                "average_win_rate_pct": (
                    float(
                        mean(win_rates)
                    )
                ),
                "average_expectancy_pct": (
                    float(
                        mean(expectancies)
                    )
                ),
            }
        )

    summary_df = pd.DataFrame(
        summary_rows
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


def build_entry_winner_matrix(
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

        numeric_columns = [
            "sharpe_ratio",
            "total_return_pct",
            "profit_factor",
            "max_drawdown_pct",
            "expectancy_pct",
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

        winner_rows.append(
            {
                "symbol": symbol,
                "winner": winner[
                    "entry_model"
                ],
                "exit_model": winner[
                    "exit_model"
                ],
                "winner_trades": (
                    int(
                        winner[
                            "total_trades"
                        ]
                    )
                ),
                "winner_return_pct": (
                    float(
                        winner[
                            "total_return_pct"
                        ]
                    )
                ),
                "winner_sharpe": (
                    float(
                        winner[
                            "sharpe_ratio"
                        ]
                    )
                ),
                "winner_profit_factor": (
                    float(
                        winner[
                            "profit_factor"
                        ]
                    )
                ),
                "winner_expectancy_pct": (
                    float(
                        winner[
                            "expectancy_pct"
                        ]
                    )
                ),
                "winner_drawdown_pct": (
                    float(
                        winner[
                            "max_drawdown_pct"
                        ]
                    )
                ),
            }
        )

    winner_df = pd.DataFrame(
        winner_rows
    )

    winner_count_df = (
        winner_df[
            "winner"
        ]
        .value_counts()
        .rename_axis(
            "entry_model"
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


def save_benchmark_results(
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
            Path(
                detail_output_path
            ),
            detail_df,
        ),
        "summary": (
            Path(
                summary_output_path
            ),
            summary_df,
        ),
        "winner matrix": (
            Path(
                winner_output_path
            ),
            winner_df,
        ),
        "winner summary": (
            Path(
                winner_count_output_path
            ),
            winner_count_df,
        ),
    }

    for (
        label,
        (
            output_path,
            dataframe,
        ),
    ) in outputs.items():
        output_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        try:
            dataframe.to_csv(
                output_path,
                index=False,
                encoding="utf-8-sig",
            )

        except PermissionError as exc:
            raise PermissionError(
                f"Không thể ghi file "
                f"{output_path}. "
                "Hãy đóng file trong Excel "
                "rồi chạy lại."
            ) from exc

        print(
            f"Đã xuất {label}: "
            f"{output_path}"
        )


def print_entry_summary(
    summary_df: pd.DataFrame,
) -> None:
    print()
    print("=" * 140)
    print(
        "ENTRY MODEL BENCHMARK SUMMARY"
    )
    print("=" * 140)

    if summary_df.empty:
        print(
            "Không có dữ liệu summary."
        )
        return

    display_columns = [
        "rank",
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
        f"Best entry model: "
        f"{best['entry_model']} | "
        f"Avg Sharpe "
        f"{best['average_sharpe']:.2f} | "
        f"Avg Return "
        f"{best['average_return_pct']:+.2f}% | "
        f"Positive "
        f"{int(best['positive_symbols'])}/"
        f"{int(best['symbols'])}"
    )


def print_entry_winner_summary(
    *,
    winner_df: pd.DataFrame,
    winner_count_df: pd.DataFrame,
) -> None:
    print()
    print("=" * 110)
    print(
        "ENTRY MODEL WINNER MATRIX"
    )
    print("=" * 110)

    if winner_df.empty:
        print(
            "Không có dữ liệu winner."
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
    print("=" * 70)
    print(
        "ENTRY MODEL WINS BY SYMBOL"
    )
    print("=" * 70)

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
            "Benchmark nhiều Entry Model "
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
        "--exit-model",
        choices=[
            "fixed",
            "atr",
            "break_even",
            "trailing_atr",
        ],
        default="fixed",
    )

    parser.add_argument(
        "--break-even-trigger",
        type=float,
        default=5.0,
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
        "--trailing-atr-multiplier",
        type=float,
        default=2.0,
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

    benchmark_entry_models(
        symbols=symbols,
        start_date=args.start,
        end_date=args.end,
        stop_loss_pct=args.sl,
        take_profit_pct=args.tp,
        max_holding_days=args.hold,
        min_adx=args.min_adx,
        exit_model_name=(
            args.exit_model
        ),
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