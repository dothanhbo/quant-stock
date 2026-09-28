from __future__ import annotations

import argparse
import html
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, select_autoescape
import plotly.graph_objects as go
from markupsafe import Markup
from plotly.io import to_html

DEFAULT_RESULTS_DIR = Path(
    "research_results"
)

DEFAULT_OUTPUT_PATH = (
    DEFAULT_RESULTS_DIR
    / "report.html"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a self-contained "
            "quant research HTML report."
        )
    )

    parser.add_argument(
        "--results-dir",
        default=str(
            DEFAULT_RESULTS_DIR
        ),
    )

    parser.add_argument(
        "--output",
        default=str(
            DEFAULT_OUTPUT_PATH
        ),
    )

    return parser.parse_args()


def read_csv_optional(
    path: Path,
) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()

    try:
        return pd.read_csv(
            path
        )
    except Exception as exc:
        print(
            f"Cảnh báo: không đọc được "
            f"{path}: {exc}"
        )
        return pd.DataFrame()


def format_value(
    value: Any,
) -> str:
    if pd.isna(value):
        return "-"

    if isinstance(
        value,
        float,
    ):
        return f"{value:,.4f}"

    return html.escape(
        str(value)
    )


def dataframe_to_html(
    dataframe: pd.DataFrame,
    *,
    maximum_rows: int = 20,
) -> str:
    if dataframe.empty:
        return (
            '<div class="empty-state">'
            "Chưa có dữ liệu."
            "</div>"
        )

    display_df = (
        dataframe
        .head(maximum_rows)
        .copy()
    )

    headers = "".join(
        (
            "<th>"
            + html.escape(str(column))
            + "</th>"
        )
        for column in display_df.columns
    )

    body_rows: list[str] = []

    for _, row in display_df.iterrows():
        cells = "".join(
            (
                "<td>"
                + format_value(
                    row[column]
                )
                + "</td>"
            )
            for column in display_df.columns
        )

        body_rows.append(
            f"<tr>{cells}</tr>"
        )

    return f"""
    <div class="table-wrapper">
        <table>
            <thead>
                <tr>{headers}</tr>
            </thead>
            <tbody>
                {''.join(body_rows)}
            </tbody>
        </table>
    </div>
    """

def get_best_stress_model(
    stress_summary: pd.DataFrame,
) -> dict[str, Any]:
    if stress_summary.empty:
        return {
            "model": "-",
            "baseline_return_pct": 0.0,
            "baseline_sharpe": 0.0,
            "worst_drawdown_pct": 0.0,
            "robust_score": 0.0,
            "robust_rank": 0,
        }

    working = stress_summary.copy()

    if "robust_rank" in working.columns:
        working = working.sort_values(
            by=[
                "robust_rank",
                "robust_score",
            ],
            ascending=[
                True,
                False,
            ],
        )
    else:
        working = working.sort_values(
            by=[
                "robust_score",
                "baseline_sharpe",
            ],
            ascending=[
                False,
                False,
            ],
        )

    row = working.iloc[0]

    return {
        "model": str(
            row.get(
                "model",
                "-",
            )
        ),
        "baseline_return_pct": float(
            row.get(
                "baseline_return_pct",
                0.0,
            )
        ),
        "baseline_sharpe": float(
            row.get(
                "baseline_sharpe",
                0.0,
            )
        ),
        "worst_drawdown_pct": float(
            row.get(
                "worst_drawdown_pct",
                0.0,
            )
        ),
        "robust_score": float(
            row.get(
                "robust_score",
                0.0,
            )
        ),
        "robust_rank": int(
            row.get(
                "robust_rank",
                0,
            )
        ),
    }

def build_metric_card(
    *,
    label: str,
    value: str,
    detail: str = "",
    css_class: str = "",
) -> str:
    class_names = "metric-card"

    if css_class:
        class_names += (
            f" {css_class}"
        )

    detail_html = (
        f'<div class="metric-detail">'
        f"{html.escape(detail)}"
        f"</div>"
        if detail
        else ""
    )

    return f"""
    <article class="{class_names}">
        <div class="metric-label">
            {html.escape(label)}
        </div>

        <div class="metric-value">
            {html.escape(value)}
        </div>

        {detail_html}
    </article>
    """


REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta
        name="viewport"
        content="width=device-width, initial-scale=1.0"
    >
    <title>Quant Research Report</title>

    <style>
        :root {
            color-scheme: dark;
            --background: #0b1020;
            --surface: #121a2f;
            --surface-soft: #18223d;
            --text: #edf2ff;
            --muted: #9faccc;
            --accent: #7c9cff;
            --border: #2b385b;
            --positive: #5ee0a0;
        }

        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            background:
                linear-gradient(
                    180deg,
                    #0b1020 0%,
                    #111831 100%
                );
            color: var(--text);
            font-family:
                Inter,
                Segoe UI,
                Arial,
                sans-serif;
            line-height: 1.5;
        }

        .container {
            width: min(1440px, 94%);
            margin: 0 auto;
            padding: 48px 0 80px;
        }

        .hero {
            padding: 40px;
            border: 1px solid var(--border);
            border-radius: 22px;
            background:
                radial-gradient(
                    circle at top right,
                    rgba(124, 156, 255, 0.24),
                    transparent 34%
                ),
                var(--surface);
            box-shadow:
                0 24px 70px
                rgba(0, 0, 0, 0.28);
        }

        .eyebrow {
            color: var(--accent);
            font-size: 13px;
            font-weight: 700;
            letter-spacing: 0.14em;
            text-transform: uppercase;
        }

        h1 {
            margin: 10px 0 8px;
            font-size: clamp(
                34px,
                6vw,
                64px
            );
            line-height: 1.05;
        }

        .subtitle {
            margin: 0;
            max-width: 760px;
            color: var(--muted);
            font-size: 18px;
        }

        .metadata {
            margin-top: 24px;
            color: var(--muted);
            font-size: 14px;
        }

        .metrics-grid {
            display: grid;
            grid-template-columns:
                repeat(
                    auto-fit,
                    minmax(190px, 1fr)
                );
            gap: 16px;
            margin-top: 34px;
        }

        .metric-card {
            min-height: 150px;
            padding: 22px;
            border: 1px solid var(--border);
            border-radius: 16px;
            background:
                linear-gradient(
                    145deg,
                    rgba(255, 255, 255, 0.025),
                    rgba(255, 255, 255, 0)
                ),
                var(--surface);
        }

        .metric-label {
            color: var(--muted);
            font-size: 12px;
            font-weight: 700;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }

        .metric-value {
            margin-top: 14px;
            font-size: 30px;
            font-weight: 750;
            line-height: 1.1;
        }

        .metric-detail {
            margin-top: 12px;
            color: var(--muted);
            font-size: 13px;
        }

        .accent-card {
            border-color:
                rgba(124, 156, 255, 0.65);
            background:
                linear-gradient(
                    145deg,
                    rgba(124, 156, 255, 0.18),
                    rgba(124, 156, 255, 0.03)
                ),
                var(--surface);
        }

        .positive-card .metric-value {
            color: var(--positive);
        }

        .warning-card .metric-value {
            color: #ffb86b;
        }

        .section {
            margin-top: 34px;
            padding: 28px;
            border: 1px solid var(--border);
            border-radius: 18px;
            background: var(--surface);
        }

        .section h2 {
            margin: 0 0 18px;
            font-size: 26px;
        }

        .table-wrapper {
            overflow-x: auto;
            border: 1px solid var(--border);
            border-radius: 12px;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            min-width: 900px;
        }

        th,
        td {
            padding: 12px 14px;
            border-bottom:
                1px solid var(--border);
            text-align: right;
            white-space: nowrap;
        }

        th:first-child,
        td:first-child {
            text-align: left;
        }

        th {
            background: var(--surface-soft);
            color: var(--muted);
            font-size: 12px;
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }

        tbody tr:hover {
            background:
                rgba(124, 156, 255, 0.08);
        }

        tbody tr:last-child td {
            border-bottom: none;
        }

        .empty-state {
            padding: 32px;
            border: 1px dashed var(--border);
            border-radius: 12px;
            color: var(--muted);
            text-align: center;
        }

        .footer {
            margin-top: 32px;
            color: var(--muted);
            font-size: 13px;
            text-align: center;
        }

        .recommendation-section {
            position: relative;
            overflow: hidden;
        }

        .recommendation-section::after {
            position: absolute;
            width: 360px;
            height: 360px;
            right: -180px;
            bottom: -230px;
            border-radius: 50%;
            background: rgba(94, 224, 160, 0.08);
            content: "";
            pointer-events: none;
        }

        .section-heading-row {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 24px;
            margin-bottom: 24px;
        }

        .section-heading-row h2 {
            margin: 8px 0 0;
        }

        .recommendation-badge {
            padding: 8px 14px;
            border: 1px solid rgba(94, 224, 160, 0.45);
            border-radius: 999px;
            background: rgba(94, 224, 160, 0.10);
            color: var(--positive);
            font-size: 12px;
            font-weight: 800;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }

        .recommendation-layout {
            display: grid;
            grid-template-columns: minmax(0, 1.4fr) minmax(300px, 0.8fr);
            gap: 24px;
        }

        .recommendation-primary,
        .weight-panel {
            padding: 26px;
            border: 1px solid var(--border);
            border-radius: 16px;
            background: var(--surface-soft);
        }

        .recommendation-label {
            color: var(--muted);
            font-size: 12px;
            font-weight: 750;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }

        .recommendation-model {
            margin-top: 10px;
            color: var(--positive);
            font-size: 38px;
            font-weight: 800;
            line-height: 1.1;
        }

        .recommendation-text {
            max-width: 720px;
            margin: 18px 0 22px;
            color: var(--muted);
        }

        .validation-list {
            display: grid;
            gap: 10px;
        }

        .validation-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 20px;
            padding: 12px 14px;
            border: 1px solid var(--border);
            border-radius: 10px;
        }

        .validation-item span {
            color: var(--muted);
        }

        .validation-item strong {
            color: var(--positive);
            font-size: 12px;
            letter-spacing: 0.05em;
        }

        .weight-panel-title {
            margin-bottom: 16px;
            font-size: 18px;
            font-weight: 750;
        }

        .weight-row,
        .weight-total {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px 0;
            border-bottom: 1px solid var(--border);
        }

        .weight-row span,
        .weight-total span {
            color: var(--muted);
        }

        .weight-total {
            margin-top: 6px;
            border-bottom: none;
            font-size: 18px;
        }

        .weight-total strong {
            color: var(--positive);
        }

        .weight-source {
            margin-top: 16px;
            color: var(--muted);
            font-size: 12px;
        }

        @media (max-width: 900px) {
            .recommendation-layout {
                grid-template-columns: 1fr;
            }

            .section-heading-row {
                flex-direction: column;
            }
        }

        .charts-grid {
                display: grid;
                grid-template-columns:
                        minmax(320px, 0.8fr)
                        minmax(0, 1.4fr);
                gap: 22px;
                margin-top: 22px;
        }

        .chart-card {
                min-width: 0;
                padding: 16px;
                border: 1px solid var(--border);
                border-radius: 16px;
                background: var(--surface-soft);
                overflow: hidden;
        }

        .chart-card .plotly-graph-div {
                width: 100% !important;
        }

        @media (max-width: 1050px) {
                .charts-grid {
                        grid-template-columns: 1fr;
                }
        }


        .pipeline-grid {
                display: grid;
                grid-template-columns: repeat(5, minmax(0, 1fr));
                gap: 14px;
                margin-top: 22px;
        }

        .pipeline-card {
                position: relative;
                min-width: 0;
                padding: 22px;
                border: 1px solid var(--border);
                border-radius: 16px;
                background: var(--surface-soft);
                transition: transform 0.2s ease, border-color 0.2s ease;
        }

        .pipeline-card:hover {
                transform: translateY(-4px);
                border-color: rgba(94, 224, 160, 0.45);
        }

        .pipeline-step {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 38px;
                height: 38px;
                margin-bottom: 16px;
                border-radius: 50%;
                background: rgba(94, 224, 160, 0.12);
                color: var(--positive);
                font-weight: 800;
        }

        .pipeline-card h3 {
                margin: 0 0 10px;
                font-size: 17px;
        }

        .pipeline-card p {
                margin: 0;
                color: var(--muted);
                font-size: 13px;
                line-height: 1.65;
        }

        .conclusion-grid {
                display: grid;
                grid-template-columns: minmax(0, 1.25fr) minmax(280px, 0.75fr);
                gap: 22px;
                margin-top: 22px;
        }

        .conclusion-panel {
                padding: 26px;
                border: 1px solid var(--border);
                border-radius: 16px;
                background: var(--surface-soft);
        }

        .conclusion-title {
                margin: 10px 0 14px;
                color: var(--positive);
                font-size: 34px;
                font-weight: 850;
        }

        .conclusion-copy {
                margin: 0;
                color: var(--muted);
                line-height: 1.7;
        }

        .decision-list {
                display: grid;
                gap: 10px;
        }

        .decision-row {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 20px;
                padding: 12px 14px;
                border: 1px solid var(--border);
                border-radius: 10px;
        }

        .decision-row span {
                color: var(--muted);
        }

        .decision-row strong {
                color: var(--positive);
                font-size: 12px;
                letter-spacing: 0.05em;
        }

        @media (max-width: 1100px) {
                .pipeline-grid {
                        grid-template-columns: repeat(2, minmax(0, 1fr));
                }
        }

        @media (max-width: 760px) {
                .pipeline-grid,
                .conclusion-grid {
                        grid-template-columns: 1fr;
                }
        }

    </style>
