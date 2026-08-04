from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from backtesting.engine import (
    build_exit_model,
    run_backtest,
)
from backtesting.position_sizers import (
    AtrRiskSizer,
    FixedFractionSizer,
    PositionSizer,
)
from research.benchmark_portfolio_models import (
    build_portfolio_model_registry,
)
from research.universes import TOP10_SYMBOLS


DEFAULT_SUMMARY_OUTPUT = (
    "research_results/"
    "position_sizers_summary.csv"
)

DEFAULT_EQUITY_OUTPUT = (
    "research_results/"
    "position_sizers_equity.csv"
)

DEFAULT_TRADES_OUTPUT = (
    "research_results/"
    "position_sizers_trades.csv"
)

DEFAULT_REPORT_OUTPUT = (
    "research_results/"
    "position_sizers_report.md"
)


@dataclass(frozen=True)
class PositionSizerSpec:
    name: str
    family: str
    factory: Callable[[], PositionSizer]
    risk_per_trade_pct: float | None = None
    max_position_size_pct: float | None = None
    position_size_pct: float | None = None


def build_position_sizer_specs(
    *,
    fixed_fraction_pct: float,
    atr_risk_levels: list[float],
    atr_stop_multiplier: float,
    max_position_size_pct: float,
) -> list[PositionSizerSpec]:
    specs = [
        PositionSizerSpec(
            name=(
                "fixed_fraction_"
                f"{fixed_fraction_pct:g}"
            ),
            family="fixed_fraction",
            factory=lambda: FixedFractionSizer(
                position_size_pct=(
                    fixed_fraction_pct
                )
            ),
            position_size_pct=(
                fixed_fraction_pct
            ),
        )
    ]

    for risk_level in atr_risk_levels:
        specs.append(
            PositionSizerSpec(
                name=(
                    "atr_risk_"
                    + str(risk_level)
                    .replace(".", "_")
                ),
                family="atr_risk",
                factory=(
                    lambda level=risk_level:
                    AtrRiskSizer(
                        risk_per_trade_pct=level,
                        atr_stop_multiplier=(
                            atr_stop_multiplier
                        ),
                        max_position_size_pct=(
                            max_position_size_pct
                        ),
                    )
                ),
                risk_per_trade_pct=(
                    risk_level
                ),
                max_position_size_pct=(
                    max_position_size_pct
                ),
            )
        )

    return specs


def safe_float(
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


def save_dataframe(
    dataframe: pd.DataFrame,
    output_path: str,
) -> None:
    output = Path(output_path)
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
            "Hãy đóng file trong Excel."
        ) from exc

    print(f"Đã xuất: {output}")


def trade_to_row(
    *,
    trade: Any,
    entry_model_name: str,
    sizer_spec: PositionSizerSpec,
) -> dict[str, Any]:
    entry_price = safe_float(
        getattr(
            trade,
            "entry_price",
            0.0,
        )
    )
    quantity = int(
        getattr(
            trade,
            "quantity",
            0,
        )
        or 0
    )

    return {
        "entry_model": entry_model_name,
        "position_sizer": sizer_spec.name,
        "sizer_family": sizer_spec.family,
        "risk_per_trade_pct": (
            sizer_spec.risk_per_trade_pct
        ),
        "max_position_size_pct": (
            sizer_spec.max_position_size_pct
        ),
        "position_size_pct": (
            sizer_spec.position_size_pct
        ),
        "symbol": getattr(
            trade,
            "symbol",
            None,
        ),
        "entry_date": getattr(
            trade,
            "entry_date",
            None,
        ),
        "exit_date": getattr(
            trade,
            "exit_date",
            None,
        ),
        "entry_price": entry_price,
        "exit_price": safe_float(
            getattr(
                trade,
                "exit_price",
                0.0,
            )
        ),
        "quantity": quantity,
        "position_value": (
            entry_price * quantity
        ),
        "gross_pnl": safe_float(
            getattr(
                trade,
                "gross_pnl",
                0.0,
            )
        ),
        "net_pnl": safe_float(
            getattr(
                trade,
                "net_pnl",
                getattr(
                    trade,
                    "pnl",
                    0.0,
                ),
            )
        ),
        "net_return_pct": safe_float(
            getattr(
                trade,
                "net_return_pct",
                getattr(
                    trade,
                    "return_pct",
                    0.0,
                ),
            )
        ),
        "holding_days": safe_float(
            getattr(
                trade,
                "holding_days",
                0.0,
            )
        ),
        "atr": getattr(
            trade,
            "atr",
            None,
        ),
        "signal_score": getattr(
            trade,
            "signal_score",
            None,
        ),
        "relative_strength": getattr(
            trade,
            "relative_strength",
            None,
        ),
        "exit_reason": str(
            getattr(
                trade,
                "exit_reason",
                "",
            )
        ),
        "transaction_cost": safe_float(
            getattr(
                trade,
                "total_transaction_cost",
                0.0,
            )
        ),
    }


