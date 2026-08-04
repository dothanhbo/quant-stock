from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd

from backtesting.engine import (
    build_exit_model,
    run_backtest,
)
from backtesting.position_sizers import (
    FixedFractionSizer,
)
from research.benchmark_portfolio_models import (
    build_portfolio_model_registry,
)
from research.universes import TOP10_SYMBOLS


DEFAULT_OUTPUT_DIR = Path(
    "research_results/portfolio_heat"
)

DEFAULT_HEAT_LEVELS: list[
    float | None
] = [
    None,
    3.0,
    4.0,
    5.0,
    6.0,
]


def _safe_float(
    value: Any,
    default: float = 0.0,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default

    if pd.isna(result):
        return default

    return result


def _heat_label(
    heat_pct: float | None,
) -> str:
    if heat_pct is None:
        return "unlimited"

    return (
        "heat_"
        + str(heat_pct)
        .replace(".", "_")
    )


def _trade_to_dict(
    trade: Any,
) -> dict[str, Any]:
    if hasattr(
        trade,
        "to_dict",
    ):
        row = trade.to_dict()

        if isinstance(row, dict):
            return row

    if hasattr(
        trade,
        "__dict__",
    ):
        return dict(
            vars(trade)
        )

    return {
        "trade": str(trade)
    }


def _markdown_table(
    dataframe: pd.DataFrame,
) -> str:
    if dataframe.empty:
        return "No data."

    columns = list(
        dataframe.columns
    )

    lines = [
        "| "
        + " | ".join(columns)
        + " |",
        "| "
        + " | ".join(
            "---"
            for _ in columns
        )
        + " |",
    ]

    for _, row in (
        dataframe.iterrows()
    ):
        values: list[str] = []

        for column in columns:
            value = row[column]

            if isinstance(
                value,
                float,
            ):
                value = f"{value:.2f}"

            values.append(
                str(value)
            )

        lines.append(
            "| "
            + " | ".join(values)
            + " |"
        )

    return "\n".join(lines)


def _build_report(
    summary_df: pd.DataFrame,
) -> str:
    report_columns = [
        "rank",
        "entry_model",
        "heat_label",
        "max_portfolio_heat_pct",
        "total_trades",
        "rejected_by_heat",
        "total_return_pct",
        "cagr_pct",
        "sharpe_ratio",
        "sortino_ratio",
        "max_drawdown_pct",
        "profit_factor",
        "win_rate_pct",
        "average_portfolio_heat_pct",
        "peak_portfolio_heat_pct",
    ]

    available = [
        column
        for column in report_columns
        if column in summary_df.columns
    ]

    lines = [
        "# Portfolio Heat Benchmark",
        "",
        (
            "This benchmark compares an unlimited "
            "portfolio-heat baseline against explicit "
            "heat limits while keeping the universe, "
            "entry model, exit model, ranking method, "
            "position sizing, and transaction costs fixed."
        ),
        "",
        "## Leaderboard",
        "",
        _markdown_table(
            summary_df[
                available
            ]
        ),
        "",
        "## Reading the results",
        "",
        (
            "- Prefer a heat limit only when drawdown "
            "or risk-adjusted return improves enough to "
            "justify rejected trades and lower exposure."
        ),
        (
            "- A limit that never rejects a trade is "
            "operationally equivalent to Unlimited for "
            "the tested strategy."
        ),
        (
            "- A very low limit may reduce drawdown but "
            "also leave too much capital unused."
        ),
        (
            "- The best full-period result should later "
            "be checked with rolling OOS validation."
        ),
        "",
    ]

    return "\n".join(lines)


def run_portfolio_heat_benchmark(
    *,
    symbols: list[str],
    entry_models: list[str],
    heat_levels: list[
        float | None
    ],
    start_date: str,
    end_date: str,
    initial_capital: float,
    position_size_pct: float,
    ranking_method: str,
    max_holding_days: int,
    min_adx: float,
    atr_stop_multiplier: float,
    atr_target_multiplier: float,
    output_dir: Path,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    registry = (
        build_portfolio_model_registry()
    )

    unknown_models = (
        set(entry_models)
        - set(registry)
    )

    if unknown_models:
        raise ValueError(
            "Entry model không hợp lệ: "
            + ", ".join(
                sorted(unknown_models)
            )
        )

    if any(
        level is not None
        and level <= 0
        for level in heat_levels
    ):
        raise ValueError(
            "Heat level phải lớn hơn 0 "
            "hoặc bằng None."
        )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    summary_rows: list[
        dict[str, Any]
    ] = []

    equity_frames: list[
        pd.DataFrame
    ] = []

    trade_frames: list[
        pd.DataFrame
    ] = []

    print("=" * 110)
    print(
        "PORTFOLIO HEAT BENCHMARK"
    )
    print("=" * 110)
    print(
        f"Symbols      : {len(symbols)}"
    )
    print(
        f"Entry models : {len(entry_models)}"
    )
    print(
        f"Heat levels  : {len(heat_levels)}"
    )
    print(
        f"Ranking      : {ranking_method}"
    )
    print(
        f"Period       : "
        f"{start_date} -> {end_date}"
    )
    print("=" * 110)

    for entry_model_name in (
        entry_models
    ):
        entry_model = registry[
            entry_model_name
        ]

        for heat_level in heat_levels:
            label = _heat_label(
                heat_level
            )

            print()
            print("-" * 110)
            print(
                f"MODEL={entry_model_name} | "
                f"HEAT={label}"
            )
            print("-" * 110)

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

            position_sizer = (
                FixedFractionSizer(
                    position_size_pct=(
                        position_size_pct
                    )
                )
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
                max_holding_days=(
                    max_holding_days
                ),
                min_adx=min_adx,
                entry_model=entry_model,
                exit_model=exit_model,
                ranking_method=(
                    ranking_method
                ),
                position_sizer=(
                    position_sizer
                ),
                max_portfolio_heat_pct=(
                    heat_level
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

            rejected_by_heat = int(
                rejected_reasons.get(
                    "portfolio_heat_limit",
                    0,
                )
            )

            missing_stop_rejections = int(
                rejected_reasons.get(
                    "missing_stop_price",
                    0,
                )
            )

            heat_series = pd.Series(
                dtype=float
            )

            if (
                not equity_df.empty
                and "portfolio_heat_pct"
                in equity_df.columns
            ):
                heat_series = (
                    pd.to_numeric(
                        equity_df[
                            "portfolio_heat_pct"
                        ],
                        errors="coerce",
                    )
                    .dropna()
                )

            summary_rows.append(
                {
                    "entry_model": (
                        entry_model_name
                    ),
                    "heat_label": label,
                    "max_portfolio_heat_pct": (
                        heat_level
                    ),
                    "symbols": len(symbols),
                    "start_date": start_date,
                    "end_date": end_date,
                    "initial_capital": (
                        initial_capital
                    ),
                    "position_size_pct": (
                        position_size_pct
                    ),
                    "ranking_method": (
                        ranking_method
                    ),
                    "total_trades": int(
                        metrics.get(
                            "total_trades",
                            len(trades),
                        )
                    ),
                    "rejected_trades": int(
                        metrics.get(
                            "rejected_trades",
                            0,
                        )
                    ),
                    "rejected_by_heat": (
                        rejected_by_heat
                    ),
                    "missing_stop_rejections": (
                        missing_stop_rejections
                    ),
                    "final_equity": (
                        _safe_float(
                            metrics.get(
                                "final_equity"
                            )
                        )
                    ),
                    "total_return_pct": (
                        _safe_float(
                            metrics.get(
                                "total_return_pct"
                            )
                        )
                    ),
                    "cagr_pct": (
                        _safe_float(
                            metrics.get(
                                "cagr_pct"
                            )
                        )
                    ),
                    "sharpe_ratio": (
                        _safe_float(
                            metrics.get(
                                "sharpe_ratio"
                            )
                        )
                    ),
                    "sortino_ratio": (
                        _safe_float(
                            metrics.get(
                                "sortino_ratio"
                            )
                        )
                    ),
                    "max_drawdown_pct": (
                        _safe_float(
                            metrics.get(
                                "max_drawdown_pct"
                            )
                        )
                    ),
                    "profit_factor": (
                        _safe_float(
                            metrics.get(
                                "profit_factor"
                            )
                        )
                    ),
                    "win_rate_pct": (
                        _safe_float(
                            metrics.get(
                                "win_rate_pct"
                            )
                        )
                    ),
                    "expectancy_pct": (
                        _safe_float(
                            metrics.get(
                                "expectancy_pct"
                            )
                        )
                    ),
                    "total_transaction_cost": (
                        _safe_float(
                            metrics.get(
                                "total_transaction_cost"
                            )
                        )
                    ),
                    "average_portfolio_heat_pct": (
                        _safe_float(
                            heat_series.mean()
                        )
                    ),
                    "median_portfolio_heat_pct": (
                        _safe_float(
                            heat_series.median()
                        )
                    ),
                    "peak_portfolio_heat_pct": (
                        _safe_float(
                            heat_series.max()
                        )
                    ),
                }
            )

            if not equity_df.empty:
                curve = equity_df.copy()
                curve[
                    "entry_model"
                ] = entry_model_name
                curve[
                    "heat_label"
                ] = label
                curve[
                    "configured_heat_limit_pct"
                ] = heat_level

                metadata = [
                    "entry_model",
                    "heat_label",
                    "configured_heat_limit_pct",
                ]

                remaining = [
                    column
                    for column
                    in curve.columns
                    if column not in metadata
                ]

                equity_frames.append(
                    curve[
                        metadata
                        + remaining
                    ]
                )

            if trades:
                trades_df = pd.DataFrame(
                    [
                        _trade_to_dict(
                            trade
                        )
                        for trade in trades
                    ]
                )

                trades_df[
                    "entry_model"
                ] = entry_model_name
                trades_df[
                    "heat_label"
                ] = label
                trades_df[
                    "configured_heat_limit_pct"
                ] = heat_level

                metadata = [
                    "entry_model",
                    "heat_label",
                    "configured_heat_limit_pct",
                ]

                remaining = [
                    column
                    for column
                    in trades_df.columns
                    if column not in metadata
                ]

                trade_frames.append(
                    trades_df[
                        metadata
                        + remaining
                    ]
                )

            print(
                f"Trades: "
                f"{metrics.get('total_trades', len(trades))}"
            )
            print(
                f"Heat rejects: "
                f"{rejected_by_heat}"
            )
            print(
                f"Return: "
                f"{metrics.get('total_return_pct', 0.0):+.2f}%"
            )
            print(
                f"Sharpe: "
                f"{metrics.get('sharpe_ratio', 0.0):.2f}"
            )
            print(
                f"Drawdown: "
                f"{metrics.get('max_drawdown_pct', 0.0):.2f}%"
            )
            print(
                f"Peak heat: "
                f"{_safe_float(heat_series.max()):.2f}%"
            )

    summary_df = pd.DataFrame(
        summary_rows
    )

    summary_df = (
        summary_df
        .sort_values(
            by=[
                "sharpe_ratio",
                "sortino_ratio",
                "total_return_pct",
                "max_drawdown_pct",
            ],
            ascending=[
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

    summary_df.insert(
        0,
        "rank",
        range(
            1,
            len(summary_df) + 1,
        ),
    )

    equity_result = (
        pd.concat(
            equity_frames,
            ignore_index=True,
        )
        if equity_frames
        else pd.DataFrame()
    )

    trades_result = (
        pd.concat(
            trade_frames,
            ignore_index=True,
        )
        if trade_frames
        else pd.DataFrame()
    )

    summary_path = (
        output_dir / "summary.csv"
    )
    equity_path = (
        output_dir / "equity.csv"
    )
    trades_path = (
        output_dir / "trades.csv"
    )
    report_path = (
        output_dir / "report.md"
    )

    summary_df.to_csv(
        summary_path,
        index=False,
        encoding="utf-8-sig",
    )
    equity_result.to_csv(
        equity_path,
        index=False,
        encoding="utf-8-sig",
    )
    trades_result.to_csv(
        trades_path,
        index=False,
        encoding="utf-8-sig",
    )
    report_path.write_text(
        _build_report(
            summary_df
        ),
        encoding="utf-8",
    )

    print()
    print(f"Đã xuất: {summary_path}")
    print(f"Đã xuất: {equity_path}")
    print(f"Đã xuất: {trades_path}")
    print(f"Đã xuất: {report_path}")

    print()
    print("=" * 180)
    print(
        "PORTFOLIO HEAT SUMMARY"
    )
    print("=" * 180)
    print(
        summary_df.to_string(
            index=False,
            float_format=(
                lambda value:
                f"{value:.2f}"
            ),
        )
    )

    return (
        summary_df,
        equity_result,
        trades_result,
    )


def _parse_heat_levels(
    raw: str,
) -> list[float | None]:
    levels: list[
        float | None
    ] = []

    for item in raw.split(","):
        value = item.strip().lower()

        if not value:
            continue

        if value in {
            "none",
            "unlimited",
            "off",
        }:
            levels.append(None)
        else:
            levels.append(
                float(value)
            )

    unique: list[
        float | None
    ] = []

    for level in levels:
        if level not in unique:
            unique.append(level)

    return unique


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark portfolio heat limits."
        )
    )

    parser.add_argument(
        "--symbol",
        nargs="+",
        default=None,
    )

    parser.add_argument(
        "--entry-model",
        nargs="+",
        default=[
            (
                "hybrid_trend_donchian_v1"
                "__trend_context"
            )
        ],
        choices=[
            "donchian_breakout_v1",
            (
                "hybrid_trend_donchian_v1"
                "__trend_context"
            ),
            (
                "hybrid_trend_donchian_v1"
                "__strict"
            ),
        ],
    )

    parser.add_argument(
        "--heat-levels",
        type=_parse_heat_levels,
        default=DEFAULT_HEAT_LEVELS,
        help=(
            "Ví dụ unlimited,3,4,5,6"
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
        "--ranking",
        default="relative_strength",
        choices=[
            "first_come",
            "signal_score",
            "relative_strength",
            "adx",
            "volume_ratio",
            "composite",
        ],
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
        "--output",
        default=str(
            DEFAULT_OUTPUT_DIR
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    symbols = (
        list(TOP10_SYMBOLS)
        if args.symbol is None
        else [
            symbol.upper().strip()
            for symbol in args.symbol
        ]
    )

    run_portfolio_heat_benchmark(
        symbols=symbols,
        entry_models=(
            args.entry_model
        ),
        heat_levels=(
            args.heat_levels
        ),
        start_date=args.start,
        end_date=args.end,
        initial_capital=(
            args.capital
        ),
        position_size_pct=(
            args.position_size
        ),
        ranking_method=(
            args.ranking
        ),
        max_holding_days=(
            args.hold
        ),
        min_adx=args.min_adx,
        atr_stop_multiplier=(
            args.atr_stop_multiplier
        ),
        atr_target_multiplier=(
            args.atr_target_multiplier
        ),
        output_dir=Path(
            args.output
        ),
    )


if __name__ == "__main__":
    main()
