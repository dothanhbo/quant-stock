"""
Research-only: Market State Incremental Attribution.

Goal
----
Test whether finer market states add information beyond the existing
entry features:
    signal_score, relative_strength, ADX, volume_ratio, market_regime.

This script does NOT change production logic.

Method
------
1. Load exact current-logic WFO OOS trades.
2. Load market-state-enriched trade data.
3. Restrict to the actual OOS universe (by default symbols appearing in the
   supplied trade file), so breadth is not contaminated by unrelated symbols.
4. Compare:
      A) base features only
      B) base features + market_state
5. Produce descriptive cell/interaction tables rather than fitting a predictive
   model. This avoids pretending that in-sample ML performance is OOS alpha.

Outputs
-------
market_state_incremental_summary.csv
market_state_by_regime.csv
market_state_by_feature_bucket.csv
market_state_fold_attribution.csv
market_state_interactions.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


BASE_FEATURES = [
    "signal_score",
    "relative_strength",
    "adx",
    "volume_ratio",
    "base_regime",
]

STATE_ORDER = [
    "HEALTHY_BULL",
    "FRAGILE_BULL",
    "DIVERGENT_BULL",
    "RECOVERY",
    "NEUTRAL",
    "BEAR",
    "UNKNOWN",
]


def pf(values: pd.Series) -> float:
    x = pd.to_numeric(values, errors="coerce").dropna()
    gains = x[x > 0].sum()
    losses = -x[x < 0].sum()
    if losses <= 0:
        return np.inf if gains > 0 else np.nan
    return gains / losses


def stats(group: pd.DataFrame) -> dict:
    r = pd.to_numeric(group["net_return_pct"], errors="coerce").dropna()
    if len(r) == 0:
        return {
            "trades": 0,
            "win_rate_pct": np.nan,
            "avg_net_return_pct": np.nan,
            "median_net_return_pct": np.nan,
            "profit_factor": np.nan,
        }

    return {
        "trades": len(r),
        "win_rate_pct": round((r > 0).mean() * 100, 2),
        "avg_net_return_pct": round(r.mean(), 4),
        "median_net_return_pct": round(r.median(), 4),
        "profit_factor": round(pf(r), 4),
    }


def bucket_series(s: pd.Series, q: int = 4) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    # rank(method='first') prevents duplicate quantile edges from breaking qcut.
    try:
        return pd.qcut(s.rank(method="first"), q=q, labels=False) + 1
    except ValueError:
        return pd.Series(np.nan, index=s.index)


def make_feature_buckets(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ["signal_score", "relative_strength", "adx", "volume_ratio"]:
        if col in out:
            out[f"{col}_q"] = bucket_series(out[col], 4)
    return out


def summarize_grouped(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    rows = []
    for keys, g in df.groupby(cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = {col: key for col, key in zip(cols, keys)}
        row.update(stats(g))
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--trades",
        required=True,
        help="Exact current-logic WFO trade_level_oos.csv",
    )
    parser.add_argument(
        "--market-state",
        default="research_results/market_state_diagnostic/trade_level_market_state.csv",
        help="Output from run_market_state_diagnostic.py",
    )
    parser.add_argument(
        "--output-dir",
        default="research_results/market_state_incremental_attribution",
    )
    args = parser.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    trades = pd.read_csv(args.trades)
    enriched = pd.read_csv(args.market_state)

    # The enriched file is derived from the exact OOS trades. Merge back to the
    # source file by stable trade identity to prevent accidental row duplication.
    keys = [
        "symbol",
        "entry_date",
        "exit_date",
        "entry_price",
        "exit_price",
    ]
    keys = [k for k in keys if k in trades.columns and k in enriched.columns]

    if not keys:
        raise ValueError("Không tìm thấy trade identity columns để ghép dữ liệu.")

    # Keep only columns that are genuinely market-state features.
    state_cols = [
        "symbol",
        "entry_date",
        "exit_date",
        "entry_price",
        "exit_price",
        "market_state",
        "base_regime",
        "breadth_ema20_pct",
        "breadth_ema50_pct",
        "breadth_ema50_change_10d",
        "breadth_ema20_change_10d",
        "ema50_slope_10d",
        "return_20d",
        "distance_ema200",
    ]
    state_cols = [c for c in state_cols if c in enriched.columns]

    state_data = enriched[state_cols].drop_duplicates(keys)

    df = trades.merge(
        state_data,
        on=keys,
        how="left",
        suffixes=("", "_state"),
        validate="one_to_one",
    )

    # Prefer original trade columns. The state file should only add context.
    if "market_state" not in df:
        df["market_state"] = "UNKNOWN"
    df["market_state"] = df["market_state"].fillna("UNKNOWN")
    df["base_regime"] = df["base_regime"].fillna(
        df["market_regime"] if "market_regime" in df else "UNKNOWN"
    )

    df = make_feature_buckets(df)

    # ------------------------------------------------------------
    # 1. Overall baseline vs market-state decomposition
    # ------------------------------------------------------------
    baseline = stats(df)
    rows = [
        {
            "model_context": "BASE_FEATURES_ONLY",
            "added_market_state": False,
            **baseline,
        }
    ]

    # This is descriptive attribution, not a fitted model:
    # compare state-specific expectancy against the full baseline.
    for state in STATE_ORDER:
        g = df[df["market_state"] == state]
        if len(g) == 0:
            continue
        s = stats(g)
        s.update(
            {
                "model_context": f"BASE + {state}",
                "added_market_state": True,
                "market_state": state,
                "delta_vs_all_avg_pp": round(
                    s["avg_net_return_pct"] - baseline["avg_net_return_pct"], 4
                ),
            }
        )
        rows.append(s)

    summary = pd.DataFrame(rows)
    summary.to_csv(out / "market_state_incremental_summary.csv", index=False)

    # ------------------------------------------------------------
    # 2. State within BULL/SIDEWAY/BEAR
    # ------------------------------------------------------------
    by_regime = summarize_grouped(
        df,
        ["base_regime", "market_state"],
    )
    by_regime.to_csv(out / "market_state_by_regime.csv", index=False)

    # ------------------------------------------------------------
    # 3. Does state still matter inside feature buckets?
    # ------------------------------------------------------------
    feature_bucket_rows = []
    for feature in [
        "signal_score_q",
        "relative_strength_q",
        "adx_q",
        "volume_ratio_q",
    ]:
        if feature not in df.columns:
            continue
        x = summarize_grouped(df, [feature, "market_state"])
        x.insert(0, "feature_bucket", feature)
        feature_bucket_rows.append(x)

    feature_buckets = (
        pd.concat(feature_bucket_rows, ignore_index=True)
        if feature_bucket_rows
        else pd.DataFrame()
    )
    feature_buckets.to_csv(out / "market_state_by_feature_bucket.csv", index=False)

    # ------------------------------------------------------------
    # 4. Fold stability: state effect by WFO fold
    # ------------------------------------------------------------
    fold = summarize_grouped(df, ["fold", "market_state"])
    fold.to_csv(out / "market_state_fold_attribution.csv", index=False)

    # ------------------------------------------------------------
    # 5. Key interactions: state x existing feature buckets
    # ------------------------------------------------------------
    interactions = []
    for feature in [
        "signal_score_q",
        "relative_strength_q",
        "adx_q",
        "volume_ratio_q",
    ]:
        if feature not in df.columns:
            continue
        x = summarize_grouped(df, ["market_state", feature])
        x.insert(0, "interaction", f"market_state x {feature}")
        interactions.append(x)

    interaction_df = (
        pd.concat(interactions, ignore_index=True)
        if interactions
        else pd.DataFrame()
    )
    interaction_df.to_csv(out / "market_state_interactions.csv", index=False)

    # ------------------------------------------------------------
    # Console report
    # ------------------------------------------------------------
    print("\n=== MARKET STATE INCREMENTAL ATTRIBUTION ===")
    print(f"OOS trades: {len(df)}")
    print(f"Unique symbols: {df['symbol'].nunique()}")
    print(f"Output: {out.resolve()}")

    print("\n--- Baseline ---")
    print(pd.DataFrame([baseline]).to_string(index=False))

    print("\n--- State decomposition ---")
    cols = [
        c
        for c in [
            "market_state",
            "trades",
            "win_rate_pct",
            "avg_net_return_pct",
            "median_net_return_pct",
            "profit_factor",
            "delta_vs_all_avg_pp",
        ]
        if c in summary.columns
    ]
    print(summary[cols].to_string(index=False))

    print("\n--- State x Base Regime ---")
    print(by_regime.to_string(index=False))

    print("\nInterpretation rules:")
    print("1) Không coi state-specific return là alpha độc lập.")
    print("2) Ưu tiên state có đủ sample và cùng hướng qua nhiều WFO folds.")
    print("3) Nếu state effect biến mất khi nhìn trong score/RS/ADX/volume buckets,")
    print("   state có thể chỉ là proxy cho entry features.")
    print("4) Nếu effect vẫn tồn tại trong nhiều buckets + nhiều folds,")
    print("   Market State đáng được đưa sang bước WFO risk/gate research.")
    print("5) Không sửa production strategy từ kết quả diagnostic này.")


if __name__ == "__main__":
    main()
