from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


DEFAULT_FILE = "research_results/grid_search.csv"


REQUIRED_COLUMNS = {
    "stop_loss_pct",
    "take_profit_pct",
    "max_holding_days",
    "min_adx",
    "total_trades",
    "cagr_pct",
    "max_drawdown_pct",
    "sharpe_ratio",
    "profit_factor",
    "strategy_vs_benchmark_pct",
}


def load_results(path: str) -> pd.DataFrame:
    file_path = Path(path)

    if not file_path.exists():
        raise FileNotFoundError(
            f"Không tìm thấy file kết quả: {file_path}"
        )

    df = pd.read_csv(file_path)

    missing_columns = REQUIRED_COLUMNS.difference(
        df.columns
    )

    if missing_columns:
        raise ValueError(
            "File grid search thiếu cột: "
            + ", ".join(sorted(missing_columns))
        )

    return df


def filter_results(
    df: pd.DataFrame,
    *,
    min_trades: int = 0,
) -> pd.DataFrame:
    result = df.copy()

    result = result[
        result["total_trades"] >= min_trades
    ]

    return result.reset_index(drop=True)


def min_max_score(
    series: pd.Series,
    *,
    higher_is_better: bool = True,
) -> pd.Series:
    numeric = pd.to_numeric(
        series,
        errors="coerce",
    ).fillna(0.0)

    minimum = numeric.min()
    maximum = numeric.max()

    if maximum == minimum:
        return pd.Series(
            0.5,
            index=numeric.index,
            dtype=float,
        )

    score = (
        numeric - minimum
    ) / (
        maximum - minimum
    )

    if not higher_is_better:
        score = 1 - score

    return score


def add_composite_score(
    df: pd.DataFrame,
) -> pd.DataFrame:
    result = df.copy()

    result["score_cagr"] = min_max_score(
        result["cagr_pct"],
    )

    result["score_sharpe"] = min_max_score(
        result["sharpe_ratio"],
    )

    result["score_profit_factor"] = min_max_score(
        result["profit_factor"],
    )

    # Drawdown càng gần 0 càng tốt.
    result["score_drawdown"] = min_max_score(
        result["max_drawdown_pct"],
    )

    result["composite_score"] = (
        result["score_cagr"] * 0.40
        + result["score_sharpe"] * 0.30
        + result["score_profit_factor"] * 0.20
        + result["score_drawdown"] * 0.10
    ) * 100

    return result


def rank_results(
    df: pd.DataFrame,
    *,
    sort_by: str,
    ascending: bool = False,
) -> pd.DataFrame:
    if sort_by not in df.columns:
        raise ValueError(
            f"Không tồn tại cột xếp hạng: {sort_by}"
        )

    return (
        df
        .sort_values(
            by=sort_by,
            ascending=ascending,
        )
        .reset_index(drop=True)
    )


def print_strategy(
    row: pd.Series,
    *,
    rank: int,
) -> None:
    print(f"#{rank}")

    print(
        f"SL / TP        : "
        f"{row['stop_loss_pct']:.0f}% / "
        f"{row['take_profit_pct']:.0f}%"
    )

    print(
        f"Hold / ADX     : "
        f"{int(row['max_holding_days'])} / "
        f"{row['min_adx']:.0f}"
    )

    print(
        f"Trades         : "
        f"{int(row['total_trades'])}"
    )

    print(
        f"Return         : "
        f"{row.get('total_return_pct', 0):+.2f}%"
    )

    print(
        f"CAGR           : "
        f"{row['cagr_pct']:+.2f}%"
    )

    print(
        f"Sharpe         : "
        f"{row['sharpe_ratio']:.2f}"
    )

    print(
        f"Profit Factor  : "
        f"{row['profit_factor']:.2f}"
    )

    print(
        f"Max Drawdown   : "
        f"{row['max_drawdown_pct']:.2f}%"
    )

    print(
        f"Excess Return  : "
        f"{row['strategy_vs_benchmark_pct']:+.2f}%"
    )

    if "composite_score" in row.index:
        print(
            f"Composite Score: "
            f"{row['composite_score']:.2f}"
        )

    print("-" * 60)


def print_top_table(
    df: pd.DataFrame,
    *,
    title: str,
    sort_by: str,
    top: int,
    ascending: bool = False,
) -> None:
    ranked = rank_results(
        df,
        sort_by=sort_by,
        ascending=ascending,
    )

    print()
    print("=" * 60)
    print(title)
    print("=" * 60)

    for rank, (_, row) in enumerate(
        ranked.head(top).iterrows(),
        start=1,
    ):
        print()
        print_strategy(
            row,
            rank=rank,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Xếp hạng kết quả Grid Search "
            "của Quant Stock."
        )
    )

    parser.add_argument(
        "--file",
        default=DEFAULT_FILE,
    )

    parser.add_argument(
        "--top",
        type=int,
        default=5,
    )

    parser.add_argument(
        "--min-trades",
        type=int,
        default=20,
    )

    parser.add_argument(
        "--sort",
        default=None,
        help=(
            "Chỉ in một bảng theo cột được chọn. "
            "Ví dụ: cagr_pct, sharpe_ratio."
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    df = load_results(
        args.file
    )

    filtered = filter_results(
        df,
        min_trades=args.min_trades,
    )

    if filtered.empty:
        print(
            "Không có chiến lược nào đạt điều kiện "
            f"tối thiểu {args.min_trades} trades."
        )
        return

    scored = add_composite_score(
        filtered
    )

    print(
        f"\nĐã giữ lại "
        f"{len(scored)}/{len(df)} chiến lược "
        f"có ít nhất {args.min_trades} trades."
    )

    if args.sort is not None:
        print_top_table(
            scored,
            title=(
                f"TOP STRATEGIES BY "
                f"{args.sort.upper()}"
            ),
            sort_by=args.sort,
            top=args.top,
        )
        return

    print_top_table(
        scored,
        title="TOP COMPOSITE SCORE",
        sort_by="composite_score",
        top=args.top,
    )

    print_top_table(
        scored,
        title="TOP CAGR",
        sort_by="cagr_pct",
        top=args.top,
    )

    print_top_table(
        scored,
        title="TOP SHARPE",
        sort_by="sharpe_ratio",
        top=args.top,
    )

    print_top_table(
        scored,
        title="TOP PROFIT FACTOR",
        sort_by="profit_factor",
        top=args.top,
    )

    print_top_table(
        scored,
        title="LOWEST DRAWDOWN",
        sort_by="max_drawdown_pct",
        top=args.top,
        ascending=False,
    )

    print_top_table(
        scored,
        title="TOP EXCESS RETURN",
        sort_by="strategy_vs_benchmark_pct",
        top=args.top,
    )


if __name__ == "__main__":
    main()