def calculate_equity_diagnostics(
    equity_df: pd.DataFrame,
) -> dict[str, float]:
    if equity_df.empty:
        return {
            "average_cash": 0.0,
            "average_equity": 0.0,
            "average_cash_utilization_pct": 0.0,
            "max_cash_utilization_pct": 0.0,
            "average_open_positions": 0.0,
        }

    cash = pd.to_numeric(
        equity_df.get(
            "cash",
            pd.Series(dtype=float),
        ),
        errors="coerce",
    )

    equity = pd.to_numeric(
        equity_df.get(
            "equity",
            pd.Series(dtype=float),
        ),
        errors="coerce",
    )

    open_positions = pd.to_numeric(
        equity_df.get(
            "open_positions",
            pd.Series(dtype=float),
        ),
        errors="coerce",
    )

    utilization = pd.Series(
        dtype=float
    )

    if (
        not cash.empty
        and not equity.empty
    ):
        utilization = (
            1
            - cash.div(
                equity.replace(
                    0,
                    pd.NA,
                )
            )
        ) * 100

    return {
        "average_cash": safe_float(
            cash.mean()
        ),
        "average_equity": safe_float(
            equity.mean()
        ),
        "average_cash_utilization_pct": (
            safe_float(
                utilization.mean()
            )
        ),
        "max_cash_utilization_pct": (
            safe_float(
                utilization.max()
            )
        ),
        "average_open_positions": (
            safe_float(
                open_positions.mean()
            )
        ),
    }


def markdown_table(
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

    for _, row in dataframe.iterrows():
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


def build_report(
    summary_df: pd.DataFrame,
) -> str:
    lines = [
        "# Position Sizer Benchmark",
        "",
        (
            "This report compares Fixed Fraction "
            "sizing with ATR Risk sizing while "
            "holding the entry model, exit model, "
            "ranking method, universe, and costs "
            "constant."
        ),
        "",
        "## Leaderboard",
        "",
    ]

    report_columns = [
        "rank",
        "entry_model",
        "position_sizer",
        "total_return_pct",
        "cagr_pct",
        "sharpe_ratio",
        "sortino_ratio",
        "max_drawdown_pct",
        "profit_factor",
        "win_rate_pct",
        "average_position_value",
        "average_cash_utilization_pct",
        "total_transaction_cost",
    ]

    available_columns = [
        column
        for column in report_columns
        if column in summary_df.columns
    ]

    lines.append(
        markdown_table(
            summary_df[
                available_columns
            ]
        )
    )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            (
                "- Compare Sharpe and drawdown before "
                "comparing raw return."
            ),
            (
                "- ATR Risk should reduce the size of "
                "high-volatility positions and increase "
                "the size of low-volatility positions, "
                "subject to the position-value cap."
            ),
            (
                "- Fixed Fraction remains the baseline. "
                "ATR Risk should only replace it if the "
                "improvement is consistent across entry "
                "models and not driven by one isolated "
                "configuration."
            ),
            (
                "- This benchmark is in-sample over the "
                "selected full period. The winning sizer "
                "should later be checked with rolling OOS "
                "validation."
            ),
            "",
        ]
    )

    return "\n".join(lines)


