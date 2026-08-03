from __future__ import annotations

import argparse
from collections import Counter
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


DEFAULT_SUMMARY_OUTPUT = (
    "research_results/"
    "candidate_competition_summary.csv"
)

DEFAULT_DAILY_OUTPUT = (
    "research_results/"
    "candidate_competition_daily.csv"
)

DEFAULT_REJECTED_OUTPUT = (
    "research_results/"
    "candidate_competition_rejected.csv"
)

DEFAULT_REPORT_OUTPUT = (
    "research_results/"
    "candidate_competition_report.md"
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
            f"Không thể ghi file {output}. "
            "Hãy đóng file trong Excel."
        ) from exc

    print(
        f"Đã xuất: {output}"
    )


def safe_float(
    value: Any,
    default: float = 0.0,
) -> float:
    try:
        result = float(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        return default

    if pd.isna(
        result
    ):
        return default

    return result


def trade_to_diagnostic_row(
    *,
    trade: Any,
    ranking_method: str,
    entry_model: str,
    rejection_reason: str,
) -> dict[str, Any]:
    return {
        "ranking_method": ranking_method,
        "entry_model": entry_model,
        "entry_date": getattr(
            trade,
            "entry_date",
            None,
        ),
        "symbol": getattr(
            trade,
            "symbol",
            None,
        ),
        "rejection_reason": (
            rejection_reason
        ),
        "signal_score": getattr(
            trade,
            "signal_score",
            None,
        ),
        "relative_strength": getattr(
            trade,
            "relative_strength",
            None,
        ),
        "adx": getattr(
            trade,
            "adx",
            None,
        ),
        "volume_ratio": getattr(
            trade,
            "volume_ratio",
            None,
        ),
        "market_regime": getattr(
            trade,
            "market_regime",
            None,
        ),
        "candidate_return_pct": getattr(
            trade,
            "return_pct",
            None,
        ),
        "candidate_net_return_pct": getattr(
            trade,
            "net_return_pct",
            None,
        ),
        "candidate_exit_date": getattr(
            trade,
            "exit_date",
            None,
        ),
        "candidate_exit_reason": str(
            getattr(
                trade,
                "exit_reason",
                "",
            )
        ),
    }


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

        symbol_trades = (
            generate_candidate_trades(
                symbol=symbol,
                config=config,
                start_date=start_date,
                end_date=end_date,
                entry_model=entry_model,
                exit_model=exit_model,
                verbose=False,
            )
        )

        candidate_trades.extend(
            symbol_trades
        )

    return candidate_trades


def count_active_before_entries(
    *,
    executed_trades: list[Any],
    event_date: pd.Timestamp,
) -> int:
    """
    Đếm vị thế còn mở sau khi xử lý các exit bình thường
    của ngày hiện tại nhưng trước khi mở entry mới.

    Trade có exit_date == event_date đã được đóng trước entry,
    nên không được tính là đang mở.
    """

    return sum(
        1
        for trade
        in executed_trades
        if (
            pd.Timestamp(
                trade.entry_date
            )
            < event_date
            and pd.Timestamp(
                trade.exit_date
            )
            > event_date
        )
    )


def analyze_one_run(
    *,
    candidate_trades: list[Any],
    ranking_method: str,
    entry_model_name: str,
    initial_capital: float,
    position_size_pct: float,
    max_positions: int,
    lot_size: int,
    transaction_cost_config: (
        TransactionCostConfig
    ),
) -> tuple[
    dict[str, Any],
    pd.DataFrame,
    pd.DataFrame,
]:
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

    executed_trades = (
        result.executed_trades
    )

    candidates_by_date: dict[
        pd.Timestamp,
        list[Any],
    ] = {}

    for trade in candidate_trades:
        entry_date = pd.Timestamp(
            trade.entry_date
        )

        candidates_by_date.setdefault(
            entry_date,
            [],
        ).append(
            trade
        )

    executed_by_date: dict[
        pd.Timestamp,
        list[Any],
    ] = {}

    for trade in executed_trades:
        entry_date = pd.Timestamp(
            trade.entry_date
        )

        executed_by_date.setdefault(
            entry_date,
            [],
        ).append(
            trade
        )

    rejected_by_date: dict[
        pd.Timestamp,
        list[Any],
    ] = {}

    rejected_rows: list[
        dict[str, Any]
    ] = []

    rejection_reason_counts = Counter()

    for rejected in (
        result.rejected_trades
    ):
        trade = rejected.trade
        entry_date = pd.Timestamp(
            trade.entry_date
        )

        rejected_by_date.setdefault(
            entry_date,
            [],
        ).append(
            rejected
        )

        rejection_reason_counts[
            rejected.reason
        ] += 1

        rejected_rows.append(
            trade_to_diagnostic_row(
                trade=trade,
                ranking_method=(
                    ranking_method
                ),
                entry_model=(
                    entry_model_name
                ),
                rejection_reason=(
                    rejected.reason
                ),
            )
        )

    daily_rows: list[
        dict[str, Any]
    ] = []

    for event_date in sorted(
        candidates_by_date
    ):
        candidates = (
            candidates_by_date[
                event_date
            ]
        )

        executed_today = (
            executed_by_date.get(
                event_date,
                [],
            )
        )

        rejected_today = (
            rejected_by_date.get(
                event_date,
                [],
            )
        )

        active_before = (
            count_active_before_entries(
                executed_trades=(
                    executed_trades
                ),
                event_date=event_date,
            )
        )

        available_slots = max(
            max_positions
            - active_before,
            0,
        )

        candidate_count = len(
            candidates
        )

        accepted_count = len(
            executed_today
        )

        max_position_rejections = sum(
            1
            for rejected
            in rejected_today
            if rejected.reason
            == "max_positions"
        )

        duplicate_rejections = sum(
            1
            for rejected
            in rejected_today
            if rejected.reason
            == "duplicate_symbol"
        )

        cash_rejections = sum(
            1
            for rejected
            in rejected_today
            if rejected.reason
            == "insufficient_cash"
        )

        competition_flag = (
            candidate_count
            > available_slots
        )

        ranking_decision_flag = (
            max_position_rejections
            > 0
        )

        selected_returns = [
            safe_float(
                getattr(
                    trade,
                    "return_pct",
                    0.0,
                )
            )
            for trade
            in executed_today
        ]

        rejected_candidate_returns = [
            safe_float(
                getattr(
                    rejected.trade,
                    "return_pct",
                    0.0,
                )
            )
            for rejected
            in rejected_today
            if rejected.reason
            == "max_positions"
        ]

        daily_rows.append(
            {
                "ranking_method": (
                    ranking_method
                ),
                "entry_model": (
                    entry_model_name
                ),
                "date": event_date,
                "candidate_count": (
                    candidate_count
                ),
                "active_before_entries": (
                    active_before
                ),
                "available_slots": (
                    available_slots
                ),
                "accepted_count": (
                    accepted_count
                ),
                "rejected_count": len(
                    rejected_today
                ),
                "max_position_rejections": (
                    max_position_rejections
                ),
                "duplicate_rejections": (
                    duplicate_rejections
                ),
                "cash_rejections": (
                    cash_rejections
                ),
                "competition_flag": (
                    competition_flag
                ),
                "ranking_decision_flag": (
                    ranking_decision_flag
                ),
                "competition_excess": max(
                    candidate_count
                    - available_slots,
                    0,
                ),
                "selected_average_return_pct": (
                    sum(
                        selected_returns
                    )
                    / len(
                        selected_returns
                    )
                    if selected_returns
                    else 0.0
                ),
                "ranking_rejected_average_return_pct": (
                    sum(
                        rejected_candidate_returns
                    )
                    / len(
                        rejected_candidate_returns
                    )
                    if rejected_candidate_returns
                    else 0.0
                ),
            }
        )

    daily_df = pd.DataFrame(
        daily_rows
    )

    rejected_df = pd.DataFrame(
        rejected_rows
    )

    signal_days = len(
        daily_df
    )

    competition_days = int(
        daily_df[
            "competition_flag"
        ].sum()
    ) if not daily_df.empty else 0

    ranking_decision_days = int(
        daily_df[
            "ranking_decision_flag"
        ].sum()
    ) if not daily_df.empty else 0

    ranking_rejected_df = (
        rejected_df[
            rejected_df[
                "rejection_reason"
            ]
            == "max_positions"
        ]
        if (
            not rejected_df.empty
            and "rejection_reason"
            in rejected_df.columns
        )
        else pd.DataFrame()
    )

    summary = {
        "ranking_method": ranking_method,
        "entry_model": (
            entry_model_name
        ),
        "candidate_trades": len(
            candidate_trades
        ),
        "executed_trades": len(
            executed_trades
        ),
        "rejected_trades": len(
            result.rejected_trades
        ),
        "signal_days": signal_days,
        "competition_days": (
            competition_days
        ),
        "competition_ratio_pct": (
            competition_days
            / signal_days
            * 100
            if signal_days
            else 0.0
        ),
        "ranking_decision_days": (
            ranking_decision_days
        ),
        "ranking_decision_ratio_pct": (
            ranking_decision_days
            / signal_days
            * 100
            if signal_days
            else 0.0
        ),
        "average_candidates_per_signal_day": (
            safe_float(
                daily_df[
                    "candidate_count"
                ].mean()
            )
            if not daily_df.empty
            else 0.0
        ),
        "median_candidates_per_signal_day": (
            safe_float(
                daily_df[
                    "candidate_count"
                ].median()
            )
            if not daily_df.empty
            else 0.0
        ),
        "maximum_candidates_one_day": (
            int(
                daily_df[
                    "candidate_count"
                ].max()
            )
            if not daily_df.empty
            else 0
        ),
        "average_available_slots": (
            safe_float(
                daily_df[
                    "available_slots"
                ].mean()
            )
            if not daily_df.empty
            else 0.0
        ),
        "max_position_rejections": (
            rejection_reason_counts.get(
                "max_positions",
                0,
            )
        ),
        "duplicate_symbol_rejections": (
            rejection_reason_counts.get(
                "duplicate_symbol",
                0,
            )
        ),
        "insufficient_cash_rejections": (
            rejection_reason_counts.get(
                "insufficient_cash",
                0,
            )
        ),
        "ranking_rejected_average_return_pct": (
            safe_float(
                ranking_rejected_df[
                    "candidate_return_pct"
                ].mean()
            )
            if (
                not ranking_rejected_df.empty
                and "candidate_return_pct"
                in ranking_rejected_df.columns
            )
            else 0.0
        ),
        "ranking_rejected_win_rate_pct": (
            (
                pd.to_numeric(
                    ranking_rejected_df[
                        "candidate_return_pct"
                    ],
                    errors="coerce",
                )
                > 0
            ).mean()
            * 100
            if (
                not ranking_rejected_df.empty
                and "candidate_return_pct"
                in ranking_rejected_df.columns
            )
            else 0.0
        ),
    }

    return (
        summary,
        daily_df,
        rejected_df,
    )


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
        + " | ".join(
            columns
        )
        + " |",
        "| "
        + " | ".join(
            "---"
            for _
            in columns
        )
        + " |",
    ]

    for _, row in dataframe.iterrows():
        values = []

        for column in columns:
            value = row[
                column
            ]

            if isinstance(
                value,
                float,
            ):
                value = (
                    f"{value:.2f}"
                )

            values.append(
                str(
                    value
                )
            )

        lines.append(
            "| "
            + " | ".join(
                values
            )
            + " |"
        )

    return "\n".join(
        lines
    )


