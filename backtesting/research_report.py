from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(slots=True, frozen=True)
class ResearchReportData:
    wfo_summary: pd.DataFrame
    folds: pd.DataFrame
    parameter_frequency: pd.DataFrame
    parameter_stability: pd.DataFrame
    robust_parameters: pd.DataFrame
    recommendations: pd.DataFrame
    heatmap_summary: pd.DataFrame


@dataclass(slots=True, frozen=True)
class ResearchReportResult:
    html_content: str

    def save(
        self,
        output_path: str | Path,
    ) -> None:
        path = Path(output_path)

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        path.write_text(
            self.html_content,
            encoding="utf-8",
        )


def _read_csv(
    path: Path,
    *,
    required: bool = True,
) -> pd.DataFrame:
    if not path.exists():
        if required:
            raise FileNotFoundError(
                f"Không tìm thấy file: {path.resolve()}"
            )

        return pd.DataFrame()

    return pd.read_csv(path)


def load_research_report_data(
    input_dir: str | Path,
) -> ResearchReportData:
    directory = Path(input_dir)

    return ResearchReportData(
        wfo_summary=_read_csv(
            directory / "summary.csv"
        ),
        folds=_read_csv(
            directory / "folds.csv"
        ),
        parameter_frequency=_read_csv(
            directory / "parameter_frequency.csv"
        ),
        parameter_stability=_read_csv(
            directory / "parameter_stability.csv"
        ),
        robust_parameters=_read_csv(
            directory / "robust_parameters.csv"
        ),
        recommendations=_read_csv(
            directory / "parameter_recommendation.csv"
        ),
        heatmap_summary=_read_csv(
            directory / "heatmap_summary.csv",
            required=False,
        ),
    )


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


def _format_number(
    value: Any,
    *,
    decimals: int = 2,
) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return html.escape(str(value))

    if pd.isna(numeric):
        return "-"

    return f"{numeric:,.{decimals}f}"


def _format_percentage(
    value: Any,
) -> str:
    return f"{_safe_float(value):+.2f}%"


def _format_currency(
    value: Any,
) -> str:
    return f"{_safe_float(value):,.0f}"


def _confidence_badge(
    confidence: str,
) -> str:
    normalized = str(confidence).upper()

    badge_class = {
        "HIGH": "badge-high",
        "MEDIUM": "badge-medium",
        "LOW": "badge-low",
    }.get(
        normalized,
        "badge-neutral",
    )

    return (
        f'<span class="badge {badge_class}">'
        f"{html.escape(normalized)}"
        "</span>"
    )


def _dataframe_to_html(
    dataframe: pd.DataFrame,
    *,
    columns: list[str] | None = None,
    max_rows: int | None = None,
) -> str:
    if dataframe.empty:
        return (
            '<div class="empty-state">'
            "Không có dữ liệu."
            "</div>"
        )

    working = dataframe.copy()

    if columns is not None:
        existing_columns = [
            column
            for column in columns
            if column in working.columns
        ]

        working = working[
            existing_columns
        ]

    if max_rows is not None:
        working = working.head(
            max_rows
        )

    return working.to_html(
        index=False,
        border=0,
        classes="data-table",
        escape=True,
        na_rep="-",
        float_format=lambda value: (
            f"{value:,.2f}"
        ),
    )


def _build_executive_summary(
    data: ResearchReportData,
) -> str:
    if data.wfo_summary.empty:
        return (
            '<div class="empty-state">'
            "Không có summary."
            "</div>"
        )

    summary = data.wfo_summary.iloc[0]

    cards = [
        (
            "Walk-Forward Return",
            _format_percentage(
                summary.get(
                    "walk_forward_return_pct"
                )
            ),
        ),
        (
            "Final Equity",
            _format_currency(
                summary.get(
                    "final_equity"
                )
            ),
        ),
        (
            "Profitable Folds",
            (
                f"{int(summary.get('profitable_folds', 0))}"
                f" / "
                f"{int(summary.get('folds', 0))}"
            ),
        ),
        (
            "Profitable Fold %",
            (
                f"{_safe_float(summary.get('profitable_fold_pct')):.2f}%"
            ),
        ),
        (
            "Total Test Trades",
            str(
                int(
                    summary.get(
                        "total_test_trades",
                        0,
                    )
                )
            ),
        ),
        (
            "Average Test Sharpe",
            _format_number(
                summary.get(
                    "average_test_sharpe"
                )
            ),
        ),
        (
            "Worst Test Drawdown",
            _format_percentage(
                summary.get(
                    "worst_test_drawdown_pct"
                )
            ),
        ),
        (
            "Best Test Return",
            _format_percentage(
                summary.get(
                    "best_test_return_pct"
                )
            ),
        ),
    ]

    card_html = "\n".join(
        (
            '<div class="metric-card">'
            f'<div class="metric-label">{html.escape(label)}</div>'
            f'<div class="metric-value">{html.escape(value)}</div>'
            "</div>"
        )
        for label, value in cards
    )

    return (
        '<div class="metric-grid">'
        f"{card_html}"
        "</div>"
    )


