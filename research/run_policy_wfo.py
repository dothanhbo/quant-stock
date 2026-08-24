"""True walk-forward optimization across the controlled 2x2x2 policy grid."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from backtesting.engine import run_backtest
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.walk_forward import WalkForwardConfig, build_walk_forward_folds
from execution.signal_executor import PaperExecutionConfig
from research.run_ablation_matrix import build_case_kwargs, build_cases
from research.universes import HOLDOUT20_SYMBOLS


def finite(value, default=-math.inf) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2018-08-07")
    parser.add_argument("--end", default="2026-08-21")
    parser.add_argument("--train-months", type=int, default=24)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument("--hold", type=int, default=30)
    parser.add_argument("--min-trades", type=int, default=20)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--output", default="research_results/policy_wfo")
    args = parser.parse_args()

    paper = PaperExecutionConfig.from_env()
    parity = BacktestPaperParityConfig.from_paper_config(
        paper,
        sell_tax_rate=paper.sell_tax_rate,
    )
    symbols = list(HOLDOUT20_SYMBOLS) if args.symbols is None else [
        item.strip().upper() for item in args.symbols if item.strip()
    ]
    folds = build_walk_forward_folds(WalkForwardConfig(
        start_date=args.start,
        end_date=args.end,
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
    ))
    cases = build_cases()
    capital = parity.initial_cash
    selection_rows: list[dict] = []
    test_rows: list[dict] = []

    for fold in folds:
        candidates = []
        for case in cases:
            _, metrics, _ = run_backtest(
                **build_case_kwargs(
                    case=case,
                    paper=paper,
                    parity=parity,
                    symbols=symbols,
                    hold=args.hold,
                ),
                start_date=str(fold.train_start.date()),
                end_date=str(fold.train_end.date()),
                initial_capital=parity.initial_cash,
                verbose=False,
            )
            trades = int(metrics.get("total_trades", 0))
            eligible = trades >= args.min_trades
            row = {
                "fold": fold.fold,
                "case_id": case.case_id,
                "eligible": eligible,
                "train_trades": trades,
                "train_return_pct": finite(metrics.get("total_return_pct"), 0.0),
                "train_sharpe": finite(metrics.get("sharpe_ratio"), -999.0),
                "train_drawdown_pct": finite(metrics.get("max_drawdown_pct"), -999.0),
            }
            selection_rows.append(row)
            candidates.append((case, row))

        eligible = [item for item in candidates if item[1]["eligible"]]
        pool = eligible or candidates
        selected, selected_row = max(
            pool,
            key=lambda item: (
                item[1]["train_sharpe"],
                item[1]["train_return_pct"],
                item[1]["train_drawdown_pct"],
            ),
        )
        trades, metrics, _ = run_backtest(
            **build_case_kwargs(
                case=selected,
                paper=paper,
                parity=parity,
                symbols=symbols,
                hold=args.hold,
            ),
            start_date=str(fold.test_start.date()),
            end_date=str(fold.test_end.date()),
            initial_capital=capital,
            verbose=False,
        )
        final_equity = finite(metrics.get("final_equity"), capital)
        test_rows.append({
            **fold.to_dict(),
            "selected_case": selected.case_id,
            "selection_train_sharpe": selected_row["train_sharpe"],
            "selection_train_return_pct": selected_row["train_return_pct"],
            "test_initial_capital": capital,
            "test_final_equity": final_equity,
            "test_trades": len(trades),
            "test_return_pct": finite(metrics.get("total_return_pct"), 0.0),
            "test_sharpe": finite(metrics.get("sharpe_ratio"), 0.0),
            "test_drawdown_pct": finite(metrics.get("max_drawdown_pct"), 0.0),
        })
        capital = final_equity
        print(
            f"Fold {fold.fold}: selected={selected.case_id}, "
            f"test={test_rows[-1]['test_return_pct']:+.2f}%"
        )

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(selection_rows).to_csv(output / "train_selections.csv", index=False)
    folds_df = pd.DataFrame(test_rows)
    folds_df.to_csv(output / "test_folds.csv", index=False)
    summary = pd.DataFrame([{
        "optimization_performed": True,
        "folds": len(folds_df),
        "initial_capital": parity.initial_cash,
        "final_equity": capital,
        "walk_forward_return_pct": (capital / parity.initial_cash - 1) * 100,
        "profitable_fold_pct": (folds_df["test_return_pct"] > 0).mean() * 100,
        "median_test_return_pct": folds_df["test_return_pct"].median(),
        "worst_test_return_pct": folds_df["test_return_pct"].min(),
        "average_test_sharpe": folds_df["test_sharpe"].mean(),
    }])
    summary.to_csv(output / "summary.csv", index=False)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
