"""
Exit Failure Attribution Audit — Quant Bot

Research-only diagnostic.

Question:
Is the strategy losing because entries are bad, or because the exit
mechanism destroys otherwise-good entry edge?

The audit uses the 908-trade trade-level diagnostic dataset when available,
and the 288-trade feature-enriched case as a fallback for pre-entry features.

It compares:
1) LOSS_NEVER_WORKED vs LOSS_GAVE_BACK_PROFIT
2) WIN_CAPTURED vs WIN_GAVE_BACK_3PCT_PLUS
3) Exit reason
4) MFE / MAE / giveback / capture ratio
5) Holding duration
6) Signal score and key entry features
7) Fold-level and regime-level breakdowns when available

No new trading rule is produced by this script.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import numpy as np
import pandas as pd

DEFAULT_DIR = Path("research_results")
OUT = DEFAULT_DIR / "exit_failure_attribution"


def pct(x):
    return f"{x:.2f}%"


def safe_mean(s):
    return pd.to_numeric(s, errors="coerce").mean()


def safe_median(s):
    return pd.to_numeric(s, errors="coerce").median()


def pf(s):
    x = pd.to_numeric(s, errors="coerce").dropna()
    gains = x[x > 0].sum()
    losses = abs(x[x < 0].sum())
    if losses == 0:
        return np.inf if gains > 0 else np.nan
    return gains / losses


def find_trade_file(root: Path) -> Path | None:
    candidates = [
        root / "trade_level_diagnostics_holdout20" / "trade_diagnostics_trades.csv",
        root / "trade_level_diagnostics_holdout20" / "trade_diagnostics.csv",
    ]
    for p in candidates:
        if p.exists():
            return p

    for p in root.rglob("*.csv"):
        try:
            cols = set(pd.read_csv(p, nrows=2).columns)
            needed = {
                "trade_path_class",
                "mfe_pct",
                "mae_pct",
                "giveback_from_mfe_pct",
                "mfe_capture_ratio",
            }
            if needed.issubset(cols):
                return p
        except Exception:
            pass
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(DEFAULT_DIR))
    parser.add_argument("--output", default=str(OUT))
    args = parser.parse_args()

    root = Path(args.root)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    trade_file = find_trade_file(root)
    if trade_file is None:
        raise FileNotFoundError(
            "Could not find the 908-trade diagnostic CSV containing "
            "trade_path_class / MFE / MAE / giveback fields."
        )

    df = pd.read_csv(trade_file)

    required = [
        "trade_path_class",
        "mfe_pct",
        "mae_pct",
        "giveback_from_mfe_pct",
        "mfe_capture_ratio",
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(f"Missing required columns: {missing}")

    numeric_cols = [
        "return_pct",
        "net_return_pct",
        "mfe_pct",
        "mae_pct",
        "giveback_from_mfe_pct",
        "mfe_capture_ratio",
        "holding_days",
        "signal_score",
        "signal_distance_ema20_pct",
        "signal_return_3d_pct",
        "signal_vol_ratio",
        "signal_adx",
        "signal_rsi",
    ]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    print("=" * 110)
    print("EXIT FAILURE ATTRIBUTION AUDIT")
    print("=" * 110)
    print(f"Source: {trade_file}")
    print(f"Trades: {len(df)}")
    print()

    # 1. Core path-class table.
    path = (
        df.groupby("trade_path_class")
        .agg(
            trades=("trade_path_class", "size"),
            avg_return_pct=("net_return_pct", "mean"),
            median_return_pct=("net_return_pct", "median"),
            avg_mfe_pct=("mfe_pct", "mean"),
            avg_mae_pct=("mae_pct", "mean"),
            avg_giveback_pct=("giveback_from_mfe_pct", "mean"),
            avg_capture_ratio=("mfe_capture_ratio", "mean"),
            avg_holding_days=("holding_days", "mean") if "holding_days" in df else ("trade_path_class", "size"),
        )
        .reset_index()
    )
    path.to_csv(output / "path_class_summary.csv", index=False, encoding="utf-8-sig")
    print("PATH CLASS")
    print(path.to_string(index=False))
    print()

    # 2. Exit reason.
    if "exit_reason" in df.columns:
        exit_tbl = (
            df.groupby("exit_reason")
            .agg(
                trades=("exit_reason", "size"),
                win_rate=("net_return_pct", lambda s: (pd.to_numeric(s, errors="coerce") > 0).mean() * 100),
                avg_return_pct=("net_return_pct", "mean"),
                median_return_pct=("net_return_pct", "median"),
                avg_mfe_pct=("mfe_pct", "mean"),
                avg_mae_pct=("mae_pct", "mean"),
                avg_giveback_pct=("giveback_from_mfe_pct", "mean"),
                avg_capture_ratio=("mfe_capture_ratio", "mean"),
            )
            .reset_index()
        )
        exit_tbl.to_csv(output / "exit_reason_summary.csv", index=False, encoding="utf-8-sig")
        print("EXIT REASON")
        print(exit_tbl.to_string(index=False))
        print()

    # 3. Direct diagnostic: how much of losses had meaningful prior profit?
    losses = df[pd.to_numeric(df["net_return_pct"], errors="coerce") < 0].copy()
    wins = df[pd.to_numeric(df["net_return_pct"], errors="coerce") > 0].copy()

    loss_never = losses[losses["trade_path_class"] == "LOSS_NEVER_WORKED"]
    loss_gave = losses[losses["trade_path_class"] == "LOSS_GAVE_BACK_PROFIT"]

    loss_diagnostic = pd.DataFrame([
        {
            "group": "all_losses",
            "trades": len(losses),
            "share_of_all_losses_pct": 100,
            "avg_final_return_pct": safe_mean(losses["net_return_pct"]),
            "avg_mfe_pct": safe_mean(losses["mfe_pct"]),
            "avg_mae_pct": safe_mean(losses["mae_pct"]),
            "avg_giveback_pct": safe_mean(losses["giveback_from_mfe_pct"]),
        },
        {
            "group": "LOSS_NEVER_WORKED",
            "trades": len(loss_never),
            "share_of_all_losses_pct": len(loss_never) / len(losses) * 100,
            "avg_final_return_pct": safe_mean(loss_never["net_return_pct"]),
            "avg_mfe_pct": safe_mean(loss_never["mfe_pct"]),
            "avg_mae_pct": safe_mean(loss_never["mae_pct"]),
            "avg_giveback_pct": safe_mean(loss_never["giveback_from_mfe_pct"]),
        },
        {
            "group": "LOSS_GAVE_BACK_PROFIT",
            "trades": len(loss_gave),
            "share_of_all_losses_pct": len(loss_gave) / len(losses) * 100,
            "avg_final_return_pct": safe_mean(loss_gave["net_return_pct"]),
            "avg_mfe_pct": safe_mean(loss_gave["mfe_pct"]),
            "avg_mae_pct": safe_mean(loss_gave["mae_pct"]),
            "avg_giveback_pct": safe_mean(loss_gave["giveback_from_mfe_pct"]),
        },
    ])
    loss_diagnostic.to_csv(
        output / "loss_failure_decomposition.csv",
        index=False,
        encoding="utf-8-sig",
    )

    print("LOSS DECOMPOSITION")
    print(loss_diagnostic.to_string(index=False))
    print()

    # 4. Winner giveback.
    winner_diagnostic = pd.DataFrame([
        {
            "group": "all_wins",
            "trades": len(wins),
            "avg_final_return_pct": safe_mean(wins["net_return_pct"]),
            "avg_mfe_pct": safe_mean(wins["mfe_pct"]),
            "avg_giveback_pct": safe_mean(wins["giveback_from_mfe_pct"]),
            "avg_capture_ratio": safe_mean(wins["mfe_capture_ratio"]),
        }
    ])

    for group in ["WIN_CAPTURED", "WIN_GAVE_BACK_3PCT_PLUS"]:
        x = wins[wins["trade_path_class"] == group]
        winner_diagnostic = pd.concat([
            winner_diagnostic,
            pd.DataFrame([{
                "group": group,
                "trades": len(x),
                "avg_final_return_pct": safe_mean(x["net_return_pct"]),
                "avg_mfe_pct": safe_mean(x["mfe_pct"]),
                "avg_giveback_pct": safe_mean(x["giveback_from_mfe_pct"]),
                "avg_capture_ratio": safe_mean(x["mfe_capture_ratio"]),
            }])
        ], ignore_index=True)

    winner_diagnostic.to_csv(
        output / "winner_giveback_decomposition.csv",
        index=False,
        encoding="utf-8-sig",
    )

    print("WINNER GIVEBACK")
    print(winner_diagnostic.to_string(index=False))
    print()

    # 5. Holding buckets — diagnostic only, not an entry feature.
    if "holding_bucket" in df.columns:
        hold = (
            df.groupby("holding_bucket")
            .agg(
                trades=("holding_bucket", "size"),
                win_rate=("net_return_pct", lambda s: (pd.to_numeric(s, errors="coerce") > 0).mean() * 100),
                avg_return_pct=("net_return_pct", "mean"),
                avg_mfe_pct=("mfe_pct", "mean"),
                avg_giveback_pct=("giveback_from_mfe_pct", "mean"),
            )
            .reset_index()
        )
        hold.to_csv(output / "holding_bucket_summary.csv", index=False, encoding="utf-8-sig")
        print("HOLDING BUCKET")
        print(hold.to_string(index=False))
        print()

    # 6. Entry features by path class.
    feature_cols = [
        c for c in [
            "signal_score_bucket",
            "signal_rs_bucket",
            "signal_adx_bucket",
            "signal_volume_bucket",
            "signal_rsi_bucket",
            "signal_distance_ema20_bucket",
            "signal_return_3d_bucket",
            "market_return20_bucket",
            "market_slope_bucket",
            "breadth_ema50_bucket",
            "entry_gap_bucket",
        ] if c in df.columns
    ]

    feature_tables = []
    for col in feature_cols:
        tmp = (
            df.groupby(col)
            .agg(
                trades=(col, "size"),
                win_rate=("net_return_pct", lambda s: (pd.to_numeric(s, errors="coerce") > 0).mean() * 100),
                avg_return_pct=("net_return_pct", "mean"),
                avg_mfe_pct=("mfe_pct", "mean"),
                avg_giveback_pct=("giveback_from_mfe_pct", "mean"),
            )
            .reset_index()
        )
        tmp.insert(0, "feature", col)
        feature_tables.append(tmp)

    if feature_tables:
        features = pd.concat(feature_tables, ignore_index=True)
        features.to_csv(
            output / "entry_feature_diagnostics.csv",
            index=False,
            encoding="utf-8-sig",
        )

    # 7. Fold diagnostics.
    if "fold" in df.columns:
        fold = (
            df.groupby("fold")
            .agg(
                trades=("fold", "size"),
                win_rate=("net_return_pct", lambda s: (pd.to_numeric(s, errors="coerce") > 0).mean() * 100),
                avg_return_pct=("net_return_pct", "mean"),
                avg_mfe_pct=("mfe_pct", "mean"),
                avg_mae_pct=("mae_pct", "mean"),
                avg_giveback_pct=("giveback_from_mfe_pct", "mean"),
                avg_capture_ratio=("mfe_capture_ratio", "mean"),
            )
            .reset_index()
        )

        # Add failure mix.
        mix = pd.crosstab(df["fold"], df["trade_path_class"], normalize="index") * 100
        mix = mix.add_prefix("path_pct_").reset_index()

        fold = fold.merge(mix, on="fold", how="left")
        fold.to_csv(output / "fold_failure_diagnostics.csv", index=False, encoding="utf-8-sig")
        print("FOLD DIAGNOSTICS")
        print(fold.to_string(index=False))
        print()

    # 8. Regime diagnostics.
    for regime_col in ["market_regime"]:
        if regime_col in df.columns:
            regime = (
                df.groupby(regime_col)
                .agg(
                    trades=(regime_col, "size"),
                    win_rate=("net_return_pct", lambda s: (pd.to_numeric(s, errors="coerce") > 0).mean() * 100),
                    avg_return_pct=("net_return_pct", "mean"),
                    avg_mfe_pct=("mfe_pct", "mean"),
                    avg_mae_pct=("mae_pct", "mean"),
                    avg_giveback_pct=("giveback_from_mfe_pct", "mean"),
                    avg_capture_ratio=("mfe_capture_ratio", "mean"),
                )
                .reset_index()
            )
            regime.to_csv(output / "regime_exit_diagnostics.csv", index=False, encoding="utf-8-sig")
            print("REGIME")
            print(regime.to_string(index=False))
            print()

    # 9. A compact interpretation report.
    loss_gave_share = len(loss_gave) / len(losses) * 100 if len(losses) else np.nan
    winner_give_share = (
        len(wins[wins["trade_path_class"] == "WIN_GAVE_BACK_3PCT_PLUS"])
        / len(wins) * 100
        if len(wins) else np.nan
    )

    report = []
    report.append(f"Source: {trade_file}")
    report.append(f"Trades: {len(df)}")
    report.append("")
    report.append("Core decomposition:")
    report.append(f"- Losses: {len(losses)}")
    report.append(f"- LOSS_NEVER_WORKED: {len(loss_never)} ({len(loss_never)/len(losses)*100:.2f}% of losses)")
    report.append(f"- LOSS_GAVE_BACK_PROFIT: {len(loss_gave)} ({loss_gave_share:.2f}% of losses)")
    report.append(f"- Wins: {len(wins)}")
    report.append(f"- WIN_GAVE_BACK_3PCT_PLUS: {len(wins[wins['trade_path_class']=='WIN_GAVE_BACK_3PCT_PLUS'])} ({winner_give_share:.2f}% of wins)")
    report.append("")
    report.append("Interpretation rule:")
    report.append("- High LOSS_NEVER_WORKED share points toward entry/regime selection.")
    report.append("- High LOSS_GAVE_BACK_PROFIT or high winner giveback points toward exit design.")
    report.append("- Holding duration is outcome-conditioned and must not be used as an entry feature.")
    (output / "interpretation.txt").write_text("\n".join(report), encoding="utf-8")

    print("=" * 110)
    print("AUDIT COMPLETE")
    print(f"Outputs: {output.resolve()}")
    print("=" * 110)


if __name__ == "__main__":
    main()
