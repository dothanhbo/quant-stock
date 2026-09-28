
import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


def num(x, default=np.nan):
    try:
        if x is None or pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def pick_col(df, candidates, required=True):
    for c in candidates:
        if c in df.columns:
            return c
    if required:
        raise ValueError(f"Missing one of {candidates}; found: {df.columns.tolist()}")
    return None


def load_health(path):
    d = pd.read_csv(path)
    time_col = pick_col(d, ["time", "date"])
    breadth_col = pick_col(d, ["breadth_ema50_pct", "breadth50"])
    change_col = pick_col(d, ["breadth_ema50_change_10d", "breadth50_chg_10d"])
    d["time"] = pd.to_datetime(d[time_col]).dt.normalize()
    d["breadth_ema50_pct"] = pd.to_numeric(d[breadth_col], errors="coerce")
    d["breadth_ema50_change_10d"] = pd.to_numeric(d[change_col], errors="coerce")
    return d.drop_duplicates("time").set_index("time").sort_index()


def classify_state(row):
    # Preserve the market-state logic used by the existing PIT diagnostic.
    regime = str(row.get("market_regime", "")).upper()
    breadth = num(row.get("breadth_ema50_pct"))
    change = num(row.get("breadth_ema50_change_10d"))

    if regime == "BEAR":
        return "BEAR"
    if regime == "BULL":
        if breadth < 50 and change < 0:
            return "DIVERGENT_BULL"
        if breadth >= 70 and change >= 0:
            return "HEALTHY_BULL"
        return "FRAGILE_BULL"
    if regime == "SIDEWAY" and breadth >= 60 and change > 0:
        return "RECOVERY"
    return "NEUTRAL"


def normalize_state_column(df):
    for c in ["market_state", "state", "market_health_state"]:
        if c in df.columns:
            df["market_state"] = df[c].astype(str)
            return df

    # If trade file has no state, reconstruct from market-health + regime.
    return df


def load_trades(path, health):
    d = pd.read_csv(path)

    symbol_col = pick_col(d, ["symbol", "ticker"])
    entry_date_col = pick_col(d, ["entry_date", "entry_time", "date"])
    return_col = pick_col(d, ["net_return_pct", "return_pct", "trade_return_pct", "pnl_pct"])
    policy_col = pick_col(d, ["policy", "variant"], required=False)

    d["symbol"] = d[symbol_col].astype(str)
    d["entry_date"] = pd.to_datetime(d[entry_date_col]).dt.normalize()
    d["net_return_pct"] = pd.to_numeric(d[return_col], errors="coerce")
    if policy_col:
        d["policy"] = d[policy_col].astype(str)
    else:
        d["policy"] = "unknown"

    # Join market-health diagnostics at signal/entry date.
    h = health.reset_index()
    d = d.merge(
        h[["time", "breadth_ema50_pct", "breadth_ema50_change_10d"]],
        left_on="entry_date",
        right_on="time",
        how="left",
    ).drop(columns=["time"], errors="ignore")

    # Prefer state already stored by the production research script.
    d = normalize_state_column(d)

    if "market_state" not in d.columns:
        regime_col = pick_col(d, ["market_regime", "regime"], required=False)
        if regime_col is None:
            raise ValueError(
                "Trade file has no market_state and no market_regime/regime; "
                "cannot reconstruct state."
            )
        d["market_regime"] = d[regime_col].astype(str)
        d["market_state"] = d.apply(classify_state, axis=1)

    return d


def assign_fold(d, fold_file=None):
    if fold_file:
        f = pd.read_csv(fold_file)
        # Accept either a fold summary or a trade-level fold map.
        if {"entry_date", "fold"}.issubset(f.columns):
            f["entry_date"] = pd.to_datetime(f["entry_date"]).dt.normalize()
            keys = [c for c in ["symbol", "entry_date", "exit_date"] if c in d.columns and c in f.columns]
            if not keys:
                keys = ["entry_date"]
            d = d.merge(f[keys + ["fold"]].drop_duplicates(keys), on=keys, how="left")
            return d

    # Default: reconstruct the 12 six-month test folds used by the WFO.
    start = pd.Timestamp("2020-08-07")
    step = pd.DateOffset(months=6)
    folds = []
    for i in range(12):
        s = start + i * step
        e = s + pd.DateOffset(months=6) - pd.Timedelta(days=1)
        folds.append((i + 1, s.normalize(), e.normalize()))

    def fold_of(x):
        for n, s, e in folds:
            if s <= x <= e:
                return n
        return np.nan

    d["fold"] = d["entry_date"].map(fold_of)
    return d


