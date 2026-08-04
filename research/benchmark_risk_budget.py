from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd

from backtesting.allocation_diagnostics import (
    analyze_allocation_diagnostics,
)
from backtesting.engine import (
    build_exit_model,
    run_backtest,
)
from backtesting.portfolio_allocation import (
    RiskBudgetAllocator,
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
    "portfolio_allocation/"
    "risk_budget_sensitivity"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark multiple risk-budget levels."
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
        "--risk-levels",
        default="0.8,1.0,1.2,1.4",
        help=(
            "Danh sách risk budget mỗi vị thế, "
            "phân cách bằng dấu phẩy."
        ),
    )

    parser.add_argument(
        "--maximum-position",
        type=float,
        default=35.0,
    )

    parser.add_argument(
        "--minimum-position",
        type=float,
        default=2.0,
    )

    parser.add_argument(
        "--output",
        default=str(
            DEFAULT_OUTPUT_DIR
        ),
    )

    return parser.parse_args()


def parse_risk_levels(
    raw_value: str,
) -> list[float]:
    levels: list[float] = []

    for item in raw_value.split(","):
        stripped = item.strip()

        if not stripped:
            continue

        value = float(
            stripped
        )

        if value <= 0:
            raise ValueError(
                "Mọi risk level phải lớn hơn 0."
            )

        levels.append(
            value
        )

    if not levels:
        raise ValueError(
            "Không có risk level hợp lệ."
        )

    return levels


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


def run_single_level(
    *,
    risk_level: float,
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

    allocator = RiskBudgetAllocator(
        target_risk_per_position_pct=(
            risk_level
        ),
        maximum_position_pct=(
            args.maximum_position
        ),
        minimum_position_pct=(
            args.minimum_position
        ),
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

    diagnostics = (
        analyze_allocation_diagnostics(
            allocator_name=(
                f"risk_budget_{risk_level:.2f}"
            ),
            trades=trades,
            metrics=metrics,
            equity_curve=equity,
            initial_capital=args.capital,
        )
    )

    summary = diagnostics.summary

    rejected_reasons = (
        metrics.get(
            "rejected_trade_reasons",
            {},
        )
        or {}
    )

    row = {
        "risk_budget_pct": (
            risk_level
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
        "average_trade_notional": (
            summary[
                "average_trade_notional"
            ]
        ),
        "average_trade_notional_pct_initial": (
            summary[
                "average_trade_notional_pct_initial"
            ]
        ),
        "average_risk_pct": (
            summary[
                "average_risk_pct"
            ]
        ),
        "median_risk_pct": (
            summary[
                "median_risk_pct"
            ]
        ),
        "average_exposure_pct": (
            summary[
                "average_exposure_pct"
            ]
        ),
        "maximum_exposure_pct": (
            summary[
                "maximum_exposure_pct"
            ]
        ),
        "average_cash_pct": (
            summary[
                "average_cash_pct"
            ]
        ),
        "minimum_cash_pct": (
            summary[
                "minimum_cash_pct"
            ]
        ),
        "average_open_positions": (
            summary[
                "average_open_positions"
            ]
        ),
        "maximum_open_positions": (
            summary[
                "maximum_open_positions"
            ]
        ),
        "average_portfolio_heat_pct": (
            summary[
                "average_portfolio_heat_pct"
            ]
        ),
        "maximum_portfolio_heat_pct": (
            summary[
                "maximum_portfolio_heat_pct"
            ]
        ),
        "total_transaction_cost": (
            summary[
                "total_transaction_cost"
            ]
        ),
    }

    print()
    print("-" * 100)
    print(
        f"RISK BUDGET = "
        f"{risk_level:.2f}%"
    )
    print("-" * 100)

    print(
        f"Trades       : "
        f"{row['total_trades']}"
    )
    print(
        f"Return       : "
        f"{row['total_return_pct']:+.2f}%"
    )
    print(
        f"Sharpe       : "
        f"{row['sharpe_ratio']:.2f}"
    )
    print(
        f"Drawdown     : "
        f"{row['max_drawdown_pct']:.2f}%"
    )
    print(
        f"Avg Exposure : "
        f"{row['average_exposure_pct']:.2f}%"
    )
    print(
        f"Avg Risk     : "
        f"{row['average_risk_pct']:.2f}%"
    )
    print(
        f"Avg Heat     : "
        f"{row['average_portfolio_heat_pct']:.2f}%"
    )
    print(
        f"Rejected     : "
        f"{rejected_reasons}"
    )

    return row


def build_ranking(
    results: pd.DataFrame,
) -> pd.DataFrame:
    ranked = results.copy()

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
        .replace(0, pd.NA)
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
        "risk_budget_pct",
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
        "average_trade_notional_pct_initial",
        "average_risk_pct",
        "average_exposure_pct",
        "average_cash_pct",
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

    risk_levels = parse_risk_levels(
        args.risk_levels
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

    print("=" * 100)
    print(
        "RISK BUDGET SENSITIVITY BENCHMARK"
    )
    print("=" * 100)
    print(f"Symbols     : {len(symbols)}")
    print(
        f"Period      : "
        f"{args.start} -> {args.end}"
    )
    print(
        f"Risk Levels : "
        + ", ".join(
            f"{level:.2f}%"
            for level in risk_levels
        )
    )
    print("=" * 100)

    rows: list[
        dict[str, Any]
    ] = []

    for risk_level in risk_levels:
        row = run_single_level(
            risk_level=risk_level,
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
    print("=" * 160)
    print(
        "RISK BUDGET SENSITIVITY RANKING"
    )
    print("=" * 160)

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