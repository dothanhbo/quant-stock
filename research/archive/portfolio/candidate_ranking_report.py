from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_INPUT = (
    "research_results/"
    "candidate_ranking_summary.csv"
)

DEFAULT_ENRICHED_OUTPUT = (
    "research_results/"
    "candidate_ranking_delta_summary.csv"
)

DEFAULT_WINNERS_OUTPUT = (
    "research_results/"
    "candidate_ranking_winners.csv"
)

DEFAULT_REPORT_OUTPUT = (
    "research_results/"
    "candidate_ranking_report.md"
)

BASELINE_METHOD = "first_come"

DELTA_COLUMNS = {
    "final_equity": "delta_final_equity",
    "total_return_pct": "delta_return_pct",
    "cagr_pct": "delta_cagr_pct",
    "sharpe_ratio": "delta_sharpe",
    "sortino_ratio": "delta_sortino",
    "profit_factor": "delta_profit_factor",
    "win_rate_pct": "delta_win_rate_pct",
    "expectancy_pct": "delta_expectancy_pct",
    "max_drawdown_pct": "delta_drawdown_pct",
    "total_transaction_cost": (
        "delta_transaction_cost"
    ),
}


def _validate_columns(
    dataframe: pd.DataFrame,
) -> None:
    required_columns = {
        "ranking_method",
        "entry_model",
        "total_return_pct",
        "cagr_pct",
        "sharpe_ratio",
        "sortino_ratio",
        "profit_factor",
        "win_rate_pct",
        "expectancy_pct",
        "max_drawdown_pct",
        "final_equity",
        "total_transaction_cost",
    }

    missing_columns = (
        required_columns
        .difference(
            dataframe.columns
        )
    )

    if missing_columns:
        raise ValueError(
            "Summary CSV thiếu cột: "
            + ", ".join(
                sorted(
                    missing_columns
                )
            )
        )


def _save_dataframe(
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
            f"Không thể ghi file "
            f"{output}. "
            "Hãy đóng file trong Excel."
        ) from exc

    print(
        f"Đã xuất: {output}"
    )


def add_baseline_deltas(
    dataframe: pd.DataFrame,
    *,
    baseline_method: str = (
        BASELINE_METHOD
    ),
) -> pd.DataFrame:
    _validate_columns(
        dataframe
    )

    baseline_df = (
        dataframe[
            dataframe[
                "ranking_method"
            ]
            == baseline_method
        ]
        .copy()
    )

    if baseline_df.empty:
        raise ValueError(
            "Không tìm thấy baseline "
            f"'{baseline_method}'."
        )

    duplicate_models = (
        baseline_df[
            "entry_model"
        ]
        .duplicated(
            keep=False
        )
    )

    if duplicate_models.any():
        duplicated_names = (
            baseline_df.loc[
                duplicate_models,
                "entry_model",
            ]
            .astype(str)
            .unique()
            .tolist()
        )

        raise ValueError(
            "Baseline có nhiều dòng "
            "cho cùng entry model: "
            + ", ".join(
                duplicated_names
            )
        )

    baseline_columns = {
        source_column: (
            f"baseline_{source_column}"
        )
        for source_column
        in DELTA_COLUMNS
    }

    baseline_df = (
        baseline_df[
            [
                "entry_model",
                *DELTA_COLUMNS.keys(),
            ]
        ]
        .rename(
            columns=baseline_columns
        )
    )

    enriched_df = (
        dataframe
        .merge(
            baseline_df,
            on="entry_model",
            how="left",
            validate="many_to_one",
        )
    )

    for (
        source_column,
        delta_column,
    ) in DELTA_COLUMNS.items():
        baseline_column = (
            baseline_columns[
                source_column
            ]
        )

        enriched_df[
            delta_column
        ] = (
            pd.to_numeric(
                enriched_df[
                    source_column
                ],
                errors="coerce",
            )
            - pd.to_numeric(
                enriched_df[
                    baseline_column
                ],
                errors="coerce",
            )
        )

    # Drawdown được lưu bằng số âm.
    # Delta dương nghĩa là drawdown tốt hơn
    # baseline, ví dụ -12% thay vì -15%.
    enriched_df[
        "beats_baseline_return"
    ] = (
        enriched_df[
            "delta_return_pct"
        ]
        > 0
    )

    enriched_df[
        "beats_baseline_sharpe"
    ] = (
        enriched_df[
            "delta_sharpe"
        ]
        > 0
    )

    enriched_df[
        "improves_drawdown"
    ] = (
        enriched_df[
            "delta_drawdown_pct"
        ]
        > 0
    )

    enriched_df[
        "beats_baseline_overall"
    ] = (
        enriched_df[
            "beats_baseline_return"
        ]
        & enriched_df[
            "beats_baseline_sharpe"
        ]
        & (
            enriched_df[
                "delta_drawdown_pct"
            ]
            >= 0
        )
    )

    enriched_df = (
        enriched_df
        .sort_values(
            by=[
                "entry_model",
                "sharpe_ratio",
                "total_return_pct",
                "max_drawdown_pct",
                "profit_factor",
            ],
            ascending=[
                True,
                False,
                False,
                False,
                False,
            ],
        )
        .reset_index(
            drop=True
        )
    )

    enriched_df[
        "ranking_within_model"
    ] = (
        enriched_df
        .groupby(
            "entry_model"
        )
        .cumcount()
        + 1
    )

    return enriched_df