def _build_recommendations(
    recommendations: pd.DataFrame,
) -> str:
    if recommendations.empty:
        return (
            '<div class="empty-state">'
            "Không có recommendation."
            "</div>"
        )

    cards: list[str] = []

    for _, row in recommendations.iterrows():
        parameter = html.escape(
            str(
                row.get(
                    "parameter",
                    "",
                )
            )
        )

        recommended = html.escape(
            str(
                row.get(
                    "recommended_value",
                    "",
                )
            )
        )

        robust_values = html.escape(
            str(
                row.get(
                    "robust_values",
                    "",
                )
            )
        )

        confidence = str(
            row.get(
                "confidence",
                "",
            )
        )

        status = html.escape(
            str(
                row.get(
                    "status",
                    "",
                )
            )
        )

        reason = html.escape(
            str(
                row.get(
                    "reason",
                    "",
                )
            )
        )

        cards.append(
            '<article class="recommendation-card">'
            '<div class="recommendation-header">'
            f"<h3>{parameter}</h3>"
            f"{_confidence_badge(confidence)}"
            "</div>"
            f'<div class="recommended-value">{recommended}</div>'
            f'<div class="recommendation-meta">'
            f"Robust values: {robust_values}<br>"
            f"Status: {status}"
            "</div>"
            f'<p class="recommendation-reason">{reason}</p>'
            "</article>"
        )

    return (
        '<div class="recommendation-grid">'
        + "\n".join(cards)
        + "</div>"
    )


def _build_fold_table(
    folds: pd.DataFrame,
) -> str:
    columns = [
        "fold",
        "train_start",
        "train_end",
        "test_start",
        "test_end",
        "selected_atr_stop_multiplier",
        "selected_atr_target_multiplier",
        "selected_max_holding_days",
        "selected_min_adx",
        "train_return_pct",
        "test_return_pct",
        "test_sharpe_ratio",
        "test_max_drawdown_pct",
        "test_trades",
    ]

    return _dataframe_to_html(
        folds,
        columns=columns,
    )


def _build_frequency_table(
    frequency: pd.DataFrame,
) -> str:
    columns = [
        "parameter",
        "value",
        "selected_count",
        "total_folds",
        "selected_pct",
        "rank_within_parameter",
        "is_mode",
    ]

    return _dataframe_to_html(
        frequency,
        columns=columns,
    )


def _build_stability_table(
    stability: pd.DataFrame,
) -> str:
    columns = [
        "parameter",
        "value",
        "stability_rank",
        "stability_score",
        "mean_sharpe_ratio",
        "std_sharpe_ratio",
        "mean_return_pct",
        "std_return_pct",
        "mean_drawdown_pct",
        "sharpe_cv",
    ]

    return _dataframe_to_html(
        stability,
        columns=columns,
    )


def _build_robust_table(
    robust: pd.DataFrame,
) -> str:
    columns = [
        "parameter",
        "robust_values",
        "robust_min",
        "robust_max",
        "recommended_value",
        "best_score",
        "score_tolerance_pct",
    ]

    return _dataframe_to_html(
        robust,
        columns=columns,
    )


def _image_section(
    *,
    image_filename: str,
    title: str,
    description: str,
) -> str:
    return (
        '<article class="chart-card">'
        f"<h3>{html.escape(title)}</h3>"
        f"<p>{html.escape(description)}</p>"
        f'<img src="{html.escape(image_filename)}" '
        f'alt="{html.escape(title)}">'
        "</article>"
    )


