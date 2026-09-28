from __future__ import annotations

import argparse
from pathlib import Path
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


DEFAULT_SUMMARY_OUTPUT = (
    "research_results/"
    "portfolio_models_summary.csv"
)

DEFAULT_EQUITY_OUTPUT = (
    "research_results/"
    "portfolio_models_equity.csv"
)

DEFAULT_TRADES_OUTPUT = (
    "research_results/"
    "portfolio_models_trades.csv"
)


def build_portfolio_model_registry(
) -> dict[str, BaseStrategy]:
    models: list[BaseStrategy] = [
        DonchianBreakoutEntryModel(),

        HybridTrendDonchianEntryModel(
            mode="trend_context",
        ),

        HybridTrendDonchianEntryModel(
            mode="strict",
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


def trade_to_row(
    *,
    trade: Any,
    entry_model_name: str,
) -> dict[str, Any]:
    return {
        "entry_model": (
            entry_model_name
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
        "entry_price": getattr(
            trade,
            "entry_price",
            0.0,
        ),
        "exit_price": getattr(
            trade,
            "exit_price",
            0.0,
        ),
        "quantity": getattr(
            trade,
            "quantity",
            0,
        ),
        "cost": getattr(
            trade,
            "cost",
            0.0,
        ),
        "gross_pnl": getattr(
            trade,
            "gross_pnl",
            0.0,
        ),
        "net_pnl": getattr(
            trade,
            "net_pnl",
            getattr(
                trade,
                "pnl",
                0.0,
            ),
        ),
        "net_return_pct": getattr(
            trade,
            "net_return_pct",
            getattr(
                trade,
                "return_pct",
                0.0,
            ),
        ),
        "holding_days": getattr(
            trade,
            "holding_days",
            0,
        ),
        "exit_reason": str(
            getattr(
                trade,
                "exit_reason",
                "",
            )
        ),
        "execution": str(
            getattr(
                trade,
                "execution",
                "",
            )
        ),
        "transaction_cost": getattr(
            trade,
            "total_transaction_cost",
            0.0,
        ),
        "is_win": bool(
            getattr(
                trade,
                "is_win",
                False,
            )
        ),
    }


def run_portfolio_benchmark(
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
    ranking_method: str = "first_come",
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

    registry = (
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

    print(
        f"Portfolio symbols: "
        f"{len(symbols)}"
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

    for (
        entry_model_name,
        entry_model,
    ) in registry.items():
        print()
        print("=" * 100)
        print(
            f"PORTFOLIO MODEL: "
            f"{entry_model_name}"
        )
        print("=" * 100)

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
            ranking_method=ranking_method,
        )

        final_equity = float(
            metrics.get(
                "final_equity",
                (
                    equity_df[
                        "equity"
                    ].iloc[-1]
                    if not equity_df.empty
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
            if not equity_df.empty
            else 0
        )

        summary_rows.append(
            {
                "entry_model": (
                    entry_model_name
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
                "position_size_pct": (
                    position_size_pct
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
                    sum(
                        float(
                            getattr(
                                trade,
                                "total_transaction_cost",
                                0.0,
                            )
                        )
                        for trade in trades
                    )
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

            equity_frames.append(
                model_equity_df
            )

        for trade in trades:
            trade_rows.append(
                trade_to_row(
                    trade=trade,
                    entry_model_name=(
                        entry_model_name
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
            f"Max open positions: "
            f"{max_open_positions}"
        )

    summary_df = pd.DataFrame(
        summary_rows
    )

    summary_df = (
        summary_df
        .sort_values(
            by=[
                "sharpe_ratio",
                "total_return_pct",
                "max_drawdown_pct",
                "profit_factor",
            ],
            ascending=[
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
    print("=" * 150)
    print(
        "PORTFOLIO MODEL SUMMARY"
    )
    print("=" * 150)

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Portfolio benchmark cho "
            "Donchian và Hybrid models."
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
        "--ranking",
        type=str,
        default="first_come",
        choices=[
            "first_come",
            "signal_score",
            "relative_strength",
            "adx",
            "volume_ratio",
            "composite",
        ],
        help="Candidate ranking method.",
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
            for symbol in args.symbol
        ]
    )

    run_portfolio_benchmark(
        symbols=symbols,
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
        max_positions=(
            args.max_positions
        ),
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
        ranking_method=args.ranking,
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