def summarize_group(d, group_cols):
    rows = []
    for keys, g in d.groupby(group_cols, dropna=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        r = pd.to_numeric(g["net_return_pct"], errors="coerce").dropna()
        gains = r[r > 0].sum()
        losses = -r[r < 0].sum()
        row = {c: k for c, k in zip(group_cols, keys)}
        row.update(
            trades=len(r),
            wins=int((r > 0).sum()),
            win_rate_pct=(r > 0).mean() * 100 if len(r) else np.nan,
            avg_return_pct=r.mean() if len(r) else np.nan,
            median_return_pct=r.median() if len(r) else np.nan,
            total_return_sum_pct=r.sum() if len(r) else np.nan,
            profit_factor=(gains / losses if losses else np.inf),
            mean_breadth_pct=pd.to_numeric(g["breadth_ema50_pct"], errors="coerce").mean(),
            mean_breadth_change_10d=pd.to_numeric(
                g["breadth_ema50_change_10d"], errors="coerce"
            ).mean(),
        )
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(
        description="Audit contribution of market states and exposure policies by WFO fold."
    )
    ap.add_argument("--trades", required=True)
    ap.add_argument("--market-health", required=True)
    ap.add_argument("--out", default="research_results/exposure_contribution_audit")
    ap.add_argument("--fold-file", default=None)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    health = load_health(args.market_health)
    trades = load_trades(args.trades, health)
    trades = assign_fold(trades, args.fold_file)

    # Keep only WFO test trades.
    trades = trades[trades["fold"].notna()].copy()
    trades["fold"] = trades["fold"].astype(int)

    # Contribution by policy x fold.
    fold = summarize_group(trades, ["policy", "fold"])
    fold.to_csv(out / "policy_fold_summary.csv", index=False)

    # State contribution by policy.
    state = summarize_group(trades, ["policy", "market_state"])
    state.to_csv(out / "policy_state_summary.csv", index=False)

    # State x fold.
    state_fold = summarize_group(trades, ["policy", "fold", "market_state"])
    state_fold.to_csv(out / "policy_state_fold_summary.csv", index=False)

    # State share of trades and return contribution inside each policy/fold.
    totals = trades.groupby(["policy", "fold"], as_index=False).agg(
        policy_fold_trades=("net_return_pct", "size"),
        policy_fold_return_sum=("net_return_pct", "sum"),
    )
    sf = state_fold.merge(totals, on=["policy", "fold"], how="left")
    sf["trade_share_pct"] = 100 * sf["trades"] / sf["policy_fold_trades"]
    sf["return_contribution_share_pct"] = np.where(
        sf["policy_fold_return_sum"].abs() > 1e-12,
        100 * sf["total_return_sum_pct"] / sf["policy_fold_return_sum"],
        np.nan,
    )
    sf.to_csv(out / "policy_state_fold_contribution.csv", index=False)

    # Direct comparison of each policy against baseline, fold by fold.
    pivot = fold.pivot(index="fold", columns="policy", values="total_return_sum_pct").reset_index()
    if "baseline" in pivot.columns:
        for c in pivot.columns:
            if c not in {"fold", "baseline"}:
                pivot[f"{c}_minus_baseline_pp"] = pivot[c] - pivot["baseline"]
    pivot.to_csv(out / "policy_vs_baseline_fold_delta.csv", index=False)

    # Identify difficult folds and print state-level diagnosis for Q70 variants.
    print("\n=== POLICY / FOLD ===")
    print(fold.to_string(index=False))

    print("\n=== POLICY / STATE ===")
    print(state.to_string(index=False))

    print("\n=== Q70 / FRAGILE-BULL DIAGNOSTIC ===")
    q70 = state_fold[
        state_fold["policy"].astype(str).str.startswith("q70")
        & (state_fold["market_state"] == "FRAGILE_BULL")
    ].copy()
    if len(q70):
        print(q70.to_string(index=False))
    else:
        print("No q70 FRAGILE_BULL rows found.")

    print("\nWrote:", out.resolve())


if __name__ == "__main__":
    main()
