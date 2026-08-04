from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd

from backtesting.engine import (
    build_exit_model,
    run_backtest,
)
from backtesting.portfolio_allocation import (
    EqualWeightAllocator,
    InverseATRAllocator,
    PortfolioAllocator,
    StopRiskAllocator,
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
    "portfolio_allocation"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark portfolio allocation "
            "methods on the same strategy."
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
        "--output",
        default=str(
            DEFAULT_OUTPUT_DIR
        ),
    )

    return parser.parse_args()


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


def build_allocator_registry(
) -> dict[str, PortfolioAllocator | None]:
    return {
        "fixed_fraction_baseline": None,
        "equal_weight": (
            EqualWeightAllocator()
        ),
        "inverse_atr": (
            InverseATRAllocator()
        ),
        "stop_risk": (
            StopRiskAllocator(
                maximum_position_pct=35.0,
            )
        ),
    }


def run_single_benchmark(
    *,
    name: str,
    allocator: PortfolioAllocator | None,
    symbols: list[str],
    args: argparse.Namespace,
    entry_model: Any,
) -> dict[str, Any]:
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

    regime_policy = (
        RegimePortfolioPolicy()
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
            regime_policy
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

    allocator_name = (
        "position_sizer"
        if allocator is None
        else allocator.name
    )

    row = {
        "model": name,
        "allocator": allocator_name,
        "symbols": len(symbols),
        "start_date": args.start,
        "end_date": args.end,
        "initial_capital": (
            args.capital
        ),
        "position_size_pct": (
            args.position_size
        ),
        "total_trades": len(
            trades
        ),
        "rejected_trades": int(
            sum(
                rejected_reasons.values()
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
        "total_transaction_cost": (
            _safe_float(
                metrics.get(
                    "total_transaction_cost"
                )
            )
        ),
        "average_portfolio_heat_pct": (
            _safe_float(
                equity.get(
                    "portfolio_heat_pct",
                    pd.Series(
                        dtype=float
                    ),
                ).mean()
                if not equity.empty
                else 0.0
            )
        ),
        "maximum_portfolio_heat_pct": (
            _safe_float(
                equity.get(
                    "portfolio_heat_pct",
                    pd.Series(
                        dtype=float
                    ),
                ).max()
                if not equity.empty
                else 0.0
            )
        ),
        "rejected_regime": int(
            rejected_reasons.get(
                "regime_entries_disabled",
                0,
            )
        ),
        "rejected_heat": int(
            rejected_reasons.get(
                "portfolio_heat_exceeded",
                0,
            )
        ),
        "rejected_cash": int(
            rejected_reasons.get(
                "insufficient_cash",
                0,
            )
        ),
        "rejected_duplicate": int(
            rejected_reasons.get(
                "duplicate_symbol",
                0,
            )
        ),
    }

    print()
    print("-" * 100)
    print(f"ALLOCATOR={name}")
    print("-" * 100)
    print(
        f"Trades   : "
        f"{row['total_trades']}"
    )
    print(
        f"Return   : "
        f"{row['total_return_pct']:+.2f}%"
    )
    print(
        f"Sharpe   : "
        f"{row['sharpe_ratio']:.2f}"
    )
    print(
        f"Drawdown : "
        f"{row['max_drawdown_pct']:.2f}%"
    )
    print(
        f"Heat Avg : "
        f"{row['average_portfolio_heat_pct']:.2f}%"
    )
    print(
        f"Heat Max : "
        f"{row['maximum_portfolio_heat_pct']:.2f}%"
    )
    print(
        f"Rejected : "
        f"{rejected_reasons}"
    )

    return row


def build_ranking(
    summary: pd.DataFrame,
) -> pd.DataFrame:
    ranked = summary.copy()

    ranked["rank"] = (
        ranked[
            "sharpe_ratio"
        ]
        .rank(
            method="dense",
            ascending=False,
        )
        .astype(int)
    )

    ranked = ranked.sort_values(
        by=[
            "rank",
            "sharpe_ratio",
            "total_return_pct",
            "max_drawdown_pct",
        ],
        ascending=[
            True,
            False,
            False,
            False,
        ],
    )

    columns = [
        "rank",
        "model",
        "allocator",
        "total_trades",
        "total_return_pct",
        "cagr_pct",
        "sharpe_ratio",
        "sortino_ratio",
        "max_drawdown_pct",
        "profit_factor",
        "win_rate_pct",
        "expectancy_pct",
        "average_portfolio_heat_pct",
        "maximum_portfolio_heat_pct",
        "total_transaction_cost",
        "rejected_trades",
    ]

    return ranked[
        columns
    ].reset_index(
        drop=True
    )


def main() -> None:
    args = parse_args()

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

    allocators = (
        build_allocator_registry()
    )

    print("=" * 100)
    print(
        "PORTFOLIO ALLOCATION BENCHMARK"
    )
    print("=" * 100)
    print(f"Symbols : {len(symbols)}")
    print(
        f"Period  : "
        f"{args.start} -> {args.end}"
    )
    print(
        f"Models  : "
        f"{len(allocators)}"
    )
    print("=" * 100)

    rows: list[
        dict[str, Any]
    ] = []

    for name, allocator in (
        allocators.items()
    ):
        row = run_single_benchmark(
            name=name,
            allocator=allocator,
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

    output_dir = Path(
        args.output
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
    print("=" * 140)
    print(
        "PORTFOLIO ALLOCATION RANKING"
    )
    print("=" * 140)

    print(
        ranking.to_string(
            index=False,
            float_format=lambda value: (
                f"{value:.2f}"
            ),
        )
    )

    print()
    print(f"Đã xuất: {summary_path}")
    print(f"Đã xuất: {ranking_path}")


if __name__ == "__main__":
    main()