def run_position_sizer_benchmark(
    *,
    symbols: list[str],
    entry_models: list[str] | None,
    start_date: str,
    end_date: str,
    initial_capital: float,
    max_positions: int,
    stop_loss_pct: float,
    take_profit_pct: float,
    max_holding_days: int,
    min_adx: float,
    ranking_method: str,
    atr_stop_multiplier: float,
    atr_target_multiplier: float,
    fixed_fraction_pct: float,
    atr_risk_levels: list[float],
    max_position_size_pct: float,
    summary_output_path: str,
    equity_output_path: str,
    trades_output_path: str,
    report_output_path: str,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    if max_positions < 1:
        raise ValueError(
            "max_positions phải từ 1 trở lên."
        )

    if not atr_risk_levels:
        raise ValueError(
            "Phải có ít nhất một ATR risk level."
        )

    if any(
        level <= 0
        for level in atr_risk_levels
    ):
        raise ValueError(
            "ATR risk level phải lớn hơn 0."
        )

    registry = (
        build_portfolio_model_registry()
    )

    if entry_models is not None:
        unknown_models = (
            set(entry_models)
            - set(registry)
        )

        if unknown_models:
            raise ValueError(
                "Entry model không hợp lệ: "
                + ", ".join(
                    sorted(
                        unknown_models
                    )
                )
            )

        registry = {
            name: registry[name]
            for name in entry_models
        }

    sizer_specs = build_position_sizer_specs(
        fixed_fraction_pct=(
            fixed_fraction_pct
        ),
        atr_risk_levels=(
            atr_risk_levels
        ),
        atr_stop_multiplier=(
            atr_stop_multiplier
        ),
        max_position_size_pct=(
            max_position_size_pct
        ),
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

    print("=" * 110)
    print(
        "POSITION SIZER BENCHMARK"
    )
    print("=" * 110)
    print(
        f"Symbols          : "
        f"{len(symbols)}"
    )
    print(
        f"Entry models     : "
        f"{len(registry)}"
    )
    print(
        f"Position sizers  : "
        f"{len(sizer_specs)}"
    )
    print(
        f"Ranking          : "
        f"{ranking_method}"
    )
    print(
        f"Period           : "
        f"{start_date} -> {end_date}"
    )
    print("=" * 110)

    for (
        entry_model_name,
        entry_model,
    ) in registry.items():
        for sizer_spec in sizer_specs:
            print()
            print("-" * 110)
            print(
                f"MODEL={entry_model_name} | "
                f"SIZER={sizer_spec.name}"
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
                sizer_spec.factory()
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
                    fixed_fraction_pct
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
                position_sizer=(
                    position_sizer
                ),
                verbose=False,
            )

            diagnostics = (
                calculate_equity_diagnostics(
                    equity_df
                )
            )

            position_values = pd.Series(
                [
                    safe_float(
                        getattr(
                            trade,
                            "entry_price",
                            0.0,
                        )
                    )
                    * int(
                        getattr(
                            trade,
                            "quantity",
                            0,
                        )
                        or 0
                    )
                    for trade in trades
                ],
                dtype=float,
            )

            quantities = pd.Series(
                [
                    int(
                        getattr(
                            trade,
                            "quantity",
                            0,
                        )
                        or 0
                    )
                    for trade in trades
                ],
                dtype=float,
            )

            final_equity = safe_float(
                metrics.get(
                    "final_equity",
                    (
                        equity_df[
                            "equity"
                        ].iloc[-1]
                        if (
                            not equity_df.empty
                            and "equity"
                            in equity_df.columns
                        )
                        else initial_capital
                    ),
                )
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
                    "entry_model": (
                        entry_model_name
                    ),
                    "position_sizer": (
                        sizer_spec.name
                    ),
                    "sizer_family": (
                        sizer_spec.family
                    ),
                    "risk_per_trade_pct": (
                        sizer_spec
                        .risk_per_trade_pct
                    ),
                    "max_position_size_pct": (
                        sizer_spec
                        .max_position_size_pct
                    ),
                    "position_size_pct": (
                        sizer_spec
                        .position_size_pct
                    ),
                    "symbols": len(symbols),
                    "start_date": start_date,
                    "end_date": end_date,
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
                    "total_trades": (
                        int(
                            metrics.get(
                                "total_trades",
                                len(trades),
                            )
                        )
                    ),
                    "total_return_pct": (
                        safe_float(
                            metrics.get(
                                "total_return_pct"
                            )
                        )
                    ),
                    "cagr_pct": (
                        safe_float(
                            metrics.get(
                                "cagr_pct"
                            )
                        )
                    ),
                    "max_drawdown_pct": (
                        safe_float(
                            metrics.get(
                                "max_drawdown_pct"
                            )
                        )
                    ),
                    "sharpe_ratio": (
                        safe_float(
                            metrics.get(
                                "sharpe_ratio"
                            )
                        )
                    ),
                    "sortino_ratio": (
                        safe_float(
                            metrics.get(
                                "sortino_ratio"
                            )
                        )
                    ),
                    "profit_factor": (
                        safe_float(
                            metrics.get(
                                "profit_factor"
                            )
                        )
                    ),
                    "win_rate_pct": (
                        safe_float(
                            metrics.get(
                                "win_rate_pct"
                            )
                        )
                    ),
                    "expectancy_pct": (
                        safe_float(
                            metrics.get(
                                "expectancy_pct"
                            )
                        )
                    ),
                    "average_quantity": (
                        safe_float(
                            quantities.mean()
                        )
                    ),
                    "average_position_value": (
                        safe_float(
                            position_values.mean()
                        )
                    ),
                    "median_position_value": (
                        safe_float(
                            position_values.median()
                        )
                    ),
                    "total_transaction_cost": (
                        safe_float(
                            metrics.get(
                                "total_transaction_cost"
                            )
                        )
                    ),
                    **diagnostics,
                }
            )

            if not equity_df.empty:
                model_equity = (
                    equity_df.copy()
                )

                model_equity[
                    "entry_model"
                ] = entry_model_name

                model_equity[
                    "position_sizer"
                ] = sizer_spec.name

                metadata_columns = [
                    "entry_model",
                    "position_sizer",
                ]

                remaining_columns = [
                    column
                    for column
                    in model_equity.columns
                    if column
                    not in metadata_columns
                ]

                model_equity = (
                    model_equity[
                        metadata_columns
                        + remaining_columns
                    ]
                )

                equity_frames.append(
                    model_equity
                )

            for trade in trades:
                trade_rows.append(
                    trade_to_row(
                        trade=trade,
                        entry_model_name=(
                            entry_model_name
                        ),
                        sizer_spec=(
                            sizer_spec
                        ),
                    )
                )

            print(
                f"Trades: "
                f"{metrics.get('total_trades', len(trades))}"
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
                f"Average position value: "
                f"{safe_float(position_values.mean()):,.0f}"
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
                "profit_factor",
            ],
            ascending=[
                False,
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

    report = build_report(
        summary_df
    )

    report_path = Path(
        report_output_path
    )
    report_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    report_path.write_text(
        report,
        encoding="utf-8",
    )
    print(
        f"Đã xuất: {report_path}"
    )

    print()
    print("=" * 210)
    print(
        "POSITION SIZER SUMMARY"
    )
    print("=" * 210)

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
        equity_result_df,
        trades_df,
    )


def parse_risk_levels(
    value: str,
) -> list[float]:
    levels = [
        float(item.strip())
        for item in value.split(",")
        if item.strip()
    ]

    return list(
        dict.fromkeys(
            levels
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark Fixed Fraction và "
            "ATR Risk position sizing."
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
        default=None,
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
        "--fixed-fraction",
        type=float,
        default=20.0,
    )

    parser.add_argument(
        "--atr-risk-levels",
        type=parse_risk_levels,
        default=[
            0.50,
            0.75,
            1.00,
            1.25,
            1.50,
        ],
        help=(
            "Danh sách cách nhau bằng dấu phẩy, "
            "ví dụ 0.5,0.75,1.0."
        ),
    )

    parser.add_argument(
        "--max-position-size",
        type=float,
        default=20.0,
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

    parser.add_argument(
        "--report-output",
        default=(
            DEFAULT_REPORT_OUTPUT
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

    run_position_sizer_benchmark(
        symbols=symbols,
        entry_models=(
            args.entry_model
        ),
        start_date=args.start,
        end_date=args.end,
        initial_capital=(
            args.capital
        ),
        max_positions=(
            args.max_positions
        ),
        stop_loss_pct=args.sl,
        take_profit_pct=args.tp,
        max_holding_days=(
            args.hold
        ),
        min_adx=args.min_adx,
        ranking_method=(
            args.ranking
        ),
        atr_stop_multiplier=(
            args.atr_stop_multiplier
        ),
        atr_target_multiplier=(
            args.atr_target_multiplier
        ),
        fixed_fraction_pct=(
            args.fixed_fraction
        ),
        atr_risk_levels=(
            args.atr_risk_levels
        ),
        max_position_size_pct=(
            args.max_position_size
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
        report_output_path=(
            args.report_output
        ),
    )


if __name__ == "__main__":
    main()
