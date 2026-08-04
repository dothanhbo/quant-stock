from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd


DEFAULT_ROW_PARAMETER = "atr_stop_multiplier"
DEFAULT_COLUMN_PARAMETER = "atr_target_multiplier"


@dataclass(slots=True, frozen=True)
class HeatmapMetricSpec:
    name: str
    source_column: str
    aggregation: str
    title: str
    value_format: str = ".2f"


@dataclass(slots=True, frozen=True)
class ParameterHeatmapResult:
    sharpe: pd.DataFrame
    total_return: pd.DataFrame
    drawdown: pd.DataFrame
    observation_count: pd.DataFrame
    summary: pd.DataFrame

    def save_csv(
        self,
        *,
        output_dir: str | Path,
    ) -> None:
        output_path = Path(output_dir)

        output_path.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.sharpe.to_csv(
            output_path / "heatmap_sharpe.csv",
            encoding="utf-8-sig",
        )

        self.total_return.to_csv(
            output_path / "heatmap_return.csv",
            encoding="utf-8-sig",
        )

        self.drawdown.to_csv(
            output_path / "heatmap_drawdown.csv",
            encoding="utf-8-sig",
        )

        self.observation_count.to_csv(
            output_path / "heatmap_observations.csv",
            encoding="utf-8-sig",
        )

        self.summary.to_csv(
            output_path / "heatmap_summary.csv",
            index=False,
            encoding="utf-8-sig",
        )


HEATMAP_METRICS = (
    HeatmapMetricSpec(
        name="sharpe",
        source_column="train_sharpe_ratio",
        aggregation="mean",
        title=(
            "Mean Train Sharpe Ratio\n"
            "ATR Stop × ATR Target"
        ),
    ),
    HeatmapMetricSpec(
        name="return",
        source_column="train_total_return_pct",
        aggregation="mean",
        title=(
            "Mean Train Return (%)\n"
            "ATR Stop × ATR Target"
        ),
    ),
    HeatmapMetricSpec(
        name="drawdown",
        source_column="train_max_drawdown_pct",
        aggregation="mean",
        title=(
            "Mean Train Drawdown (%)\n"
            "ATR Stop × ATR Target"
        ),
    ),
)


def _validate_train_search(
    train_search: pd.DataFrame,
    *,
    row_parameter: str,
    column_parameter: str,
) -> None:
    required_columns = {
        "fold",
        row_parameter,
        column_parameter,
        "train_sharpe_ratio",
        "train_total_return_pct",
        "train_max_drawdown_pct",
    }

    missing_columns = (
        required_columns
        - set(train_search.columns)
    )

    if missing_columns:
        raise ValueError(
            "train_search thiếu cột: "
            + ", ".join(
                sorted(missing_columns)
            )
        )

    if train_search.empty:
        raise ValueError(
            "train_search không có dữ liệu."
        )

    if row_parameter == column_parameter:
        raise ValueError(
            "row_parameter và column_parameter "
            "không được giống nhau."
        )


def _prepare_train_search(
    train_search: pd.DataFrame,
    *,
    row_parameter: str,
    column_parameter: str,
) -> pd.DataFrame:
    working = train_search.copy()

    numeric_columns = {
        "fold",
        row_parameter,
        column_parameter,
        "train_sharpe_ratio",
        "train_total_return_pct",
        "train_max_drawdown_pct",
    }

    for column in numeric_columns:
        working[column] = pd.to_numeric(
            working[column],
            errors="coerce",
        )

    working = working.dropna(
        subset=[
            "fold",
            row_parameter,
            column_parameter,
            "train_sharpe_ratio",
            "train_total_return_pct",
            "train_max_drawdown_pct",
        ]
    ).copy()

    if working.empty:
        raise ValueError(
            "Không có dữ liệu hợp lệ "
            "để tạo heatmap."
        )

    return working


def _build_heatmap(
    train_search: pd.DataFrame,
    *,
    row_parameter: str,
    column_parameter: str,
    value_column: str,
    aggregation: str = "mean",
) -> pd.DataFrame:
    table = pd.pivot_table(
        train_search,
        index=row_parameter,
        columns=column_parameter,
        values=value_column,
        aggfunc=aggregation,
    )

    table = table.sort_index(
        axis=0
    ).sort_index(
        axis=1
    )

    table.index.name = row_parameter
    table.columns.name = column_parameter

    return table


