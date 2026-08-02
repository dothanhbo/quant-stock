from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any
from time import perf_counter

import pandas as pd

from backtesting.engine import run_backtest
from research.parameter_space import (
    DEFAULT_PARAMETER_SPACE,
)
from research.utils import (
    generate_parameter_sets,
)


DEFAULT_OUTPUT = "research_results/grid_search.csv"


def run_grid_search(
    *,
    symbols: list[str],
    start_date: str | None,
    end_date: str | None,
    output_path: str = DEFAULT_OUTPUT,
    limit: int | None = None,
) -> pd.DataFrame:
    parameter_sets = list(
        generate_parameter_sets(
            DEFAULT_PARAMETER_SPACE
        )
    )

    if limit is not None:
        parameter_sets = parameter_sets[:limit]

    results: list[dict[str, Any]] = []

    total = len(parameter_sets)

    for index, parameters in enumerate(
        parameter_sets,
        start=1,
    ):
        print(
            f"[{index}/{total}] "
            f"SL={parameters['stop_loss_pct']} "
            f"TP={parameters['take_profit_pct']} "
            f"H={parameters['max_holding_days']} "
            f"ADX={parameters['min_adx']}"
        )

        start_time = perf_counter()

        _, metrics, _ = run_backtest(
            symbols=symbols,
            stop_loss_pct=parameters[
                "stop_loss_pct"
            ],
            take_profit_pct=parameters[
                "take_profit_pct"
            ],
            max_holding_days=parameters[
                "max_holding_days"
            ],
            min_adx=parameters[
                "min_adx"
            ],
            start_date=start_date,
            end_date=end_date,
            verbose=False,
        )

        elapsed = perf_counter() - start_time
        print(
            f"   ✓ {elapsed:.2f}s | "
            f"Return {metrics['total_return_pct']:+.2f}% | "
            f"Sharpe {metrics['sharpe_ratio']:.2f}"
        )

        row = {
            **parameters,
            "symbols": ",".join(symbols),
            "elapsed_seconds": round(
                elapsed,
                2,
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
            "benchmark_return_pct": metrics.get(
                "benchmark_return_pct",
                0.0,
            ),
            "strategy_vs_benchmark_pct": metrics.get(
                "strategy_vs_benchmark_pct",
                0.0,
            ),
        }

        results.append(row)

    result_df = pd.DataFrame(results)

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

    print()
    print(f"Đã xuất: {output}")
    print(f"Tổng số kết quả: {len(result_df)}")

    return result_df


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Grid Search cho Quant Stock."
    )

    parser.add_argument(
        "--symbol",
        nargs="+",
        required=True,
    )

    parser.add_argument(
        "--start",
        default=None,
    )

    parser.add_argument(
        "--end",
        default=None,
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Giới hạn số tổ hợp để test nhanh.",
    )

    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    symbols = [
        symbol.upper().strip()
        for symbol in args.symbol
    ]

    run_grid_search(
        symbols=symbols,
        start_date=args.start,
        end_date=args.end,
        output_path=args.output,
        limit=args.limit,
    )


if __name__ == "__main__":
    main()