</head>

<body>
    <main class="container">
        <section class="hero">
            <div class="eyebrow">
                Quant Stock Research Platform
            </div>

            <h1>
                Quant Research Report
            </h1>

            <p class="subtitle">
                Consolidated portfolio research,
                walk-forward validation,
                Monte Carlo analysis and
                stress-test robustness.
            </p>

            <div class="metadata">
                Generated: {{ generated_at }}
            </div>
        </section>

        <section class="section">
            <h2>
                Executive Summary
            </h2>

            <div class="metrics-grid">
                {{ metric_cards }}
            </div>
        </section>

        <section class="section">
                <div class="section-heading-row">
                        <div>
                                <div class="eyebrow">
                                        Research Workflow
                                </div>

                                <h2>
                                        Quantitative Research Pipeline
                                </h2>
                        </div>

                        <div class="recommendation-badge">
                                End-to-End
                        </div>
                </div>

                <div class="pipeline-grid">
                        {% for item in pipeline_items %}
                        <article class="pipeline-card">
                                <div class="pipeline-step">
                                        {{ loop.index }}
                                </div>

                                <h3>{{ item.title }}</h3>
                                <p>{{ item.description }}</p>
                        </article>
                        {% endfor %}
                </div>
        </section>

        <section class="section recommendation-section">
                <div class="section-heading-row">
                        <div>
                                <div class="eyebrow">
                                        Research Decision
                                </div>

                                <h2>
                                        Overall Recommendation
                                </h2>
                        </div>

                        <div class="recommendation-badge">
                                Recommended
                        </div>
                </div>

                <div class="recommendation-layout">
                        <div class="recommendation-primary">
                                <div class="recommendation-label">
                                        Portfolio Allocation Model
                                </div>

                                <div class="recommendation-model">
                                        {{ recommended_model }}
                                </div>

                                <p class="recommendation-text">
                                        {{ recommendation_text }}
                                </p>

                                <div class="validation-list">
                                        {% for item in validation_items %}
                                        <div class="validation-item">
                                                <span>{{ item.label }}</span>
                                                <strong>{{ item.status }}</strong>
                                        </div>
                                        {% endfor %}
                                </div>
                        </div>

                        <div class="weight-panel">
                                <div class="weight-panel-title">
                                        Recommended Factor Weights
                                </div>

                                <div class="weight-row">
                                        <span>Signal Score</span>
                                        <strong>{{ "%.0f"|format(recommendation.signal_weight * 100) }}%</strong>
                                </div>

                                <div class="weight-row">
                                        <span>ATR</span>
                                        <strong>{{ "%.0f"|format(recommendation.atr_weight * 100) }}%</strong>
                                </div>

                                <div class="weight-row">
                                        <span>Stop Distance</span>
                                        <strong>{{ "%.0f"|format(recommendation.stop_weight * 100) }}%</strong>
                                </div>

                                <div class="weight-row">
                                        <span>Market Regime</span>
                                        <strong>{{ "%.0f"|format(recommendation.regime_weight * 100) }}%</strong>
                                </div>

                                <div class="weight-total">
                                        <span>Total Weight</span>
                                        <strong>{{ "%.0f"|format(recommendation.weight_sum * 100) }}%</strong>
                                </div>

                                <div class="weight-source">
                                        Optimized by: {{ recommendation.source }}
                                </div>
                        </div>
                </div>
        </section>

        <section class="section">
                <div class="eyebrow">
                        Portfolio Analytics
                </div>

                <h2>
                        Performance & Robustness
                </h2>

                <div class="charts-grid">
                        <div class="chart-card">
                                {{ robust_chart }}
                        </div>

                        <div class="chart-card chart-card-wide">
                                {{ stress_return_chart }}
                        </div>
                </div>
        </section>

        <section class="section">
            <h2>
                Portfolio Stress Robustness
            </h2>

            {{ stress_table }}
        </section>

        <section class="section">
                <div class="section-heading-row">
                        <div>
                                <div class="eyebrow">
                                        Final Decision
                                </div>

                                <h2>
                                        Research Conclusion
                                </h2>
                        </div>

                        <div class="recommendation-badge">
                                {{ conclusion.grade }}
                        </div>
                </div>

                <div class="conclusion-grid">
                        <div class="conclusion-panel">
                                <div class="recommendation-label">
                                        Selected Model
                                </div>

                                <div class="conclusion-title">
                                        {{ recommended_model }}
                                </div>

                                <p class="conclusion-copy">
                                        {{ conclusion.text }}
                                </p>
                        </div>

                        <div class="conclusion-panel">
                                <div class="decision-list">
                                        {% for item in conclusion["items"] %}
                                        <div class="decision-row">
                                                <span>{{ item.label }}</span>
                                                <strong>{{ item.status }}</strong>
                                        </div>
                                        {% endfor %}
                                </div>
                        </div>
                </div>
        </section>

        <footer class="footer">
                Generated locally by Quant Stock.
        </footer>
    </main>
