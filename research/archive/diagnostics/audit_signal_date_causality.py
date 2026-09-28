"""Fail-closed audit for signal-time vs execution-time market-state features."""
from __future__ import annotations

import argparse
from pathlib import Path
import pandas as pd

from backtesting.engine import BacktestConfig, generate_candidate_trades


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", default="research_results/signal_date_causality_audit.csv")
    args = parser.parse_args()

    config = BacktestConfig()

    rows = []
    failures = []
    for symbol in args.symbols:
        trades = generate_candidate_trades(
            symbol=symbol,
            config=config,
            db_path=args.db_path,
            start_date=args.start,
            end_date=args.end,
        )
        for t in trades:
            signal = getattr(t, "signal_date", None)
            entry = getattr(t, "entry_date", None)
            ok = signal is not None and entry is not None and pd.Timestamp(signal) < pd.Timestamp(entry)
            rows.append({
                "symbol": symbol,
                "signal_date": signal,
                "entry_date": entry,
                "causal": ok,
                "delta_days": (pd.Timestamp(entry) - pd.Timestamp(signal)).days if signal is not None and entry is not None else None,
            })
            if not ok:
                failures.append(rows[-1])

    df = pd.DataFrame(rows)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    print(f"candidates={len(df)} causal={int(df.causal.sum()) if not df.empty else 0} failures={len(failures)}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
