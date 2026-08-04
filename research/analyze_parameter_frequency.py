from __future__ import annotations

import argparse
from pathlib import Path

from backtesting.parameter_frequency import (
    analyze_parameter_frequency_from_csv,
    print_parameter_frequency_report,
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
            "Analyze selected parameter "
            "frequency from WFO train search."
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

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    result = (
        analyze_parameter_frequency_from_csv(
            args.input
        )
    )

    print_parameter_frequency_report(
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
        f"{output_dir / 'selected_parameters.csv'}"
    )
    print(
        "Đã xuất: "
        f"{output_dir / 'parameter_frequency.csv'}"
    )
    print(
        "Đã xuất: "
        f"{output_dir / 'combination_frequency.csv'}"
    )
    print(
        "Đã xuất: "
        f"{output_dir / 'parameter_frequency_summary.csv'}"
    )


if __name__ == "__main__":
    main()