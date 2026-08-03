from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd

from backtesting.engine import (
    build_exit_model,
    run_backtest,
)
from backtesting.ranking import RankingMethod
from research.benchmark_portfolio_models import (
    build_portfolio_model_registry,
    trade_to_row,
)
from research.universes import TOP10_SYMBOLS


DEFAULT_SUMMARY_OUTPUT = (
    "research_results/"
    "candidate_ranking_summary.csv"
)

DEFAULT_EQUITY_OUTPUT = (
    "research_results/"
    "candidate_ranking_equity.csv"
)

DEFAULT_TRADES_OUTPUT = (
    "research_results/"
    "candidate_ranking_trades.csv"
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
            f"Không thể ghi file {output}. "
            "Hãy đóng file trong Excel rồi chạy lại."
        ) from exc

    print(
        f"Đã xuất: {output}"
    )


def _safe_metric(
    metrics: dict[str, Any],
    name: str,
    default: float = 0.0,
) -> float:
    value = metrics.get(
        name,
        default,
    )

    try:
        return float(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        return float(
            default
        )


def run_candidate_ranking_benchmark(
    *,
    symbols: list[str],
    start_date: str,
    end_date: str,
    initial_capital: float,
    max_positions: int,
    stop_loss_pct: float,
    take_profit_pct: float,
    max_holding_days: int,
    min_adx: float,
    atr_stop_multiplier: float,
    atr_target_multiplier: float,
    ranking_methods: list[str],
    summary_output_path: str,
    equity_output_path: str,
    trades_output_path: str,
) -> tuple[
    pd.DataFrame,
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

    model_registry = (
        build_portfolio_model_registry()
    )

    summary_rows: list[
        dict[str, Any]
    ] = []

    equity_frames: list[
        pd.DataFrame
    ] = []

    trade_rows: list[
        dict[str, Any]
    ] = []

    total_runs = (
        len(
            ranking_methods
        )
        * len(
            model_registry
        )
    )

    current_run = 0

    print(
        f"Symbols: {len(symbols)}"
    )
    print(
        f"Initial capital: "
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
    print(
        f"Total runs: "
        f"{total_runs}"
    )

    for ranking_method in ranking_methods:
        print()
        print(
            "=" * 110
        )
        print(
            "RANKING METHOD: "
            f"{ranking_method}"
        )
        print(
            "=" * 110
        )

        for (
            entry_model_name,
            entry_model,
        ) in model_registry.items():
            current_run += 1

            print()
            print(
                f"[{current_run}/{total_runs}] "
                f"{entry_model_name}"
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
                ranking_method=(
                    ranking_method
                ),
                verbose=False,
            )

            final_equity = _safe_metric(
                metrics,
                "final_equity",
                (
                    float(
                        equity_df[
                            "equity"
                        ].iloc[-1]
                    )
                    if not equity_df.empty
                    else initial_capital
                ),
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
                if (
                    not equity_df.empty
                    and "open_positions"
                    in equity_df.columns
                )
                else 0
            )

            summary_rows.append(
                {
                    "ranking_method": (
                        ranking_method
                    ),
                    "entry_model": (
                        entry_model_name
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
                    "total_trades": int(
                        metrics.get(
                            "total_trades",
                            len(
                                trades
                            ),
                        )
                    ),
                    "total_return_pct": (
                        _safe_metric(
                            metrics,
                            "total_return_pct",
                        )
                    ),
                    "cagr_pct": (
                        _safe_metric(
                            metrics,
                            "cagr_pct",
                        )
                    ),
                    "max_drawdown_pct": (
                        _safe_metric(
                            metrics,
                            "max_drawdown_pct",
                        )
                    ),
                    "sharpe_ratio": (
                        _safe_metric(
                            metrics,
                            "sharpe_ratio",
                        )
                    ),
                    "sortino_ratio": (
                        _safe_metric(
                            metrics,
                            "sortino_ratio",
                        )
                    ),
                    "profit_factor": (
                        _safe_metric(
                            metrics,
                            "profit_factor",
                        )
                    ),
                    "win_rate_pct": (
                        _safe_metric(
                            metrics,
                            "win_rate_pct",
                        )
                    ),
                    "expectancy_pct": (
                        _safe_metric(
                            metrics,
                            "expectancy_pct",
                        )
                    ),
                    "rejected_trades": int(
                        metrics.get(
                            "rejected_trades",
                            0,
                        )
                    ),
                    "total_transaction_cost": sum(
                        float(
                            getattr(
                                trade,
                                "total_transaction_cost",
                                0.0,
                            )
                        )
                        for trade
                        in trades
                    ),
                }
            )

            if not equity_df.empty:
                model_equity_df = (
                    equity_df.copy()
                )

                model_equity_df.insert(
                    0,
                    "entry_model",
                    entry_model_name,
                )

                model_equity_df.insert(
                    0,
                    "ranking_method",
                    ranking_method,
                )

                equity_frames.append(
                    model_equity_df
                )

            for trade in trades:
                row = trade_to_row(
                    trade=trade,
                    entry_model_name=(
                        entry_model_name
                    ),
                )

                row = {
                    "ranking_method": (
                        ranking_method
                    ),
                    **row,
                }

                trade_rows.append(
                    row
                )

            print(
                f"Trades: "
                f"{metrics.get('total_trades', len(trades))}"
            )
            print(
                f"Return: "
                f"{_safe_metric(metrics, 'total_return_pct'):+.2f}%"
            )
            print(
                f"Sharpe: "
                f"{_safe_metric(metrics, 'sharpe_ratio'):.2f}"
            )
            print(
                f"Drawdown: "
                f"{_safe_metric(metrics, 'max_drawdown_pct'):.2f}%"
            )
            print(
                f"PF: "
                f"{_safe_metric(metrics, 'profit_factor'):.2f}"
            )

    summary_df = pd.DataFrame(
        summary_rows
    )

    if not summary_df.empty:
        summary_df = (
            summary_df
            .sort_values(
                by=[
                    "entry_model",
                    "sharpe_ratio",
                    "total_return_pct",
                    "max_drawdown_pct",
                    "profit_factor",
                ],
                ascending=[
                    True,
                    False,
                    False,
                    False,
                    False,
                ],
            )
            .reset_index(
                drop=True
            )
        )

        summary_df[
            "model_rank"
        ] = (
            summary_df
            .groupby(
                "entry_model"
            )
            .cumcount()
            + 1
        )

        summary_df.insert(
            0,
            "rank",
            range(
                1,
                len(
                    summary_df
                )
                + 1,
            ),
        )

    equity_result_df = (
        pd.concat(
            equity_frames,
            ignore_index=True,
        )
        if equity_frames
        else pd.DataFrame()
    )

    trades_df = pd.DataFrame(
        trade_rows
    )

    save_dataframe(
        summary_df,
        summary_output_path,
    )

    save_dataframe(
        equity_result_df,
        equity_output_path,
    )

    save_dataframe(
        trades_df,
        trades_output_path,
    )

    print()
    print(
        "=" * 190
    )
    print(
        "CANDIDATE RANKING SUMMARY"
    )
    print(
        "=" * 190
    )

    if summary_df.empty:
        print(
            "Không có kết quả."
        )

    else:
        display_columns = [
            "model_rank",
            "entry_model",
            "ranking_method",
            "total_trades",
            "total_return_pct",
            "cagr_pct",
            "sharpe_ratio",
            "sortino_ratio",
            "profit_factor",
            "win_rate_pct",
            "expectancy_pct",
            "max_drawdown_pct",
            "rejected_trades",
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

    return (
        summary_df,
        equity_result_df,
        trades_df,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark toàn bộ Candidate "
            "Ranking Methods."
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
        "--hold",
        type=int,
        default=30,
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
        default=5.0,
    )

    parser.add_argument(
        "--ranking",
        nargs="+",
        default=[
            method.value
            for method
            in RankingMethod
        ],
        choices=[
            method.value
            for method
            in RankingMethod
        ],
        help=(
            "Một hoặc nhiều ranking method. "
            "Mặc định chạy toàn bộ."
        ),
    )

    parser.add_argument(
        "--summary-output",
        default=(
            DEFAULT_SUMMARY_OUTPUT
        ),
    )

    parser.add_argument(
        "--equity-output",
        default=(
            DEFAULT_EQUITY_OUTPUT
        ),
    )

    parser.add_argument(
        "--trades-output",
        default=(
            DEFAULT_TRADES_OUTPUT
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
            for symbol
            in args.symbol
        ]
    )

    ranking_methods = list(
        dict.fromkeys(
            args.ranking
        )
    )

    run_candidate_ranking_benchmark(
        symbols=symbols,
        start_date=args.start,
        end_date=args.end,
        initial_capital=(
            args.capital
        ),
        max_positions=(
            args.max_positions
        ),
        stop_loss_pct=(
            args.sl
        ),
        take_profit_pct=(
            args.tp
        ),
        max_holding_days=(
            args.hold
        ),
        min_adx=(
            args.min_adx
        ),
        atr_stop_multiplier=(
            args.atr_stop_multiplier
        ),
        atr_target_multiplier=(
            args.atr_target_multiplier
        ),
        ranking_methods=(
            ranking_methods
        ),
        summary_output_path=(
            args.summary_output
        ),
        equity_output_path=(
            args.equity_output
        ),
        trades_output_path=(
            args.trades_output
        ),
    )


if __name__ == "__main__":
    main()
