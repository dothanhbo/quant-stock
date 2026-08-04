from __future__ import annotations

import argparse
import itertools
from pathlib import Path
from typing import Any

import pandas as pd

from backtesting.allocation_factors import (
    ATRFactor,
    RegimeFactor,
    SignalScoreFactor,
    StopDistanceFactor,
    WeightedAllocationFactor,
)
from backtesting.engine import (
    build_exit_model,
    run_backtest,
)
from backtesting.portfolio_allocation import (
    CompositeAllocator,
)
from backtesting.position_sizers import (
    FixedFractionSizer,
)
from backtesting.regime_policy import (
    RegimePortfolioPolicy,
)
from research.benchmark_portfolio_models import (
    build_portfolio_model_registry,
)
from research.universes import (
    TOP10_SYMBOLS,
)


DEFAULT_OUTPUT_DIR = Path(
    "research_results/"
    "composite_weights"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark CompositeAllocator "
            "factor weight combinations."
        )
    )

    parser.add_argument(
        "--symbols",
        nargs="*",
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
        "--position-size",
        type=float,
        default=20.0,
    )

    parser.add_argument(
        "--hold",
        type=int,
        default=40,
    )

    parser.add_argument(
        "--min-adx",
        type=float,
        default=20.0,
    )

    parser.add_argument(
        "--atr-stop",
        type=float,
        default=2.5,
    )

    parser.add_argument(
        "--atr-target",
        type=float,
        default=5.0,
    )

    parser.add_argument(
        "--entry-model",
        default=(
            "hybrid_trend_donchian_v1"
            "__trend_context"
        ),
    )

    parser.add_argument(
        "--aggregation",
        choices=[
            "sum",
            "product",
        ],
        default="sum",
    )

    parser.add_argument(
        "--weight-values",
        default=(
            "0.10,0.15,0.20,0.25,"
            "0.30,0.35,0.40"
        ),
        help=(
            "Các giá trị weight được phép, "
            "phân cách bằng dấu phẩy."
        ),
    )

    parser.add_argument(
        "--maximum-position",
        type=float,
        default=40.0,
    )

    parser.add_argument(
        "--output",
        default=str(
            DEFAULT_OUTPUT_DIR
        ),
    )

    return parser.parse_args()


def parse_weight_values(
    raw_value: str,
) -> list[float]:
    values: list[float] = []

    for item in raw_value.split(","):
        stripped = item.strip()

        if not stripped:
            continue

        value = float(
            stripped
        )

        if value < 0:
            raise ValueError(
                "Weight không được âm."
            )

        values.append(
            value
        )

    if not values:
        raise ValueError(
            "Không có weight value hợp lệ."
        )

    return sorted(
        set(values)
    )


def generate_weight_grid(
    weight_values: list[float],
    *,
    target_sum: float = 1.0,
    tolerance: float = 1e-9,
) -> list[
    tuple[
        float,
        float,
        float,
        float,
    ]
]:
    combinations: list[
        tuple[
            float,
            float,
            float,
            float,
        ]
    ] = []

    for weights in itertools.product(
        weight_values,
        repeat=4,
    ):
        if abs(
            sum(weights)
            - target_sum
        ) <= tolerance:
            combinations.append(
                (
                    float(weights[0]),
                    float(weights[1]),
                    float(weights[2]),
                    float(weights[3]),
                )
            )

    if not combinations:
        raise ValueError(
            "Không tạo được tổ hợp weight "
            "có tổng bằng 1.0."
        )

    return combinations


def _safe_float(
    value: Any,
    default: float = 0.0,
) -> float:
    try:
        result = float(value)
    except (
        TypeError,
        ValueError,
    ):
        return default

    if pd.isna(result):
        return default

    return result