def build_report(
    *,
    summary_df: pd.DataFrame,
    daily_df: pd.DataFrame,
) -> str:
    report_lines = [
        "# Candidate Competition Report",
        "",
        (
            "This report measures how often "
            "candidate ranking can actually "
            "affect portfolio selection."
        ),
        "",
        "## Summary",
        "",
        markdown_table(
            summary_df[
                [
                    "entry_model",
                    "ranking_method",
                    "candidate_trades",
                    "executed_trades",
                    "signal_days",
                    "competition_days",
                    "competition_ratio_pct",
                    "ranking_decision_days",
                    "ranking_decision_ratio_pct",
                    "max_position_rejections",
                    "maximum_candidates_one_day",
                ]
            ]
        ),
        "",
        "## Most Competitive Days",
        "",
    ]

    if daily_df.empty:
        report_lines.append(
            "No daily competition data."
        )

    else:
        top_days = (
            daily_df
            .sort_values(
                by=[
                    "competition_excess",
                    "candidate_count",
                ],
                ascending=[
                    False,
                    False,
                ],
            )
            .head(
                20
            )
        )

        report_lines.append(
            markdown_table(
                top_days[
                    [
                        "entry_model",
                        "ranking_method",
                        "date",
                        "candidate_count",
                        "active_before_entries",
                        "available_slots",
                        "accepted_count",
                        "max_position_rejections",
                        "competition_excess",
                    ]
                ]
            )
        )

    report_lines.extend(
        [
            "",
            "## Interpretation",
            "",
            (
                "- `competition_flag` is true "
                "when candidate count exceeds "
                "the slots available before "
                "new entries."
            ),
            (
                "- `ranking_decision_flag` is "
                "true only when at least one "
                "candidate is rejected because "
                "the portfolio reached "
                "`max_positions`."
            ),
            (
                "- A low ranking-decision ratio "
                "explains why different ranking "
                "methods can produce nearly "
                "identical portfolio results."
            ),
            (
                "- Rejected candidate returns "
                "are hypothetical candidate "
                "backtest outcomes, not returns "
                "earned by the portfolio."
            ),
            "",
        ]
    )

    return "\n".join(
        report_lines
    )


