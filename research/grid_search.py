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
from concurrent.futures import (
    ProcessPoolExecutor,
    as_completed,
)
from research.utils import (
    generate_parameter_sets,
)


DEFAULT_OUTPUT = "research_results/grid_search.csv"

def run_single_parameter_set(
    payload: dict[str, Any],
) -> dict[str, Any]:
    parameters = payload["parameters"]
    symbols = payload["symbols"]
    start_date = payload["start_date"]
    end_date = payload["end_date"]
    sequence = payload["sequence"]

    started_at = perf_counter()

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

    elapsed_seconds = (
        perf_counter() - started_at
    )

    return {
        "_sequence": sequence,
        **parameters,
        "symbols": ",".join(symbols),
        "elapsed_seconds": round(
            elapsed_seconds,
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

def format_duration(
    seconds: float,
) -> str:
    seconds = max(0, int(seconds))

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

def run_grid_search(
    *,
    symbols: list[str],
    start_date: str | None,
    end_date: str | None,
    output_path: str = DEFAULT_OUTPUT,
    limit: int | None = None,
    workers: int = 1,
) -> pd.DataFrame:
    parameter_sets = list(
        generate_parameter_sets(
            DEFAULT_PARAMETER_SPACE
        )
    )

    if limit is not None:
        parameter_sets = parameter_sets[:limit]
 
    tasks = [
        {
            "sequence": index,
            "parameters": parameters,
            "symbols": symbols,
            "start_date": start_date,
            "end_date": end_date,
        }
        for index, parameters in enumerate(
            parameter_sets,
            start=1,
        )
    ]

    results: list[dict[str, Any]] = []

    total = len(tasks)
    workers = max(1, workers)

    search_started_at = perf_counter()

    print(
        f"Chạy {total} tổ hợp "
        f"với {workers} worker(s)."
    )

    if workers == 1:
        for completed, task in enumerate(
            tasks,
            start=1,
        ):
            row = run_single_parameter_set(
                task
            )

            results.append(row)

            elapsed = (
                perf_counter()
                - search_started_at
            )

            average = elapsed / completed

            eta = average * (
                total - completed
            )

            print(
                f"[{completed}/{total}] "
                f"SL={row['stop_loss_pct']} "
                f"TP={row['take_profit_pct']} "
                f"H={row['max_holding_days']} "
                f"ADX={row['min_adx']} | "
                f"{row['elapsed_seconds']:.2f}s | "
                f"Return "
                f"{row['total_return_pct']:+.2f}% | "
                f"ETA {format_duration(eta)}"
            )

    else:
        with ProcessPoolExecutor(
            max_workers=workers
        ) as executor:
            future_map = {
                executor.submit(
                    run_single_parameter_set,
                    task,
                ): task
                for task in tasks
            }

            for completed, future in enumerate(
                as_completed(future_map),
                start=1,
            ):
                row = future.result()

                results.append(row)

                elapsed = (
                    perf_counter()
                    - search_started_at
                )

                average = elapsed / completed

                eta = average * (
                    total - completed
                )

                print(
                    f"[{completed}/{total}] "
                    f"SL={row['stop_loss_pct']} "
                    f"TP={row['take_profit_pct']} "
                    f"H={row['max_holding_days']} "
                    f"ADX={row['min_adx']} | "
                    f"{row['elapsed_seconds']:.2f}s | "
                    f"Return "
                    f"{row['total_return_pct']:+.2f}% | "
                    f"ETA {format_duration(eta)}"
                )

    result_df = pd.DataFrame(results)

    if not result_df.empty:
        result_df = (
            result_df
            .sort_values("_sequence")
            .drop(columns=["_sequence"])
            .reset_index(drop=True)
        )

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

    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "Số process chạy song song. "
            "Nên bắt đầu với 2 hoặc 4."
        ),
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
        workers=args.workers,
    )


if __name__ == "__main__":
    main()