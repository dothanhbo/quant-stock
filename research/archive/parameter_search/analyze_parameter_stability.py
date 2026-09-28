from __future__ import annotations

import argparse
from pathlib import Path

from backtesting.parameter_stability import (
    analyze_parameter_stability_from_csv,
    print_parameter_stability_report,
)


DEFAULT_INPUT = Path(
    "research_results/"
    "walk_forward_optimizer/"
    "train_search.csv"
)

DEFAULT_OUTPUT = Path(
    "research_results/"
    "walk_forward_optimizer"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze parameter stability "
            "from WFO train search."
        )
    )

    parser.add_argument(
        "--input",
        default=str(
            DEFAULT_INPUT
        ),
    )

    parser.add_argument(
        "--output",
        default=str(
            DEFAULT_OUTPUT
        ),
    )

    parser.add_argument(
        "--robust-tolerance",
        type=float,
        default=10.0,
        help=(
            "Cho phép stability score thấp hơn "
            "best score bao nhiêu phần trăm "
            "để được xem là robust."
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not 0 <= args.robust_tolerance < 100:
        raise ValueError(
            "robust-tolerance phải nằm trong "
            "khoảng [0, 100)."
        )

    result = (
        analyze_parameter_stability_from_csv(
            args.input,
            robust_score_tolerance_pct=(
                args.robust_tolerance
            ),
        )
    )

    print_parameter_stability_report(
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
        f"{output_dir / 'parameter_stability.csv'}"
    )
    print(
        "Đã xuất: "
        f"{output_dir / 'stability_ranking.csv'}"
    )
    print(
        "Đã xuất: "
        f"{output_dir / 'parameter_stability_summary.csv'}"
    )
    print(
        "Đã xuất: "
        f"{output_dir / 'robust_parameters.csv'}"
    )


if __name__ == "__main__":
    main()