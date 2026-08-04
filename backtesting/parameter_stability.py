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
class ParameterStabilityResult:
    parameter_stability: pd.DataFrame
    stability_ranking: pd.DataFrame
    stability_summary: pd.DataFrame
    robust_parameters: pd.DataFrame

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

        self.parameter_stability.to_csv(
            output_path / "parameter_stability.csv",
            index=False,
            encoding="utf-8-sig",
        )

        self.stability_ranking.to_csv(
            output_path / "stability_ranking.csv",
            index=False,
            encoding="utf-8-sig",
        )

        self.stability_summary.to_csv(
            output_path / "parameter_stability_summary.csv",
            index=False,
            encoding="utf-8-sig",
        )

        self.robust_parameters.to_csv(
            output_path / "robust_parameters.csv",
            index=False,
            encoding="utf-8-sig",
        )


def _safe_float(
    value: Any,
    default: float = 0.0,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default

    if not math.isfinite(result):
        return default

    return result


def _validate_train_search(
    train_search: pd.DataFrame,
) -> None:
    required_columns = {
        "fold",
        "objective_score",
        "train_total_return_pct",
        "train_sharpe_ratio",
        "train_max_drawdown_pct",
        "train_profit_factor",
        "train_win_rate_pct",
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


def _prepare_train_search(
    train_search: pd.DataFrame,
) -> pd.DataFrame:
    working = train_search.copy()

    numeric_columns = [
        "fold",
        "objective_score",
        "train_total_return_pct",
        "train_sharpe_ratio",
        "train_max_drawdown_pct",
        "train_profit_factor",
        "train_win_rate_pct",
        *PARAMETER_COLUMNS,
    ]

    if "train_trades" in working.columns:
        numeric_columns.append(
            "train_trades"
        )

    for column in numeric_columns:
        working[column] = pd.to_numeric(
            working[column],
            errors="coerce",
        )

    working = working.dropna(
        subset=[
            "fold",
            "objective_score",
            *PARAMETER_COLUMNS,
        ]
    ).copy()

    working = working[
        working["objective_score"].map(
            math.isfinite
        )
    ].copy()

    if working.empty:
        raise ValueError(
            "Không có dữ liệu train hợp lệ."
        )

    return working


def _coefficient_of_variation(
    mean_value: float,
    std_value: float,
) -> float:
    if abs(mean_value) < 1e-12:
        return math.inf

    return abs(
        std_value / mean_value
    )


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

    if math.isclose(
        float(minimum),
        float(maximum),
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        return pd.Series(
            1.0,
            index=series.index,
        )

    normalized = (
        numeric - minimum
    ) / (
        maximum - minimum
    )

    if higher_is_better:
        return normalized

    return 1 - normalized


def _build_parameter_stability(
    train_search: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    total_folds = int(
        train_search["fold"].nunique()
    )

    for parameter in PARAMETER_COLUMNS:
        grouped = train_search.groupby(
            parameter,
            dropna=False,
        )

        for value, frame in grouped:
            folds_present = int(
                frame["fold"].nunique()
            )

            mean_objective = _safe_float(
                frame[
                    "objective_score"
                ].mean()
            )

            std_objective = _safe_float(
                frame[
                    "objective_score"
                ].std(ddof=0)
            )

            mean_return = _safe_float(
                frame[
                    "train_total_return_pct"
                ].mean()
            )

            std_return = _safe_float(
                frame[
                    "train_total_return_pct"
                ].std(ddof=0)
            )

            mean_sharpe = _safe_float(
                frame[
                    "train_sharpe_ratio"
                ].mean()
            )

            std_sharpe = _safe_float(
                frame[
                    "train_sharpe_ratio"
                ].std(ddof=0)
            )

            mean_drawdown = _safe_float(
                frame[
                    "train_max_drawdown_pct"
                ].mean()
            )

            std_drawdown = _safe_float(
                frame[
                    "train_max_drawdown_pct"
                ].std(ddof=0)
            )

            mean_profit_factor = _safe_float(
                frame[
                    "train_profit_factor"
                ].mean()
            )

            mean_win_rate = _safe_float(
                frame[
                    "train_win_rate_pct"
                ].mean()
            )

            objective_cv = (
                _coefficient_of_variation(
                    mean_objective,
                    std_objective,
                )
            )

            return_cv = (
                _coefficient_of_variation(
                    mean_return,
                    std_return,
                )
            )

            sharpe_cv = (
                _coefficient_of_variation(
                    mean_sharpe,
                    std_sharpe,
                )
            )

            rows.append(
                {
                    "parameter": parameter,
                    "value": value,
                    "observations": int(
                        len(frame)
                    ),
                    "folds_present": (
                        folds_present
                    ),
                    "total_folds": (
                        total_folds
                    ),
                    "fold_coverage_pct": float(
                        folds_present
                        / total_folds
                        * 100
                    ),
                    "mean_objective_score": (
                        mean_objective
                    ),
                    "std_objective_score": (
                        std_objective
                    ),
                    "objective_cv": (
                        objective_cv
                    ),
                    "mean_return_pct": (
                        mean_return
                    ),
                    "std_return_pct": (
                        std_return
                    ),
                    "return_cv": (
                        return_cv
                    ),
                    "mean_sharpe_ratio": (
                        mean_sharpe
                    ),
                    "std_sharpe_ratio": (
                        std_sharpe
                    ),
                    "sharpe_cv": (
                        sharpe_cv
                    ),
                    "mean_drawdown_pct": (
                        mean_drawdown
                    ),
                    "std_drawdown_pct": (
                        std_drawdown
                    ),
                    "mean_profit_factor": (
                        mean_profit_factor
                    ),
                    "mean_win_rate_pct": (
                        mean_win_rate
                    ),
                }
            )

    result = pd.DataFrame(
        rows
    )

    if result.empty:
        raise ValueError(
            "Không tạo được parameter stability."
        )

    result = result.replace(
        [math.inf, -math.inf],
        pd.NA,
    )

    scored_frames: list[
        pd.DataFrame
    ] = []

    for parameter, frame in result.groupby(
        "parameter",
        sort=False,
    ):
        ranked = frame.copy()

        ranked[
            "score_mean_objective"
        ] = _min_max_score(
            ranked[
                "mean_objective_score"
            ],
            higher_is_better=True,
        )

        ranked[
            "score_mean_sharpe"
        ] = _min_max_score(
            ranked[
                "mean_sharpe_ratio"
            ],
            higher_is_better=True,
        )

        ranked[
            "score_mean_return"
        ] = _min_max_score(
            ranked[
                "mean_return_pct"
            ],
            higher_is_better=True,
        )

        ranked[
            "score_drawdown"
        ] = _min_max_score(
            ranked[
                "mean_drawdown_pct"
            ].abs(),
            higher_is_better=False,
        )

        ranked[
            "score_objective_stability"
        ] = _min_max_score(
            ranked[
                "std_objective_score"
            ],
            higher_is_better=False,
        )

        ranked[
            "score_sharpe_stability"
        ] = _min_max_score(
            ranked[
                "std_sharpe_ratio"
            ],
            higher_is_better=False,
        )

        ranked[
            "score_return_stability"
        ] = _min_max_score(
            ranked[
                "std_return_pct"
            ],
            higher_is_better=False,
        )

        ranked[
            "score_cv_stability"
        ] = _min_max_score(
            ranked[
                "sharpe_cv"
            ].fillna(
                ranked[
                    "sharpe_cv"
                ].max()
            ),
            higher_is_better=False,
        )

        ranked[
            "stability_score"
        ] = (
            ranked[
                "score_mean_objective"
            ]
            * 0.20
            + ranked[
                "score_mean_sharpe"
            ]
            * 0.20
            + ranked[
                "score_mean_return"
            ]
            * 0.15
            + ranked[
                "score_drawdown"
            ]
            * 0.10
            + ranked[
                "score_objective_stability"
            ]
            * 0.10
            + ranked[
                "score_sharpe_stability"
            ]
            * 0.10
            + ranked[
                "score_return_stability"
            ]
            * 0.05
            + ranked[
                "score_cv_stability"
            ]
            * 0.10
        ) * 100

        ranked[
            "stability_rank"
        ] = (
            ranked[
                "stability_score"
            ]
            .rank(
                method="dense",
                ascending=False,
            )
            .astype(int)
        )

        ranked[
            "is_stability_winner"
        ] = (
            ranked[
                "stability_rank"
            ]
            == 1
        )

        scored_frames.append(
            ranked
        )

    return (
        pd.concat(
            scored_frames,
            ignore_index=True,
        )
        .sort_values(
            by=[
                "parameter",
                "stability_rank",
                "value",
            ],
            ascending=[
                True,
                True,
                True,
            ],
        )
        .reset_index(
            drop=True
        )
    )


def _build_stability_ranking(
    parameter_stability: pd.DataFrame,
) -> pd.DataFrame:
    columns = [
        "parameter",
        "value",
        "stability_rank",
        "stability_score",
        "mean_objective_score",
        "std_objective_score",
        "mean_sharpe_ratio",
        "std_sharpe_ratio",
        "sharpe_cv",
        "mean_return_pct",
        "std_return_pct",
        "mean_drawdown_pct",
        "mean_profit_factor",
        "fold_coverage_pct",
    ]

    return (
        parameter_stability[
            columns
        ]
        .sort_values(
            by=[
                "parameter",
                "stability_rank",
                "stability_score",
            ],
            ascending=[
                True,
                True,
                False,
            ],
        )
        .reset_index(
            drop=True
        )
    )


def _build_stability_summary(
    parameter_stability: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    for parameter in PARAMETER_COLUMNS:
        frame = parameter_stability[
            parameter_stability[
                "parameter"
            ]
            == parameter
        ]

        if frame.empty:
            continue

        winner = (
            frame
            .sort_values(
                by=[
                    "stability_rank",
                    "stability_score",
                ],
                ascending=[
                    True,
                    False,
                ],
            )
            .iloc[0]
        )

        rows.append(
            {
                "parameter": parameter,
                "best_value": (
                    winner["value"]
                ),
                "stability_score": (
                    winner[
                        "stability_score"
                    ]
                ),
                "mean_objective_score": (
                    winner[
                        "mean_objective_score"
                    ]
                ),
                "std_objective_score": (
                    winner[
                        "std_objective_score"
                    ]
                ),
                "mean_sharpe_ratio": (
                    winner[
                        "mean_sharpe_ratio"
                    ]
                ),
                "std_sharpe_ratio": (
                    winner[
                        "std_sharpe_ratio"
                    ]
                ),
                "sharpe_cv": (
                    winner["sharpe_cv"]
                ),
                "mean_return_pct": (
                    winner[
                        "mean_return_pct"
                    ]
                ),
                "mean_drawdown_pct": (
                    winner[
                        "mean_drawdown_pct"
                    ]
                ),
                "reason": (
                    "Highest stability score "
                    "within parameter"
                ),
            }
        )

    return pd.DataFrame(
        rows
    )


def _build_robust_parameters(
    parameter_stability: pd.DataFrame,
    *,
    score_tolerance_pct: float = 10.0,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []

    for parameter in PARAMETER_COLUMNS:
        frame = parameter_stability[
            parameter_stability[
                "parameter"
            ]
            == parameter
        ].copy()

        if frame.empty:
            continue

        best_score = float(
            frame[
                "stability_score"
            ].max()
        )

        threshold = (
            best_score
            * (
                1
                - score_tolerance_pct
                / 100
            )
        )

        robust = frame[
            frame[
                "stability_score"
            ]
            >= threshold
        ].copy()

        values = sorted(
            robust["value"].tolist()
        )

        if not values:
            continue

        rows.append(
            {
                "parameter": parameter,
                "best_score": best_score,
                "score_threshold": (
                    threshold
                ),
                "score_tolerance_pct": (
                    score_tolerance_pct
                ),
                "robust_value_count": len(
                    values
                ),
                "robust_values": "|".join(
                    str(value)
                    for value in values
                ),
                "robust_min": min(values),
                "robust_max": max(values),
                "recommended_value": (
                    robust
                    .sort_values(
                        by=[
                            "stability_score",
                            "mean_sharpe_ratio",
                            "mean_return_pct",
                        ],
                        ascending=[
                            False,
                            False,
                            False,
                        ],
                    )
                    .iloc[0]["value"]
                ),
            }
        )

    return pd.DataFrame(
        rows
    )


def analyze_parameter_stability(
    train_search: pd.DataFrame,
    *,
    robust_score_tolerance_pct: float = 10.0,
) -> ParameterStabilityResult:
    _validate_train_search(
        train_search
    )

    prepared = _prepare_train_search(
        train_search
    )

    parameter_stability = (
        _build_parameter_stability(
            prepared
        )
    )

    stability_ranking = (
        _build_stability_ranking(
            parameter_stability
        )
    )

    stability_summary = (
        _build_stability_summary(
            parameter_stability
        )
    )

    robust_parameters = (
        _build_robust_parameters(
            parameter_stability,
            score_tolerance_pct=(
                robust_score_tolerance_pct
            ),
        )
    )

    return ParameterStabilityResult(
        parameter_stability=(
            parameter_stability
        ),
        stability_ranking=(
            stability_ranking
        ),
        stability_summary=(
            stability_summary
        ),
        robust_parameters=(
            robust_parameters
        ),
    )


def analyze_parameter_stability_from_csv(
    train_search_path: str | Path,
    *,
    robust_score_tolerance_pct: float = 10.0,
) -> ParameterStabilityResult:
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

    return analyze_parameter_stability(
        train_search,
        robust_score_tolerance_pct=(
            robust_score_tolerance_pct
        ),
    )


def print_parameter_stability_report(
    result: ParameterStabilityResult,
) -> None:
    print()
    print("=" * 100)
    print("PARAMETER STABILITY ANALYSIS")
    print("=" * 100)

    print()
    print("STABILITY WINNERS")
    print("-" * 100)

    display_columns = [
        "parameter",
        "best_value",
        "stability_score",
        "mean_sharpe_ratio",
        "std_sharpe_ratio",
        "sharpe_cv",
        "mean_return_pct",
        "mean_drawdown_pct",
    ]

    print(
        result.stability_summary[
            display_columns
        ]
        .to_string(
            index=False,
            float_format=lambda value: (
                f"{value:.2f}"
            ),
        )
    )

    print()
    print("ROBUST PARAMETER REGIONS")
    print("-" * 100)

    robust_columns = [
        "parameter",
        "robust_values",
        "robust_min",
        "robust_max",
        "recommended_value",
        "best_score",
    ]

    print(
        result.robust_parameters[
            robust_columns
        ]
        .to_string(
            index=False,
            float_format=lambda value: (
                f"{value:.2f}"
            ),
        )
    )

    print("=" * 100)