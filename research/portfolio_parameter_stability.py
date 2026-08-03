from __future__ import annotations

import argparse
from itertools import product
from pathlib import Path
from time import perf_counter
from typing import Any

import pandas as pd

from backtesting.engine import (
    build_exit_model,
    run_backtest,
)
from research.universes import TOP10_SYMBOLS
from strategy.hybrid_trend_donchian_entry import (
    HybridTrendDonchianEntryModel,
)


DEFAULT_DETAIL_OUTPUT = (
    "research_results/"
    "portfolio_parameter_stability_detail.csv"
)

DEFAULT_RANKING_OUTPUT = (
    "research_results/"
    "portfolio_parameter_stability_ranking.csv"
)


HOLDING_DAY_GRID = [
    10,
    15,
    20,
    30,
]

ATR_STOP_GRID = [
    1.5,
    2.0,
    2.5,
]

ATR_TARGET_GRID = [
    3.0,
    4.0,
    5.0,
]


def build_parameter_grid(
) -> list[dict[str, float | int]]:
    return [
        {
            "max_holding_days": (
                max_holding_days
            ),
            "atr_stop_multiplier": (
                atr_stop_multiplier
            ),
            "atr_target_multiplier": (
                atr_target_multiplier
            ),
        }
        for (
            max_holding_days,
            atr_stop_multiplier,
            atr_target_multiplier,
        ) in product(
            HOLDING_DAY_GRID,
            ATR_STOP_GRID,
            ATR_TARGET_GRID,
        )
    ]


