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


DEFAULT_DETAIL_OUTPUT = (
    "research_results/"
    "donchian_oos_detail.csv"
)

DEFAULT_SUMMARY_OUTPUT = (
    "research_results/"
    "donchian_oos_summary.csv"
)

DEFAULT_WINDOW_WINNER_OUTPUT = (
    "research_results/"
    "donchian_oos_window_winners.csv"
)

DEFAULT_RANKING_OUTPUT = (
    "research_results/"
    "donchian_oos_ranking.csv"
)


def build_donchian_registry(
) -> dict[str, BaseStrategy]:
    models: list[BaseStrategy] = [
        DonchianBreakoutEntryModel(),

        DonchianBreakoutEntryModel(
            use_distance_filter=False,
        ),

        DonchianBreakoutEntryModel(
            use_volume_breakout_score=False,
        ),

        DonchianBreakoutEntryModel(
            use_overheated_filter=False,
        ),

        DonchianBreakoutEntryModel(
            use_relative_strength=False,
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
                "Donchian model bị trùng tên: "
                f"{model_name}"
            )

        registry[
            model_name
        ] = model

    return registry


def evaluate_donchian_window(
    *,
    symbols: list[str],
    window: WalkForwardWindow,
    entry_model_name: str,
    entry_model: BaseStrategy,
    stop_loss_pct: float,
    take_profit_pct: float,
    max_holding_days: int,
    min_adx: float,
    atr_stop_multiplier: float,
    atr_target_multiplier: float,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
]:
    symbol_rows: list[
        dict[str, Any]
    ] = []

    for symbol in symbols:
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
            start_date=(
                window.test_start
            ),
            end_date=(
                window.test_end
            ),
            verbose=False,
            entry_model=entry_model,
            exit_model=exit_model,
        )

        symbol_rows.append(
            {
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
                "entry_model": (
                    entry_model_name
                ),
                "exit_model": "atr",
                "symbol": symbol,
                "atr_stop_multiplier": (
                    atr_stop_multiplier
                ),
                "atr_target_multiplier": (
                    atr_target_multiplier
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


def run_donchian_diagnostics(
    *,
    symbols: list[str],
    windows: list[
        WalkForwardWindow
    ],
    stop_loss_pct: float,
    take_profit_pct: float,
    max_holding_days: int,
    min_adx: float,
    atr_stop_multiplier: float,
    atr_target_multiplier: float,
    detail_output_path: str,
    summary_output_path: str,
    window_winner_output_path: str,
    ranking_output_path: str,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    registry = (
        build_donchian_registry()
    )

    detail_rows: list[
        dict[str, Any]
    ] = []

    summary_rows: list[
        dict[str, Any]
    ] = []

    total_evaluations = (
        len(windows)
        * len(registry)
    )

    completed = 0
    started_at = perf_counter()

    print(
        f"Chạy {len(windows)} window(s) × "
        f"{len(registry)} Donchian variant(s) "
        f"= {total_evaluations} "
        f"OOS evaluations."
    )

    print(
        "Exit cố định: "
        f"ATR Stop={atr_stop_multiplier} | "
        f"ATR Target={atr_target_multiplier}"
    )

    for window in windows:
        print()
        print("=" * 100)
        print(
            f"WINDOW "
            f"{window.window_number} | "
            f"TEST "
            f"{window.test_start} "
            f"→ {window.test_end}"
        )
        print("=" * 100)

        for (
            entry_model_name,
            entry_model,
        ) in registry.items():
            completed += 1

            (
                aggregate_row,
                symbol_rows,
            ) = evaluate_donchian_window(
                symbols=symbols,
                window=window,
                entry_model_name=(
                    entry_model_name
                ),
                entry_model=entry_model,
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
                atr_stop_multiplier=(
                    atr_stop_multiplier
                ),
                atr_target_multiplier=(
                    atr_target_multiplier
                ),
            )

            summary_rows.append(
                aggregate_row
            )

            detail_rows.extend(
                symbol_rows
            )

            elapsed = (
                perf_counter()
                - started_at
            )

            average_seconds = (
                elapsed / completed
            )

            remaining = (
                total_evaluations
                - completed
            )

            eta_seconds = (
                average_seconds
                * remaining
            )

            print(
                f"[{completed}/"
                f"{total_evaluations}] "
                f"{entry_model_name} | "
                f"Return "
                f"{aggregate_row['average_return_pct']:+.2f}% | "
                f"Median "
                f"{aggregate_row['median_return_pct']:+.2f}% | "
                f"Sharpe "
                f"{aggregate_row['average_sharpe']:.2f} | "
                f"Positive "
                f"{aggregate_row['positive_symbols']}/"
                f"{aggregate_row['symbols']} | "
                f"Trades "
                f"{aggregate_row['total_trades']} | "
                f"ETA "
                f"{format_duration(eta_seconds)}"
            )

    detail_df = pd.DataFrame(
        detail_rows
    )

    summary_df = pd.DataFrame(
        summary_rows
    )

    summary_df = (
        summary_df
        .sort_values(
            by=[
                "window_number",
                "average_sharpe",
                "median_return_pct",
                "average_return_pct",
            ],
            ascending=[
                True,
                False,
                False,
                False,
            ],
        )
        .reset_index(drop=True)
    )

    window_winner_df = (
        build_window_winners(
            summary_df
        )
    )

    ranking_df = (
        build_overall_ranking(
            summary_df
        )
    )

    save_outputs(
        detail_df=detail_df,
        summary_df=summary_df,
        window_winner_df=(
            window_winner_df
        ),
        ranking_df=ranking_df,
        detail_output_path=(
            detail_output_path
        ),
        summary_output_path=(
            summary_output_path
        ),
        window_winner_output_path=(
            window_winner_output_path
        ),
        ranking_output_path=(
            ranking_output_path
        ),
    )

    print_results(
        summary_df=summary_df,
        window_winner_df=(
            window_winner_df
        ),
        ranking_df=ranking_df,
    )

    return (
        detail_df,
        summary_df,
        window_winner_df,
        ranking_df,
    )


def build_window_winners(
    summary_df: pd.DataFrame,
) -> pd.DataFrame:
    if summary_df.empty:
        return pd.DataFrame()

    rows: list[
        dict[str, Any]
    ] = []

    for (
        window_number,
        group,
    ) in summary_df.groupby(
        "window_number",
        sort=True,
    ):
        ranked = (
            group
            .sort_values(
                by=[
                    "average_sharpe",
                    "median_return_pct",
                    "average_return_pct",
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

        winner = ranked.iloc[0]

        rows.append(
            {
                "window_number": int(
                    window_number
                ),
                "test_start": winner[
                    "test_start"
                ],
                "test_end": winner[
                    "test_end"
                ],
                "winner_entry_model": (
                    winner[
                        "entry_model"
                    ]
                ),
                "winner_average_return_pct": (
                    winner[
                        "average_return_pct"
                    ]
                ),
                "winner_median_return_pct": (
                    winner[
                        "median_return_pct"
                    ]
                ),
                "winner_average_sharpe": (
                    winner[
                        "average_sharpe"
                    ]
                ),
                "winner_median_sharpe": (
                    winner[
                        "median_sharpe"
                    ]
                ),
                "winner_profit_factor": (
                    winner[
                        "average_profit_factor"
                    ]
                ),
                "winner_drawdown_pct": (
                    winner[
                        "average_drawdown_pct"
                    ]
                ),
                "winner_positive_symbols": (
                    winner[
                        "positive_symbols"
                    ]
                ),
                "winner_total_trades": (
                    winner[
                        "total_trades"
                    ]
                ),
                "winner_profitable": bool(
                    winner[
                        "average_return_pct"
                    ] > 0
                ),
            }
        )

    return pd.DataFrame(
        rows
    )


def build_overall_ranking(
    summary_df: pd.DataFrame,
) -> pd.DataFrame:
    if summary_df.empty:
        return pd.DataFrame()

    winner_df = build_window_winners(
        summary_df
    )

    winner_counts = (
        winner_df[
            "winner_entry_model"
        ]
        .value_counts()
    )

    rows: list[
        dict[str, Any]
    ] = []

    for (
        entry_model,
        group,
    ) in summary_df.groupby(
        "entry_model",
        sort=False,
    ):
        average_returns = numeric_series(
            group,
            "average_return_pct",
        )

        median_returns = numeric_series(
            group,
            "median_return_pct",
        )

        average_sharpes = numeric_series(
            group,
            "average_sharpe",
        )

        median_sharpes = numeric_series(
            group,
            "median_sharpe",
        )

        profit_factors = numeric_series(
            group,
            "average_profit_factor",
        )

        drawdowns = numeric_series(
            group,
            "average_drawdown_pct",
        )

        rows.append(
            {
                "entry_model": (
                    entry_model
                ),
                "exit_model": "atr",
                "windows": len(group),
                "positive_windows": int(
                    (
                        average_returns
                        > 0
                    ).sum()
                ),
                "window_wins": int(
                    winner_counts.get(
                        entry_model,
                        0,
                    )
                ),
                "average_oos_return_pct": (
                    float(
                        mean(
                            average_returns
                        )
                    )
                ),
                "median_oos_return_pct": (
                    float(
                        median_returns.median()
                    )
                ),
                "average_oos_sharpe": (
                    float(
                        mean(
                            average_sharpes
                        )
                    )
                ),
                "median_oos_sharpe": (
                    float(
                        median_sharpes.median()
                    )
                ),
                "average_oos_profit_factor": (
                    float(
                        mean(
                            profit_factors
                        )
                    )
                ),
                "average_oos_drawdown_pct": (
                    float(
                        mean(
                            drawdowns
                        )
                    )
                ),
            }
        )

    ranking_df = pd.DataFrame(
        rows
    )

    ranking_df = (
        ranking_df
        .sort_values(
            by=[
                "average_oos_sharpe",
                "median_oos_return_pct",
                "average_oos_return_pct",
                "positive_windows",
                "window_wins",
                "average_oos_profit_factor",
                "average_oos_drawdown_pct",
            ],
            ascending=[
                False,
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

    ranking_df.insert(
        0,
        "rank",
        range(
            1,
            len(ranking_df) + 1,
        ),
    )

    return ranking_df


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
    detail_df: pd.DataFrame,
    summary_df: pd.DataFrame,
    window_winner_df: pd.DataFrame,
    ranking_df: pd.DataFrame,
    detail_output_path: str,
    summary_output_path: str,
    window_winner_output_path: str,
    ranking_output_path: str,
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
        window_winner_df,
        window_winner_output_path,
    )

    save_dataframe(
        ranking_df,
        ranking_output_path,
    )


def print_results(
    *,
    summary_df: pd.DataFrame,
    window_winner_df: pd.DataFrame,
    ranking_df: pd.DataFrame,
) -> None:
    print()
    print("=" * 190)
    print(
        "DONCHIAN OOS DIAGNOSTICS"
    )
    print("=" * 190)

    print(
        summary_df.to_string(
            index=False,
            float_format=(
                lambda value:
                f"{value:.2f}"
            ),
        )
    )

    print()
    print("=" * 150)
    print(
        "BEST DONCHIAN VARIANT BY WINDOW"
    )
    print("=" * 150)

    print(
        window_winner_df.to_string(
            index=False,
            float_format=(
                lambda value:
                f"{value:.2f}"
            ),
        )
    )

    print()
    print("=" * 165)
    print(
        "OVERALL DONCHIAN OOS RANKING"
    )
    print("=" * 165)

    print(
        ranking_df.to_string(
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
            "OOS Walk-Forward Diagnostics "
            "cho Donchian Ablation Models."
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
        "--window-winner-output",
        default=(
            DEFAULT_WINDOW_WINNER_OUTPUT
        ),
    )

    parser.add_argument(
        "--ranking-output",
        default=(
            DEFAULT_RANKING_OUTPUT
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

    run_donchian_diagnostics(
        symbols=symbols,
        windows=windows,
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
        window_winner_output_path=(
            args.window_winner_output
        ),
        ranking_output_path=(
            args.ranking_output
        ),
    )


if __name__ == "__main__":
    main()