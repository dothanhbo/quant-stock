from __future__ import annotations

import argparse
from pathlib import Path

from backtesting.parameter_recommendation import (
    build_parameter_recommendations_from_csv,
    print_parameter_recommendation_report,
)


DEFAULT_DIRECTORY = Path(
    "research_results/"
    "walk_forward_optimizer"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate parameter recommendations "
            "from frequency and stability results."
        )
    )

    parser.add_argument(
        "--input-dir",
        default=str(
            DEFAULT_DIRECTORY
        ),
    )

    parser.add_argument(
        "--output",
        default=str(
            DEFAULT_DIRECTORY
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    input_dir = Path(
        args.input_dir
    )

    result = (
        build_parameter_recommendations_from_csv(
            frequency_path=(
                input_dir
                / "parameter_frequency.csv"
            ),
            stability_path=(
                input_dir
                / "parameter_stability.csv"
            ),
            robust_path=(
                input_dir
                / "robust_parameters.csv"
            ),
        )
    )

    print_parameter_recommendation_report(
        result
    )

    result.save(
        output_dir=args.output
    )

    output_dir = Path(
        args.output
    )

    print()
    print(
        "Đã xuất: "
        f"{output_dir / 'parameter_recommendation.csv'}"
    )
    print(
        "Đã xuất: "
        f"{output_dir / 'parameter_recommendation_summary.csv'}"
    )


if __name__ == "__main__":
    main()