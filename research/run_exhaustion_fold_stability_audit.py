"""
Exhaustion Risk Overlay — Fold Stability Audit

Research-only. Reads the existing sensitivity-WFO folds.csv and compares
each exhaustion sizing variant against baseline fold-by-fold.

Expected input:
research_results/exhaustion_risk_adjustment_sensitivity_wfo/folds.csv

Variants:
baseline, size_x0.75, size_x0.50, size_x0.25, size_x0

Outputs:
- fold_comparison.csv
- stability_summary.csv
- recent_2024_2026.csv
- winners_by_fold.csv
"""

from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd


VARIANTS = ["baseline", "size_x0.75", "size_x0.50", "size_x0.25", "size_x0"]


def pick_column(df: pd.DataFrame, names: list[str]) -> str:
    for name in names:
        if name in df.columns:
            return name
    raise KeyError(f"None of these columns found: {names}")


def compound(values: pd.Series) -> float:
    x = pd.to_numeric(values, errors="coerce").dropna()
    if x.empty:
        return 0.0
    return float(((1.0 + x / 100.0).prod() - 1.0) * 100.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default="research_results/exhaustion_risk_adjustment_sensitivity_wfo/folds.csv",
    )
    parser.add_argument(
        "--output",
        default="research_results/exhaustion_risk_adjustment_sensitivity_wfo/fold_stability",
    )
    args = parser.parse_args()

    src = Path(args.input)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(src)

    variant_col = pick_column(df, ["variant"])
    fold_col = pick_column(df, ["fold"])
    return_col = pick_column(df, ["test_return_pct"])
    sharpe_col = pick_column(df, ["test_sharpe_ratio"])
    dd_col = pick_column(df, ["test_max_drawdown_pct"])
    trades_col = pick_column(df, ["test_trades"])
    profitable_col = "test_profitable" if "test_profitable" in df.columns else None
    test_start_col = "test_start" if "test_start" in df.columns else None
    test_end_col = "test_end" if "test_end" in df.columns else None

    df = df[df[variant_col].isin(VARIANTS)].copy()
    df[fold_col] = pd.to_numeric(df[fold_col], errors="coerce").astype("Int64")
    for c in [return_col, sharpe_col, dd_col, trades_col]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # Keep one row per variant/fold.
    df = df.sort_values([fold_col, variant_col])

    base = (
        df[df[variant_col] == "baseline"]
        [[fold_col, return_col, sharpe_col, dd_col, trades_col]]
        .rename(
            columns={
                return_col: "baseline_return_pct",
                sharpe_col: "baseline_sharpe",
                dd_col: "baseline_dd_pct",
                trades_col: "baseline_trades",
            }
        )
    )

    rows = []
    for variant in VARIANTS:
        cur = df[df[variant_col] == variant].copy()
        cur = cur.merge(base, on=fold_col, how="left")

        cur["delta_return_vs_baseline_pp"] = (
            cur[return_col] - cur["baseline_return_pct"]
        )
        cur["delta_sharpe_vs_baseline"] = (
            cur[sharpe_col] - cur["baseline_sharpe"]
        )
        # Less negative DD is an improvement.
        cur["delta_dd_vs_baseline_pp"] = (
            cur[dd_col] - cur["baseline_dd_pct"]
        )
        cur["return_better_than_baseline"] = (
            cur["delta_return_vs_baseline_pp"] > 1e-12
        )
        cur["dd_better_than_baseline"] = (
            cur["delta_dd_vs_baseline_pp"] > 1e-12
        )

        if profitable_col:
            cur["profitable"] = cur[profitable_col].astype(str).str.lower().eq("true")
        else:
            cur["profitable"] = cur[return_col] > 0

        cur["variant"] = variant
        rows.append(cur)

    detail = pd.concat(rows, ignore_index=True)

    # Recent regime: test periods whose start is in 2024 or later.
    if test_start_col:
        detail["_test_start"] = pd.to_datetime(detail[test_start_col], errors="coerce")
        recent = detail[detail["_test_start"] >= pd.Timestamp("2024-01-01")].copy()
    else:
        # Fall back to the latest 5 folds if dates are unavailable.
        latest = sorted(detail[fold_col].dropna().unique())[-5:]
        recent = detail[detail[fold_col].isin(latest)].copy()

    # Fold-by-fold comparison matrix.
    pivot = (
        detail.pivot_table(
            index=fold_col,
            columns=variant_col,
            values=return_col,
            aggfunc="first",
        )
        .reset_index()
    )

    for v in VARIANTS:
        if v not in pivot.columns:
            pivot[v] = float("nan")

    pivot["best_variant"] = pivot[VARIANTS].idxmax(axis=1)
    pivot["best_return_pct"] = pivot[VARIANTS].max(axis=1)
    for v in VARIANTS[1:]:
        pivot[f"{v}_delta_vs_baseline_pp"] = (
            pivot[v] - pivot["baseline"]
        )

    pivot.to_csv(out / "fold_comparison.csv", index=False)

    # Stability summary.
    summary_rows = []
    for variant in VARIANTS:
        x = detail[detail[variant_col] == variant].copy()

        summary_rows.append(
            {
                "variant": variant,
                "folds": len(x),
                "profitable_folds": int(x["profitable"].sum()),
                "profitable_fold_pct": float(x["profitable"].mean() * 100.0),
                "mean_fold_return_pct": float(x[return_col].mean()),
                "median_fold_return_pct": float(x[return_col].median()),
                "compound_wf_return_pct": compound(x[return_col]),
                "mean_sharpe": float(x[sharpe_col].mean()),
                "worst_fold_return_pct": float(x[return_col].min()),
                "best_fold_return_pct": float(x[return_col].max()),
                "mean_dd_pct": float(x[dd_col].mean()),
                "worst_dd_pct": float(x[dd_col].min()),
                "total_test_trades": int(x[trades_col].sum()),
                "folds_beating_baseline": (
                    int(x["return_better_than_baseline"].sum())
                    if variant != "baseline"
                    else 0
                ),
                "folds_improving_dd": (
                    int(x["dd_better_than_baseline"].sum())
                    if variant != "baseline"
                    else 0
                ),
                "mean_return_delta_vs_baseline_pp": (
                    float(x["delta_return_vs_baseline_pp"].mean())
                    if variant != "baseline"
                    else 0.0
                ),
                "median_return_delta_vs_baseline_pp": (
                    float(x["delta_return_vs_baseline_pp"].median())
                    if variant != "baseline"
                    else 0.0
                ),
                "recent_return_compound_pct": compound(
                    recent.loc[recent[variant_col] == variant, return_col]
                ),
            }
        )

    summary = pd.DataFrame(summary_rows)
    summary.to_csv(out / "stability_summary.csv", index=False)

    recent.sort_values([fold_col, variant_col]).to_csv(
        out / "recent_2024_2026.csv", index=False
    )

    winners = pivot[
        [fold_col, "best_variant", "best_return_pct", *VARIANTS]
    ].copy()
    winners.to_csv(out / "winners_by_fold.csv", index=False)

    print("=" * 100)
    print("EXHAUSTION OVERLAY — FOLD STABILITY")
    print("=" * 100)
    print(summary.to_string(index=False))

    print("\n" + "=" * 100)
    print("FOLD-BY-FOLD RETURN")
    print("=" * 100)
    print(pivot.to_string(index=False))

    print("\n" + "=" * 100)
    print("RECENT 2024-2026")
    print("=" * 100)
    recent_summary = (
        recent.groupby(variant_col)
        .agg(
            folds=(fold_col, "count"),
            mean_return=(return_col, "mean"),
            median_return=(return_col, "median"),
            mean_sharpe=(sharpe_col, "mean"),
            worst_return=(return_col, "min"),
            profitable_folds=("profitable", "sum"),
        )
        .reset_index()
    )
    print(recent_summary.to_string(index=False))

    print(f"\nSaved: {out.resolve()}")


if __name__ == "__main__":
    main()