def run_competition_research(
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
    summary_output_path: str,
    daily_output_path: str,
    rejected_output_path: str,
    report_output_path: str,
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
            set(
                entry_models
            )
            - set(
                model_registry
            )
        )

        if unknown_models:
            raise ValueError(
                "Entry model không hợp lệ: "
                + ", ".join(
                    sorted(
                        unknown_models
                    )
                )
            )

        model_registry = {
            name: model_registry[
                name
            ]
            for name
            in entry_models
        }

    summary_rows: list[
        dict[str, Any]
    ] = []

    daily_frames: list[
        pd.DataFrame
    ] = []

    rejected_frames: list[
        pd.DataFrame
    ] = []

    for (
        entry_model_name,
        entry_model,
    ) in model_registry.items():
        print()
        print(
            "=" * 110
        )
        print(
            f"ENTRY MODEL: "
            f"{entry_model_name}"
        )
        print(
            "=" * 110
        )

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

        for ranking_method in (
            ranking_methods
        ):
            print(
                f"Analyze ranking: "
                f"{ranking_method}"
            )

            (
                summary,
                daily_df,
                rejected_df,
            ) = analyze_one_run(
                candidate_trades=(
                    candidate_trades
                ),
                ranking_method=(
                    ranking_method
                ),
                entry_model_name=(
                    entry_model_name
                ),
                initial_capital=(
                    initial_capital
                ),
                position_size_pct=(
                    position_size_pct
                ),
                max_positions=(
                    max_positions
                ),
                lot_size=lot_size,
                transaction_cost_config=(
                    transaction_cost_config
                ),
            )

            summary_rows.append(
                summary
            )

            if not daily_df.empty:
                daily_frames.append(
                    daily_df
                )

            if not rejected_df.empty:
                rejected_frames.append(
                    rejected_df
                )

    summary_df = pd.DataFrame(
        summary_rows
    )

    daily_result_df = (
        pd.concat(
            daily_frames,
            ignore_index=True,
        )
        if daily_frames
        else pd.DataFrame()
    )

    rejected_result_df = (
        pd.concat(
            rejected_frames,
            ignore_index=True,
        )
        if rejected_frames
        else pd.DataFrame()
    )

    save_dataframe(
        summary_df,
        summary_output_path,
    )

    save_dataframe(
        daily_result_df,
        daily_output_path,
    )

    save_dataframe(
        rejected_result_df,
        rejected_output_path,
    )

    report_text = build_report(
        summary_df=summary_df,
        daily_df=daily_result_df,
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
    print(
        "=" * 180
    )
    print(
        "CANDIDATE COMPETITION SUMMARY"
    )
    print(
        "=" * 180
    )

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
        daily_result_df,
        rejected_result_df,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Phân tích mức độ cạnh tranh "
            "giữa candidate cùng ngày."
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
            method.value
            for method
            in RankingMethod
        ],
        choices=[
            method.value
            for method
            in RankingMethod
        ],
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
        "--summary-output",
        default=(
            DEFAULT_SUMMARY_OUTPUT
        ),
    )

    parser.add_argument(
        "--daily-output",
        default=(
            DEFAULT_DAILY_OUTPUT
        ),
    )

    parser.add_argument(
        "--rejected-output",
        default=(
            DEFAULT_REJECTED_OUTPUT
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

    run_competition_research(
        symbols=symbols,
        start_date=args.start,
        end_date=args.end,
        initial_capital=(
            args.capital
        ),
        max_positions=(
            args.max_positions
        ),
        lot_size=args.lot_size,
        stop_loss_pct=args.sl,
        take_profit_pct=args.tp,
        max_holding_days=(
            args.hold
        ),
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
        summary_output_path=(
            args.summary_output
        ),
        daily_output_path=(
            args.daily_output
        ),
        rejected_output_path=(
            args.rejected_output
        ),
        report_output_path=(
            args.report_output
        ),
    )


if __name__ == "__main__":
    main()
