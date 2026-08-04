from __future__ import annotations

import argparse
from pathlib import Path
import webbrowser

from backtesting.monte_carlo_comparison import (
    compare_monte_carlo_methods,
    print_monte_carlo_comparison,
)


DEFAULT_BASE_DIR = Path(
    "research_results/monte_carlo_v2"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare Trade, Block and "
            "Regime bootstrap Monte Carlo results."
        )
    )

    parser.add_argument(
        "--trade",
        default=str(
            DEFAULT_BASE_DIR
            / "trade"
            / "summary_v2.csv"
        ),
    )

    parser.add_argument(
        "--block",
        default=str(
            DEFAULT_BASE_DIR
            / "block_10"
            / "summary_v2.csv"
        ),
    )

    parser.add_argument(
        "--regime",
        default=str(
            DEFAULT_BASE_DIR
            / "regime"
            / "summary_v2.csv"
        ),
    )

    parser.add_argument(
        "--output",
        default=str(
            DEFAULT_BASE_DIR
            / "comparison"
        ),
    )

    parser.add_argument(
        "--open",
        action="store_true",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    result = compare_monte_carlo_methods(
        trade_summary_path=args.trade,
        block_summary_path=args.block,
        regime_summary_path=args.regime,
    )

    print_monte_carlo_comparison(
        result
    )

    result.save(
        output_dir=args.output
    )

    output_dir = Path(
        args.output
    )

    html_path = (
        output_dir
        / "comparison.html"
    )

    print()
    print(
        f"Đã xuất: "
        f"{output_dir / 'comparison.csv'}"
    )
    print(
        f"Đã xuất: "
        f"{output_dir / 'comparison_ranking.csv'}"
    )
    print(
        f"Đã xuất: "
        f"{html_path}"
    )

    if args.open:
        webbrowser.open(
            html_path.resolve().as_uri()
        )


if __name__ == "__main__":
    main()