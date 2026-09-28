"""
Placebo / permutation test for Exhaustion Overlay V1.

Research question:
Does the real exhaustion classification
    signal 3D return >= 6%
    AND volume ratio >= 2.5x
contain information beyond simply reducing the size of a similar number
of randomly selected trades?

Design:
- Use the same 288-trade diagnostic case used in the exhaustion research.
- Real treatment: exhaustion trades get P&L contribution x0.50.
- Placebo: randomly select the same number of trades and apply x0.50.
- Repeat many times.
- Compare real treatment against the empirical placebo distribution.

Important:
This is a trade-level placebo test, not production-parity portfolio WFO.
It tests whether the exhaustion labels preferentially identify harmful trades.
A positive result is evidence for information content, not proof of deployable alpha.

Outputs:
research_results/exhaustion_placebo_test/
    placebo_summary.csv
    placebo_distribution.csv
    real_vs_placebo.png
"""

from __future__ import annotations

import argparse
from pathlib import Path
import numpy as np
import pandas as pd


DEFAULT_INPUT = Path(
    "research_results/bull_entry_regime_audit_v2/selected_case_feature_enriched.csv"
)
DEFAULT_OUTPUT = Path("research_results/exhaustion_placebo_test")
RETURN_THRESHOLD = 6.0
VOLUME_THRESHOLD = 2.5
SIZE_FACTOR = 0.50
DEFAULT_N = 5000
DEFAULT_SEED = 20260905


def profit_factor(x: pd.Series) -> float:
    x = pd.to_numeric(x, errors="coerce").dropna()
    gains = x[x > 0].sum()
    losses = abs(x[x < 0].sum())
    if losses == 0:
        return float("inf") if gains > 0 else np.nan
    return float(gains / losses)


def avg_return(x: pd.Series) -> float:
    return float(pd.to_numeric(x, errors="coerce").mean())