def build_composite_allocator(
    *,
    signal_weight: float,
    atr_weight: float,
    stop_weight: float,
    regime_weight: float,
    aggregation: str,
    maximum_position_pct: float,
) -> CompositeAllocator:
    factors = [
        WeightedAllocationFactor(
            factor=SignalScoreFactor(
                minimum_score=40,
                maximum_score=100,
            ),
            weight=signal_weight,
        ),
        WeightedAllocationFactor(
            factor=ATRFactor(
                target_atr_pct=3.0,
            ),
            weight=atr_weight,
        ),
        WeightedAllocationFactor(
            factor=StopDistanceFactor(
                target_stop_distance_pct=6.0,
            ),
            weight=stop_weight,
        ),
        WeightedAllocationFactor(
            factor=RegimeFactor(),
            weight=regime_weight,
        ),
    ]

    return CompositeAllocator(
        factors=factors,
        maximum_position_pct=(
            maximum_position_pct
        ),
        aggregation=aggregation,
        name=(
            f"composite_{aggregation}"
        ),
    )


def run_single_combination(
    *,
    combination_id: int,
    weights: tuple[
        float,
        float,
        float,
        float,
    ],
    symbols: list[str],
    args: argparse.Namespace,
    entry_model: Any,
) -> dict[str, Any]:
    (
        signal_weight,
        atr_weight,
        stop_weight,
        regime_weight,
    ) = weights

    allocator = build_composite_allocator(
        signal_weight=signal_weight,
        atr_weight=atr_weight,
        stop_weight=stop_weight,
        regime_weight=regime_weight,
        aggregation=args.aggregation,
        maximum_position_pct=(
            args.maximum_position
        ),
    )

    exit_model = build_exit_model(
        name="atr",
        stop_atr_multiplier=(
            args.atr_stop
        ),
        target_atr_multiplier=(
            args.atr_target
        ),
        break_even_trigger=5.0,
        trailing_atr_multiplier=2.0,
    )

    position_sizer = FixedFractionSizer(
        position_size_pct=(
            args.position_size
        )
    )

    trades, metrics, equity = run_backtest(
        symbols=symbols,
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
        position_size_pct=(
            args.position_size
        ),
        max_holding_days=args.hold,
        min_adx=args.min_adx,
        entry_model=entry_model,
        exit_model=exit_model,
        ranking_method=(
            "relative_strength"
        ),
        position_sizer=(
            position_sizer
        ),
        regime_policy=(
            RegimePortfolioPolicy()
        ),
        portfolio_allocator=(
            allocator
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

    average_heat = 0.0
    maximum_heat = 0.0

    if (
        not equity.empty
        and "portfolio_heat_pct"
        in equity.columns
    ):
        heat_series = pd.to_numeric(
            equity[
                "portfolio_heat_pct"
            ],
            errors="coerce",
        ).dropna()

        if not heat_series.empty:
            average_heat = float(
                heat_series.mean()
            )

            maximum_heat = float(
                heat_series.max()
            )

    row = {
        "combination_id": combination_id,
        "aggregation": (
            args.aggregation
        ),
        "signal_weight": (
            signal_weight
        ),
        "atr_weight": atr_weight,
        "stop_weight": stop_weight,
        "regime_weight": regime_weight,
        "weight_sum": sum(
            weights
        ),
        "total_trades": len(
            trades
        ),
        "rejected_trades": int(
            sum(
                rejected_reasons.values()
            )
        ),
        "rejected_heat": int(
            rejected_reasons.get(
                "portfolio_heat_limit",
                0,
            )
            + rejected_reasons.get(
                "portfolio_heat_exceeded",
                0,
            )
        ),
        "final_equity": _safe_float(
            metrics.get(
                "final_equity"
            ),
            args.capital,
        ),
        "total_return_pct": (
            _safe_float(
                metrics.get(
                    "total_return_pct"
                )
            )
        ),
        "cagr_pct": _safe_float(
            metrics.get(
                "cagr_pct"
            )
        ),
        "sharpe_ratio": _safe_float(
            metrics.get(
                "sharpe_ratio"
            )
        ),
        "sortino_ratio": _safe_float(
            metrics.get(
                "sortino_ratio"
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
        "average_portfolio_heat_pct": (
            average_heat
        ),
        "maximum_portfolio_heat_pct": (
            maximum_heat
        ),
        "total_transaction_cost": (
            _safe_float(
                metrics.get(
                    "total_transaction_cost"
                )
            )
        ),
    }

    print(
        f"[{combination_id:>3}] "
        f"S={signal_weight:.2f} "
        f"A={atr_weight:.2f} "
        f"T={stop_weight:.2f} "
        f"R={regime_weight:.2f} | "
        f"Return={row['total_return_pct']:+.2f}% | "
        f"Sharpe={row['sharpe_ratio']:.3f} | "
        f"DD={row['max_drawdown_pct']:.2f}%"
    )

    return row


def build_ranking(
    summary: pd.DataFrame,
) -> pd.DataFrame:
    ranked = summary.copy()

    ranked[
        "return_to_drawdown"
    ] = (
        ranked[
            "total_return_pct"
        ]
        / ranked[
            "max_drawdown_pct"
        ]
        .abs()
        .replace(
            0,
            pd.NA,
        )
    ).fillna(0.0)

    ranked[
        "rank"
    ] = (
        ranked[
            "sharpe_ratio"
        ]
        .rank(
            method="dense",
            ascending=False,
        )
        .astype(int)
    )

    columns = [
        "rank",
        "combination_id",
        "aggregation",
        "signal_weight",
        "atr_weight",
        "stop_weight",
        "regime_weight",
        "weight_sum",
        "total_trades",
        "total_return_pct",
        "cagr_pct",
        "sharpe_ratio",
        "sortino_ratio",
        "max_drawdown_pct",
        "return_to_drawdown",
        "profit_factor",
        "win_rate_pct",
        "expectancy_pct",
        "average_portfolio_heat_pct",
        "maximum_portfolio_heat_pct",
        "rejected_trades",
        "rejected_heat",
        "total_transaction_cost",
    ]

    return (
        ranked[
            columns
        ]
        .sort_values(
            by=[
                "rank",
                "sharpe_ratio",
                "return_to_drawdown",
                "total_return_pct",
            ],
            ascending=[
                True,
                False,
                False,
                False,
            ],
        )
        .reset_index(
            drop=True
        )
    )


def main() -> None:
    args = parse_args()

    weight_values = parse_weight_values(
        args.weight_values
    )

    weight_grid = generate_weight_grid(
        weight_values
    )

    symbols = (
        list(TOP10_SYMBOLS)
        if args.symbols is None
        else [
            symbol.upper().strip()
            for symbol in args.symbols
            if symbol.strip()
        ]
    )

    registry = (
        build_portfolio_model_registry()
    )

    if args.entry_model not in registry:
        raise ValueError(
            "Entry model không hợp lệ: "
            f"{args.entry_model}"
        )

    entry_model = registry[
        args.entry_model
    ]

    print("=" * 110)
    print(
        "COMPOSITE WEIGHT BENCHMARK"
    )
    print("=" * 110)
    print(
        f"Aggregation  : "
        f"{args.aggregation}"
    )
    print(
        f"Symbols      : "
        f"{len(symbols)}"
    )
    print(
        f"Period       : "
        f"{args.start} -> {args.end}"
    )
    print(
        f"Combinations : "
        f"{len(weight_grid)}"
    )
    print(
        "Weight values: "
        + ", ".join(
            f"{value:.2f}"
            for value in weight_values
        )
    )
    print("=" * 110)

    rows: list[
        dict[str, Any]
    ] = []

    for combination_id, weights in enumerate(
        weight_grid,
        start=1,
    ):
        row = run_single_combination(
            combination_id=(
                combination_id
            ),
            weights=weights,
            symbols=symbols,
            args=args,
            entry_model=entry_model,
        )

        rows.append(
            row
        )

    summary = pd.DataFrame(
        rows
    )

    ranking = build_ranking(
        summary
    )

    output_dir = (
        Path(args.output)
        / args.aggregation
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    summary_path = (
        output_dir
        / "summary.csv"
    )

    ranking_path = (
        output_dir
        / "ranking.csv"
    )

    summary.to_csv(
        summary_path,
        index=False,
        encoding="utf-8-sig",
    )

    ranking.to_csv(
        ranking_path,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print("=" * 170)
    print(
        "COMPOSITE WEIGHT RANKING"
    )
    print("=" * 170)

    print(
        ranking.head(
            20
        ).to_string(
            index=False,
            float_format=lambda value: (
                f"{value:.4f}"
            ),
        )
    )

    print()
    print(f"Đã xuất: {summary_path}")
    print(f"Đã xuất: {ranking_path}")


if __name__ == "__main__":
    main()