def build_research_report(
    *,
    data: ResearchReportData,
    report_title: str = (
        "Quant Stock Research Report"
    ),
) -> ResearchReportResult:
    executive_summary = (
        _build_executive_summary(
            data
        )
    )

    recommendations = (
        _build_recommendations(
            data.recommendations
        )
    )

    folds_table = (
        _build_fold_table(
            data.folds
        )
    )

    frequency_table = (
        _build_frequency_table(
            data.parameter_frequency
        )
    )

    stability_table = (
        _build_stability_table(
            data.parameter_stability
        )
    )

    robust_table = (
        _build_robust_table(
            data.robust_parameters
        )
    )

    heatmap_summary = (
        _dataframe_to_html(
            data.heatmap_summary
        )
    )

    charts = "\n".join(
        [
            _image_section(
                image_filename=(
                    "heatmap_sharpe.png"
                ),
                title="Sharpe Heatmap",
                description=(
                    "Mean train Sharpe by ATR stop "
                    "and ATR target."
                ),
            ),
            _image_section(
                image_filename=(
                    "heatmap_return.png"
                ),
                title="Return Heatmap",
                description=(
                    "Mean train return by ATR stop "
                    "and ATR target."
                ),
            ),
            _image_section(
                image_filename=(
                    "heatmap_drawdown.png"
                ),
                title="Drawdown Heatmap",
                description=(
                    "Mean train drawdown by ATR stop "
                    "and ATR target."
                ),
            ),
        ]
    )

    document = f"""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="utf-8">
    <meta
        name="viewport"
        content="width=device-width, initial-scale=1"
    >
    <title>{html.escape(report_title)}</title>

    <style>
        :root {{
            --background: #f5f7fb;
            --surface: #ffffff;
            --surface-alt: #eef2f7;
            --text: #182230;
            --muted: #637083;
            --border: #dce3ec;
            --accent: #2457d6;
            --success: #137a4f;
            --warning: #9b6500;
            --danger: #a93636;
            --shadow: 0 10px 30px rgba(20, 35, 60, 0.08);
        }}

        * {{
            box-sizing: border-box;
        }}

        body {{
            margin: 0;
            font-family:
                Inter,
                Arial,
                Helvetica,
                sans-serif;
            color: var(--text);
            background: var(--background);
            line-height: 1.55;
        }}

        .page {{
            max-width: 1440px;
            margin: 0 auto;
            padding: 36px 24px 80px;
        }}

        .hero {{
            padding: 42px;
            border-radius: 22px;
            background:
                linear-gradient(
                    135deg,
                    #142a63,
                    #2457d6
                );
            color: white;
            box-shadow: var(--shadow);
        }}

        .hero h1 {{
            margin: 0 0 12px;
            font-size: 38px;
            letter-spacing: -0.8px;
        }}

        .hero p {{
            margin: 0;
            max-width: 800px;
            opacity: 0.88;
        }}

        section {{
            margin-top: 32px;
            padding: 30px;
            border-radius: 18px;
            background: var(--surface);
            box-shadow: var(--shadow);
        }}

        section h2 {{
            margin-top: 0;
            font-size: 25px;
        }}

        .section-description {{
            margin-top: -10px;
            color: var(--muted);
        }}

        .metric-grid {{
            display: grid;
            grid-template-columns:
                repeat(
                    auto-fit,
                    minmax(180px, 1fr)
                );
            gap: 16px;
        }}

        .metric-card {{
            padding: 18px;
            border: 1px solid var(--border);
            border-radius: 14px;
            background: var(--surface-alt);
        }}

        .metric-label {{
            color: var(--muted);
            font-size: 13px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.4px;
        }}

        .metric-value {{
            margin-top: 6px;
            font-size: 25px;
            font-weight: 750;
        }}

        .recommendation-grid {{
            display: grid;
            grid-template-columns:
                repeat(
                    auto-fit,
                    minmax(260px, 1fr)
                );
            gap: 18px;
        }}

        .recommendation-card {{
            padding: 22px;
            border: 1px solid var(--border);
            border-radius: 16px;
        }}

        .recommendation-header {{
            display: flex;
            justify-content: space-between;
            gap: 12px;
            align-items: center;
        }}

        .recommendation-header h3 {{
            margin: 0;
            font-size: 17px;
        }}

        .recommended-value {{
            margin-top: 18px;
            font-size: 32px;
            font-weight: 800;
            color: var(--accent);
        }}

        .recommendation-meta {{
            margin-top: 8px;
            color: var(--muted);
            font-size: 14px;
        }}

        .recommendation-reason {{
            margin-bottom: 0;
            font-size: 14px;
        }}

        .badge {{
            display: inline-block;
            padding: 5px 10px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 800;
        }}

        .badge-high {{
            color: var(--success);
            background: #dff5ea;
        }}

        .badge-medium {{
            color: var(--warning);
            background: #fff0c9;
        }}

        .badge-low {{
            color: var(--danger);
            background: #fde1e1;
        }}

        .badge-neutral {{
            color: var(--muted);
            background: var(--surface-alt);
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
            border-bottom: 1px solid var(--border);
            white-space: nowrap;
            text-align: right;
        }}

        .data-table th {{
            position: sticky;
            top: 0;
            color: #ffffff;
            background: #26364f;
            text-align: right;
        }}

        .data-table th:first-child,
        .data-table td:first-child {{
            text-align: left;
        }}

        .data-table tbody tr:hover {{
            background: #f3f6fb;
        }}

        .chart-grid {{
            display: grid;
            grid-template-columns:
                repeat(
                    auto-fit,
                    minmax(340px, 1fr)
                );
            gap: 20px;
        }}

        .chart-card {{
            padding: 18px;
            border: 1px solid var(--border);
            border-radius: 16px;
        }}

        .chart-card h3 {{
            margin-top: 0;
        }}

        .chart-card p {{
            color: var(--muted);
            font-size: 14px;
        }}

        .chart-card img {{
            width: 100%;
            height: auto;
            border-radius: 10px;
        }}

        .empty-state {{
            padding: 18px;
            color: var(--muted);
            background: var(--surface-alt);
            border-radius: 12px;
        }}

        footer {{
            margin-top: 28px;
            text-align: center;
            color: var(--muted);
            font-size: 13px;
        }}

        @media (max-width: 720px) {{
            .page {{
                padding: 18px 12px 50px;
            }}

            .hero {{
                padding: 28px 22px;
            }}

            .hero h1 {{
                font-size: 29px;
            }}

            section {{
                padding: 20px;
            }}
        }}
    </style>
</head>

<body>
    <main class="page">
        <header class="hero">
            <h1>{html.escape(report_title)}</h1>
            <p>
                Walk-forward optimization, parameter stability,
                robust-region analysis and automated research
                recommendations.
            </p>
        </header>

        <section>
            <h2>Executive Summary</h2>
            <p class="section-description">
                Tổng quan hiệu suất ngoài mẫu của Walk-Forward
                Optimization.
            </p>
            {executive_summary}
        </section>

        <section>
            <h2>Parameter Recommendations</h2>
            <p class="section-description">
                Khuyến nghị dựa trên frequency, stability và
                robust-region analysis.
            </p>
            {recommendations}
        </section>

        <section>
            <h2>Parameter Heatmaps</h2>
            <p class="section-description">
                Bề mặt hiệu suất theo ATR stop và ATR target.
            </p>
            <div class="chart-grid">
                {charts}
            </div>
        </section>

        <section>
            <h2>Heatmap Summary</h2>
            <div class="table-wrapper">
                {heatmap_summary}
            </div>
        </section>

        <section>
            <h2>Walk-Forward Folds</h2>
            <p class="section-description">
                Bộ tham số được chọn và kết quả ngoài mẫu của
                từng fold.
            </p>
            <div class="table-wrapper">
                {folds_table}
            </div>
        </section>

        <section>
            <h2>Parameter Frequency</h2>
            <div class="table-wrapper">
                {frequency_table}
            </div>
        </section>

        <section>
            <h2>Parameter Stability</h2>
            <div class="table-wrapper">
                {stability_table}
            </div>
        </section>

        <section>
            <h2>Robust Parameter Regions</h2>
            <div class="table-wrapper">
                {robust_table}
            </div>
        </section>

        <footer>
            Generated by Quant Stock Research Platform.
        </footer>
    </main>
</body>
</html>
"""

    return ResearchReportResult(
        html_content=document
    )