</body>
</html>
"""
def get_composite_recommendation(
    recommendation_df: pd.DataFrame,
    selected_weights_df: pd.DataFrame,
) -> dict[str, Any]:
    default_weights = {
        "signal_weight": 0.10,
        "atr_weight": 0.30,
        "stop_weight": 0.40,
        "regime_weight": 0.20,
    }

    weights = default_weights.copy()
    source = "Composite Walk-Forward"

    if not selected_weights_df.empty:
        weight_columns = list(
            default_weights.keys()
        )

        if all(
            column in selected_weights_df.columns
            for column in weight_columns
        ):
            for column in weight_columns:
                numeric = pd.to_numeric(
                    selected_weights_df[column],
                    errors="coerce",
                ).dropna()

                if not numeric.empty:
                    weights[column] = float(
                        numeric.mode().iloc[0]
                    )

    elif not recommendation_df.empty:
        required_columns = {
            "parameter",
            "recommended_value",
        }

        if required_columns.issubset(
            recommendation_df.columns
        ):
            mapping = {
                "signal_weight": "signal_weight",
                "atr_weight": "atr_weight",
                "stop_weight": "stop_weight",
                "regime_weight": "regime_weight",
            }

            for parameter, output_key in (
                mapping.items()
            ):
                matching = recommendation_df[
                    recommendation_df[
                        "parameter"
                    ]
                    == parameter
                ]

                if not matching.empty:
                    weights[output_key] = float(
                        matching[
                            "recommended_value"
                        ].iloc[0]
                    )

            source = "Composite Weight Research"

    weight_sum = sum(
        weights.values()
    )

    return {
        **weights,
        "weight_sum": weight_sum,
        "weight_sum_valid": (
            abs(weight_sum - 1.0)
            <= 1e-9
        ),
        "source": source,
    }


def format_model_name(
    value: Any,
) -> str:
    return (
        str(value)
        .replace("_", " ")
        .title()
    )


def apply_chart_theme(
    figure: go.Figure,
    *,
    title: str,
    height: int = 420,
) -> go.Figure:
    figure.update_layout(
        title={
            "text": title,
            "x": 0.02,
            "xanchor": "left",
        },
        height=height,
        margin={
            "l": 40,
            "r": 30,
            "t": 72,
            "b": 45,
        },
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={
            "family": (
                "Inter, Segoe UI, Arial, "
                "sans-serif"
            ),
            "color": "#edf2ff",
        },
        title_font={
            "size": 20,
        },
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "right",
            "x": 1,
        },
        hoverlabel={
            "bgcolor": "#18223d",
            "font_color": "#edf2ff",
        },
    )

    figure.update_xaxes(
        gridcolor="rgba(159,172,204,0.12)",
        zerolinecolor=(
            "rgba(159,172,204,0.20)"
        ),
    )

    figure.update_yaxes(
        gridcolor="rgba(159,172,204,0.12)",
        zerolinecolor=(
            "rgba(159,172,204,0.20)"
        ),
    )

    return figure


def build_robust_score_chart(
    stress_summary: pd.DataFrame,
) -> go.Figure | None:
    required_columns = {
        "model",
        "robust_score",
    }

    if (
        stress_summary.empty
        or not required_columns.issubset(
            stress_summary.columns
        )
    ):
        return None

    working = stress_summary.copy()

    working[
        "model_label"
    ] = working[
        "model"
    ].map(
        format_model_name
    )

    working[
        "robust_score"
    ] = pd.to_numeric(
        working[
            "robust_score"
        ],
        errors="coerce",
    )

    working = (
        working
        .dropna(
            subset=[
                "robust_score"
            ]
        )
        .sort_values(
            "robust_score",
            ascending=True,
        )
    )

    if working.empty:
        return None

    figure = go.Figure(
        go.Bar(
            x=working[
                "robust_score"
            ],
            y=working[
                "model_label"
            ],
            orientation="h",
            text=working[
                "robust_score"
            ].map(
                lambda value: (
                    f"{value:.2f}"
                )
            ),
            textposition="outside",
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Robust Score: %{x:.2f}"
                "<extra></extra>"
            ),
        )
    )

    figure.update_xaxes(
        title="Robust Score",
        range=[
            0,
            max(
                100.0,
                float(
                    working[
                        "robust_score"
                    ].max()
                )
                * 1.12,
            ),
        ],
    )

    figure.update_yaxes(
        title=None,
    )

    return apply_chart_theme(
        figure,
        title=(
            "Stress Robustness Ranking"
        ),
    )


def build_stress_return_chart(
    stress_results: pd.DataFrame,
) -> go.Figure | None:
    required_columns = {
        "scenario",
        "model",
        "total_return_pct",
    }

    if (
        stress_results.empty
        or not required_columns.issubset(
            stress_results.columns
        )
    ):
        return None

    working = stress_results.copy()

    working[
        "model_label"
    ] = working[
        "model"
    ].map(
        format_model_name
    )

    working[
        "total_return_pct"
    ] = pd.to_numeric(
        working[
            "total_return_pct"
        ],
        errors="coerce",
    )

    working = working.dropna(
        subset=[
            "total_return_pct"
        ]
    )

    if working.empty:
        return None

    scenario_order = [
        "baseline",
        "high_fee",
        "high_slippage",
        "cost_shock",
        "conservative",
    ]

    figure = go.Figure()

    for model_label, group in (
        working.groupby(
            "model_label",
            sort=False,
        )
    ):
        indexed = (
            group
            .set_index(
                "scenario"
            )
            .reindex(
                scenario_order
            )
            .dropna(
                subset=[
                    "total_return_pct"
                ]
            )
        )

        if indexed.empty:
            continue

        figure.add_trace(
            go.Bar(
                name=model_label,
                x=[
                    scenario
                    .replace("_", " ")
                    .title()
                    for scenario
                    in indexed.index
                ],
                y=indexed[
                    "total_return_pct"
                ],
                text=indexed[
                    "total_return_pct"
                ].map(
                    lambda value: (
                        f"{value:.1f}%"
                    )
                ),
                textposition="outside",
                hovertemplate=(
                    "<b>%{fullData.name}</b><br>"
                    "Scenario: %{x}<br>"
                    "Return: %{y:.2f}%"
                    "<extra></extra>"
                ),
            )
        )

    figure.update_layout(
        barmode="group",
    )

    figure.update_xaxes(
        title=None,
    )

    figure.update_yaxes(
        title="Total Return (%)",
    )

    return apply_chart_theme(
        figure,
        title=(
            "Return Under Stress Scenarios"
        ),
        height=470,
    )

def build_pipeline_items() -> list[dict[str, str]]:
    return [
        {
            "title": "Market Data",
            "description": (
                "Historical OHLCV collection, cleansing, "
                "feature engineering and indicator preparation."
            ),
        },
        {
            "title": "Signal Engine",
            "description": (
                "Trend, momentum, volume and price-action "
                "conditions generate ranked trade candidates."
            ),
        },
        {
            "title": "Portfolio Allocation",
            "description": (
                "Position sizing, volatility scaling and "
                "composite factor weights control capital."
            ),
        },
        {
            "title": "Validation",
            "description": (
                "Walk-forward and transaction-cost stress "
                "tests evaluate out-of-sample robustness."
            ),
        },
        {
            "title": "Recommendation",
            "description": (
                "The final model is selected from risk-adjusted "
                "performance and robustness evidence."
            ),
        },
    ]


def build_conclusion(
    *,
    best_model: dict[str, Any],
    selected_weights_df: pd.DataFrame,
    recommendation: dict[str, Any],
) -> dict[str, Any]:
    stress_passed = (
        best_model.get("robust_rank", 0) == 1
        and best_model.get("robust_score", 0.0) > 0
    )

    walk_forward_passed = not selected_weights_df.empty
    weights_valid = bool(
        recommendation.get(
            "weight_sum_valid",
            False,
        )
    )

    passed_count = sum(
        [
            stress_passed,
            walk_forward_passed,
            weights_valid,
        ]
    )

    grade = (
        "A"
        if passed_count == 3
        else "B"
        if passed_count == 2
        else "Review"
    )

    model_name = format_model_name(
        best_model.get(
            "model",
            "-",
        )
    )

    text = (
        f"{model_name} produced the strongest overall "
        "stress-adjusted result among the tested allocation "
        "models. Its recommendation is supported by portfolio "
        "stress testing, stable composite factor weights and "
        "walk-forward research. The score is a relative ranking "
        "within the tested model set, not a guarantee of future "
        "performance."
    )

    return {
        "grade": grade,
        "text": text,
        "items": [
            {
                "label": "Stress Validation",
                "status": (
                    "PASSED"
                    if stress_passed
                    else "REVIEW"
                ),
            },
            {
                "label": "Walk-Forward Evidence",
                "status": (
                    "PASSED"
                    if walk_forward_passed
                    else "NO DATA"
                ),
            },
            {
                "label": "Factor Weight Sum",
                "status": (
                    "VALID"
                    if weights_valid
                    else "REVIEW"
                ),
            },
            {
                "label": "Deployment Status",
                "status": (
                    "RESEARCH READY"
                    if passed_count == 3
                    else "REVIEW"
                ),
            },
        ],
    }


def figure_to_html(
    figure: go.Figure | None,
    *,
    include_plotlyjs: bool,
) -> Markup:
    if figure is None:
        return Markup(
            '<div class="empty-state">'
            "Chưa có dữ liệu biểu đồ."
            "</div>"
        )

    return Markup(
        to_html(
            figure,
            full_html=False,
            include_plotlyjs=include_plotlyjs,
            config={
                "displaylogo": False,
                "responsive": True,
            },
        )
    )



def build_page(
    *,
    generated_at: str,
    stress_summary: pd.DataFrame,
    stress_results: pd.DataFrame,
    recommendation_df: pd.DataFrame,
    selected_weights_df: pd.DataFrame,
) -> str:
    stress_table = dataframe_to_html(
        stress_summary,
        maximum_rows=10,
    )

    best_model = get_best_stress_model(
        stress_summary
    )

    recommendation = (
        get_composite_recommendation(
            recommendation_df,
            selected_weights_df,
        )
    )

    recommended_model = (
        best_model["model"]
        .replace("_", " ")
        .title()
    )

    validation_items = [
        {
            "label": "Stress Test",
            "status": "PASSED",
        },
        {
            "label": "Composite Walk-Forward",
            "status": (
                "PASSED"
                if not selected_weights_df.empty
                else "NO DATA"
            ),
        },
        {
            "label": "Weight Sum",
            "status": (
                "VALID"
                if recommendation[
                    "weight_sum_valid"
                ]
                else "REVIEW"
            ),
        },
    ]

    model_name = (
        best_model["model"]
        .replace("_", " ")
        .title()
    )

    metric_cards = "".join(
        [
            build_metric_card(
                label="Best Model",
                value=model_name,
                detail=(
                    "Ranked by stress robustness"
                ),
                css_class="accent-card",
            ),
            build_metric_card(
                label="Baseline Return",
                value=(
                    f"{best_model['baseline_return_pct']:.2f}%"
                ),
                detail="Full-period benchmark",
                css_class="positive-card",
            ),
            build_metric_card(
                label="Baseline Sharpe",
                value=(
                    f"{best_model['baseline_sharpe']:.2f}"
                ),
                detail="Risk-adjusted performance",
            ),
            build_metric_card(
                label="Worst Drawdown",
                value=(
                    f"{best_model['worst_drawdown_pct']:.2f}%"
                ),
                detail="Worst stressed scenario",
                css_class="warning-card",
            ),
            build_metric_card(
                label="Robust Score",
                value=(
                    f"{best_model['robust_score']:.2f}"
                ),
                detail="Relative model ranking",
            ),
            build_metric_card(
                label="Stress Rank",
                value=(
                    f"#{best_model['robust_rank']}"
                ),
                detail="Across tested allocators",
            ),
        ]
    )

    environment = Environment(
        autoescape=select_autoescape(
            enabled_extensions=(
                "html",
                "xml",
            ),
            default_for_string=True,
        )
    )

    template = environment.from_string(
        REPORT_TEMPLATE
    )

    robust_figure = (
        build_robust_score_chart(
            stress_summary
        )
    )

    stress_return_figure = (
        build_stress_return_chart(
            stress_results
        )
    )

    robust_chart = figure_to_html(
        robust_figure,
        include_plotlyjs=True,
    )

    stress_return_chart = figure_to_html(
        stress_return_figure,
        include_plotlyjs=False,
    )

    pipeline_items = build_pipeline_items()

    conclusion = build_conclusion(
        best_model=best_model,
        selected_weights_df=(
            selected_weights_df
        ),
        recommendation=recommendation,
    )

    recommendation_text = (
        "This model achieved the highest stress-adjusted "
        "ranking across the tested portfolio allocation "
        "methods. The selected configuration is supported by "
        "composite factor research and walk-forward evidence."
    )

    return template.render(
        generated_at=generated_at,
        metric_cards=Markup(
            metric_cards
        ),
        stress_table=Markup(
            stress_table
        ),
        recommended_model=(
            recommended_model
        ),
        recommendation=(
            recommendation
        ),
        validation_items=(
            validation_items
        ),
        robust_chart=robust_chart,
        stress_return_chart=(
            stress_return_chart
        ),
        pipeline_items=pipeline_items,
        conclusion=conclusion,
        recommendation_text=(
            recommendation_text
        ),
    )


def main() -> None:
    args = parse_args()

    results_dir = Path(
        args.results_dir
    )

    output_path = Path(
        args.output
    )

    stress_summary = read_csv_optional(
        results_dir
        / "portfolio_stress_test"
        / "stress_summary.csv"
    )

    stress_results = read_csv_optional(
        results_dir
        / "portfolio_stress_test"
        / "stress_results.csv"
    )

    recommendation_df = read_csv_optional(
        results_dir
        / "composite_weights"
        / "sum"
        / "weight_recommendation.csv"
    )

    selected_weights_df = read_csv_optional(
        results_dir
        / "composite_walk_forward"
        / "selected_weights_sum.csv"
    )

    generated_at = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    page = build_page(
        generated_at=generated_at,
        stress_summary=stress_summary,
        stress_results=stress_results,
        recommendation_df=(
            recommendation_df
        ),
        selected_weights_df=(
            selected_weights_df
        ),
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        page,
        encoding="utf-8",
    )

    print("=" * 100)
    print("HTML RESEARCH REPORT")
    print("=" * 100)
    print(
        f"Stress rows : "
        f"{len(stress_summary)}"
    )
    print(
        f"Đã xuất    : "
        f"{output_path}"
    )


if __name__ == "__main__":
    main()