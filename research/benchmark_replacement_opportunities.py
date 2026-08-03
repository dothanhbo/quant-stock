from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd

from backtesting.engine import (
    BacktestConfig,
    build_exit_model,
    generate_candidate_trades,
)
from backtesting.portfolio_simulator import (
    PortfolioSimulator,
)
from backtesting.ranking import RankingMethod
from backtesting.transaction_cost import (
    TransactionCostConfig,
)
from research.benchmark_portfolio_models import (
    build_portfolio_model_registry,
)
from research.universes import TOP10_SYMBOLS


DEFAULT_OPPORTUNITIES_OUTPUT = (
    "research_results/"
    "replacement_opportunities.csv"
)

DEFAULT_SUMMARY_OUTPUT = (
    "research_results/"
    "replacement_summary.csv"
)

DEFAULT_REPORT_OUTPUT = (
    "research_results/"
    "replacement_report.md"
)


def safe_float(
    value: Any,
    default: float | None = None,
) -> float | None:
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


def build_candidate_trades(
    *,
    symbols: list[str],
    start_date: str,
    end_date: str,
    config: BacktestConfig,
    entry_model: Any,
    exit_model: Any,
) -> list[Any]:
    candidate_trades: list[Any] = []

    for index, symbol in enumerate(
        symbols,
        start=1,
    ):
        print(
            f"[{index}/{len(symbols)}] "
            f"Generate {symbol}"
        )

        symbol_trades = generate_candidate_trades(
            symbol=symbol,
            config=config,
            start_date=start_date,
            end_date=end_date,
            entry_model=entry_model,
            exit_model=exit_model,
            verbose=False,
        )

        candidate_trades.extend(
            symbol_trades
        )

    return candidate_trades


def opportunity_to_row(
    *,
    opportunity: Any,
    entry_model_name: str,
    ranking_method: str,
) -> dict[str, Any]:
    decision = opportunity.decision
    candidate = decision.candidate
    weakest = decision.weakest_trade

    candidate_return = safe_float(
        getattr(
            candidate,
            "net_return_pct",
            getattr(
                candidate,
                "return_pct",
                None,
            ),
        )
    )

    weakest_return = safe_float(
        getattr(
            weakest,
            "net_return_pct",
            getattr(
                weakest,
                "return_pct",
                None,
            ),
        )
        if weakest is not None
        else None
    )

    return_gap = (
        candidate_return
        - weakest_return
        if (
            candidate_return is not None
            and weakest_return is not None
        )
        else None
    )

    return {
        "entry_model": entry_model_name,
        "ranking_method": ranking_method,
        "event_date": opportunity.event_date,
        "candidate_symbol": getattr(
            candidate,
            "symbol",
            None,
        ),
        "candidate_entry_date": getattr(
            candidate,
            "entry_date",
            None,
        ),
        "candidate_exit_date": getattr(
            candidate,
            "exit_date",
            None,
        ),
        "candidate_quality": (
            decision.candidate_quality
        ),
        "candidate_signal_score": getattr(
            candidate,
            "signal_score",
            None,
        ),
        "candidate_relative_strength": getattr(
            candidate,
            "relative_strength",
            None,
        ),
        "candidate_adx": getattr(
            candidate,
            "adx",
            None,
        ),
        "candidate_volume_ratio": getattr(
            candidate,
            "volume_ratio",
            None,
        ),
        "candidate_market_regime": getattr(
            candidate,
            "market_regime",
            None,
        ),
        "candidate_return_pct": candidate_return,
        "candidate_exit_reason": str(
            getattr(
                candidate,
                "exit_reason",
                "",
            )
        ),
        "weakest_symbol": getattr(
            weakest,
            "symbol",
            None,
        ),
        "weakest_entry_date": getattr(
            weakest,
            "entry_date",
            None,
        ),
        "weakest_exit_date": getattr(
            weakest,
            "exit_date",
            None,
        ),
        "weakest_quality": (
            decision.weakest_quality
        ),
        "weakest_signal_score": getattr(
            weakest,
            "signal_score",
            None,
        ),
        "weakest_relative_strength": getattr(
            weakest,
            "relative_strength",
            None,
        ),
        "weakest_adx": getattr(
            weakest,
            "adx",
            None,
        ),
        "weakest_volume_ratio": getattr(
            weakest,
            "volume_ratio",
            None,
        ),
        "weakest_market_regime": getattr(
            weakest,
            "market_regime",
            None,
        ),
        "weakest_return_pct": weakest_return,
        "weakest_exit_reason": str(
            getattr(
                weakest,
                "exit_reason",
                "",
            )
        ),
        "quality_gap": decision.quality_gap,
        "replacement_threshold": (
            decision.replacement_threshold
        ),
        "hypothetical_return_gap_pct": return_gap,
        "candidate_outperformed": (
            return_gap > 0
            if return_gap is not None
            else False
        ),
        "candidate_was_winner": (
            candidate_return > 0
            if candidate_return is not None
            else False
        ),
        "weakest_was_winner": (
            weakest_return > 0
            if weakest_return is not None
            else False
        ),
        "decision_reason": decision.reason,
    }


