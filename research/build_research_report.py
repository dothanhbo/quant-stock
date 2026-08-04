from __future__ import annotations

import argparse
from pathlib import Path
import webbrowser

from backtesting.research_report import (
    build_research_report,
    load_research_report_data,
)


DEFAULT_DIRECTORY = Path(
    "research_results/"
    "walk_forward_optimizer"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build HTML research report from "
            "Walk-Forward Optimization results."
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
            / "report.html"
        ),
    )

    parser.add_argument(
        "--title",
        default=(
            "Quant Stock Walk-Forward "
            "Research Report"
        ),
    )

    parser.add_argument(
        "--open",
        action="store_true",
        help=(
            "Mở report bằng trình duyệt "
            "sau khi tạo."
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    input_dir = Path(
        args.input_dir
    )

    output_path = Path(
        args.output
    )

    data = load_research_report_data(
        input_dir
    )

    result = build_research_report(
        data=data,
        report_title=args.title,
    )

    result.save(
        output_path
    )

    print()
    print("=" * 80)
    print("HTML RESEARCH REPORT")
    print("=" * 80)
    print(f"Input : {input_dir}")
    print(f"Output: {output_path}")
    print("=" * 80)

    if args.open:
        webbrowser.open(
            output_path.resolve().as_uri()
        )


if __name__ == "__main__":
    main()