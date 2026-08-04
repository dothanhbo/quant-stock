from __future__ import annotations

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
class ParameterRecommendationResult:
    recommendations: pd.DataFrame
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

        self.recommendations.to_csv(
            output_path
            / "parameter_recommendation.csv",
            index=False,
            encoding="utf-8-sig",
        )

        pd.DataFrame(
            [self.summary]
        ).to_csv(
            output_path
            / "parameter_recommendation_summary.csv",
            index=False,
            encoding="utf-8-sig",
        )


def _validate_inputs(
    *,
    parameter_frequency: pd.DataFrame,
    parameter_stability: pd.DataFrame,
    robust_parameters: pd.DataFrame,
) -> None:
    frequency_required = {
        "parameter",
        "value",
        "selected_count",
        "selected_pct",
        "rank_within_parameter",
    }

    stability_required = {
        "parameter",
        "value",
        "stability_score",
        "stability_rank",
        "mean_sharpe_ratio",
        "mean_return_pct",
        "mean_drawdown_pct",
    }

    robust_required = {
        "parameter",
        "robust_values",
        "robust_min",
        "robust_max",
        "recommended_value",
        "best_score",
    }

    checks = (
        (
            "parameter_frequency",
            parameter_frequency,
            frequency_required,
        ),
        (
            "parameter_stability",
            parameter_stability,
            stability_required,
        ),
        (
            "robust_parameters",
            robust_parameters,
            robust_required,
        ),
    )

    for (
        name,
        dataframe,
        required_columns,
    ) in checks:
        missing = (
            required_columns
            - set(dataframe.columns)
        )

        if missing:
            raise ValueError(
                f"{name} thiếu cột: "
                + ", ".join(
                    sorted(missing)
                )
            )

        if dataframe.empty:
            raise ValueError(
                f"{name} không có dữ liệu."
            )


def _confidence_label(
    *,
    frequency_pct: float,
    stability_score: float,
    robust_value_count: int,
) -> str:
    if (
        frequency_pct >= 75
        and stability_score >= 80
        and robust_value_count <= 2
    ):
        return "HIGH"

    if (
        frequency_pct >= 40
        and stability_score >= 65
    ):
        return "MEDIUM"

    return "LOW"


def _build_reason(
    *,
    parameter: str,
    frequency_pct: float,
    stability_score: float,
    robust_values: str,
    mean_sharpe: float,
    mean_return: float,
    mean_drawdown: float,
) -> str:
    reasons: list[str] = []

    if frequency_pct >= 75:
        reasons.append(
            "selected consistently across folds"
        )
    elif frequency_pct >= 40:
        reasons.append(
            "selected frequently across folds"
        )
    else:
        reasons.append(
            "limited fold selection frequency"
        )

    if stability_score >= 80:
        reasons.append(
            "high parameter stability"
        )
    elif stability_score >= 65:
        reasons.append(
            "moderate parameter stability"
        )
    else:
        reasons.append(
            "low parameter stability"
        )

    if "|" in str(robust_values):
        reasons.append(
            "multiple robust values available"
        )
    else:
        reasons.append(
            "single concentrated robust value"
        )

    reasons.append(
        f"mean Sharpe {mean_sharpe:.2f}"
    )

    reasons.append(
        f"mean return {mean_return:.2f}%"
    )

    reasons.append(
        f"mean drawdown {mean_drawdown:.2f}%"
    )

    if parameter == "min_adx":
        reasons.append(
            "parameter currently appears insensitive"
        )

    return "; ".join(
        reasons
    )


