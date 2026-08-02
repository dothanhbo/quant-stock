from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


DEFAULT_PATTERN = "research_results/*.csv"
DEFAULT_OUTPUT = "research_results/exit_model_summary.csv"

METRIC_COLUMNS = [
    "total_trades",
    "total_return_pct",
    "cagr_pct",
    "max_drawdown_pct",
    "sharpe_ratio",
    "sortino_ratio",
    "profit_factor",
    "win_rate_pct",
    "expectancy_pct",
]


def load_result_files(
    pattern: str,
) -> pd.DataFrame:
    files = sorted(Path().glob(pattern))

    if not files:
        raise FileNotFoundError(
            f"Không tìm thấy file theo pattern: {pattern}"
        )

    frames: list[pd.DataFrame] = []

    for file_path in files:
        try:
            df = pd.read_csv(file_path)
        except Exception as error:
            print(
                f"⚠️ Bỏ qua {file_path}: {error}"
            )
            continue

        required = {
            "exit_model",
            "total_return_pct",
            "sharpe_ratio",
            "profit_factor",
            "max_drawdown_pct",
        }

        if not required.issubset(df.columns):
            continue

        df = df.copy()
        df["source_file"] = file_path.name

        frames.append(df)

    if not frames:
        raise ValueError(
            "Không có file nào chứa kết quả exit model hợp lệ."
        )

    return pd.concat(
        frames,
        ignore_index=True,
    )


def prepare_summary(
    df: pd.DataFrame,
) -> pd.DataFrame:
    result = df.copy()

    for column in METRIC_COLUMNS:
        if column in result.columns:
            result[column] = pd.to_numeric(
                result[column],
                errors="coerce",
            )

    if "break_even_trigger" not in result.columns:
        result["break_even_trigger"] = pd.NA

    result["model_label"] = result.apply(
        lambda row: (
            f"BreakEven {row['break_even_trigger']}%"
            if row["exit_model"] == "break_even"
            else str(row["exit_model"])
        ),
        axis=1,
    )

    result = result.sort_values(
        by=[
            "sharpe_ratio",
            "total_return_pct",
            "profit_factor",
        ],
        ascending=[
            False,
            False,
            False,
        ],
    ).reset_index(drop=True)

    result.insert(
        0,
        "rank",
        range(1, len(result) + 1),
    )

    return result


def print_summary(
    df: pd.DataFrame,
    *,
    top: int,
) -> None:
    print()
    print("=" * 90)
    print("EXIT MODEL COMPARISON")
    print("=" * 90)

    columns = [
        "rank",
        "model_label",
        "total_trades",
        "total_return_pct",
        "cagr_pct",
        "max_drawdown_pct",
        "sharpe_ratio",
        "profit_factor",
        "expectancy_pct",
    ]

    available_columns = [
        column
        for column in columns
        if column in df.columns
    ]

    display = df[
        available_columns
    ].head(top).copy()

    print(
        display.to_string(
            index=False,
            float_format=lambda value: f"{value:.2f}",
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Gộp và xếp hạng kết quả nghiên cứu "
            "các Exit Model."
        )
    )

    parser.add_argument(
        "--pattern",
        default=DEFAULT_PATTERN,
    )

    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
    )

    parser.add_argument(
        "--top",
        type=int,
        default=20,
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    results = load_result_files(
        args.pattern
    )

    summary = prepare_summary(
        results
    )

    output_path = Path(
        args.output
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    summary.to_csv(
        output_path,
        index=False,
        encoding="utf-8-sig",
    )

    print_summary(
        summary,
        top=args.top,
    )

    print()
    print(f"Đã xuất: {output_path}")


if __name__ == "__main__":
    main()