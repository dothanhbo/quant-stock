from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

BG = "rgba(0,0,0,0)"
GRID = "rgba(148,163,184,0.10)"
TEXT = "#9aa8bd"
PURPLE = "#7c5cff"
BLUE = "#2f80ed"
GREEN = "#2ed47a"
RED = "#ff5c6c"
ORANGE = "#ff9f43"


def _base_layout(height: int = 300) -> dict:
    return {
        "height": height,
        "margin": dict(l=12, r=12, t=20, b=8),
        "paper_bgcolor": BG,
        "plot_bgcolor": BG,
        "font": dict(color=TEXT, family="Inter, sans-serif"),
        "xaxis": dict(gridcolor=GRID, zeroline=False, showline=False),
        "yaxis": dict(gridcolor=GRID, zeroline=False, showline=False),
        "legend": dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        "hovermode": "x unified",
    }


def equity_curve(snapshots: pd.DataFrame, benchmark: pd.DataFrame | None = None) -> go.Figure:
    figure = go.Figure()
    if not snapshots.empty:
        x = snapshots["created_at"]
        y = snapshots["equity"]
        figure.add_trace(
            go.Scatter(
                x=x,
                y=y,
                name="Equity",
                mode="lines",
                line=dict(color=PURPLE, width=3),
                fill="tozeroy",
                fillcolor="rgba(124,92,255,0.08)",
            )
        )
    if benchmark is not None and not benchmark.empty:
        figure.add_trace(
            go.Scatter(
                x=benchmark["date"],
                y=benchmark["normalized"],
                name="VNINDEX",
                mode="lines",
                line=dict(color=BLUE, width=2, dash="dash"),
            )
        )
    layout = _base_layout(310)
    layout["yaxis"]["tickformat"] = ",.0f"
    figure.update_layout(**layout)
    return figure


def allocation(cash: float, positions_value: float) -> go.Figure:
    values = [max(cash, 0.0), max(positions_value, 0.0)]
    figure = go.Figure(
        go.Pie(
            labels=["Tiền mặt", "Cổ phiếu"],
            values=values,
            hole=0.72,
            marker=dict(colors=[BLUE, RED]),
            textinfo="none",
            hovertemplate="%{label}: %{percent}<extra></extra>",
        )
    )
    figure.update_layout(
        height=310,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor=BG,
        plot_bgcolor=BG,
        font=dict(color=TEXT),
        legend=dict(orientation="h", y=-0.05, x=0.1),
        annotations=[
            dict(
                text=f"{(positions_value / (cash + positions_value) * 100 if cash + positions_value else 0):.1f}%<br><span style='font-size:11px'>Đầu tư</span>",
                x=0.5,
                y=0.5,
                font=dict(size=22, color="#f8fafc"),
                showarrow=False,
            )
        ],
    )
    return figure


def drawdown_chart(snapshots: pd.DataFrame) -> go.Figure:
    figure = go.Figure()
    if not snapshots.empty:
        running_max = snapshots["equity"].cummax()
        drawdown = snapshots["equity"].div(running_max).sub(1.0).mul(100)
        figure.add_trace(
            go.Scatter(
                x=snapshots["created_at"],
                y=drawdown,
                mode="lines",
                line=dict(color=RED, width=2),
                fill="tozeroy",
                fillcolor="rgba(255,92,108,0.15)",
                name="Drawdown",
            )
        )
    layout = _base_layout(280)
    layout["yaxis"]["ticksuffix"] = "%"
    figure.update_layout(**layout)
    return figure


def monthly_returns(snapshots: pd.DataFrame) -> go.Figure:
    figure = go.Figure()
    if not snapshots.empty:
        data = snapshots.copy()
        data["month"] = data["created_at"].dt.to_period("M").astype(str)
        monthly = data.groupby("month", as_index=False)["equity"].last()
        monthly["return"] = monthly["equity"].pct_change().fillna(0).mul(100)
        colors = [GREEN if value >= 0 else RED for value in monthly["return"]]
        figure.add_trace(
            go.Bar(
                x=monthly["month"],
                y=monthly["return"],
                marker_color=colors,
                name="Monthly Return",
            )
        )
    layout = _base_layout(280)
    layout["yaxis"]["ticksuffix"] = "%"
    figure.update_layout(**layout)
    return figure
