from __future__ import annotations

import argparse
from pathlib import Path
import numpy as np
import pandas as pd

FEATURES = ["signal_score", "relative_strength", "adx", "volume_ratio"]
REQUIRED = ["symbol", "entry_date", "net_return_pct", "fold", *FEATURES]


def stats(x: pd.Series) -> dict:
    x = pd.to_numeric(x, errors="coerce").dropna()
    if x.empty:
        return {"n": 0, "win_rate_pct": np.nan, "avg_return_pct": np.nan,
                "median_return_pct": np.nan, "pf": np.nan, "sum_return_pct": 0.0}
    gains = x[x > 0].sum()
    losses = -x[x < 0].sum()
    return {
        "n": int(len(x)),
        "win_rate_pct": float((x > 0).mean() * 100),
        "avg_return_pct": float(x.mean()),
        "median_return_pct": float(x.median()),
        "pf": float(gains / losses) if losses > 0 else np.inf,
        "sum_return_pct": float(x.sum()),
    }


def add_train_quality(train: pd.DataFrame, test: pd.DataFrame) -> pd.DataFrame:
    """Frozen train-only percentile composite; higher is better for every feature."""
    out = test.copy()
    train = train.copy()
    for c in FEATURES:
        train[c] = pd.to_numeric(train[c], errors="coerce")
        out[c] = pd.to_numeric(out[c], errors="coerce")
        med = train[c].median()
        if pd.isna(med):
            med = 0.0
        train[c] = train[c].fillna(med)
        out[c] = out[c].fillna(med)
        vals = np.sort(train[c].to_numpy(dtype=float))
        # empirical CDF, frozen entirely from TRAIN
        out[c + "_pct"] = np.searchsorted(vals, out[c].to_numpy(dtype=float), side="right") / max(len(vals), 1)
    pct_cols = [c + "_pct" for c in FEATURES]
    out["quality_composite_pct"] = out[pct_cols].mean(axis=1)
    return out


def policy_return(row: pd.Series, policy: str) -> float:
    r = float(row["net_return_pct"])
    q = float(row["quality_composite_pct"])
    state = str(row["market_state"])
    if policy == "baseline":
        return r
    if policy == "quality_q4":
        return r if q >= 0.75 else 0.0
    if policy == "state_only":
        if state == "DIVERGENT_BULL":
            return 0.0
        if state == "FRAGILE_BULL":
            return r * 0.50
        return r
    if policy == "state_quality_soft":
        # Fixed, pre-declared risk policy; no threshold fitted on test.
        if state == "DIVERGENT_BULL":
            mult = 0.50
        elif state == "FRAGILE_BULL":
            mult = 1.00 if q >= 0.75 else 0.50
        elif state == "RECOVERY":
            mult = 1.00 if q >= 0.50 else 0.75
        else:
            mult = 1.00 if q >= 0.50 else 0.75
        return r * mult
    if policy == "state_quality_gate":
        if state == "DIVERGENT_BULL":
            return 0.0
        if state == "FRAGILE_BULL" and q < 0.75:
            return 0.0
        return r
    raise ValueError(policy)


