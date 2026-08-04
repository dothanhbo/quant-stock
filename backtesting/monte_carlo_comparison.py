from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(slots=True, frozen=True)
class MonteCarloComparisonResult:
    comparison: pd.DataFrame
    ranking: pd.DataFrame
    html_content: str

    def save(
        self,
        *,
        output_dir: str | Path,
    ) -> None:
        output_path = Path(output_dir)

        output_path.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.comparison.to_csv(
            output_path / "comparison.csv",
            index=False,
            encoding="utf-8-sig",
        )

        self.ranking.to_csv(
            output_path / "comparison_ranking.csv",
            index=False,
            encoding="utf-8-sig",
        )

        (
            output_path
            / "comparison.html"
        ).write_text(
            self.html_content,
            encoding="utf-8",
        )


def _read_summary(
    path: Path,
    *,
    label: str,
) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Không tìm thấy summary cho "
            f"{label}: {path.resolve()}"
        )

    dataframe = pd.read_csv(
        path
    )

    if dataframe.empty:
        raise ValueError(
            f"Summary của {label} không có dữ liệu."
        )

    row = dataframe.iloc[0].to_dict()

    row["method_label"] = label

    return row


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


def _build_comparison(
    rows: list[dict[str, Any]],
) -> pd.DataFrame:
    comparison = pd.DataFrame(
        rows
    )

    required_columns = [
        "method_label",
        "bootstrap_method",
        "block_size",
        "simulations",
        "trade_count",
        "median_return_pct",
        "mean_return_pct",
        "worst_return_pct",
        "best_return_pct",
        "return_lower_bound_pct",
        "return_upper_bound_pct",
        "median_drawdown_pct",
        "worst_drawdown_pct",
        "drawdown_95pct_bound",
        "probability_of_loss_pct",
        "probability_drawdown_10_pct",
        "probability_drawdown_20_pct",
        "probability_drawdown_30_pct",
    ]

    existing_columns = [
        column
        for column in required_columns
        if column in comparison.columns
    ]

    comparison = comparison[
        existing_columns
    ].copy()

    numeric_columns = [
        column
        for column in comparison.columns
        if column not in {
            "method_label",
            "bootstrap_method",
        }
    ]

    for column in numeric_columns:
        comparison[column] = pd.to_numeric(
            comparison[column],
            errors="coerce",
        )

    return comparison


def _min_max_score(
    series: pd.Series,
    *,
    higher_is_better: bool,
) -> pd.Series:
    numeric = pd.to_numeric(
        series,
        errors="coerce",
    )

    minimum = numeric.min()
    maximum = numeric.max()

    if pd.isna(minimum) or pd.isna(maximum):
        return pd.Series(
            0.0,
            index=series.index,
        )

    if abs(
        float(maximum)
        - float(minimum)
    ) < 1e-12:
        return pd.Series(
            1.0,
            index=series.index,
        )

    score = (
        numeric - minimum
    ) / (
        maximum - minimum
    )

    if higher_is_better:
        return score

    return 1 - score