def _build_observation_count(
    train_search: pd.DataFrame,
    *,
    row_parameter: str,
    column_parameter: str,
) -> pd.DataFrame:
    table = pd.pivot_table(
        train_search,
        index=row_parameter,
        columns=column_parameter,
        values="fold",
        aggfunc="count",
        fill_value=0,
    )

    table = table.sort_index(
        axis=0
    ).sort_index(
        axis=1
    )

    table.index.name = row_parameter
    table.columns.name = column_parameter

    return table


def _find_best_cell(
    heatmap: pd.DataFrame,
    *,
    higher_is_better: bool,
) -> dict[str, Any]:
    stacked = (
        heatmap
        .stack(
            future_stack=True
        )
        .dropna()
    )

    if stacked.empty:
        return {
            "row_value": None,
            "column_value": None,
            "metric_value": None,
        }

    location = (
        stacked.idxmax()
        if higher_is_better
        else stacked.idxmin()
    )

    metric_value = float(
        stacked.loc[location]
    )

    return {
        "row_value": location[0],
        "column_value": location[1],
        "metric_value": metric_value,
    }


def _build_summary(
    *,
    sharpe: pd.DataFrame,
    total_return: pd.DataFrame,
    drawdown: pd.DataFrame,
    row_parameter: str,
    column_parameter: str,
) -> pd.DataFrame:
    specifications = [
        (
            "mean_sharpe_ratio",
            sharpe,
            True,
        ),
        (
            "mean_return_pct",
            total_return,
            True,
        ),
        (
            "mean_drawdown_pct",
            drawdown,
            True,
        ),
    ]

    rows: list[dict[str, Any]] = []

    for (
        metric_name,
        heatmap,
        higher_is_better,
    ) in specifications:
        best = _find_best_cell(
            heatmap,
            higher_is_better=(
                higher_is_better
            ),
        )

        rows.append(
            {
                "metric": metric_name,
                "row_parameter": (
                    row_parameter
                ),
                "column_parameter": (
                    column_parameter
                ),
                "best_row_value": (
                    best["row_value"]
                ),
                "best_column_value": (
                    best["column_value"]
                ),
                "best_metric_value": (
                    best["metric_value"]
                ),
            }
        )

    return pd.DataFrame(
        rows
    )


def analyze_parameter_heatmaps(
    train_search: pd.DataFrame,
    *,
    row_parameter: str = (
        DEFAULT_ROW_PARAMETER
    ),
    column_parameter: str = (
        DEFAULT_COLUMN_PARAMETER
    ),
) -> ParameterHeatmapResult:
    _validate_train_search(
        train_search,
        row_parameter=row_parameter,
        column_parameter=(
            column_parameter
        ),
    )

    prepared = _prepare_train_search(
        train_search,
        row_parameter=row_parameter,
        column_parameter=(
            column_parameter
        ),
    )

    sharpe = _build_heatmap(
        prepared,
        row_parameter=row_parameter,
        column_parameter=(
            column_parameter
        ),
        value_column=(
            "train_sharpe_ratio"
        ),
    )

    total_return = _build_heatmap(
        prepared,
        row_parameter=row_parameter,
        column_parameter=(
            column_parameter
        ),
        value_column=(
            "train_total_return_pct"
        ),
    )

    drawdown = _build_heatmap(
        prepared,
        row_parameter=row_parameter,
        column_parameter=(
            column_parameter
        ),
        value_column=(
            "train_max_drawdown_pct"
        ),
    )

    observation_count = (
        _build_observation_count(
            prepared,
            row_parameter=row_parameter,
            column_parameter=(
                column_parameter
            ),
        )
    )

    summary = _build_summary(
        sharpe=sharpe,
        total_return=total_return,
        drawdown=drawdown,
        row_parameter=row_parameter,
        column_parameter=(
            column_parameter
        ),
    )

    return ParameterHeatmapResult(
        sharpe=sharpe,
        total_return=total_return,
        drawdown=drawdown,
        observation_count=(
            observation_count
        ),
        summary=summary,
    )


def analyze_parameter_heatmaps_from_csv(
    train_search_path: str | Path,
    *,
    row_parameter: str = (
        DEFAULT_ROW_PARAMETER
    ),
    column_parameter: str = (
        DEFAULT_COLUMN_PARAMETER
    ),
) -> ParameterHeatmapResult:
    path = Path(
        train_search_path
    )

    if not path.exists():
        raise FileNotFoundError(
            f"Không tìm thấy file: "
            f"{path.resolve()}"
        )

    train_search = pd.read_csv(
        path
    )

    return analyze_parameter_heatmaps(
        train_search,
        row_parameter=row_parameter,
        column_parameter=(
            column_parameter
        ),
    )