def run_parameter_stability(
    *,
    symbols: list[str],
    start_date: str,
    end_date: str,
    initial_capital: float,
    max_positions: int,
    stop_loss_pct: float,
    take_profit_pct: float,
    min_adx: float,
    detail_output_path: str,
    ranking_output_path: str,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
]:
    if max_positions <= 0:
        raise ValueError(
            "max_positions phải lớn hơn 0."
        )

    position_size_pct = (
        100.0
        / max_positions
    )

    parameter_grid = (
        build_parameter_grid()
    )

    rows: list[
        dict[str, Any]
    ] = []

    total_runs = len(
        parameter_grid
    )

    started_at = perf_counter()

    print(
        f"Chạy {total_runs} "
        "portfolio parameter combinations."
    )

    print(
        f"Symbols: {len(symbols)}"
    )

    print(
        f"Capital: "
        f"{initial_capital:,.0f}"
    )

    print(
        f"Max positions: "
        f"{max_positions}"
    )

    print(
        f"Position size: "
        f"{position_size_pct:.2f}%"
    )

    for (
        run_number,
        parameters,
    ) in enumerate(
        parameter_grid,
        start=1,
    ):
        run_started_at = (
            perf_counter()
        )

        max_holding_days = int(
            parameters[
                "max_holding_days"
            ]
        )

        atr_stop_multiplier = float(
            parameters[
                "atr_stop_multiplier"
            ]
        )

        atr_target_multiplier = float(
            parameters[
                "atr_target_multiplier"
            ]
        )

        entry_model = (
            HybridTrendDonchianEntryModel(
                mode="trend_context",
            )
        )

        exit_model = build_exit_model(
            name="atr",
            stop_atr_multiplier=(
                atr_stop_multiplier
            ),
            target_atr_multiplier=(
                atr_target_multiplier
            ),
            break_even_trigger=5.0,
            trailing_atr_multiplier=2.0,
        )

        (
            trades,
            metrics,
            equity_df,
        ) = run_backtest(
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            initial_capital=(
                initial_capital
            ),
            position_size_pct=(
                position_size_pct
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
            entry_model=entry_model,
            exit_model=exit_model,
            verbose=False,
        )

        final_equity = (
            float(
                equity_df[
                    "equity"
                ].iloc[-1]
            )
            if not equity_df.empty
            else initial_capital
        )

        max_open_positions = (
            int(
                pd.to_numeric(
                    equity_df[
                        "open_positions"
                    ],
                    errors="coerce",
                )
                .fillna(0)
                .max()
            )
            if not equity_df.empty
            else 0
        )

        elapsed_seconds = (
            perf_counter()
            - run_started_at
        )

        total_transaction_cost = sum(
            float(
                getattr(
                    trade,
                    "total_transaction_cost",
                    0.0,
                )
            )
            for trade in trades
        )

        rows.append(
            {
                "run_number": (
                    run_number
                ),
                "entry_model": (
                    "hybrid_trend_donchian_v1"
                    "__trend_context"
                ),
                "symbols": len(
                    symbols
                ),
                "start_date": (
                    start_date
                ),
                "end_date": (
                    end_date
                ),
                "initial_capital": (
                    initial_capital
                ),
                "final_equity": (
                    final_equity
                ),
                "max_positions": (
                    max_positions
                ),
                "max_open_positions": (
                    max_open_positions
                ),
                "position_size_pct": (
                    position_size_pct
                ),
                "max_holding_days": (
                    max_holding_days
                ),
                "atr_stop_multiplier": (
                    atr_stop_multiplier
                ),
                "atr_target_multiplier": (
                    atr_target_multiplier
                ),
                "total_trades": (
                    metrics.get(
                        "total_trades",
                        len(trades),
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
                "total_transaction_cost": (
                    total_transaction_cost
                ),
                "elapsed_seconds": round(
                    elapsed_seconds,
                    2,
                ),
            }
        )

        total_elapsed = (
            perf_counter()
            - started_at
        )

        average_seconds = (
            total_elapsed
            / run_number
        )

        remaining_runs = (
            total_runs
            - run_number
        )

        eta_seconds = (
            average_seconds
            * remaining_runs
        )

        current = rows[-1]

        print(
            f"[{run_number}/"
            f"{total_runs}] "
            f"Hold={max_holding_days} | "
            f"ATR-S="
            f"{atr_stop_multiplier:.1f} | "
            f"ATR-T="
            f"{atr_target_multiplier:.1f} | "
            f"Trades "
            f"{current['total_trades']} | "
            f"Return "
            f"{current['total_return_pct']:+.2f}% | "
            f"Sharpe "
            f"{current['sharpe_ratio']:.2f} | "
            f"DD "
            f"{current['max_drawdown_pct']:.2f}% | "
            f"ETA "
            f"{format_duration(eta_seconds)}"
        )

    detail_df = pd.DataFrame(
        rows
    )

    ranking_df = (
        build_stability_ranking(
            detail_df
        )
    )

    save_dataframe(
        detail_df,
        detail_output_path,
    )

    save_dataframe(
        ranking_df,
        ranking_output_path,
    )

    print_results(
        detail_df=detail_df,
        ranking_df=ranking_df,
    )

    return (
        detail_df,
        ranking_df,
    )


def build_stability_ranking(
    detail_df: pd.DataFrame,
) -> pd.DataFrame:
    if detail_df.empty:
        return pd.DataFrame()

    ranking_df = (
        detail_df.copy()
    )

    numeric_columns = [
        "total_return_pct",
        "cagr_pct",
        "max_drawdown_pct",
        "sharpe_ratio",
        "sortino_ratio",
        "profit_factor",
        "expectancy_pct",
        "total_transaction_cost",
        "total_trades",
    ]

    for column in numeric_columns:
        ranking_df[column] = (
            pd.to_numeric(
                ranking_df[column],
                errors="coerce",
            )
            .fillna(0.0)
        )

    ranking_df[
        "profitable"
    ] = (
        ranking_df[
            "total_return_pct"
        ] > 0
    )

    ranking_df[
        "positive_sharpe"
    ] = (
        ranking_df[
            "sharpe_ratio"
        ] > 0
    )

    ranking_df[
        "pf_above_one"
    ] = (
        ranking_df[
            "profit_factor"
        ] > 1
    )

    ranking_df[
        "acceptable_drawdown"
    ] = (
        ranking_df[
            "max_drawdown_pct"
        ] >= -20
    )

    ranking_df[
        "robustness_score"
    ] = (
        ranking_df[
            "profitable"
        ].astype(int)
        + ranking_df[
            "positive_sharpe"
        ].astype(int)
        + ranking_df[
            "pf_above_one"
        ].astype(int)
        + ranking_df[
            "acceptable_drawdown"
        ].astype(int)
    )

    ranking_df = (
        ranking_df
        .sort_values(
            by=[
                "robustness_score",
                "sharpe_ratio",
                "total_return_pct",
                "profit_factor",
                "max_drawdown_pct",
                "total_transaction_cost",
            ],
            ascending=[
                False,
                False,
                False,
                False,
                False,
                True,
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


def print_results(
    *,
    detail_df: pd.DataFrame,
    ranking_df: pd.DataFrame,
) -> None:
    print()
    print("=" * 190)
    print(
        "PORTFOLIO PARAMETER STABILITY"
    )
    print("=" * 190)

    display_columns = [
        "rank",
        "max_holding_days",
        "atr_stop_multiplier",
        "atr_target_multiplier",
        "total_trades",
        "total_return_pct",
        "cagr_pct",
        "max_drawdown_pct",
        "sharpe_ratio",
        "sortino_ratio",
        "profit_factor",
        "win_rate_pct",
        "expectancy_pct",
        "total_transaction_cost",
        "robustness_score",
    ]

    print(
        ranking_df[
            display_columns
        ]
        .head(20)
        .to_string(
            index=False,
            float_format=(
                lambda value:
                f"{value:.2f}"
            ),
        )
    )

    if ranking_df.empty:
        return

    best = ranking_df.iloc[0]

    print()
    print(
        "Best combination:"
    )

    print(
        f"Hold="
        f"{int(best['max_holding_days'])} | "
        f"ATR Stop="
        f"{best['atr_stop_multiplier']:.1f} | "
        f"ATR Target="
        f"{best['atr_target_multiplier']:.1f}"
    )

    print(
        f"Return "
        f"{best['total_return_pct']:+.2f}% | "
        f"Sharpe "
        f"{best['sharpe_ratio']:.2f} | "
        f"PF "
        f"{best['profit_factor']:.2f} | "
        f"DD "
        f"{best['max_drawdown_pct']:.2f}%"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Portfolio parameter stability "
            "cho Hybrid Trend Context."
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
        "--capital",
        type=float,
        default=100_000_000,
    )

    parser.add_argument(
        "--max-positions",
        type=int,
        default=5,
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

    run_parameter_stability(
        symbols=symbols,
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
        max_positions=(
            args.max_positions
        ),
        stop_loss_pct=args.sl,
        take_profit_pct=args.tp,
        min_adx=args.min_adx,
        detail_output_path=(
            args.detail_output
        ),
        ranking_output_path=(
            args.ranking_output
        ),
    )


if __name__ == "__main__":
    main()