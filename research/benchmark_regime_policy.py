from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from backtesting.engine import build_exit_model, run_backtest
from backtesting.position_sizers import FixedFractionSizer
from backtesting.regime_policy import (
    RegimePortfolioPolicy,
    RegimePortfolioRule,
)
from research.benchmark_portfolio_models import (
    build_portfolio_model_registry,
)
from research.universes import TOP10_SYMBOLS


DEFAULT_OUTPUT_DIR = Path(
    "research_results/regime_policy"
)


@dataclass(frozen=True)
class RegimePolicySpec:
    name: str
    factory: Callable[
        [],
        RegimePortfolioPolicy | None,
    ]


def build_policy_specs() -> list[RegimePolicySpec]:
    return [
        RegimePolicySpec(
            name="static_baseline",
            factory=lambda: None,
        ),
        RegimePolicySpec(
            name="adaptive_default",
            factory=RegimePortfolioPolicy,
        ),
        RegimePolicySpec(
            name="bull_only",
            factory=lambda: RegimePortfolioPolicy(
                rules={
                    "BULL": RegimePortfolioRule(
                        allow_new_positions=True,
                        max_positions=5,
                        max_portfolio_heat_pct=5.0,
                    ),
                    "SIDEWAY": RegimePortfolioRule(
                        allow_new_positions=False,
                        max_positions=0,
                        max_portfolio_heat_pct=None,
                    ),
                    "BEAR": RegimePortfolioRule(
                        allow_new_positions=False,
                        max_positions=0,
                        max_portfolio_heat_pct=None,
                    ),
                }
            ),
        ),
        RegimePolicySpec(
            name="bull_sideway_no_heat",
            factory=lambda: RegimePortfolioPolicy(
                rules={
                    "BULL": RegimePortfolioRule(
                        allow_new_positions=True,
                        max_positions=5,
                        max_portfolio_heat_pct=None,
                    ),
                    "SIDEWAY": RegimePortfolioRule(
                        allow_new_positions=True,
                        max_positions=5,
                        max_portfolio_heat_pct=None,
                    ),
                    "BEAR": RegimePortfolioRule(
                        allow_new_positions=False,
                        max_positions=0,
                        max_portfolio_heat_pct=None,
                    ),
                }
            ),
        ),
    ]


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