def _format_axis_value(
    value: Any,
) -> str:
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return str(value)

    if numeric_value.is_integer():
        return str(
            int(numeric_value)
        )

    return f"{numeric_value:g}"


def save_heatmap_png(
    heatmap: pd.DataFrame,
    *,
    output_path: str | Path,
    title: str,
    row_label: str,
    column_label: str,
    value_format: str = ".2f",
) -> None:
    if heatmap.empty:
        raise ValueError(
            "Heatmap không có dữ liệu."
        )

    output = Path(
        output_path
    )

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    figure_width = max(
        7.0,
        len(heatmap.columns) * 1.8,
    )

    figure_height = max(
        5.0,
        len(heatmap.index) * 1.3,
    )

    figure, axis = plt.subplots(
        figsize=(
            figure_width,
            figure_height,
        )
    )

    image = axis.imshow(
        heatmap.to_numpy(
            dtype=float
        ),
        aspect="auto",
    )

    axis.set_title(
        title,
        pad=18,
    )

    axis.set_xlabel(
        column_label
    )

    axis.set_ylabel(
        row_label
    )

    axis.set_xticks(
        range(
            len(heatmap.columns)
        )
    )

    axis.set_xticklabels(
        [
            _format_axis_value(value)
            for value in heatmap.columns
        ]
    )

    axis.set_yticks(
        range(
            len(heatmap.index)
        )
    )

    axis.set_yticklabels(
        [
            _format_axis_value(value)
            for value in heatmap.index
        ]
    )

    for row_index in range(
        len(heatmap.index)
    ):
        for column_index in range(
            len(heatmap.columns)
        ):
            value = heatmap.iloc[
                row_index,
                column_index,
            ]

            if pd.isna(value):
                label = "N/A"
            else:
                label = format(
                    float(value),
                    value_format,
                )

            axis.text(
                column_index,
                row_index,
                label,
                ha="center",
                va="center",
            )

    figure.colorbar(
        image,
        ax=axis,
        shrink=0.85,
    )

    figure.tight_layout()

    figure.savefig(
        output,
        dpi=180,
        bbox_inches="tight",
    )

    plt.close(
        figure
    )


def save_parameter_heatmap_images(
    result: ParameterHeatmapResult,
    *,
    output_dir: str | Path,
    row_label: str = "ATR Stop Multiplier",
    column_label: str = (
        "ATR Target Multiplier"
    ),
) -> None:
    output_path = Path(
        output_dir
    )

    output_path.mkdir(
        parents=True,
        exist_ok=True,
    )

    save_heatmap_png(
        result.sharpe,
        output_path=(
            output_path
            / "heatmap_sharpe.png"
        ),
        title=(
            "Mean Train Sharpe Ratio\n"
            "ATR Stop × ATR Target"
        ),
        row_label=row_label,
        column_label=column_label,
    )

    save_heatmap_png(
        result.total_return,
        output_path=(
            output_path
            / "heatmap_return.png"
        ),
        title=(
            "Mean Train Return (%)\n"
            "ATR Stop × ATR Target"
        ),
        row_label=row_label,
        column_label=column_label,
    )

    save_heatmap_png(
        result.drawdown,
        output_path=(
            output_path
            / "heatmap_drawdown.png"
        ),
        title=(
            "Mean Train Drawdown (%)\n"
            "ATR Stop × ATR Target"
        ),
        row_label=row_label,
        column_label=column_label,
    )


def print_parameter_heatmap_report(
    result: ParameterHeatmapResult,
) -> None:
    print()
    print("=" * 100)
    print("PARAMETER HEATMAP ANALYSIS")
    print("=" * 100)

    print()
    print("MEAN SHARPE HEATMAP")
    print("-" * 100)
    print(
        result.sharpe.to_string(
            float_format=lambda value: (
                f"{value:.2f}"
            ),
        )
    )

    print()
    print("MEAN RETURN HEATMAP")
    print("-" * 100)
    print(
        result.total_return.to_string(
            float_format=lambda value: (
                f"{value:.2f}"
            ),
        )
    )

    print()
    print("MEAN DRAWDOWN HEATMAP")
    print("-" * 100)
    print(
        result.drawdown.to_string(
            float_format=lambda value: (
                f"{value:.2f}"
            ),
        )
    )

    print()
    print("BEST CELLS")
    print("-" * 100)
    print(
        result.summary.to_string(
            index=False,
            float_format=lambda value: (
                f"{value:.2f}"
            ),
        )
    )

    print("=" * 100)