def _build_ranking(
    comparison: pd.DataFrame,
) -> pd.DataFrame:
    ranking = comparison.copy()

    ranking[
        "score_median_return"
    ] = _min_max_score(
        ranking[
            "median_return_pct"
        ],
        higher_is_better=True,
    )

    ranking[
        "score_lower_bound"
    ] = _min_max_score(
        ranking[
            "return_lower_bound_pct"
        ],
        higher_is_better=True,
    )

    ranking[
        "score_median_drawdown"
    ] = _min_max_score(
        ranking[
            "median_drawdown_pct"
        ].abs(),
        higher_is_better=False,
    )

    ranking[
        "score_drawdown_bound"
    ] = _min_max_score(
        ranking[
            "drawdown_95pct_bound"
        ].abs(),
        higher_is_better=False,
    )

    ranking[
        "score_loss_probability"
    ] = _min_max_score(
        ranking[
            "probability_of_loss_pct"
        ],
        higher_is_better=False,
    )

    ranking[
        "score_20pct_drawdown"
    ] = _min_max_score(
        ranking[
            "probability_drawdown_20_pct"
        ],
        higher_is_better=False,
    )

    ranking[
        "robustness_score"
    ] = (
        ranking[
            "score_median_return"
        ]
        * 0.15
        + ranking[
            "score_lower_bound"
        ]
        * 0.25
        + ranking[
            "score_median_drawdown"
        ]
        * 0.15
        + ranking[
            "score_drawdown_bound"
        ]
        * 0.20
        + ranking[
            "score_loss_probability"
        ]
        * 0.15
        + ranking[
            "score_20pct_drawdown"
        ]
        * 0.10
    ) * 100

    ranking[
        "rank"
    ] = (
        ranking[
            "robustness_score"
        ]
        .rank(
            method="dense",
            ascending=False,
        )
        .astype(int)
    )

    ranking[
        "recommendation"
    ] = ranking[
        "rank"
    ].map(
        lambda rank: (
            "PREFERRED"
            if rank == 1
            else "SECONDARY"
        )
    )

    columns = [
        "rank",
        "method_label",
        "bootstrap_method",
        "block_size",
        "robustness_score",
        "recommendation",
        "median_return_pct",
        "return_lower_bound_pct",
        "median_drawdown_pct",
        "drawdown_95pct_bound",
        "probability_of_loss_pct",
        "probability_drawdown_20_pct",
    ]

    return (
        ranking[
            columns
        ]
        .sort_values(
            by=[
                "rank",
                "robustness_score",
            ],
            ascending=[
                True,
                False,
            ],
        )
        .reset_index(
            drop=True
        )
    )


def _format_pct(
    value: Any,
) -> str:
    return (
        f"{_safe_float(value):+.2f}%"
    )