def run_regime_policy_benchmark(
    *,
    symbols: list[str],
    entry_model_name: str,
    start_date: str,
    end_date: str,
    initial_capital: float,
    position_size_pct: float,
    max_holding_days: int,
    min_adx: float,
    ranking_method: str,
    atr_stop_multiplier: float,
    atr_target_multiplier: float,
    output_dir: Path,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
]:
    registry = build_portfolio_model_registry()

    if entry_model_name not in registry:
        raise ValueError(
            f"Entry model không hợp lệ: {entry_model_name}"
        )

    entry_model = registry[
        entry_model_name
    ]

    specs = build_policy_specs()

    summary_rows: list[
        dict[str, Any]
    ] = []

    equity_frames: list[
        pd.DataFrame
    ] = []

    print("=" * 110)
    print("REGIME POLICY BENCHMARK")
    print("=" * 110)
    print(f"Symbols      : {len(symbols)}")
    print(f"Policies     : {len(specs)}")
    print(f"Entry        : {entry_model_name}")
    print(f"Ranking      : {ranking_method}")
    print(f"Period       : {start_date} -> {end_date}")
    print("=" * 110)

    for spec in specs:
        print()
        print("-" * 110)
        print(f"POLICY={spec.name}")
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

        position_sizer = FixedFractionSizer(
            position_size_pct=(
                position_size_pct
            )
        )

        policy = spec.factory()

        trades, metrics, equity_df = run_backtest(
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            position_size_pct=(
                position_size_pct
            ),
            max_holding_days=(
                max_holding_days
            ),
            min_adx=min_adx,
            entry_model=entry_model,
            exit_model=exit_model,
            ranking_method=ranking_method,
            position_sizer=position_sizer,
            regime_policy=policy,
            verbose=False,
        )

        rejected_reasons = (
            metrics.get(
                "rejected_trade_reasons",
                {},
            )
            or {}
        )
        print("Rejected reasons:", rejected_reasons)


        summary_rows.append(
            {
                "policy": spec.name,
                "entry_model": entry_model_name,
                "symbols": len(symbols),
                "start_date": start_date,
                "end_date": end_date,
                "initial_capital": (
                    initial_capital
                ),
                "position_size_pct": (
                    position_size_pct
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
                "rejected_bear": int(
                    rejected_reasons.get(
                        "regime_entries_disabled",
                        0,
                    )
                ),
                "rejected_unknown": int(
                    rejected_reasons.get(
                        "unknown_market_regime",
                        0,
                    )
                ),
                "rejected_heat": int(
                    rejected_reasons.get(
                        "portfolio_heat_limit",
                        0,
                    )
                ),
                "final_equity": safe_float(
                    metrics.get(
                        "final_equity"
                    )
                ),
                "total_return_pct": safe_float(
                    metrics.get(
                        "total_return_pct"
                    )
                ),
                "cagr_pct": safe_float(
                    metrics.get(
                        "cagr_pct"
                    )
                ),
                "sharpe_ratio": safe_float(
                    metrics.get(
                        "sharpe_ratio"
                    )
                ),
                "sortino_ratio": safe_float(
                    metrics.get(
                        "sortino_ratio"
                    )
                ),
                "max_drawdown_pct": safe_float(
                    metrics.get(
                        "max_drawdown_pct"
                    )
                ),
                "profit_factor": safe_float(
                    metrics.get(
                        "profit_factor"
                    )
                ),
                "win_rate_pct": safe_float(
                    metrics.get(
                        "win_rate_pct"
                    )
                ),
                "expectancy_pct": safe_float(
                    metrics.get(
                        "expectancy_pct"
                    )
                ),
                "total_transaction_cost": (
                    safe_float(
                        metrics.get(
                            "total_transaction_cost"
                        )
                    )
                ),
            }
        )

        if not equity_df.empty:
            curve = equity_df.copy()
            curve["policy"] = spec.name

            metadata = ["policy"]
            remaining = [
                column
                for column in curve.columns
                if column not in metadata
            ]

            equity_frames.append(
                curve[
                    metadata
                    + remaining
                ]
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
            "Regime rejects: "
            f"{rejected_reasons.get('regime_entries_disabled', 0)}"
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

    equity_result = (
        pd.concat(
            equity_frames,
            ignore_index=True,
        )
        if equity_frames
        else pd.DataFrame()
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    summary_path = (
        output_dir / "summary.csv"
    )
    equity_path = (
        output_dir / "equity.csv"
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

    print()
    print(f"Đã xuất: {summary_path}")
    print(f"Đã xuất: {equity_path}")

    print()
    print("=" * 190)
    print("REGIME POLICY SUMMARY")
    print("=" * 190)
    print(
        summary_df.to_string(
            index=False,
            float_format=(
                lambda value:
                f"{value:.2f}"
            ),
        )
    )

    return summary_df, equity_result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark adaptive regime "
            "portfolio policies."
        )
    )

    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
    )

    parser.add_argument(
        "--entry-model",
        default=(
            "hybrid_trend_donchian_v1"
            "__trend_context"
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
        if args.symbols is None
        else [
            symbol.upper().strip()
            for symbol in args.symbols
        ]
    )

    run_regime_policy_benchmark(
        symbols=symbols,
        entry_model_name=(
            args.entry_model
        ),
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
        position_size_pct=(
            args.position_size
        ),
        max_holding_days=args.hold,
        min_adx=args.min_adx,
        ranking_method=args.ranking,
        atr_stop_multiplier=(
            args.atr_stop_multiplier
        ),
        atr_target_multiplier=(
            args.atr_target_multiplier
        ),
        output_dir=Path(args.output),
    )


if __name__ == "__main__":
    main()