def build_parameter_recommendations(
    *,
    parameter_frequency: pd.DataFrame,
    parameter_stability: pd.DataFrame,
    robust_parameters: pd.DataFrame,
) -> ParameterRecommendationResult:
    _validate_inputs(
        parameter_frequency=(
            parameter_frequency
        ),
        parameter_stability=(
            parameter_stability
        ),
        robust_parameters=(
            robust_parameters
        ),
    )

    frequency_modes = (
        parameter_frequency[
            parameter_frequency[
                "rank_within_parameter"
            ]
            == 1
        ]
        .copy()
    )

    stability_winners = (
        parameter_stability[
            parameter_stability[
                "stability_rank"
            ]
            == 1
        ]
        .copy()
    )

    rows: list[dict[str, Any]] = []

    for parameter in PARAMETER_COLUMNS:
        frequency_row = (
            frequency_modes[
                frequency_modes[
                    "parameter"
                ]
                == parameter
            ]
        )

        stability_row = (
            stability_winners[
                stability_winners[
                    "parameter"
                ]
                == parameter
            ]
        )

        robust_row = (
            robust_parameters[
                robust_parameters[
                    "parameter"
                ]
                == parameter
            ]
        )

        if (
            frequency_row.empty
            or stability_row.empty
            or robust_row.empty
        ):
            continue

        frequency_row = (
            frequency_row.iloc[0]
        )

        stability_row = (
            stability_row.iloc[0]
        )

        robust_row = (
            robust_row.iloc[0]
        )

        frequency_value = (
            frequency_row["value"]
        )

        stability_value = (
            stability_row["value"]
        )

        recommended_value = (
            robust_row[
                "recommended_value"
            ]
        )

        frequency_pct = float(
            frequency_row[
                "selected_pct"
            ]
        )

        stability_score = float(
            stability_row[
                "stability_score"
            ]
        )

        robust_value_count = int(
            robust_row[
                "robust_value_count"
            ]
        )

        agreement = (
            float(frequency_value)
            == float(stability_value)
            == float(recommended_value)
        )

        confidence = _confidence_label(
            frequency_pct=frequency_pct,
            stability_score=(
                stability_score
            ),
            robust_value_count=(
                robust_value_count
            ),
        )

        if parameter == "min_adx":
            confidence = "LOW"

        recommendation_status = (
            "RECOMMENDED"
            if confidence in {
                "HIGH",
                "MEDIUM",
            }
            else "REVIEW"
        )

        reason = _build_reason(
            parameter=parameter,
            frequency_pct=frequency_pct,
            stability_score=(
                stability_score
            ),
            robust_values=str(
                robust_row[
                    "robust_values"
                ]
            ),
            mean_sharpe=float(
                stability_row[
                    "mean_sharpe_ratio"
                ]
            ),
            mean_return=float(
                stability_row[
                    "mean_return_pct"
                ]
            ),
            mean_drawdown=float(
                stability_row[
                    "mean_drawdown_pct"
                ]
            ),
        )

        rows.append(
            {
                "parameter": parameter,
                "frequency_winner": (
                    frequency_value
                ),
                "frequency_pct": (
                    frequency_pct
                ),
                "stability_winner": (
                    stability_value
                ),
                "stability_score": (
                    stability_score
                ),
                "robust_values": (
                    robust_row[
                        "robust_values"
                    ]
                ),
                "robust_min": (
                    robust_row[
                        "robust_min"
                    ]
                ),
                "robust_max": (
                    robust_row[
                        "robust_max"
                    ]
                ),
                "recommended_value": (
                    recommended_value
                ),
                "frequency_stability_agreement": (
                    agreement
                ),
                "confidence": (
                    confidence
                ),
                "status": (
                    recommendation_status
                ),
                "mean_sharpe_ratio": float(
                    stability_row[
                        "mean_sharpe_ratio"
                    ]
                ),
                "mean_return_pct": float(
                    stability_row[
                        "mean_return_pct"
                    ]
                ),
                "mean_drawdown_pct": float(
                    stability_row[
                        "mean_drawdown_pct"
                    ]
                ),
                "reason": reason,
            }
        )

    recommendations = pd.DataFrame(
        rows
    )

    if recommendations.empty:
        raise ValueError(
            "Không tạo được recommendation."
        )

    summary: dict[str, Any] = {
        "parameters_analyzed": int(
            len(recommendations)
        ),
        "high_confidence": int(
            (
                recommendations[
                    "confidence"
                ]
                == "HIGH"
            ).sum()
        ),
        "medium_confidence": int(
            (
                recommendations[
                    "confidence"
                ]
                == "MEDIUM"
            ).sum()
        ),
        "low_confidence": int(
            (
                recommendations[
                    "confidence"
                ]
                == "LOW"
            ).sum()
        ),
        "recommended_parameters": int(
            (
                recommendations[
                    "status"
                ]
                == "RECOMMENDED"
            ).sum()
        ),
        "review_parameters": int(
            (
                recommendations[
                    "status"
                ]
                == "REVIEW"
            ).sum()
        ),
    }

    for _, row in (
        recommendations.iterrows()
    ):
        parameter = str(
            row["parameter"]
        )

        summary[
            f"{parameter}_recommended"
        ] = row[
            "recommended_value"
        ]

        summary[
            f"{parameter}_confidence"
        ] = row[
            "confidence"
        ]

    return ParameterRecommendationResult(
        recommendations=(
            recommendations
        ),
        summary=summary,
    )


def build_parameter_recommendations_from_csv(
    *,
    frequency_path: str | Path,
    stability_path: str | Path,
    robust_path: str | Path,
) -> ParameterRecommendationResult:
    frequency_file = Path(
        frequency_path
    )

    stability_file = Path(
        stability_path
    )

    robust_file = Path(
        robust_path
    )

    for path in (
        frequency_file,
        stability_file,
        robust_file,
    ):
        if not path.exists():
            raise FileNotFoundError(
                f"Không tìm thấy file: "
                f"{path.resolve()}"
            )

    parameter_frequency = pd.read_csv(
        frequency_file
    )

    parameter_stability = pd.read_csv(
        stability_file
    )

    robust_parameters = pd.read_csv(
        robust_file
    )

    return build_parameter_recommendations(
        parameter_frequency=(
            parameter_frequency
        ),
        parameter_stability=(
            parameter_stability
        ),
        robust_parameters=(
            robust_parameters
        ),
    )


def print_parameter_recommendation_report(
    result: ParameterRecommendationResult,
) -> None:
    print()
    print("=" * 120)
    print("PARAMETER RECOMMENDATION ENGINE")
    print("=" * 120)

    display_columns = [
        "parameter",
        "recommended_value",
        "robust_values",
        "frequency_pct",
        "stability_score",
        "confidence",
        "status",
    ]

    print(
        result.recommendations[
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
    print("RECOMMENDATION DETAILS")
    print("-" * 120)

    for _, row in (
        result.recommendations.iterrows()
    ):
        print()
        print(
            f"{row['parameter']}: "
            f"{row['recommended_value']}"
        )
        print(
            f"Confidence: "
            f"{row['confidence']}"
        )
        print(
            f"Reason: "
            f"{row['reason']}"
        )

    print()
    print("SUMMARY")
    print("-" * 120)

    print(
        f"High confidence   : "
        f"{result.summary['high_confidence']}"
    )
    print(
        f"Medium confidence : "
        f"{result.summary['medium_confidence']}"
    )
    print(
        f"Low confidence    : "
        f"{result.summary['low_confidence']}"
    )

    print("=" * 120)