def _build_html(
    *,
    comparison: pd.DataFrame,
    ranking: pd.DataFrame,
) -> str:
    best = ranking.iloc[0]

    recommendation_text = (
        f"Phương pháp được ưu tiên là "
        f"{best['method_label']} "
        f"với robustness score "
        f"{best['robustness_score']:.2f}/100."
    )

    comparison_html = (
        comparison.to_html(
            index=False,
            border=0,
            classes="data-table",
            float_format=lambda value: (
                f"{value:,.2f}"
            ),
        )
    )

    ranking_html = (
        ranking.to_html(
            index=False,
            border=0,
            classes="data-table",
            float_format=lambda value: (
                f"{value:,.2f}"
            ),
        )
    )

    cards = []

    for _, row in comparison.iterrows():
        cards.append(
            f"""
            <article class="method-card">
                <h3>{html.escape(str(row['method_label']))}</h3>
                <div class="metric">
                    Median Return
                    <strong>
                        {_format_pct(row['median_return_pct'])}
                    </strong>
                </div>
                <div class="metric">
                    95% Lower Return
                    <strong>
                        {_format_pct(row['return_lower_bound_pct'])}
                    </strong>
                </div>
                <div class="metric">
                    Median Drawdown
                    <strong>
                        {_format_pct(row['median_drawdown_pct'])}
                    </strong>
                </div>
                <div class="metric">
                    95% Drawdown Bound
                    <strong>
                        {_format_pct(row['drawdown_95pct_bound'])}
                    </strong>
                </div>
                <div class="metric">
                    Probability of Loss
                    <strong>
                        {_safe_float(
                            row['probability_of_loss_pct']
                        ):.2f}%
                    </strong>
                </div>
            </article>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="utf-8">
    <meta
        name="viewport"
        content="width=device-width, initial-scale=1"
    >
    <title>Monte Carlo V2 Comparison</title>

    <style>
        body {{
            margin: 0;
            font-family: Arial, sans-serif;
            background: #f4f6fa;
            color: #1c2735;
        }}

        main {{
            max-width: 1400px;
            margin: auto;
            padding: 32px 20px 70px;
        }}

        header {{
            padding: 36px;
            color: white;
            background:
                linear-gradient(
                    135deg,
                    #162954,
                    #315fd1
                );
            border-radius: 20px;
        }}

        header h1 {{
            margin: 0 0 10px;
        }}

        section {{
            margin-top: 28px;
            padding: 26px;
            background: white;
            border-radius: 16px;
            box-shadow:
                0 8px 25px
                rgba(20, 35, 60, 0.08);
        }}

        .recommendation {{
            padding: 18px;
            border-left: 5px solid #1a7c54;
            background: #e6f6ee;
            border-radius: 10px;
            font-size: 18px;
        }}

        .method-grid {{
            display: grid;
            grid-template-columns:
                repeat(
                    auto-fit,
                    minmax(260px, 1fr)
                );
            gap: 18px;
        }}

        .method-card {{
            padding: 20px;
            border: 1px solid #dfe5ee;
            border-radius: 14px;
        }}

        .method-card h3 {{
            margin-top: 0;
            color: #315fd1;
        }}

        .metric {{
            display: flex;
            justify-content: space-between;
            gap: 15px;
            padding: 9px 0;
            border-bottom: 1px solid #edf0f5;
        }}

        .table-wrapper {{
            overflow-x: auto;
        }}

        .data-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }}

        .data-table th,
        .data-table td {{
            padding: 10px 12px;
            border-bottom: 1px solid #dfe5ee;
            white-space: nowrap;
            text-align: right;
        }}

        .data-table th {{
            color: white;
            background: #253854;
        }}

        .data-table th:first-child,
        .data-table td:first-child {{
            text-align: left;
        }}
    </style>
</head>

<body>
    <main>
        <header>
            <h1>Monte Carlo V2 Comparison</h1>
            <p>
                Trade Bootstrap vs Block Bootstrap
                vs Regime Bootstrap.
            </p>
        </header>

        <section>
            <h2>Recommendation</h2>
            <div class="recommendation">
                {html.escape(recommendation_text)}
            </div>
        </section>

        <section>
            <h2>Method Overview</h2>
            <div class="method-grid">
                {''.join(cards)}
            </div>
        </section>

        <section>
            <h2>Robustness Ranking</h2>
            <div class="table-wrapper">
                {ranking_html}
            </div>
        </section>

        <section>
            <h2>Full Comparison</h2>
            <div class="table-wrapper">
                {comparison_html}
            </div>
        </section>
    </main>
</body>
</html>
"""


def compare_monte_carlo_methods(
    *,
    trade_summary_path: str | Path,
    block_summary_path: str | Path,
    regime_summary_path: str | Path,
) -> MonteCarloComparisonResult:
    rows = [
        _read_summary(
            Path(trade_summary_path),
            label="Trade Bootstrap",
        ),
        _read_summary(
            Path(block_summary_path),
            label="Block Bootstrap",
        ),
        _read_summary(
            Path(regime_summary_path),
            label="Regime Bootstrap",
        ),
    ]

    comparison = _build_comparison(
        rows
    )

    ranking = _build_ranking(
        comparison
    )

    html_content = _build_html(
        comparison=comparison,
        ranking=ranking,
    )

    return MonteCarloComparisonResult(
        comparison=comparison,
        ranking=ranking,
        html_content=html_content,
    )


def print_monte_carlo_comparison(
    result: MonteCarloComparisonResult,
) -> None:
    print()
    print("=" * 110)
    print("MONTE CARLO V2 COMPARISON")
    print("=" * 110)

    columns = [
        "rank",
        "method_label",
        "robustness_score",
        "median_return_pct",
        "return_lower_bound_pct",
        "median_drawdown_pct",
        "drawdown_95pct_bound",
        "probability_of_loss_pct",
    ]

    print(
        result.ranking[
            columns
        ].to_string(
            index=False,
            float_format=lambda value: (
                f"{value:.2f}"
            ),
        )
    )

    best = result.ranking.iloc[0]

    print()
    print(
        f"Preferred Method: "
        f"{best['method_label']}"
    )
    print(
        f"Robustness Score: "
        f"{best['robustness_score']:.2f}"
    )

    print("=" * 110)