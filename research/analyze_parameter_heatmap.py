from __future__ import annotations

import argparse
from pathlib import Path

from backtesting.parameter_heatmap import (
    analyze_parameter_heatmaps_from_csv,
    print_parameter_heatmap_report,
    save_parameter_heatmap_images,
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
            "Create parameter heatmaps "
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
        "--row-parameter",
        default=(
            "atr_stop_multiplier"
        ),
    )

    parser.add_argument(
        "--column-parameter",
        default=(
            "atr_target_multiplier"
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    result = (
        analyze_parameter_heatmaps_from_csv(
            args.input,
            row_parameter=(
                args.row_parameter
            ),
            column_parameter=(
                args.column_parameter
            ),
        )
    )

    print_parameter_heatmap_report(
        result
    )

    result.save_csv(
        output_dir=args.output
    )

    save_parameter_heatmap_images(
        result,
        output_dir=args.output,
    )

    output_dir = Path(
        args.output
    )

    generated_files = [
        "heatmap_sharpe.csv",
        "heatmap_return.csv",
        "heatmap_drawdown.csv",
        "heatmap_observations.csv",
        "heatmap_summary.csv",
        "heatmap_sharpe.png",
        "heatmap_return.png",
        "heatmap_drawdown.png",
    ]

    print()
    print("Đã xuất:")

    for filename in generated_files:
        print(
            f"- {output_dir / filename}"
        )


if __name__ == "__main__":
    main()