def main() -> None:
    ap = argparse.ArgumentParser(description="Trade-level WFO audit for Market State × Entry Quality.")
    ap.add_argument("--trades", required=True)
    ap.add_argument("--market-state", required=True)
    ap.add_argument("--output", default="research_results/market_state_quality_wfo")
    args = ap.parse_args()

    trades = pd.read_csv(args.trades)
    state = pd.read_csv(args.market_state)
    missing = [c for c in REQUIRED if c not in trades.columns]
    if missing:
        raise SystemExit(f"Missing trade columns: {missing}")
    if "market_state" not in state.columns:
        raise SystemExit("market-state file must contain market_state")

    # Merge on stable trade identity. State file may contain additional breadth columns.
    keys = ["symbol", "entry_date", "exit_date"]
    merged = trades.merge(state[keys + ["market_state"]], on=keys, how="left", validate="many_to_one")
    if merged["market_state"].isna().any():
        n = int(merged["market_state"].isna().sum())
        raise SystemExit(f"Market-state merge missing {n} trades")

    merged["fold"] = pd.to_numeric(merged["fold"], errors="raise").astype(int)
    merged["entry_date"] = pd.to_datetime(merged["entry_date"])
    merged = merged.sort_values(["fold", "entry_date", "symbol"]).reset_index(drop=True)

    folds = sorted(merged["fold"].unique())
    # Infer train as the immediately preceding 24-month period ending before each test fold's first entry.
    # The exact trade list is OOS, so train quality is estimated from earlier folds only.
    # This is intentionally conservative and prevents using same-fold test trades to define quality.
    policies = ["baseline", "quality_q4", "state_only", "state_quality_soft", "state_quality_gate"]
    fold_rows = []
    trade_rows = []

    for fold in folds:
        test = merged[merged.fold == fold].copy()
        prior = merged[merged.fold < fold].copy()
        if prior.empty:
            # No prior trades: cannot fit a frozen quality distribution. Baseline only.
            train = test.iloc[0:0].copy()
        else:
            train = prior.copy()
        if len(train) < 30:
            # Still compute using available prior folds, but mark the fold as low-train-sample.
            train_sample_warning = True
        else:
            train_sample_warning = False
        testq = add_train_quality(train, test) if len(train) else test.assign(quality_composite_pct=np.nan)
        if testq["quality_composite_pct"].isna().all():
            # First fold: don't manufacture a quality threshold.
            for p in policies:
                if p == "baseline":
                    rr = test["net_return_pct"].astype(float)
                else:
                    rr = pd.Series([np.nan] * len(test), index=test.index)
                s = stats(rr)
                fold_rows.append({"fold": fold, "policy": p, **s,
                                  "train_trades": len(train), "test_trades": len(test),
                                  "train_sample_warning": train_sample_warning,
                                  "usable_quality_policy": p == "baseline"})
            continue

        for p in policies:
            rr = testq.apply(lambda r: policy_return(r, p), axis=1)
            s = stats(rr)
            active = int((rr != 0).sum())
            fold_rows.append({"fold": fold, "policy": p, **s,
                              "active_trades": active,
                              "train_trades": len(train), "test_trades": len(test),
                              "train_sample_warning": train_sample_warning,
                              "usable_quality_policy": True})
        testq["quality_fold"] = fold
        for p in policies:
            testq[p + "_return_pct"] = testq.apply(lambda r: policy_return(r, p), axis=1)
        trade_rows.append(testq)

    fold_df = pd.DataFrame(fold_rows)
    trade_df = pd.concat(trade_rows, ignore_index=True) if trade_rows else pd.DataFrame()

    summary_rows = []
    for p in policies:
        f = fold_df[(fold_df.policy == p) & fold_df.usable_quality_policy]
        x = f["avg_return_pct"].dropna()
        profitable = int((x > 0).sum())
        summary_rows.append({
            "policy": p,
            "folds_evaluated": int(len(f)),
            "profitable_folds": profitable,
            "profitable_fold_pct": float(profitable / len(f) * 100) if len(f) else np.nan,
            "median_fold_avg_return_pct": float(x.median()) if len(x) else np.nan,
            "mean_fold_avg_return_pct": float(x.mean()) if len(x) else np.nan,
            "total_test_trades": int(f.test_trades.sum()) if len(f) else 0,
            "train_sample_warning_folds": int(f.train_sample_warning.sum()) if len(f) else 0,
        })
    summary = pd.DataFrame(summary_rows)

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    fold_df.to_csv(out / "fold_policy_summary.csv", index=False)
    summary.to_csv(out / "policy_summary.csv", index=False)
    if not trade_df.empty:
        trade_df.to_csv(out / "trade_level_policy_returns.csv", index=False)

    print(summary.to_string(index=False))
    print(f"\nWrote: {out / 'policy_summary.csv'}")
    print(f"Wrote: {out / 'fold_policy_summary.csv'}")
    if not trade_df.empty:
        print(f"Wrote: {out / 'trade_level_policy_returns.csv'}")
    print("\nIMPORTANT: this is a trade-level WFO audit, not a portfolio backtest. Any winner must be re-run through the real backtesting engine before production consideration.")


if __name__ == "__main__":
    main()
