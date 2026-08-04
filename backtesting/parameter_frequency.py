from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


PARAMETER_COLUMNS = (
    "atr_stop_multiplier",
    "atr_target_multiplier",
    "max_holding_days",
    "min_adx",
)


@dataclass(slots=True, frozen=True)
class ParameterFrequencyResult:
    selected_parameters: pd.DataFrame
    parameter_frequency: pd.DataFrame
    combination_frequency: pd.DataFrame
    summary: dict[str, Any]

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

        self.selected_parameters.to_csv(
            output_path / "selected_parameters.csv",
            index=False,
            encoding="utf-8-sig",
        )

        self.parameter_frequency.to_csv(
            output_path / "parameter_frequency.csv",
            index=False,
            encoding="utf-8-sig",
        )

        self.combination_frequency.to_csv(
            output_path / "combination_frequency.csv",
            index=False,
            encoding="utf-8-sig",
        )

        pd.DataFrame(
            [self.summary]
        ).to_csv(
            output_path / "parameter_frequency_summary.csv",
            index=False,
            encoding="utf-8-sig",
        )


def _validate_train_search(
    train_search: pd.DataFrame,
) -> None:
    required_columns = {
        "fold",
        "objective_score",
        "train_total_return_pct",
        "train_sharpe_ratio",
        "train_max_drawdown_pct",
        *PARAMETER_COLUMNS,
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


def _safe_numeric(
    series: pd.Series,
) -> pd.Series:
    return pd.to_numeric(
        series,
        errors="coerce",
    )


def _select_best_per_fold(
    train_search: pd.DataFrame,
) -> pd.DataFrame:
    """
    Dùng cùng nguyên tắc tie-break với Walk-Forward Optimizer:

    1. Objective score cao nhất.
    2. Train return cao nhất.
    3. Train Sharpe cao nhất.
    4. Drawdown tuyệt đối thấp nhất.
    5. Combination ID nhỏ nhất để kết quả deterministic.
    """
    working = train_search.copy()

    numeric_columns = [
        "fold",
        "objective_score",
        "train_total_return_pct",
        "train_sharpe_ratio",
        "train_max_drawdown_pct",
        *PARAMETER_COLUMNS,
    ]

    if "combination" in working.columns:
        numeric_columns.append(
            "combination"
        )
    else:
        working["combination"] = range(
            1,
            len(working) + 1,
        )
        numeric_columns.append(
            "combination"
        )

    for column in numeric_columns:
        working[column] = _safe_numeric(
            working[column]
        )

    working = working.dropna(
        subset=[
            "fold",
            "objective_score",
            *PARAMETER_COLUMNS,
        ]
    )

    working = working[
        working["objective_score"].map(
            math.isfinite
        )
    ].copy()

    if working.empty:
        raise ValueError(
            "Không có combination hợp lệ "
            "để chọn theo fold."
        )

    working[
        "_absolute_drawdown"
    ] = (
        working[
            "train_max_drawdown_pct"
        ]
        .abs()
    )

    working = working.sort_values(
        by=[
            "fold",
            "objective_score",
            "train_total_return_pct",
            "train_sharpe_ratio",
            "_absolute_drawdown",
            "combination",
        ],
        ascending=[
            True,
            False,
            False,
            False,
            True,
            True,
        ],
    )

    selected = (
        working
        .groupby(
            "fold",
            as_index=False,
            sort=True,
        )
        .first()
    )

    output_columns = [
        "fold",
        "combination",
        *PARAMETER_COLUMNS,
        "objective",
        "objective_score",
        "train_trades",
        "train_total_return_pct",
        "train_sharpe_ratio",
        "train_sortino_ratio",
        "train_max_drawdown_pct",
        "train_profit_factor",
        "train_win_rate_pct",
    ]

    output_columns = [
        column
        for column in output_columns
        if column in selected.columns
    ]

    return selected[
        output_columns
    ].reset_index(
        drop=True
    )


def _build_parameter_frequency(
    selected_parameters: pd.DataFrame,
) -> pd.DataFrame:
    total_folds = len(
        selected_parameters
    )

    rows: list[dict[str, Any]] = []

    for parameter in PARAMETER_COLUMNS:
        counts = (
            selected_parameters[
                parameter
            ]
            .value_counts(
                dropna=False
            )
            .sort_index()
        )

        for value, count in counts.items():
            rows.append(
                {
                    "parameter": parameter,
                    "value": value,
                    "selected_count": int(
                        count
                    ),
                    "total_folds": (
                        total_folds
                    ),
                    "selected_pct": float(
                        count
                        / total_folds
                        * 100
                    ),
                }
            )

    result = pd.DataFrame(
        rows
    )

    if result.empty:
        return result

    result["rank_within_parameter"] = (
        result
        .groupby(
            "parameter"
        )[
            "selected_count"
        ]
        .rank(
            method="dense",
            ascending=False,
        )
        .astype(int)
    )

    result["is_mode"] = (
        result[
            "rank_within_parameter"
        ]
        == 1
    )

    return result.sort_values(
        by=[
            "parameter",
            "rank_within_parameter",
            "value",
        ],
        ascending=[
            True,
            True,
            True,
        ],
    ).reset_index(
        drop=True
    )


def _build_combination_frequency(
    selected_parameters: pd.DataFrame,
) -> pd.DataFrame:
    total_folds = len(
        selected_parameters
    )

    grouped = (
        selected_parameters
        .groupby(
            list(PARAMETER_COLUMNS),
            dropna=False,
        )
        .agg(
            selected_count=(
                "fold",
                "count",
            ),
            folds=(
                "fold",
                lambda values: ",".join(
                    str(int(value))
                    for value in sorted(
                        values
                    )
                ),
            ),
            mean_objective_score=(
                "objective_score",
                "mean",
            ),
            mean_train_return_pct=(
                "train_total_return_pct",
                "mean",
            ),
            mean_train_sharpe_ratio=(
                "train_sharpe_ratio",
                "mean",
            ),
            mean_train_drawdown_pct=(
                "train_max_drawdown_pct",
                "mean",
            ),
        )
        .reset_index()
    )

    grouped["selected_pct"] = (
        grouped["selected_count"]
        / total_folds
        * 100
    )

    grouped["rank"] = (
        grouped[
            "selected_count"
        ]
        .rank(
            method="dense",
            ascending=False,
        )
        .astype(int)
    )

    return grouped.sort_values(
        by=[
            "selected_count",
            "mean_objective_score",
            "mean_train_sharpe_ratio",
            "mean_train_return_pct",
        ],
        ascending=[
            False,
            False,
            False,
            False,
        ],
    ).reset_index(
        drop=True
    )


def analyze_parameter_frequency(
    train_search: pd.DataFrame,
) -> ParameterFrequencyResult:
    _validate_train_search(
        train_search
    )

    selected_parameters = (
        _select_best_per_fold(
            train_search
        )
    )

    parameter_frequency = (
        _build_parameter_frequency(
            selected_parameters
        )
    )

    combination_frequency = (
        _build_combination_frequency(
            selected_parameters
        )
    )

    total_folds = len(
        selected_parameters
    )

    summary: dict[str, Any] = {
        "folds": total_folds,
        "selected_combinations": int(
            len(
                combination_frequency
            )
        ),
    }

    for parameter in PARAMETER_COLUMNS:
        parameter_rows = (
            parameter_frequency[
                parameter_frequency[
                    "parameter"
                ]
                == parameter
            ]
        )

        mode_rows = (
            parameter_rows[
                parameter_rows[
                    "is_mode"
                ]
            ]
        )

        if mode_rows.empty:
            continue

        summary[
            f"{parameter}_mode"
        ] = "|".join(
            str(value)
            for value in mode_rows[
                "value"
            ].tolist()
        )

        summary[
            f"{parameter}_mode_count"
        ] = int(
            mode_rows[
                "selected_count"
            ].max()
        )

        summary[
            f"{parameter}_mode_pct"
        ] = float(
            mode_rows[
                "selected_pct"
            ].max()
        )

    return ParameterFrequencyResult(
        selected_parameters=(
            selected_parameters
        ),
        parameter_frequency=(
            parameter_frequency
        ),
        combination_frequency=(
            combination_frequency
        ),
        summary=summary,
    )


def analyze_parameter_frequency_from_csv(
    train_search_path: str | Path,
) -> ParameterFrequencyResult:
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

    return analyze_parameter_frequency(
        train_search
    )


def print_parameter_frequency_report(
    result: ParameterFrequencyResult,
) -> None:
    print()
    print("=" * 90)
    print("PARAMETER FREQUENCY ANALYSIS")
    print("=" * 90)

    print(
        f"Folds analyzed      : "
        f"{result.summary['folds']}"
    )

    print(
        f"Unique combinations : "
        f"{result.summary['selected_combinations']}"
    )

    print()
    print("MOST FREQUENT VALUES")
    print("-" * 90)

    for parameter in PARAMETER_COLUMNS:
        mode_key = (
            f"{parameter}_mode"
        )

        count_key = (
            f"{parameter}_mode_count"
        )

        pct_key = (
            f"{parameter}_mode_pct"
        )

        if mode_key not in result.summary:
            continue

        print(
            f"{parameter:<25}: "
            f"{result.summary[mode_key]} "
            f"| "
            f"{result.summary[count_key]} folds "
            f"({result.summary[pct_key]:.2f}%)"
        )

    print()
    print("TOP PARAMETER COMBINATIONS")
    print("-" * 90)

    columns = [
        *PARAMETER_COLUMNS,
        "selected_count",
        "selected_pct",
        "folds",
    ]

    print(
        result.combination_frequency[
            columns
        ]
        .head(10)
        .to_string(
            index=False,
            float_format=lambda value: (
                f"{value:.2f}"
            ),
        )
    )

    print("=" * 90)