def build_winner_summary(
    enriched_df: pd.DataFrame,
) -> pd.DataFrame:
    winner_df = (
        enriched_df[
            enriched_df[
                "ranking_within_model"
            ]
            == 1
        ]
        .copy()
    )

    winner_columns = [
        "entry_model",
        "ranking_method",
        "total_trades",
        "total_return_pct",
        "delta_return_pct",
        "cagr_pct",
        "delta_cagr_pct",
        "sharpe_ratio",
        "delta_sharpe",
        "profit_factor",
        "delta_profit_factor",
        "max_drawdown_pct",
        "delta_drawdown_pct",
        "expectancy_pct",
        "delta_expectancy_pct",
        "beats_baseline_overall",
    ]

    winner_columns = [
        column
        for column
        in winner_columns
        if column
        in winner_df.columns
    ]

    winner_df = (
        winner_df[
            winner_columns
        ]
        .sort_values(
            "entry_model"
        )
        .reset_index(
            drop=True
        )
    )

    return winner_df


def build_method_win_summary(
    winners_df: pd.DataFrame,
) -> pd.DataFrame:
    if winners_df.empty:
        return pd.DataFrame(
            columns=[
                "ranking_method",
                "model_wins",
                "win_rate_pct",
            ]
        )

    method_wins = (
        winners_df[
            "ranking_method"
        ]
        .value_counts()
        .rename_axis(
            "ranking_method"
        )
        .reset_index(
            name="model_wins"
        )
    )

    method_wins[
        "win_rate_pct"
    ] = (
        method_wins[
            "model_wins"
        ]
        / len(
            winners_df
        )
        * 100
    )

    return (
        method_wins
        .sort_values(
            by=[
                "model_wins",
                "ranking_method",
            ],
            ascending=[
                False,
                True,
            ],
        )
        .reset_index(
            drop=True
        )
    )


