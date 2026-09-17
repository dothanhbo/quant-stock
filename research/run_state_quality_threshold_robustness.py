from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run candidate State+Quality WFO across multiple frozen quality thresholds."
    )
    parser.add_argument("--market-health", required=True)
    parser.add_argument("--db-path", default="data/market.db")
    parser.add_argument("--start", default="2018-08-07")
    parser.add_argument("--end", default="2026-08-21")
    parser.add_argument("--train-months", type=int, default=24)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument(
        "--thresholds",
        default="0.60,0.65,0.70,0.75,0.80,0.85",
    )
    parser.add_argument(
        "--script",
        default="research/run_state_quality_candidate_wfo_v4.py",
        help="Candidate-level WFO script to invoke.",
    )
    parser.add_argument(
        "--output",
        default="research_results/state_quality_threshold_robustness",
    )
    args = parser.parse_args()

    thresholds = [
        float(x.strip())
        for x in args.thresholds.split(",")
        if x.strip()
    ]
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)

    summaries = []

    for threshold in thresholds:
        tag = f"t{threshold:.2f}"
        out_dir = root / tag

        cmd = [
            sys.executable,
            args.script,
            "--market-health", args.market_health,
            "--db-path", args.db_path,
            "--start", args.start,
            "--end", args.end,
            "--train-months", str(args.train_months),
            "--test-months", str(args.test_months),
            "--step-months", str(args.step_months),
            "--quality-threshold", str(threshold),
            "--output", str(out_dir),
        ]

        print("\n" + "=" * 90)
        print(f"THRESHOLD {threshold:.2f}")
        print("=" * 90)

        result = subprocess.run(cmd)
        if result.returncode != 0:
            raise SystemExit(
                f"Threshold {threshold:.2f} failed with exit code {result.returncode}"
            )

        summary_file = out_dir / "policy_summary.csv"
        fold_file = out_dir / "fold_policy_summary.csv"

        if summary_file.exists():
            df = pd.read_csv(summary_file)
            df["threshold_run"] = threshold
            summaries.append(df)

        # Keep the per-fold result for later stability analysis.
        if fold_file.exists():
            fold = pd.read_csv(fold_file)
            fold["threshold_run"] = threshold
            fold.to_csv(
                root / f"fold_policy_summary_t{threshold:.2f}.csv",
                index=False,
            )

    if summaries:
        combined = pd.concat(summaries, ignore_index=True)
        combined.to_csv(root / "threshold_robustness_summary.csv", index=False)

        print("\n" + "=" * 90)
        print("THRESHOLD ROBUSTNESS SUMMARY")
        print("=" * 90)
        print(combined.to_string(index=False))

    print(f"\nSaved under: {root}")


if __name__ == "__main__":
    main()
