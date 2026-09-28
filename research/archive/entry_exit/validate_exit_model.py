from __future__ import annotations

import argparse
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


DEFAULT_OUTPUT = (
    "research_results/"
    "exit_model_validation.csv"
)


def validate_exit_model(
    *,
    symbols: list[str],
    start_date: str | None,
    end_date: str | None,
    exit_model_name: str,
    break_even_trigger: float,
    atr_stop_multiplier: float,
    atr_target_multiplier: float,
    trailing_atr_multiplier: float,
    stop_loss_pct: float,
    take_profit_pct: float,
    max_holding_days: int,
    min_adx: float,
    output_path: str,
) -> pd.DataFrame:
    rows: list[dict] = []

    total = len(symbols)

    for index, symbol in enumerate(
        symbols,
        start=1,
    ):
        print(
            f"[{index}/{total}] "
            f"Validate {symbol}"
        )

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

        _, metrics, _ = run_backtest(
            symbols=[symbol],
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            max_holding_days=max_holding_days,
            min_adx=min_adx,
            start_date=start_date,
            end_date=end_date,
            verbose=False,
            exit_model=exit_model,
        )

        row = {
            "symbol": symbol,
            "exit_model": exit_model_name,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "max_holding_days": max_holding_days,
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
            "total_trades": metrics.get(
                "total_trades",
                0,
            ),
            "total_return_pct": metrics.get(
                "total_return_pct",
                0.0,
            ),
            "cagr_pct": metrics.get(
                "cagr_pct",
                0.0,
            ),
            "max_drawdown_pct": metrics.get(
                "max_drawdown_pct",
                0.0,
            ),
            "sharpe_ratio": metrics.get(
                "sharpe_ratio",
                0.0,
            ),
            "sortino_ratio": metrics.get(
                "sortino_ratio",
                0.0,
            ),
            "profit_factor": metrics.get(
                "profit_factor",
                0.0,
            ),
            "win_rate_pct": metrics.get(
                "win_rate_pct",
                0.0,
            ),
            "expectancy_pct": metrics.get(
                "expectancy_pct",
                0.0,
            ),
        }

        rows.append(row)

        print(
            f"    Return "
            f"{row['total_return_pct']:+.2f}% | "
            f"Sharpe "
            f"{row['sharpe_ratio']:.2f} | "
            f"DD "
            f"{row['max_drawdown_pct']:.2f}%"
        )

    result_df = pd.DataFrame(rows)

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

    print_summary(result_df)

    print()
    print(f"Đã xuất: {output}")

    return result_df


def print_summary(
    result_df: pd.DataFrame,
) -> None:
    if result_df.empty:
        print("Không có kết quả.")
        return

    average_return = mean(
        result_df[
            "total_return_pct"
        ].astype(float)
    )

    average_sharpe = mean(
        result_df[
            "sharpe_ratio"
        ].astype(float)
    )

    average_drawdown = mean(
        result_df[
            "max_drawdown_pct"
        ].astype(float)
    )

    average_profit_factor = mean(
        result_df[
            "profit_factor"
        ].astype(float)
    )

    best_row = result_df.loc[
        result_df[
            "sharpe_ratio"
        ].astype(float).idxmax()
    ]

    worst_row = result_df.loc[
        result_df[
            "sharpe_ratio"
        ].astype(float).idxmin()
    ]

    positive_count = int(
        (
            result_df[
                "total_return_pct"
            ].astype(float)
            > 0
        ).sum()
    )

    print()
    print("=" * 60)
    print("EXIT MODEL VALIDATION SUMMARY")
    print("=" * 60)
    print(
        f"Symbols          : "
        f"{len(result_df)}"
    )
    print(
        f"Positive Return  : "
        f"{positive_count}/"
        f"{len(result_df)}"
    )
    print(
        f"Average Return   : "
        f"{average_return:+.2f}%"
    )
    print(
        f"Average Sharpe   : "
        f"{average_sharpe:.2f}"
    )
    print(
        f"Average Drawdown : "
        f"{average_drawdown:.2f}%"
    )
    print(
        f"Average PF       : "
        f"{average_profit_factor:.2f}"
    )
    print(
        f"Best Symbol      : "
        f"{best_row['symbol']} "
        f"(Sharpe "
        f"{float(best_row['sharpe_ratio']):.2f})"
    )
    print(
        f"Worst Symbol     : "
        f"{worst_row['symbol']} "
        f"(Sharpe "
        f"{float(worst_row['sharpe_ratio']):.2f})"
    )
    print("=" * 60)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate một Exit Model "
            "trên nhiều cổ phiếu."
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
        default="2015-07-16",
    )

    parser.add_argument(
        "--end",
        default="2026-07-31",
    )

    parser.add_argument(
        "--exit-model",
        choices=[
            "fixed",
            "atr",
            "break_even",
            "trailing_atr",
        ],
        default="trailing_atr",
    )

    parser.add_argument(
        "--break-even-trigger",
        type=float,
        default=5.0,
    )

    parser.add_argument(
        "--atr-stop-multiplier",
        type=float,
        default=2.5,
    )

    parser.add_argument(
        "--atr-target-multiplier",
        type=float,
        default=5.0,
    )

    parser.add_argument(
        "--trailing-atr-multiplier",
        type=float,
        default=2.5,
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

    validate_exit_model(
        symbols=symbols,
        start_date=args.start,
        end_date=args.end,
        exit_model_name=args.exit_model,
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
        stop_loss_pct=args.sl,
        take_profit_pct=args.tp,
        max_holding_days=args.hold,
        min_adx=args.min_adx,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()