def _format_number(
    value: Any,
    decimals: int = 2,
    suffix: str = "",
    show_sign: bool = False,
) -> str:
    try:
        numeric_value = float(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        return ""

    sign_format = (
        "+"
        if show_sign
        else ""
    )

    number_text = format(
        numeric_value,
        f"{sign_format}.{decimals}f",
    )

    return (
        f"{number_text}"
        f"{suffix}"
    )


def _markdown_table(
    dataframe: pd.DataFrame,
    columns: list[str],
    labels: dict[str, str],
    formatters: dict[
        str,
        Any,
    ],
) -> str:
    selected_columns = [
        column
        for column
        in columns
        if column
        in dataframe.columns
    ]

    headers = [
        labels.get(
            column,
            column,
        )
        for column
        in selected_columns
    ]

    lines = [
        "| "
        + " | ".join(
            headers
        )
        + " |",
        "| "
        + " | ".join(
            "---"
            for _
            in headers
        )
        + " |",
    ]

    for _, row in dataframe.iterrows():
        values: list[str] = []

        for column in selected_columns:
            value = row[
                column
            ]

            formatter = (
                formatters.get(
                    column
                )
            )

            if formatter is not None:
                value = formatter(
                    value
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


def build_markdown_report(
    enriched_df: pd.DataFrame,
    winners_df: pd.DataFrame,
    method_wins_df: pd.DataFrame,
    *,
    baseline_method: str,
    source_path: str,
) -> str:
    labels = {
        "entry_model": "Entry Model",
        "ranking_method": "Ranking",
        "total_trades": "Trades",
        "total_return_pct": "Return",
        "delta_return_pct": "Δ Return",
        "cagr_pct": "CAGR",
        "delta_cagr_pct": "Δ CAGR",
        "sharpe_ratio": "Sharpe",
        "delta_sharpe": "Δ Sharpe",
        "profit_factor": "PF",
        "delta_profit_factor": "Δ PF",
        "max_drawdown_pct": "Drawdown",
        "delta_drawdown_pct": "Δ Drawdown",
        "expectancy_pct": "Expectancy",
        "delta_expectancy_pct": "Δ Expectancy",
        "model_wins": "Model Wins",
        "win_rate_pct": "Win Rate",
    }

    formatters = {
        "total_return_pct": (
            lambda value:
            _format_number(
                value,
                suffix="%",
                show_sign=True,
            )
        ),
        "delta_return_pct": (
            lambda value:
            _format_number(
                value,
                decimals=4,
                suffix=" pp",
                show_sign=True,
            )
        ),
        "cagr_pct": (
            lambda value:
            _format_number(
                value,
                suffix="%",
                show_sign=True,
            )
        ),
        "delta_cagr_pct": (
            lambda value:
            _format_number(
                value,
                decimals=4,
                suffix=" pp",
                show_sign=True,
            )
        ),
        "sharpe_ratio": (
            lambda value:
            _format_number(
                value,
                decimals=4,
            )
        ),
        "delta_sharpe": (
            lambda value:
            _format_number(
                value,
                decimals=6,
                show_sign=True,
            )
        ),
        "profit_factor": (
            lambda value:
            _format_number(
                value,
                decimals=4,
            )
        ),
        "delta_profit_factor": (
            lambda value:
            _format_number(
                value,
                decimals=6,
                show_sign=True,
            )
        ),
        "max_drawdown_pct": (
            lambda value:
            _format_number(
                value,
                suffix="%",
            )
        ),
        "delta_drawdown_pct": (
            lambda value:
            _format_number(
                value,
                decimals=4,
                suffix=" pp",
                show_sign=True,
            )
        ),
        "expectancy_pct": (
            lambda value:
            _format_number(
                value,
                decimals=4,
                suffix="%",
                show_sign=True,
            )
        ),
        "delta_expectancy_pct": (
            lambda value:
            _format_number(
                value,
                decimals=6,
                suffix=" pp",
                show_sign=True,
            )
        ),
        "win_rate_pct": (
            lambda value:
            _format_number(
                value,
                suffix="%",
            )
        ),
    }

    report_lines = [
        "# Candidate Ranking Research Report",
        "",
        f"- Source: `{source_path}`",
        f"- Baseline: `{baseline_method}`",
        f"- Entry models: "
        f"{enriched_df['entry_model'].nunique()}",
        f"- Ranking methods: "
        f"{enriched_df['ranking_method'].nunique()}",
        "",
        "## Winner by Entry Model",
        "",
        _markdown_table(
            winners_df,
            columns=[
                "entry_model",
                "ranking_method",
                "total_trades",
                "total_return_pct",
                "delta_return_pct",
                "sharpe_ratio",
                "delta_sharpe",
                "profit_factor",
                "delta_profit_factor",
                "max_drawdown_pct",
                "delta_drawdown_pct",
            ],
            labels=labels,
            formatters=formatters,
        ),
        "",
        "## Ranking Method Wins",
        "",
        _markdown_table(
            method_wins_df,
            columns=[
                "ranking_method",
                "model_wins",
                "win_rate_pct",
            ],
            labels=labels,
            formatters=formatters,
        ),
        "",
    ]

    for entry_model in (
        enriched_df[
            "entry_model"
        ]
        .drop_duplicates()
        .tolist()
    ):
        model_df = (
            enriched_df[
                enriched_df[
                    "entry_model"
                ]
                == entry_model
            ]
            .sort_values(
                "ranking_within_model"
            )
        )

        winner_row = (
            model_df.iloc[0]
        )

        report_lines.extend(
            [
                f"## {entry_model}",
                "",
                (
                    f"Winner: "
                    f"`{winner_row['ranking_method']}`"
                ),
                "",
                _markdown_table(
                    model_df,
                    columns=[
                        "ranking_method",
                        "total_trades",
                        "total_return_pct",
                        "delta_return_pct",
                        "cagr_pct",
                        "delta_cagr_pct",
                        "sharpe_ratio",
                        "delta_sharpe",
                        "profit_factor",
                        "delta_profit_factor",
                        "max_drawdown_pct",
                        "delta_drawdown_pct",
                        "expectancy_pct",
                        "delta_expectancy_pct",
                    ],
                    labels=labels,
                    formatters=formatters,
                ),
                "",
            ]
        )

    overall_winner = (
        method_wins_df.iloc[0]
        if not method_wins_df.empty
        else None
    )

    report_lines.extend(
        [
            "## Conclusion",
            "",
        ]
    )

    if overall_winner is None:
        report_lines.append(
            "No winner could be determined."
        )

    else:
        report_lines.append(
            (
                "The ranking method with the "
                "most entry-model wins was "
                f"`{overall_winner['ranking_method']}`, "
                f"with "
                f"{int(overall_winner['model_wins'])} "
                f"wins out of "
                f"{len(winners_df)} models."
            )
        )

    report_lines.extend(
        [
            "",
            (
                "A positive `Δ Drawdown` means "
                "the drawdown was less negative "
                "than the first-come baseline."
            ),
            "",
            (
                "Very small deltas should be "
                "treated as economically weak "
                "until they survive additional "
                "universes and out-of-sample tests."
            ),
            "",
        ]
    )

    return "\n".join(
        report_lines
    )


def run_report(
    *,
    input_path: str,
    enriched_output_path: str,
    winners_output_path: str,
    report_output_path: str,
    baseline_method: str = (
        BASELINE_METHOD
    ),
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
]:
    input_file = Path(
        input_path
    )

    if not input_file.exists():
        raise FileNotFoundError(
            f"Không tìm thấy file: "
            f"{input_file.resolve()}"
        )

    summary_df = pd.read_csv(
        input_file
    )

    enriched_df = (
        add_baseline_deltas(
            summary_df,
            baseline_method=(
                baseline_method
            ),
        )
    )

    winners_df = (
        build_winner_summary(
            enriched_df
        )
    )

    method_wins_df = (
        build_method_win_summary(
            winners_df
        )
    )

    _save_dataframe(
        enriched_df,
        enriched_output_path,
    )

    _save_dataframe(
        winners_df,
        winners_output_path,
    )

    report_text = (
        build_markdown_report(
            enriched_df,
            winners_df,
            method_wins_df,
            baseline_method=(
                baseline_method
            ),
            source_path=(
                input_path
            ),
        )
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
        "=" * 140
    )
    print(
        "WINNER BY ENTRY MODEL"
    )
    print(
        "=" * 140
    )

    if winners_df.empty:
        print(
            "Không có kết quả."
        )

    else:
        print(
            winners_df.to_string(
                index=False,
                float_format=(
                    lambda value:
                    f"{value:.4f}"
                ),
            )
        )

    print()
    print(
        "=" * 80
    )
    print(
        "RANKING METHOD WINS"
    )
    print(
        "=" * 80
    )

    print(
        method_wins_df.to_string(
            index=False,
            float_format=(
                lambda value:
                f"{value:.2f}"
            ),
        )
    )

    return (
        enriched_df,
        winners_df,
        method_wins_df,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Tạo delta report và winner "
            "summary cho Candidate Ranking."
        )
    )

    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT,
    )

    parser.add_argument(
        "--baseline",
        default=BASELINE_METHOD,
    )

    parser.add_argument(
        "--enriched-output",
        default=(
            DEFAULT_ENRICHED_OUTPUT
        ),
    )

    parser.add_argument(
        "--winners-output",
        default=(
            DEFAULT_WINNERS_OUTPUT
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

    run_report(
        input_path=args.input,
        enriched_output_path=(
            args.enriched_output
        ),
        winners_output_path=(
            args.winners_output
        ),
        report_output_path=(
            args.report_output
        ),
        baseline_method=(
            args.baseline
        ),
    )


if __name__ == "__main__":
    main()
