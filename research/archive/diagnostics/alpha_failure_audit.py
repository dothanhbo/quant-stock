"""Research-only alpha failure audit.

Reads previously generated research artifacts; does not run market-data I/O and
never changes production strategy/configuration. The goal is to identify where
edge degrades: regime, entry conditions, execution gaps, or exit path.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import numpy as np
import pandas as pd


def finite(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan)


def metrics(df: pd.DataFrame) -> dict:
    r = finite(df["net_return_pct"]).dropna()
    wins = r[r > 0]
    losses = r[r < 0]
    gross_profit = wins.sum()
    gross_loss = abs(losses.sum())
    return {
        "trades": int(len(r)),
        "win_rate_pct": float((r > 0).mean() * 100) if len(r) else np.nan,
        "avg_return_pct": float(r.mean()) if len(r) else np.nan,
        "median_return_pct": float(r.median()) if len(r) else np.nan,
        "expectancy_pct": float(r.mean()) if len(r) else np.nan,
        "profit_factor": float(gross_profit / gross_loss) if gross_loss else np.nan,
        "total_net_pnl": float(finite(df["net_pnl"]).sum()),
    }


def bootstrap_ci(values: pd.Series, seed: int = 42, n: int = 5000) -> tuple[float, float]:
    x = finite(values).dropna().to_numpy()
    if len(x) < 10:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(n, len(x)), replace=True).mean(axis=1)
    return tuple(np.percentile(means, [2.5, 97.5]))


def bucket_table(df: pd.DataFrame, dimension: str, bucket: str) -> pd.DataFrame:
    rows = []
    for value, g in df.groupby(bucket, dropna=False, observed=False):
        ci_lo, ci_hi = bootstrap_ci(g["net_return_pct"])
        rows.append({"dimension": dimension, "bucket": str(value), **metrics(g),
                     "expectancy_ci95_low": ci_lo, "expectancy_ci95_high": ci_hi})
    return pd.DataFrame(rows)


def add_integrity_buckets(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["score_bucket_audit"] = pd.cut(finite(d["signal_score"]), [-np.inf, 69.999, 79.999, 89.999, np.inf], labels=["<70", "70-79", "80-89", "90+"])
    d["adx_bucket_audit"] = pd.cut(finite(d["adx"]), [-np.inf, 19.999, 24.999, 29.999, 34.999, np.inf], labels=["<20", "20-24", "25-29", "30-34", "35+"])
    d["rs_bucket_audit"] = pd.cut(finite(d["relative_strength"]), [-np.inf, -0.0001, 1.999, 3.999, 5.999, np.inf], labels=["<0", "0-1.99", "2-3.99", "4-5.99", "6+"])
    d["volume_bucket_audit"] = pd.cut(finite(d["volume_ratio"]), [-np.inf, .9999, 1.4999, 1.9999, 2.9999, np.inf], labels=["<1.0", "1.0-1.49", "1.5-1.99", "2.0-2.99", "3.0+"])
    return d


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--integrity", default="research_results/research_integrity_baseline/integrity_trades.csv")
    p.add_argument("--enriched", default="research_results/bull_entry_regime_audit_v2/selected_case_feature_enriched.csv")
    p.add_argument("--output", default="research_results/alpha_failure_audit")
    args = p.parse_args()
    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)

    integrity = pd.read_csv(args.integrity)
    integrity = add_integrity_buckets(integrity)
    tables = [
        bucket_table(integrity, "score", "score_bucket_audit"),
        bucket_table(integrity, "adx", "adx_bucket_audit"),
        bucket_table(integrity, "relative_strength", "rs_bucket_audit"),
        bucket_table(integrity, "volume", "volume_bucket_audit"),
        bucket_table(integrity, "market_regime", "market_regime"),
        bucket_table(integrity, "entry_year", "entry_year"),
        bucket_table(integrity, "exit_reason", "exit_reason"),
    ]
    all_buckets = pd.concat(tables, ignore_index=True)
    all_buckets.to_csv(out / "integrity_bucket_metrics.csv", index=False)

    enriched = pd.read_csv(args.enriched)
    feature_specs = {
        "signal_rsi": "signal_rsi_bucket",
        "distance_ema20": "signal_distance_ema20_bucket",
        "return_3d": "signal_return_3d_bucket",
        "adx": "signal_adx_bucket",
        "volume": "signal_volume_bucket",
        "score": "signal_score_bucket",
        "market_return20": "market_return20_bucket",
        "market_slope": "market_slope_bucket",
        "market_distance_ema200": "market_distance_ema200_bucket",
        "breadth_ema50": "breadth_ema50_bucket",
        "entry_gap": "entry_gap_bucket",
    }
    enriched_tables = [bucket_table(enriched, k, v) for k, v in feature_specs.items() if v in enriched.columns]
    pd.concat(enriched_tables, ignore_index=True).to_csv(out / "entry_feature_metrics.csv", index=False)

    lines = ["# Quant Bot — Alpha Failure Audit", "", "Research-only; no production changes.", ""]
    overall = metrics(integrity)
    lines += ["## Baseline", f"- Trades: {overall['trades']}", f"- Win rate: {overall['win_rate_pct']:.2f}%", f"- Expectancy: {overall['expectancy_pct']:.3f}%", f"- PF: {overall['profit_factor']:.3f}", ""]
    lines += ["## Findings", ""]
    lines += ["1. **Regime:** inspect regime-specific expectancy before adding filters.", "2. **Score:** a higher score is not monotonic evidence of better entry quality; do not harden the score threshold without OOS confirmation.", "3. **ADX:** very high ADX should be treated as a hypothesis, not automatically as a quality filter.", "4. **Entry quality:** the enriched audit contains RSI, EMA20 extension, 3D return, breakout distance, market slope/extension, breadth and entry gap; these should be tested with pre-declared brackets and WFO, not optimized post-hoc.", "5. **Exit:** separate losses that never worked from winners/losses that gave back MFE before changing the exit model.", ""]
    lines += ["## Guardrails", "- No production parameter change from this report alone.", "- Require OOS/WFO stability for any candidate rule.", "- Keep the current execution/risk infrastructure unchanged while diagnosing alpha.", ""]
    (out / "README.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
