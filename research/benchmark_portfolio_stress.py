from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from research.universes import (
    TOP10_SYMBOLS,
)

from typing import Any

from backtesting.allocation_factors import (
    ATRFactor,
    RegimeFactor,
    SignalScoreFactor,
    StopDistanceFactor,
    WeightedAllocationFactor,
)
from backtesting.portfolio_allocation import (
    CompositeAllocator,
    VolatilityScalingAllocator,
)

import pandas as pd

from backtesting.engine import (
    build_exit_model,
    run_backtest,
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

DEFAULT_OUTPUT_DIR = Path(
    "research_results/"
    "portfolio_stress_test"
)

BASE_BUY_COMMISSION_PCT = 0.15
BASE_SELL_COMMISSION_PCT = 0.15
BASE_SELL_TAX_PCT = 0.10

BASE_BUY_SLIPPAGE_PCT = 0.05
BASE_SELL_SLIPPAGE_PCT = 0.05

@dataclass(frozen=True)
class StressScenario:
    name: str

    fee_multiplier: float

    slippage_multiplier: float

    maximum_position_pct: float

    description: str

@dataclass(frozen=True)
class StressModel:
    name: str
    allocator: Any | None
    description: str

def build_stress_scenarios() -> list[
    StressScenario
]:
    return [
        StressScenario(
            name="baseline",
            fee_multiplier=1.0,
            slippage_multiplier=1.0,
            maximum_position_pct=40.0,
            description=(
                "Normal trading"
            ),
        ),
        StressScenario(
            name="high_fee",
            fee_multiplier=2.0,
            slippage_multiplier=1.0,
            maximum_position_pct=40.0,
            description=(
                "Transaction fee x2"
            ),
        ),
        StressScenario(
            name="high_slippage",
            fee_multiplier=1.0,
            slippage_multiplier=2.0,
            maximum_position_pct=40.0,
            description=(
                "Slippage x2"
            ),
        ),
        StressScenario(
            name="cost_shock",
            fee_multiplier=2.0,
            slippage_multiplier=2.0,
            maximum_position_pct=40.0,
            description=(
                "Fee x2 + Slippage x2"
            ),
        ),
        StressScenario(
            name="conservative",
            fee_multiplier=1.0,
            slippage_multiplier=1.0,
            maximum_position_pct=25.0,
            description=(
                "Reduce maximum position"
            ),
        ),
    ]

def build_stress_models(
    *,
    maximum_position_pct: float,
) -> list[StressModel]:
    composite_factors = [
        WeightedAllocationFactor(
            factor=SignalScoreFactor(
                minimum_score=40,
                maximum_score=100,
            ),
            weight=0.10,
        ),
        WeightedAllocationFactor(
            factor=ATRFactor(
                target_atr_pct=3.0,
            ),
            weight=0.30,
        ),
        WeightedAllocationFactor(
            factor=StopDistanceFactor(
                target_stop_distance_pct=6.0,
            ),
            weight=0.40,
        ),
        WeightedAllocationFactor(
            factor=RegimeFactor(),
            weight=0.20,
        ),
    ]

    return [
        StressModel(
            name="fixed_fraction_baseline",
            allocator=None,
            description=(
                "Baseline fixed fraction sizing"
            ),
        ),
        StressModel(
            name="volatility_scaling",
            allocator=(
                VolatilityScalingAllocator(
                    target_volatility_pct=3.0,
                    scaling_power=1.5,
                    maximum_position_pct=(
                        maximum_position_pct
                    ),
                )
            ),
            description=(
                "Volatility-scaled allocation"
            ),
        ),
        StressModel(
            name="composite_sum",
            allocator=(
                CompositeAllocator(
                    factors=(
                        composite_factors
                    ),
                    maximum_position_pct=(
                        maximum_position_pct
                    ),
                    aggregation="sum",
                    name="composite_sum",
                )
            ),
            description=(
                "Recommended composite weights "
                "0.10/0.30/0.40/0.20"
            ),
        ),
    ]

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark portfolio models "
            "under stress scenarios."
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

def run_stress_case(
    *,
    scenario: StressScenario,
    model: StressModel,
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

    buy_commission_pct = (
        BASE_BUY_COMMISSION_PCT
        * scenario.fee_multiplier
    )

    sell_commission_pct = (
        BASE_SELL_COMMISSION_PCT
        * scenario.fee_multiplier
    )

    sell_tax_pct = (
        BASE_SELL_TAX_PCT
        * scenario.fee_multiplier
    )

    buy_slippage_pct = (
        BASE_BUY_SLIPPAGE_PCT
        * scenario.slippage_multiplier
    )

    sell_slippage_pct = (
        BASE_SELL_SLIPPAGE_PCT
        * scenario.slippage_multiplier
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
            model.allocator
        ),
        buy_commission_pct=(
            buy_commission_pct
        ),
        sell_commission_pct=(
            sell_commission_pct
        ),
        sell_tax_pct=(
            sell_tax_pct
        ),
        buy_slippage_pct=(
            buy_slippage_pct
        ),
        sell_slippage_pct=(
            sell_slippage_pct
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

    return {
        "scenario": scenario.name,
        "model": model.name,
        "allocator": (
            "position_sizer"
            if model.allocator is None
            else model.allocator.name
        ),
        "description": (
            scenario.description
        ),
        "fee_multiplier": (
            scenario.fee_multiplier
        ),
        "slippage_multiplier": (
            scenario.slippage_multiplier
        ),
        "maximum_position_pct": (
            scenario.maximum_position_pct
        ),
        "buy_commission_pct": (
            buy_commission_pct
        ),
        "sell_commission_pct": (
            sell_commission_pct
        ),
        "sell_tax_pct": (
            sell_tax_pct
        ),
        "buy_slippage_pct": (
            buy_slippage_pct
        ),
        "sell_slippage_pct": (
            sell_slippage_pct
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
        "final_equity": float(
            metrics.get(
                "final_equity",
                args.capital,
            )
        ),
        "total_return_pct": float(
            metrics.get(
                "total_return_pct",
                0.0,
            )
        ),
        "cagr_pct": float(
            metrics.get(
                "cagr_pct",
                0.0,
            )
        ),
        "sharpe_ratio": float(
            metrics.get(
                "sharpe_ratio",
                0.0,
            )
        ),
        "sortino_ratio": float(
            metrics.get(
                "sortino_ratio",
                0.0,
            )
        ),
        "max_drawdown_pct": float(
            metrics.get(
                "max_drawdown_pct",
                0.0,
            )
        ),
        "profit_factor": float(
            metrics.get(
                "profit_factor",
                0.0,
            )
        ),
        "win_rate_pct": float(
            metrics.get(
                "win_rate_pct",
                0.0,
            )
        ),
        "expectancy_pct": float(
            metrics.get(
                "expectancy_pct",
                0.0,
            )
        ),
        "average_portfolio_heat_pct": (
            average_heat
        ),
        "maximum_portfolio_heat_pct": (
            maximum_heat
        ),
        "total_transaction_cost": float(
            metrics.get(
                "total_transaction_cost",
                0.0,
            )
        ),
    }

def build_stress_degradation(
    results_df: pd.DataFrame,
) -> pd.DataFrame:
    if results_df.empty:
        raise ValueError(
            "stress results không có dữ liệu."
        )

    baseline_df = (
        results_df[
            results_df["scenario"]
            == "baseline"
        ]
        .copy()
        .set_index("model")
    )

    if baseline_df.empty:
        raise ValueError(
            "Không tìm thấy baseline."
        )

    rows: list[
        dict[str, Any]
    ] = []

    for _, row in results_df.iterrows():
        model = row["model"]

        if model not in baseline_df.index:
            continue

        baseline = baseline_df.loc[
            model
        ]

        baseline_return = float(
            baseline[
                "total_return_pct"
            ]
        )

        baseline_sharpe = float(
            baseline[
                "sharpe_ratio"
            ]
        )

        baseline_drawdown = float(
            baseline[
                "max_drawdown_pct"
            ]
        )

        baseline_cost = float(
            baseline[
                "total_transaction_cost"
            ]
        )

        return_value = float(
            row[
                "total_return_pct"
            ]
        )

        sharpe_value = float(
            row[
                "sharpe_ratio"
            ]
        )

        drawdown_value = float(
            row[
                "max_drawdown_pct"
            ]
        )

        cost_value = float(
            row[
                "total_transaction_cost"
            ]
        )

        rows.append(
            {
                "scenario": row["scenario"],
                "model": model,
                "baseline_return_pct": (
                    baseline_return
                ),
                "stress_return_pct": (
                    return_value
                ),
                "return_change_points": (
                    return_value
                    - baseline_return
                ),
                "return_retention_pct": (
                    return_value
                    / baseline_return
                    * 100
                    if abs(
                        baseline_return
                    ) > 1e-12
                    else 0.0
                ),
                "baseline_sharpe": (
                    baseline_sharpe
                ),
                "stress_sharpe": (
                    sharpe_value
                ),
                "sharpe_change": (
                    sharpe_value
                    - baseline_sharpe
                ),
                "sharpe_retention_pct": (
                    sharpe_value
                    / baseline_sharpe
                    * 100
                    if abs(
                        baseline_sharpe
                    ) > 1e-12
                    else 0.0
                ),
                "baseline_drawdown_pct": (
                    baseline_drawdown
                ),
                "stress_drawdown_pct": (
                    drawdown_value
                ),
                "drawdown_change_points": (
                    drawdown_value
                    - baseline_drawdown
                ),
                "baseline_transaction_cost": (
                    baseline_cost
                ),
                "stress_transaction_cost": (
                    cost_value
                ),
                "cost_change": (
                    cost_value
                    - baseline_cost
                ),
                "cost_change_pct": (
                    (
                        cost_value
                        / baseline_cost
                        - 1
                    )
                    * 100
                    if abs(
                        baseline_cost
                    ) > 1e-12
                    else 0.0
                ),
            }
        )

    return pd.DataFrame(
        rows
    )

def build_stress_ranking(
    results_df: pd.DataFrame,
) -> pd.DataFrame:
    ranking = (
        results_df
        .copy()
        .sort_values(
            by=[
                "scenario",
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
        .reset_index(
            drop=True
        )
    )

    ranking[
        "scenario_rank"
    ] = (
        ranking
        .groupby(
            "scenario"
        )
        .cumcount()
        + 1
    )

    columns = [
        "scenario",
        "scenario_rank",
        "model",
        "allocator",
        "total_trades",
        "total_return_pct",
        "sharpe_ratio",
        "sortino_ratio",
        "max_drawdown_pct",
        "profit_factor",
        "win_rate_pct",
        "expectancy_pct",
        "average_portfolio_heat_pct",
        "maximum_portfolio_heat_pct",
        "total_transaction_cost",
    ]

    return ranking[
        columns
    ]

def build_stress_summary(
    results_df: pd.DataFrame,
    degradation_df: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[
        dict[str, Any]
    ] = []

    for model, group in (
        results_df.groupby(
            "model"
        )
    ):
        baseline = group[
            group["scenario"]
            == "baseline"
        ]

        stressed = group[
            group["scenario"]
            != "baseline"
        ]

        model_degradation = (
            degradation_df[
                degradation_df["model"]
                == model
            ]
        )

        baseline_return = (
            float(
                baseline[
                    "total_return_pct"
                ].iloc[0]
            )
            if not baseline.empty
            else 0.0
        )

        baseline_sharpe = (
            float(
                baseline[
                    "sharpe_ratio"
                ].iloc[0]
            )
            if not baseline.empty
            else 0.0
        )

        worst_row = (
            stressed
            .sort_values(
                by=[
                    "sharpe_ratio",
                    "total_return_pct",
                ],
                ascending=[
                    True,
                    True,
                ],
            )
            .iloc[0]
            if not stressed.empty
            else None
        )

        return_retention = (
            pd.to_numeric(
                model_degradation[
                    "return_retention_pct"
                ],
                errors="coerce",
            )
            .dropna()
        )

        sharpe_retention = (
            pd.to_numeric(
                model_degradation[
                    "sharpe_retention_pct"
                ],
                errors="coerce",
            )
            .dropna()
        )

        mean_return_retention = (
            float(
                return_retention.mean()
            )
            if not return_retention.empty
            else 0.0
        )

        mean_sharpe_retention = (
            float(
                sharpe_retention.mean()
            )
            if not sharpe_retention.empty
            else 0.0
        )


        rows.append(
            {
                "model": model,
                "baseline_return_pct": (
                    baseline_return
                ),
                "baseline_sharpe": (
                    baseline_sharpe
                ),
                "mean_stress_return_pct": (
                    float(
                        stressed[
                            "total_return_pct"
                        ].mean()
                    )
                    if not stressed.empty
                    else baseline_return
                ),
                "mean_stress_sharpe": (
                    float(
                        stressed[
                            "sharpe_ratio"
                        ].mean()
                    )
                    if not stressed.empty
                    else baseline_sharpe
                ),
                "worst_scenario": (
                    worst_row["scenario"]
                    if worst_row is not None
                    else "baseline"
                ),
                "worst_return_pct": (
                    float(
                        worst_row[
                            "total_return_pct"
                        ]
                    )
                    if worst_row is not None
                    else baseline_return
                ),
                "worst_sharpe": (
                    float(
                        worst_row[
                            "sharpe_ratio"
                        ]
                    )
                    if worst_row is not None
                    else baseline_sharpe
                ),
                "worst_drawdown_pct": (
                    float(
                        stressed[
                            "max_drawdown_pct"
                        ].min()
                    )
                    if not stressed.empty
                    else float(
                        baseline[
                            "max_drawdown_pct"
                        ].iloc[0]
                    )
                ),
                "mean_return_retention_pct": (
                    mean_return_retention
                ),
                "mean_sharpe_retention_pct": (
                    mean_sharpe_retention
                ),
            }
        )

    summary = pd.DataFrame(
        rows
    )

    if summary.empty:
        raise ValueError(
            "Không tạo được stress summary."
        )

    def normalize_higher_is_better(
        series: pd.Series,
    ) -> pd.Series:
        numeric = pd.to_numeric(
            series,
            errors="coerce",
        ).fillna(0.0)

        minimum = float(
            numeric.min()
        )

        maximum = float(
            numeric.max()
        )

        if abs(
            maximum - minimum
        ) <= 1e-12:
            return pd.Series(
                1.0,
                index=numeric.index,
                dtype=float,
            )

        return (
            numeric - minimum
        ) / (
            maximum - minimum
        )

    def normalize_lower_is_better(
        series: pd.Series,
    ) -> pd.Series:
        numeric = pd.to_numeric(
            series,
            errors="coerce",
        ).fillna(0.0)

        minimum = float(
            numeric.min()
        )

        maximum = float(
            numeric.max()
        )

        if abs(
            maximum - minimum
        ) <= 1e-12:
            return pd.Series(
                1.0,
                index=numeric.index,
                dtype=float,
            )

        return (
            maximum - numeric
        ) / (
            maximum - minimum
        )

    summary[
        "score_stress_return"
    ] = normalize_higher_is_better(
        summary[
            "mean_stress_return_pct"
        ]
    )

    summary[
        "score_stress_sharpe"
    ] = normalize_higher_is_better(
        summary[
            "mean_stress_sharpe"
        ]
    )

    summary[
        "score_worst_drawdown"
    ] = normalize_lower_is_better(
        summary[
            "worst_drawdown_pct"
        ].abs()
    )

    summary[
        "score_return_retention"
    ] = normalize_higher_is_better(
        summary[
            "mean_return_retention_pct"
        ]
    )

    summary[
        "score_sharpe_retention"
    ] = normalize_higher_is_better(
        summary[
            "mean_sharpe_retention_pct"
        ]
    )

    summary[
        "robust_score"
    ] = (
        summary[
            "score_stress_return"
        ] * 0.35
        + summary[
            "score_stress_sharpe"
        ] * 0.35
        + summary[
            "score_worst_drawdown"
        ] * 0.15
        + summary[
            "score_return_retention"
        ] * 0.075
        + summary[
            "score_sharpe_retention"
        ] * 0.075
    ) * 100

    summary[
        "robust_rank"
    ] = (
        summary[
            "robust_score"
        ]
        .rank(
            method="dense",
            ascending=False,
        )
        .astype(int)
    )

    return (
        summary
        .sort_values(
            by=[
                "robust_rank",
                "mean_stress_sharpe",
                "mean_stress_return_pct",
                "worst_drawdown_pct",
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

    summary[
        "robust_rank"
    ] = (
        summary[
            "robust_score"
        ]
        .rank(
            method="dense",
            ascending=False,
        )
        .astype(int)
    )

    return (
        summary
        .sort_values(
            by=[
                "robust_rank",
                "baseline_sharpe",
                "baseline_return_pct",
            ],
            ascending=[
                True,
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

    symbols = (
        list(TOP10_SYMBOLS)
        if args.symbols is None
        else [
            symbol.upper().strip()
            for symbol in args.symbols
            if symbol.strip()
        ]
    )

    if not symbols:
        raise ValueError(
            "Không có symbol hợp lệ."
        )

    output_dir = Path(
        args.output
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    scenarios = (
        build_stress_scenarios()
    )

    print("=" * 100)
    print(
        "PORTFOLIO STRESS TEST"
    )
    print("=" * 100)

    print(
        f"Scenarios : "
        f"{len(scenarios)}"
    )

    print(
        f"Symbols   : "
        f"{len(symbols)}"
    )

    print(
        f"Period    : "
        f"{args.start} -> {args.end}"
    )

    print(
        f"Capital   : "
        f"{args.capital:,.0f}"
    )

    print(
        f"Output    : "
        f"{output_dir}"
    )

    print()

    for index, scenario in enumerate(
        scenarios,
        start=1,
    ):
        print(
            f"[{index}] "
            f"{scenario.name}"
        )

        print(
            f"    Fee x"
            f"{scenario.fee_multiplier}"
        )

        print(
            f"    Slippage x"
            f"{scenario.slippage_multiplier}"
        )

        print(
            f"    Max Position "
            f"{scenario.maximum_position_pct:.0f}%"
        )

        print(
            f"    {scenario.description}"
        )

        models = build_stress_models(
            maximum_position_pct=(
                scenario.maximum_position_pct
            )
        )

        print(
            f"    Models: {len(models)}"
        )

        for model in models:
            allocator_name = (
                "position_sizer"
                if model.allocator is None
                else model.allocator.name
            )

            print(
                f"      - {model.name} "
                f"({allocator_name})"
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

    rows: list[
        dict[str, Any]
    ] = []

    total_runs = (
        len(scenarios) * 3
    )

    completed = 0

    print()
    print("=" * 120)
    print("RUN PORTFOLIO STRESS TEST")
    print("=" * 120)

    for scenario in scenarios:
        models = build_stress_models(
            maximum_position_pct=(
                scenario.maximum_position_pct
            )
        )

        for model in models:
            completed += 1

            row = run_stress_case(
                scenario=scenario,
                model=model,
                symbols=symbols,
                args=args,
                entry_model=entry_model,
            )

            rows.append(
                row
            )

            print(
                f"[{completed}/{total_runs}] "
                f"{scenario.name:<16} "
                f"{model.name:<24} | "
                f"Return="
                f"{row['total_return_pct']:+.2f}% | "
                f"Sharpe="
                f"{row['sharpe_ratio']:.3f} | "
                f"DD="
                f"{row['max_drawdown_pct']:.2f}% | "
                f"Cost="
                f"{row['total_transaction_cost']:,.0f}"
            )

    results_df = pd.DataFrame(
        rows
    )

    degradation_df = (
        build_stress_degradation(
            results_df
        )
    )

    ranking_df = (
        build_stress_ranking(
            results_df
        )
    )

    summary_df = (
        build_stress_summary(
            results_df,
            degradation_df,
        )
    )

    results_path = (
        output_dir
        / "stress_results.csv"
    )

    degradation_path = (
        output_dir
        / "stress_degradation.csv"
    )

    ranking_path = (
        output_dir
        / "stress_ranking.csv"
    )

    summary_path = (
        output_dir
        / "stress_summary.csv"
    )

    results_df.to_csv(
        results_path,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print("=" * 160)
    print("PORTFOLIO STRESS RESULTS")
    print("=" * 160)

    print(
        results_df[
            [
                "scenario",
                "model",
                "total_trades",
                "total_return_pct",
                "sharpe_ratio",
                "max_drawdown_pct",
                "profit_factor",
                "total_transaction_cost",
            ]
        ].to_string(
            index=False,
            float_format=lambda value: (
                f"{value:.4f}"
            ),
        )
    )

    print()
    print(f"Đã xuất: {results_path}")

    degradation_df.to_csv(
        degradation_path,
        index=False,
        encoding="utf-8-sig",
    )

    ranking_df.to_csv(
        ranking_path,
        index=False,
        encoding="utf-8-sig",
    )

    summary_df.to_csv(
        summary_path,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print("=" * 150)
    print("STRESS ROBUSTNESS SUMMARY")
    print("=" * 150)

    summary_display_columns = [
        "model",
        "baseline_return_pct",
        "baseline_sharpe",
        "mean_stress_return_pct",
        "mean_stress_sharpe",
        "worst_scenario",
        "worst_return_pct",
        "worst_sharpe",
        "worst_drawdown_pct",
        "mean_return_retention_pct",
        "mean_sharpe_retention_pct",
        "score_stress_return",
        "score_stress_sharpe",
        "score_worst_drawdown",
        "robust_score",
        "robust_rank",
    ]

    print(
        summary_df[
            summary_display_columns
        ].to_string(
            index=False,
            float_format=lambda value: (
                f"{value:.4f}"
            ),
        )
    )

    print()
    print(f"Đã xuất: {results_path}")
    print(f"Đã xuất: {degradation_path}")
    print(f"Đã xuất: {ranking_path}")
    print(f"Đã xuất: {summary_path}")

if __name__ == "__main__":
    main()