def adjusted_returns(base: np.ndarray, mask: np.ndarray) -> np.ndarray:
    out = base.copy()
    out[mask] *= SIZE_FACTOR
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    input_path = Path(args.input)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(input_path)

    required = [
        "signal_return_3d_pct",
        "signal_vol_ratio",
        "net_return_pct",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"Missing required columns: {missing}")

    # Do not silently mix this test with the 908-trade diagnostic sample.
    if len(df) != 288:
        raise ValueError(
            f"Expected the 288-trade bull_entry_regime_audit_v2 case, "
            f"got {len(df)} rows."
        )

    df["signal_return_3d_pct"] = pd.to_numeric(
        df["signal_return_3d_pct"], errors="coerce"
    )
    df["signal_vol_ratio"] = pd.to_numeric(
        df["signal_vol_ratio"], errors="coerce"
    )
    df["net_return_pct"] = pd.to_numeric(
        df["net_return_pct"], errors="coerce"
    )

    usable = df[
        df["signal_return_3d_pct"].notna()
        & df["signal_vol_ratio"].notna()
        & df["net_return_pct"].notna()
    ].copy()

    if len(usable) != len(df):
        raise ValueError(
            f"Expected all 288 rows to have required values; "
            f"usable={len(usable)}."
        )

    base = usable["net_return_pct"].to_numpy(dtype=float)
    n = len(base)

    exhaustion_mask = (
        (usable["signal_return_3d_pct"].to_numpy(dtype=float) >= RETURN_THRESHOLD)
        & (usable["signal_vol_ratio"].to_numpy(dtype=float) >= VOLUME_THRESHOLD)
    )

    k = int(exhaustion_mask.sum())
    real_adjusted = adjusted_returns(base, exhaustion_mask)

    baseline_avg = avg_return(pd.Series(base))
    real_avg = avg_return(pd.Series(real_adjusted))
    baseline_pf = profit_factor(pd.Series(base))
    real_pf = profit_factor(pd.Series(real_adjusted))

    # Use a deterministic RNG for reproducibility.
    rng = np.random.default_rng(args.seed)

    placebo_rows = []
    for i in range(args.n):
        mask = np.zeros(n, dtype=bool)
        selected = rng.choice(n, size=k, replace=False)
        mask[selected] = True

        adjusted = adjusted_returns(base, mask)

        placebo_rows.append({
            "iteration": i + 1,
            "avg_return_pct": avg_return(pd.Series(adjusted)),
            "pf": profit_factor(pd.Series(adjusted)),
            "compound_trade_proxy_pct": (
                (np.prod(1.0 + adjusted / 100.0) - 1.0) * 100.0
            ),
        })

    placebo = pd.DataFrame(placebo_rows)

    # Empirical one-sided percentile:
    # How often does a random placebo equal/exceed the real improvement?
    real_improvement = real_avg - baseline_avg
    placebo["avg_improvement_pp"] = placebo["avg_return_pct"] - baseline_avg

    percentile = float(
        (placebo["avg_improvement_pp"] < real_improvement).mean() * 100.0
    )
    p_value = float(
        (1.0 + (placebo["avg_improvement_pp"] >= real_improvement).sum())
        / (len(placebo) + 1.0)
    )

    real_compound = (
        (np.prod(1.0 + real_adjusted / 100.0) - 1.0) * 100.0
    )
    placebo_compound = placebo["compound_trade_proxy_pct"]

    compound_percentile = float(
        (placebo_compound < real_compound).mean() * 100.0
    )
    compound_p = float(
        (1.0 + (placebo_compound >= real_compound).sum())
        / (len(placebo_compound) + 1.0)
    )

    summary = pd.DataFrame([{
        "source": str(input_path),
        "n_trades": n,
        "exhaustion_trades": k,
        "exhaustion_pct": k / n * 100.0,
        "return_threshold_pct": RETURN_THRESHOLD,
        "volume_threshold_x": VOLUME_THRESHOLD,
        "size_factor": SIZE_FACTOR,
        "baseline_avg_return_pct": baseline_avg,
        "real_avg_return_pct": real_avg,
        "real_avg_improvement_pp": real_improvement,
        "baseline_pf": baseline_pf,
        "real_pf": real_pf,
        "placebo_mean_improvement_pp": placebo["avg_improvement_pp"].mean(),
        "placebo_median_improvement_pp": placebo["avg_improvement_pp"].median(),
        "placebo_p95_improvement_pp": placebo["avg_improvement_pp"].quantile(0.95),
        "placebo_p99_improvement_pp": placebo["avg_improvement_pp"].quantile(0.99),
        "real_improvement_percentile": percentile,
        "empirical_one_sided_p_value": p_value,
        "real_compound_trade_proxy_pct": real_compound,
        "placebo_mean_compound_trade_proxy_pct": placebo_compound.mean(),
        "real_compound_percentile": compound_percentile,
        "compound_empirical_one_sided_p_value": compound_p,
        "seed": args.seed,
        "iterations": args.n,
    }])

    summary.to_csv(
        output / "placebo_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    placebo.to_csv(
        output / "placebo_distribution.csv",
        index=False,
        encoding="utf-8-sig",
    )

    # Optional chart; no explicit colors/styles.
    try:
        import matplotlib.pyplot as plt

        plt.figure(figsize=(10, 6))
        plt.hist(placebo["avg_improvement_pp"], bins=50)
        plt.axvline(real_improvement, linewidth=2)
        plt.xlabel("Average return improvement vs baseline (pp)")
        plt.ylabel("Placebo count")
        plt.title("Exhaustion Overlay vs Random Placebo")
        plt.tight_layout()
        plt.savefig(output / "real_vs_placebo.png", dpi=160)
        plt.close()
    except Exception as exc:
        print(f"Chart skipped: {exc}")

    print("=" * 100)
    print("EXHAUSTION PLACEBO / PERMUTATION TEST")
    print("=" * 100)
    print(f"Trades:                 {n}")
    print(f"Real exhaustion trades: {k} ({k/n*100:.2f}%)")
    print(f"Baseline avg return:    {baseline_avg:+.4f}%")
    print(f"Real avg return:        {real_avg:+.4f}%")
    print(f"Real improvement:       {real_improvement:+.4f}pp")
    print(f"Real PF:                {real_pf:.4f}")
    print()
    print(f"Placebo mean delta:     {placebo['avg_improvement_pp'].mean():+.4f}pp")
    print(f"Placebo median delta:   {placebo['avg_improvement_pp'].median():+.4f}pp")
    print(f"Placebo P95 delta:      {placebo['avg_improvement_pp'].quantile(.95):+.4f}pp")
    print(f"Placebo P99 delta:      {placebo['avg_improvement_pp'].quantile(.99):+.4f}pp")
    print()
    print(f"Real percentile:        {percentile:.2f}%")
    print(f"Empirical p-value:      {p_value:.5f}")
    print()
    print(f"Real compound proxy:    {real_compound:+.4f}%")
    print(f"Compound percentile:    {compound_percentile:.2f}%")
    print(f"Compound p-value:       {compound_p:.5f}")
    print("=" * 100)
    print(f"Saved: {output.resolve()}")


if __name__ == "__main__":
    main()
