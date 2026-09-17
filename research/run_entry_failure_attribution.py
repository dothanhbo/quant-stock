from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


FEATURES = [
    "signal_score",
    "relative_strength",
    "adx",
    "volume_ratio",
    "atr",
    "risk_pct",
    "market_regime",
]


def load_trades(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)

    required = {
        "symbol",
        "entry_date",
        "signal_score",
        "relative_strength",
        "adx",
        "volume_ratio",
        "atr",
        "market_regime",
        "net_return_pct",
        "is_win",
        "fold",
    }

    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Missing columns: {sorted(missing)}"
        )

    return df.copy()


def add_buckets(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()

    x["score_bucket"] = pd.cut(
        x["signal_score"],
        bins=[-float("inf"), 69, 74, 79, 84, float("inf")],
        labels=["<70", "70-74", "75-79", "80-84", "85+"],
    )

    x["adx_bucket"] = pd.cut(
        x["adx"],
        bins=[-float("inf"), 24.999, 29.999, 39.999, float("inf")],
        labels=["<25", "25-29.9", "30-39.9", "40+"],
    )

    x["volume_bucket"] = pd.cut(
        x["volume_ratio"],
        bins=[-float("inf"), 1.299, 1.799, 2.499, float("inf")],
        labels=["<1.3", "1.3-1.79", "1.8-2.49", "2.5+"],
    )

    return x


def summarize(
    df: pd.DataFrame,
    group_cols: list[str],
    min_trades: int,
) -> pd.DataFrame:
    rows = []

    for keys, g in df.groupby(
        group_cols,
        observed=True,
        dropna=False,
    ):
        if len(g) < min_trades:
            continue

        if not isinstance(keys, tuple):
            keys = (keys,)

        row = dict(zip(group_cols, keys))

        row["trades"] = len(g)
        row["win_rate_pct"] = 100 * g["is_win"].mean()
        row["avg_return_pct"] = g["net_return_pct"].mean()
        row["median_return_pct"] = g["net_return_pct"].median()

        winners = g.loc[g["net_return_pct"] > 0, "net_return_pct"]
        losers = g.loc[g["net_return_pct"] <= 0, "net_return_pct"]

        gross_profit = winners.sum()
        gross_loss = abs(losers.sum())

        row["profit_factor"] = (
            gross_profit / gross_loss
            if gross_loss > 0
            else float("inf")
        )

        rows.append(row)

    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Entry failure attribution from current WFO trade-level data."
    )

    parser.add_argument(
        "--input",
        default="research_results/current_logic_walk_forward/trade_level_oos.csv",
    )

    parser.add_argument(
        "--output",
        default="research_results/current_logic_walk_forward/entry_failure_attribution",
    )

    parser.add_argument(
        "--min-trades",
        type=int,
        default=15,
    )

    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    df = load_trades(input_path)
    df = add_buckets(df)

    print(f"Trades: {len(df)}")
    print(f"Folds: {df['fold'].nunique()}")

    # ---------------------------------------------------------
    # 1. Single-variable attribution
    # ---------------------------------------------------------

    analyses = {
        "score": ["score_bucket"],
        "adx": ["adx_bucket"],
        "volume": ["volume_bucket"],
        "regime": ["market_regime"],
    }

    for name, cols in analyses.items():
        result = summarize(
            df,
            cols,
            args.min_trades,
        )

        result = result.sort_values(
            "avg_return_pct",
            ascending=False,
        )

        result.to_csv(
            output_dir / f"{name}.csv",
            index=False,
        )

    # ---------------------------------------------------------
    # 2. Interaction analysis
    # ---------------------------------------------------------

    interactions = {
        "score_x_volume": [
            "score_bucket",
            "volume_bucket",
        ],
        "adx_x_volume": [
            "adx_bucket",
            "volume_bucket",
        ],
        "score_x_adx": [
            "score_bucket",
            "adx_bucket",
        ],
        "regime_x_volume": [
            "market_regime",
            "volume_bucket",
        ],
        "regime_x_adx": [
            "market_regime",
            "adx_bucket",
        ],
    }

    for name, cols in interactions.items():
        result = summarize(
            df,
            cols,
            args.min_trades,
        )

        result = result.sort_values(
            ["avg_return_pct", "profit_factor"],
            ascending=False,
        )

        result.to_csv(
            output_dir / f"{name}.csv",
            index=False,
        )

    # ---------------------------------------------------------
    # 3. Fold stability
    # ---------------------------------------------------------

    fold_rows = []

    for fold, g in df.groupby("fold"):
        fold_rows.append(
            {
                "fold": fold,
                "trades": len(g),
                "win_rate_pct": 100 * g["is_win"].mean(),
                "avg_return_pct": g["net_return_pct"].mean(),
                "median_return_pct": g["net_return_pct"].median(),
                "avg_score": g["signal_score"].mean(),
                "avg_adx": g["adx"].mean(),
                "avg_volume_ratio": g["volume_ratio"].mean(),
                "avg_relative_strength": g["relative_strength"].mean(),
            }
        )

    pd.DataFrame(fold_rows).to_csv(
        output_dir / "fold_profile.csv",
        index=False,
    )

    # ---------------------------------------------------------
    # 4. Worst trade clusters
    # ---------------------------------------------------------

    bad = df[df["net_return_pct"] < 0].copy()

    bad["score_bucket"] = bad["score_bucket"].astype(str)
    bad["adx_bucket"] = bad["adx_bucket"].astype(str)
    bad["volume_bucket"] = bad["volume_bucket"].astype(str)

    bad_summary = summarize(
        bad,
        [
            "market_regime",
            "score_bucket",
            "adx_bucket",
            "volume_bucket",
        ],
        args.min_trades,
    )

    if not bad_summary.empty:
        bad_summary = bad_summary.sort_values(
            "avg_return_pct"
        )

    bad_summary.to_csv(
        output_dir / "worst_loss_clusters.csv",
        index=False,
    )

    print()
    print("DONE")
    print(f"Output: {output_dir}")


if __name__ == "__main__":
    main()