def summarize_opportunities(
    opportunities_df: pd.DataFrame,
    *,
    entry_model_name: str,
    ranking_method: str,
    candidate_trades: int,
    executed_trades: int,
) -> dict[str, Any]:
    opportunities = len(
        opportunities_df
    )

    if opportunities_df.empty:
        return {
            "entry_model": entry_model_name,
            "ranking_method": ranking_method,
            "candidate_trades": candidate_trades,
            "executed_trades": executed_trades,
            "replacement_opportunities": 0,
            "opportunity_ratio_pct": 0.0,
            "candidate_outperformed_count": 0,
            "candidate_outperformed_rate_pct": 0.0,
            "average_quality_gap": 0.0,
            "median_quality_gap": 0.0,
            "average_candidate_return_pct": 0.0,
            "average_weakest_return_pct": 0.0,
            "average_hypothetical_return_gap_pct": 0.0,
            "median_hypothetical_return_gap_pct": 0.0,
            "positive_return_gap_count": 0,
            "candidate_win_rate_pct": 0.0,
            "weakest_win_rate_pct": 0.0,
            "best_hypothetical_return_gap_pct": 0.0,
            "worst_hypothetical_return_gap_pct": 0.0,
        }

    return_gap = pd.to_numeric(
        opportunities_df[
            "hypothetical_return_gap_pct"
        ],
        errors="coerce",
    )

    candidate_return = pd.to_numeric(
        opportunities_df[
            "candidate_return_pct"
        ],
        errors="coerce",
    )

    weakest_return = pd.to_numeric(
        opportunities_df[
            "weakest_return_pct"
        ],
        errors="coerce",
    )

    quality_gap = pd.to_numeric(
        opportunities_df[
            "quality_gap"
        ],
        errors="coerce",
    )

    outperformed_count = int(
        (return_gap > 0).sum()
    )

    return {
        "entry_model": entry_model_name,
        "ranking_method": ranking_method,
        "candidate_trades": candidate_trades,
        "executed_trades": executed_trades,
        "replacement_opportunities": opportunities,
        "opportunity_ratio_pct": (
            opportunities
            / candidate_trades
            * 100
            if candidate_trades
            else 0.0
        ),
        "candidate_outperformed_count": (
            outperformed_count
        ),
        "candidate_outperformed_rate_pct": (
            outperformed_count
            / opportunities
            * 100
            if opportunities
            else 0.0
        ),
        "average_quality_gap": (
            quality_gap.mean()
        ),
        "median_quality_gap": (
            quality_gap.median()
        ),
        "average_candidate_return_pct": (
            candidate_return.mean()
        ),
        "average_weakest_return_pct": (
            weakest_return.mean()
        ),
        "average_hypothetical_return_gap_pct": (
            return_gap.mean()
        ),
        "median_hypothetical_return_gap_pct": (
            return_gap.median()
        ),
        "positive_return_gap_count": (
            outperformed_count
        ),
        "candidate_win_rate_pct": (
            (candidate_return > 0).mean()
            * 100
        ),
        "weakest_win_rate_pct": (
            (weakest_return > 0).mean()
            * 100
        ),
        "best_hypothetical_return_gap_pct": (
            return_gap.max()
        ),
        "worst_hypothetical_return_gap_pct": (
            return_gap.min()
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

            values.append(str(value))

        lines.append(
            "| "
            + " | ".join(values)
            + " |"
        )

    return "\n".join(lines)


def build_report(
    *,
    summary_df: pd.DataFrame,
    opportunities_df: pd.DataFrame,
) -> str:
    lines = [
        "# Replacement Opportunity Report",
        "",
        (
            "This report evaluates candidates "
            "that were rejected while the "
            "portfolio was full but had higher "
            "quality than the weakest open "
            "position."
        ),
        "",
        "## Summary",
        "",
    ]

    summary_columns = [
        "entry_model",
        "ranking_method",
        "replacement_opportunities",
        "opportunity_ratio_pct",
        "candidate_outperformed_rate_pct",
        "average_quality_gap",
        "average_candidate_return_pct",
        "average_weakest_return_pct",
        "average_hypothetical_return_gap_pct",
        "median_hypothetical_return_gap_pct",
        "candidate_win_rate_pct",
        "weakest_win_rate_pct",
    ]

    lines.append(
        markdown_table(
            summary_df[
                summary_columns
            ]
        )
    )

    lines.extend(
        [
            "",
            "## Best Hypothetical Replacements",
            "",
        ]
    )

    if opportunities_df.empty:
        lines.append(
            "No replacement opportunities."
        )

    else:
        top_df = (
            opportunities_df
            .sort_values(
                "hypothetical_return_gap_pct",
                ascending=False,
            )
            .head(20)
        )

        lines.append(
            markdown_table(
                top_df[
                    [
                        "entry_model",
                        "ranking_method",
                        "event_date",
                        "candidate_symbol",
                        "weakest_symbol",
                        "quality_gap",
                        "candidate_return_pct",
                        "weakest_return_pct",
                        "hypothetical_return_gap_pct",
                    ]
                ]
            )
        )

        lines.extend(
            [
                "",
                "## Worst Hypothetical Replacements",
                "",
            ]
        )

        worst_df = (
            opportunities_df
            .sort_values(
                "hypothetical_return_gap_pct",
                ascending=True,
            )
            .head(20)
        )

        lines.append(
            markdown_table(
                worst_df[
                    [
                        "entry_model",
                        "ranking_method",
                        "event_date",
                        "candidate_symbol",
                        "weakest_symbol",
                        "quality_gap",
                        "candidate_return_pct",
                        "weakest_return_pct",
                        "hypothetical_return_gap_pct",
                    ]
                ]
            )
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            (
                "- `hypothetical_return_gap_pct` "
                "equals the rejected candidate's "
                "full candidate return minus the "
                "weakest open trade's eventual "
                "portfolio return."
            ),
            (
                "- This is an offline diagnostic, "
                "not a true replacement backtest."
            ),
            (
                "- It does not account for the "
                "replacement exit price, remaining "
                "holding period, extra transaction "
                "cost, or altered future portfolio "
                "capacity."
            ),
            (
                "- Auto replacement should only be "
                "implemented if the opportunity "
                "count is meaningful and the return "
                "gap remains positive after stricter "
                "simulation."
            ),
            "",
        ]
    )

    return "\n".join(lines)


def run_replacement_research(
    *,
    symbols: list[str],
    start_date: str,
    end_date: str,
    initial_capital: float,
    max_positions: int,
    lot_size: int,
    stop_loss_pct: float,
    take_profit_pct: float,
    max_holding_days: int,
    min_adx: float,
    atr_stop_multiplier: float,
    atr_target_multiplier: float,
    ranking_methods: list[str],
    entry_models: list[str] | None,
    replacement_threshold: float,
    opportunities_output_path: str,
    summary_output_path: str,
    report_output_path: str,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
]:
    if max_positions <= 0:
        raise ValueError(
            "max_positions phải lớn hơn 0."
        )

    if replacement_threshold < 0:
        raise ValueError(
            "replacement_threshold không được âm."
        )

    position_size_pct = (
        100.0 / max_positions
    )

    transaction_cost_config = (
        TransactionCostConfig(
            buy_commission_pct=0.15,
            sell_commission_pct=0.15,
            sell_tax_pct=0.10,
            buy_slippage_pct=0.05,
            sell_slippage_pct=0.05,
        )
    )

    model_registry = (
        build_portfolio_model_registry()
    )

    if entry_models is not None:
        unknown_models = (
            set(entry_models)
            - set(model_registry)
        )

        if unknown_models:
            raise ValueError(
                "Entry model không hợp lệ: "
                + ", ".join(
                    sorted(unknown_models)
                )
            )

        model_registry = {
            name: model_registry[name]
            for name in entry_models
        }

    all_opportunity_rows: list[
        dict[str, Any]
    ] = []

    summary_rows: list[
        dict[str, Any]
    ] = []

    for (
        entry_model_name,
        entry_model,
    ) in model_registry.items():
        print()
        print("=" * 110)
        print(
            f"ENTRY MODEL: "
            f"{entry_model_name}"
        )
        print("=" * 110)

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

        base_config = BacktestConfig(
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
            initial_capital=(
                initial_capital
            ),
            position_size_pct=(
                position_size_pct
            ),
            buy_commission_pct=0.15,
            sell_commission_pct=0.15,
            sell_tax_pct=0.10,
            buy_slippage_pct=0.05,
            sell_slippage_pct=0.05,
            ranking_method=(
                "first_come"
            ),
        )

        candidate_trades = (
            build_candidate_trades(
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
                config=base_config,
                entry_model=entry_model,
                exit_model=exit_model,
            )
        )

        print(
            f"Candidate trades: "
            f"{len(candidate_trades)}"
        )

        for ranking_method in ranking_methods:
            if (
                ranking_method
                == RankingMethod.FIRST_COME.value
            ):
                print(
                    "Skip first_come: "
                    "không có quality score."
                )
                continue

            print(
                f"Analyze ranking: "
                f"{ranking_method}"
            )

            simulator = PortfolioSimulator(
                initial_cash=initial_capital,
                position_size_pct=(
                    position_size_pct
                ),
                max_positions=max_positions,
                lot_size=lot_size,
                ranking_method=ranking_method,
                transaction_cost_config=(
                    transaction_cost_config
                ),
            )

            result = simulator.simulate(
                candidate_trades
            )

            run_rows = [
                opportunity_to_row(
                    opportunity=opportunity,
                    entry_model_name=(
                        entry_model_name
                    ),
                    ranking_method=(
                        ranking_method
                    ),
                )
                for opportunity
                in result.replacement_opportunities
                if (
                    opportunity.decision.quality_gap
                    is not None
                    and opportunity.decision.quality_gap
                    >= replacement_threshold
                )
            ]

            run_df = pd.DataFrame(
                run_rows
            )

            all_opportunity_rows.extend(
                run_rows
            )

            summary_rows.append(
                summarize_opportunities(
                    run_df,
                    entry_model_name=(
                        entry_model_name
                    ),
                    ranking_method=(
                        ranking_method
                    ),
                    candidate_trades=len(
                        candidate_trades
                    ),
                    executed_trades=len(
                        result.executed_trades
                    ),
                )
            )

    opportunities_df = pd.DataFrame(
        all_opportunity_rows
    )

    summary_df = pd.DataFrame(
        summary_rows
    )

    if not summary_df.empty:
        summary_df = (
            summary_df
            .sort_values(
                by=[
                    "candidate_outperformed_rate_pct",
                    "average_hypothetical_return_gap_pct",
                    "replacement_opportunities",
                ],
                ascending=[
                    False,
                    False,
                    False,
                ],
            )
            .reset_index(
                drop=True
            )
        )

    if not opportunities_df.empty:
        opportunities_df = (
            opportunities_df
            .sort_values(
                by=[
                    "entry_model",
                    "ranking_method",
                    "event_date",
                    "candidate_symbol",
                ]
            )
            .reset_index(
                drop=True
            )
        )

    save_dataframe(
        opportunities_df,
        opportunities_output_path,
    )

    save_dataframe(
        summary_df,
        summary_output_path,
    )

    report_text = build_report(
        summary_df=summary_df,
        opportunities_df=(
            opportunities_df
        ),
    )

    report_output = Path(
        report_output_path
    )
    report_output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    report_output.write_text(
        report_text,
        encoding="utf-8",
    )

    print(
        f"Đã xuất: {report_output}"
    )

    print()
    print("=" * 190)
    print(
        "REPLACEMENT OPPORTUNITY SUMMARY"
    )
    print("=" * 190)

    if summary_df.empty:
        print("Không có kết quả.")
    else:
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
        opportunities_df,
        summary_df,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Phân tích cơ hội thay thế "
            "vị thế trong portfolio."
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
        "--ranking",
        nargs="+",
        default=[
            "signal_score",
            "relative_strength",
            "adx",
            "volume_ratio",
            "composite",
        ],
        choices=[
            method.value
            for method in RankingMethod
            if (
                method
                != RankingMethod.FIRST_COME
            )
        ],
    )

    parser.add_argument(
        "--replacement-threshold",
        type=float,
        default=0.0,
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
        "--lot-size",
        type=int,
        default=100,
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
        "--opportunities-output",
        default=(
            DEFAULT_OPPORTUNITIES_OUTPUT
        ),
    )

    parser.add_argument(
        "--summary-output",
        default=(
            DEFAULT_SUMMARY_OUTPUT
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
        TOP10_SYMBOLS
        if args.symbol is None
        else [
            symbol.upper().strip()
            for symbol
            in args.symbol
        ]
    )

    ranking_methods = list(
        dict.fromkeys(
            args.ranking
        )
    )

    run_replacement_research(
        symbols=symbols,
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
        max_positions=(
            args.max_positions
        ),
        lot_size=args.lot_size,
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
        ranking_methods=(
            ranking_methods
        ),
        entry_models=(
            args.entry_model
        ),
        replacement_threshold=(
            args.replacement_threshold
        ),
        opportunities_output_path=(
            args.opportunities_output
        ),
        summary_output_path=(
            args.summary_output
        ),
        report_output_path=(
            args.report_output
        ),
    )


if __name__ == "__main__":
    main()
