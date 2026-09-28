from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


DEFAULT_OUTPUT = Path(
    "research_results/composite_weights"
)

WEIGHT_COLUMNS = [
    "signal_weight",
    "atr_weight",
    "stop_weight",
    "regime_weight",
]

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--aggregation",
        choices=[
            "sum",
            "product",
        ],
        default="sum",
    )

    parser.add_argument(
        "--top-percent",
        type=float,
        default=20.0,
    )

    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
    )

    return parser.parse_args()

def load_summary(
    output_dir: Path,
    aggregation: str,
) -> pd.DataFrame:

    summary_path = (
        output_dir
        / aggregation
        / "summary.csv"
    )

    if not summary_path.exists():
        raise FileNotFoundError(
            summary_path
        )

    return pd.read_csv(
        summary_path
    )

def select_top_rows(
    summary: pd.DataFrame,
    *,
    top_percent: float,
) -> pd.DataFrame:

    ranking = (
        summary
        .sort_values(
            "sharpe_ratio",
            ascending=False,
        )
        .reset_index(drop=True)
    )

    count = max(
        1,
        int(
            len(ranking)
            * top_percent
            / 100
        ),
    )

    return ranking.head(count)

def build_weight_frequency(
    top_rows: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, float | int | str]] = []

    total_top_rows = len(
        top_rows
    )

    for parameter in WEIGHT_COLUMNS:
        counts = (
            top_rows[
                parameter
            ]
            .value_counts()
            .sort_index()
        )

        for value, selected_count in (
            counts.items()
        ):
            selected_pct = (
                selected_count
                / total_top_rows
                * 100
                if total_top_rows > 0
                else 0.0
            )

            rows.append(
                {
                    "parameter": parameter,
                    "value": float(value),
                    "selected_count": int(
                        selected_count
                    ),
                    "selected_pct": float(
                        selected_pct
                    ),
                }
            )

    frequency = pd.DataFrame(
        rows
    )

    frequency[
        "rank_within_parameter"
    ] = (
        frequency
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

    frequency[
        "is_mode"
    ] = (
        frequency[
            "rank_within_parameter"
        ]
        == 1
    )

    return frequency.sort_values(
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

def build_weight_stability(
    summary: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, float | int | str]] = []

    for parameter in WEIGHT_COLUMNS:
        grouped = summary.groupby(
            parameter,
            dropna=False,
        )

        for value, group in grouped:
            sharpe = pd.to_numeric(
                group["sharpe_ratio"],
                errors="coerce",
            ).dropna()

            returns = pd.to_numeric(
                group["total_return_pct"],
                errors="coerce",
            ).dropna()

            drawdowns = pd.to_numeric(
                group["max_drawdown_pct"],
                errors="coerce",
            ).dropna()

            if sharpe.empty:
                continue

            mean_sharpe = float(
                sharpe.mean()
            )

            std_sharpe = float(
                sharpe.std(ddof=0)
            )

            sharpe_cv = (
                abs(std_sharpe / mean_sharpe)
                if abs(mean_sharpe) > 1e-12
                else float("inf")
            )

            mean_return = (
                float(returns.mean())
                if not returns.empty
                else 0.0
            )

            std_return = (
                float(returns.std(ddof=0))
                if not returns.empty
                else 0.0
            )

            mean_drawdown = (
                float(drawdowns.mean())
                if not drawdowns.empty
                else 0.0
            )

            std_drawdown = (
                float(drawdowns.std(ddof=0))
                if not drawdowns.empty
                else 0.0
            )

            rows.append(
                {
                    "parameter": parameter,
                    "value": float(value),
                    "observations": int(
                        len(group)
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
                    "mean_return_pct": (
                        mean_return
                    ),
                    "std_return_pct": (
                        std_return
                    ),
                    "mean_drawdown_pct": (
                        mean_drawdown
                    ),
                    "std_drawdown_pct": (
                        std_drawdown
                    ),
                }
            )

    stability = pd.DataFrame(
        rows
    )

    if stability.empty:
        raise ValueError(
            "Không tạo được weight stability."
        )

    return stability

def add_stability_scores(
    stability: pd.DataFrame,
) -> pd.DataFrame:
    working = stability.copy()

    score_rows: list[
        pd.DataFrame
    ] = []

    for parameter, group in working.groupby(
        "parameter",
        sort=False,
    ):
        section = group.copy()

        sharpe_min = section[
            "mean_sharpe_ratio"
        ].min()

        sharpe_max = section[
            "mean_sharpe_ratio"
        ].max()

        return_min = section[
            "mean_return_pct"
        ].min()

        return_max = section[
            "mean_return_pct"
        ].max()

        drawdown_abs = section[
            "mean_drawdown_pct"
        ].abs()

        drawdown_min = drawdown_abs.min()
        drawdown_max = drawdown_abs.max()

        cv_min = section[
            "sharpe_cv"
        ].min()

        cv_max = section[
            "sharpe_cv"
        ].max()

        def normalize(
            series: pd.Series,
            minimum: float,
            maximum: float,
        ) -> pd.Series:
            if abs(
                float(maximum)
                - float(minimum)
            ) < 1e-12:
                return pd.Series(
                    1.0,
                    index=series.index,
                )

            return (
                series - minimum
            ) / (
                maximum - minimum
            )

        section[
            "score_sharpe"
        ] = normalize(
            section[
                "mean_sharpe_ratio"
            ],
            sharpe_min,
            sharpe_max,
        )

        section[
            "score_return"
        ] = normalize(
            section[
                "mean_return_pct"
            ],
            return_min,
            return_max,
        )

        section[
            "score_drawdown"
        ] = 1 - normalize(
            drawdown_abs,
            drawdown_min,
            drawdown_max,
        )

        section[
            "score_cv"
        ] = 1 - normalize(
            section[
                "sharpe_cv"
            ],
            cv_min,
            cv_max,
        )

        section[
            "stability_score"
        ] = (
            section[
                "score_sharpe"
            ] * 0.40
            + section[
                "score_return"
            ] * 0.25
            + section[
                "score_drawdown"
            ] * 0.15
            + section[
                "score_cv"
            ] * 0.20
        ) * 100

        section[
            "stability_rank"
        ] = (
            section[
                "stability_score"
            ]
            .rank(
                method="dense",
                ascending=False,
            )
            .astype(int)
        )

        score_rows.append(
            section
        )

    result = pd.concat(
        score_rows,
        ignore_index=True,
    )

    return result.sort_values(
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
    ).reset_index(
        drop=True
    )

def build_robust_weight_region(
    *,
    frequency: pd.DataFrame,
    stability: pd.DataFrame,
    minimum_frequency_pct: float = 20.0,
    minimum_stability_score: float = 60.0,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    for parameter in WEIGHT_COLUMNS:
        frequency_section = frequency[
            frequency["parameter"]
            == parameter
        ].copy()

        stability_section = stability[
            stability["parameter"]
            == parameter
        ].copy()

        merged = frequency_section.merge(
            stability_section[
                [
                    "parameter",
                    "value",
                    "stability_score",
                    "stability_rank",
                    "mean_sharpe_ratio",
                    "mean_return_pct",
                    "mean_drawdown_pct",
                ]
            ],
            on=[
                "parameter",
                "value",
            ],
            how="inner",
        )

        robust = merged[
            (
                merged["selected_pct"]
                >= minimum_frequency_pct
            )
            & (
                merged["stability_score"]
                >= minimum_stability_score
            )
        ].copy()

        if robust.empty:
            robust = (
                merged
                .sort_values(
                    by=[
                        "stability_score",
                        "selected_pct",
                    ],
                    ascending=[
                        False,
                        False,
                    ],
                )
                .head(1)
                .copy()
            )

        robust_values = sorted(
            float(value)
            for value in robust["value"]
        )

        recommended_row = (
            robust
            .sort_values(
                by=[
                    "stability_score",
                    "selected_pct",
                    "mean_sharpe_ratio",
                ],
                ascending=[
                    False,
                    False,
                    False,
                ],
            )
            .iloc[0]
        )

        rows.append(
            {
                "parameter": parameter,
                "robust_values": "|".join(
                    f"{value:.2f}"
                    for value in robust_values
                ),
                "robust_value_count": len(
                    robust_values
                ),
                "robust_min": min(
                    robust_values
                ),
                "robust_max": max(
                    robust_values
                ),
                "recommended_value": float(
                    recommended_row["value"]
                ),
                "recommended_frequency_pct": float(
                    recommended_row[
                        "selected_pct"
                    ]
                ),
                "recommended_stability_score": float(
                    recommended_row[
                        "stability_score"
                    ]
                ),
                "recommended_mean_sharpe": float(
                    recommended_row[
                        "mean_sharpe_ratio"
                    ]
                ),
                "recommended_mean_return_pct": float(
                    recommended_row[
                        "mean_return_pct"
                    ]
                ),
                "recommended_mean_drawdown_pct": float(
                    recommended_row[
                        "mean_drawdown_pct"
                    ]
                ),
            }
        )

    return pd.DataFrame(
        rows
    )

def build_weight_recommendation(
    robust_region: pd.DataFrame,
) -> pd.DataFrame:
    if robust_region.empty:
        raise ValueError(
            "robust_region không có dữ liệu."
        )

    rows: list[dict[str, object]] = []

    for _, row in robust_region.iterrows():
        stability_score = float(
            row[
                "recommended_stability_score"
            ]
        )

        frequency_pct = float(
            row[
                "recommended_frequency_pct"
            ]
        )

        robust_count = int(
            row[
                "robust_value_count"
            ]
        )

        if (
            stability_score >= 80
            and frequency_pct >= 25
            and robust_count <= 2
        ):
            confidence = "HIGH"
        elif (
            stability_score >= 60
            and frequency_pct >= 15
        ):
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        reason_parts = [
            (
                f"frequency "
                f"{frequency_pct:.2f}%"
            ),
            (
                f"stability score "
                f"{stability_score:.2f}"
            ),
            (
                f"robust region "
                f"{row['robust_values']}"
            ),
            (
                f"mean Sharpe "
                f"{float(row['recommended_mean_sharpe']):.4f}"
            ),
            (
                f"mean return "
                f"{float(row['recommended_mean_return_pct']):.2f}%"
            ),
        ]

        rows.append(
            {
                "parameter": (
                    row["parameter"]
                ),
                "recommended_value": float(
                    row[
                        "recommended_value"
                    ]
                ),
                "robust_values": (
                    row[
                        "robust_values"
                    ]
                ),
                "robust_min": float(
                    row["robust_min"]
                ),
                "robust_max": float(
                    row["robust_max"]
                ),
                "frequency_pct": (
                    frequency_pct
                ),
                "stability_score": (
                    stability_score
                ),
                "confidence": (
                    confidence
                ),
                "status": (
                    "RECOMMENDED"
                    if confidence
                    in {
                        "HIGH",
                        "MEDIUM",
                    }
                    else "REVIEW"
                ),
                "mean_sharpe_ratio": float(
                    row[
                        "recommended_mean_sharpe"
                    ]
                ),
                "mean_return_pct": float(
                    row[
                        "recommended_mean_return_pct"
                    ]
                ),
                "mean_drawdown_pct": float(
                    row[
                        "recommended_mean_drawdown_pct"
                    ]
                ),
                "reason": "; ".join(
                    reason_parts
                ),
            }
        )

    recommendations = pd.DataFrame(
        rows
    )

    recommended_sum = float(
        recommendations[
            "recommended_value"
        ].sum()
    )

    recommendations[
        "recommended_weight_sum"
    ] = recommended_sum

    recommendations[
        "sum_is_valid"
    ] = abs(
        recommended_sum - 1.0
    ) <= 1e-9

    return recommendations

def print_robust_region_report(
    robust_region: pd.DataFrame,
) -> None:
    print()
    print("=" * 120)
    print("ROBUST WEIGHT REGION")
    print("=" * 120)

    print(
        robust_region[
            [
                "parameter",
                "robust_values",
                "robust_min",
                "robust_max",
                "recommended_value",
                "recommended_frequency_pct",
                "recommended_stability_score",
            ]
        ].to_string(
            index=False,
            float_format=lambda value: (
                f"{value:.4f}"
            ),
        )
    )


def print_weight_recommendation_report(
    recommendations: pd.DataFrame,
) -> None:
    print()
    print("=" * 120)
    print("COMPOSITE WEIGHT RECOMMENDATION")
    print("=" * 120)

    print(
        recommendations[
            [
                "parameter",
                "recommended_value",
                "robust_values",
                "frequency_pct",
                "stability_score",
                "confidence",
                "status",
            ]
        ].to_string(
            index=False,
            float_format=lambda value: (
                f"{value:.4f}"
            ),
        )
    )

    recommended_sum = (
        recommendations[
            "recommended_value"
        ].sum()
    )

    print()
    print(
        f"Recommended weight sum: "
        f"{recommended_sum:.4f}"
    )

def print_stability_report(
    stability: pd.DataFrame,
) -> None:
    print()
    print("=" * 120)
    print("COMPOSITE WEIGHT STABILITY")
    print("=" * 120)

    display_columns = [
        "value",
        "observations",
        "mean_sharpe_ratio",
        "std_sharpe_ratio",
        "sharpe_cv",
        "mean_return_pct",
        "mean_drawdown_pct",
        "stability_score",
        "stability_rank",
    ]

    for parameter in WEIGHT_COLUMNS:
        section = stability[
            stability[
                "parameter"
            ]
            == parameter
        ]

        print()
        print(parameter.upper())
        print("-" * 120)

        print(
            section[
                display_columns
            ].to_string(
                index=False,
                float_format=lambda value: (
                    f"{value:.4f}"
                ),
            )
        )

def print_frequency_report(
    frequency: pd.DataFrame,
) -> None:
    print()
    print("=" * 100)
    print("COMPOSITE WEIGHT FREQUENCY")
    print("=" * 100)

    for parameter in WEIGHT_COLUMNS:
        section = frequency[
            frequency[
                "parameter"
            ]
            == parameter
        ]

        print()
        print(parameter.upper())
        print("-" * 100)

        print(
            section[
                [
                    "value",
                    "selected_count",
                    "selected_pct",
                    "rank_within_parameter",
                ]
            ]
            .to_string(
                index=False,
                float_format=lambda value: (
                    f"{value:.2f}"
                ),
            )
        )

def main():

    args = parse_args()

    summary = load_summary(
        Path(args.output),
        args.aggregation,
    )

    top_rows = select_top_rows(
        summary,
        top_percent=args.top_percent,
    )

    frequency = build_weight_frequency(
        top_rows
    )

    stability = build_weight_stability(
        summary
    )

    stability = add_stability_scores(
        stability
    )

    robust_region = (
        build_robust_weight_region(
            frequency=frequency,
            stability=stability,
            minimum_frequency_pct=20.0,
            minimum_stability_score=60.0,
        )
    )

    recommendations = (
        build_weight_recommendation(
            robust_region
        )
    )

    print()

    print("=" * 80)

    print("COMPOSITE WEIGHT ANALYSIS")

    print("=" * 80)

    print(
        f"Aggregation : {args.aggregation}"
    )

    print(
        f"Rows        : {len(summary)}"
    )

    print(
        f"Top rows    : {len(top_rows)}"
    )

    print()

    print(
        top_rows[
            [
                "signal_weight",
                "atr_weight",
                "stop_weight",
                "regime_weight",
                "sharpe_ratio",
                "total_return_pct",
            ]
        ]
        .to_string(index=False)
    )

    print_frequency_report(
        frequency
    )

    print_stability_report(
        stability
    )

    output_dir = (
        Path(args.output)
        / args.aggregation
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    print_robust_region_report(
        robust_region
    )

    print_weight_recommendation_report(
        recommendations
    )

    frequency_path = (
        output_dir
        / "weight_frequency.csv"
    )

    frequency.to_csv(
        frequency_path,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print(
        f"Đã xuất: {frequency_path}"
    )

    stability_path = (
        output_dir
        / "weight_stability.csv"
    )

    stability.to_csv(
        stability_path,
        index=False,
        encoding="utf-8-sig",
    )

    print(
        f"Đã xuất: {stability_path}"
    )

    robust_region_path = (
        output_dir
        / "robust_weight_region.csv"
    )

    recommendation_path = (
        output_dir
        / "weight_recommendation.csv"
    )

    robust_region.to_csv(
        robust_region_path,
        index=False,
        encoding="utf-8-sig",
    )

    recommendations.to_csv(
        recommendation_path,
        index=False,
        encoding="utf-8-sig",
    )

    print(
        f"Đã xuất: {robust_region_path}"
    )

    print(
        f"Đã xuất: {recommendation_path}"
    )


if __name__ == "__main__":
    main()