from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import altair as alt


def grouped_bar_chart(
    rows: Iterable[Mapping[str, Any]],
    *,
    category: str,
    series: str,
    value: str,
    y_title: str,
    value_format: str,
) -> alt.Chart:
    data = [dict(row) for row in rows]
    return (
        alt.Chart(alt.Data(values=data))
        .mark_bar()
        .encode(
            x=alt.X(f"{category}:N", title=category, sort=None),
            xOffset=alt.XOffset(f"{series}:N"),
            y=alt.Y(f"{value}:Q", title=y_title, scale=alt.Scale(zero=True)),
            color=alt.Color(f"{series}:N", title=series),
            tooltip=(
                alt.Tooltip(f"{category}:N", title=category),
                alt.Tooltip(f"{series}:N", title=series),
                alt.Tooltip(f"{value}:Q", title=y_title, format=value_format),
            ),
        )
        .properties(height=245)
    )


def bar_chart(
    rows: Iterable[Mapping[str, Any]],
    *,
    category: str,
    value: str,
    y_title: str,
    value_format: str,
) -> alt.Chart:
    data = [dict(row) for row in rows]
    return (
        alt.Chart(alt.Data(values=data))
        .mark_bar(color="#287A72")
        .encode(
            x=alt.X(f"{category}:N", title=category, sort=None),
            y=alt.Y(f"{value}:Q", title=y_title, scale=alt.Scale(zero=True)),
            tooltip=(
                alt.Tooltip(f"{category}:N", title=category),
                alt.Tooltip(f"{value}:Q", title=y_title, format=value_format),
            ),
        )
        .properties(height=220)
    )


def line_chart(
    rows: Iterable[Mapping[str, Any]],
    *,
    category: str,
    series: str,
    value: str,
    y_title: str,
    value_format: str,
    zero: bool,
) -> alt.Chart:
    data = [dict(row) for row in rows]
    return (
        alt.Chart(alt.Data(values=data))
        .mark_line(point=True)
        .encode(
            x=alt.X(f"{category}:N", title=category, sort=None),
            y=alt.Y(f"{value}:Q", title=y_title, scale=alt.Scale(zero=zero)),
            color=alt.Color(f"{series}:N", title="Series"),
            tooltip=(
                alt.Tooltip(f"{category}:N", title=category),
                alt.Tooltip(f"{series}:N", title="Series"),
                alt.Tooltip(f"{value}:Q", title=y_title, format=value_format),
            ),
        )
        .properties(height=235)
    )


__all__ = ("bar_chart", "grouped_bar_chart", "line_chart")
