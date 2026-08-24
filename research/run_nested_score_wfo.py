from __future__ import annotations

import argparse
import math
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> bool:
        return False

from backtesting.current_logic import CurrentScannerExitModel
from backtesting.engine import run_backtest
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.position_sizers import FixedFractionSizer
from backtesting.walk_forward import (
    WalkForwardConfig,
    build_walk_forward_folds,
    calculate_chained_drawdown_pct,
)
from execution.signal_executor import PaperExecutionConfig
from research.run_entry_ablation import compound_return
from research.universes import HOLDOUT20_SYMBOLS
from strategy.donchian_breakout_entry import DonchianBreakoutEntryModel
from strategy.hybrid_trend_donchian_entry import (
    HybridTrendDonchianEntryModel,
)


DEFAULT_OUTPUT_DIR = Path(
    "research_results/nested_score_wfo_holdout20"
)
DEFAULT_SCORES = (75, 80, 85, 90)


def finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Nested walk-forward selection of min_hybrid_score only. "
            "Volume-only regime threshold, current exit and fixed20 sizing."
        )
    )
    parser.add_argument("--db", default="market.db")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--scores", nargs="+", type=int, default=DEFAULT_SCORES)
    parser.add_argument("--start", default="2018-08-07")
    parser.add_argument("--end", default="2026-08-21")
    parser.add_argument("--train-months", type=int, default=24)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument("--hold", type=int, default=30)
    parser.add_argument("--minimum-train-trades", type=int, default=20)
    parser.add_argument("--sell-tax-rate", type=float, default=0.001)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def validate_scores(scores: list[int] | tuple[int, ...]) -> list[int]:
    resolved = sorted(set(int(score) for score in scores))
    if not resolved:
        raise ValueError("Score grid không được rỗng.")
    if any(score < 0 or score > 100 for score in resolved):
        raise ValueError("Mỗi score phải nằm trong khoảng 0–100.")
    return resolved


def build_entry_model(min_hybrid_score: int):
    return HybridTrendDonchianEntryModel(
        mode="trend_context",
        min_hybrid_score=min_hybrid_score,
        donchian_model=DonchianBreakoutEntryModel(
            use_regime_thresholds=True,
            regime_threshold_fields={"min_volume_ratio"},
        ),
        use_regime_thresholds=True,
        require_hybrid_score=True,
    )


def build_backtest_kwargs(
    *,
    score: int,
    paper: PaperExecutionConfig,
    parity: BacktestPaperParityConfig,
    symbols: list[str],
    hold: int,
    db_path: str,
) -> dict:
    return {
        "db_path": db_path,
        "symbols": symbols,
        "max_holding_days": hold,
        "entry_model": build_entry_model(score),
        "exit_model": CurrentScannerExitModel(),
        "ranking_method": "signal_score",
        "position_sizer": FixedFractionSizer(position_size_pct=20.0),
        "buy_commission_pct": parity.commission_pct,
        "sell_commission_pct": parity.commission_pct,
        "sell_tax_pct": parity.sell_tax_pct,
        "buy_slippage_pct": parity.slippage_pct,
        "sell_slippage_pct": parity.slippage_pct,
        "max_positions": parity.maximum_open_positions,
        "lot_size": parity.lot_size,
        "max_new_positions_per_day": paper.maximum_orders_per_scan,
        "maximum_gross_exposure_pct": (
            parity.maximum_gross_exposure_pct
        ),
        "minimum_cash_buffer_pct": parity.minimum_cash_buffer_pct,
    }


def select_best_score(rows: list[dict]) -> tuple[dict, bool]:
    if not rows:
        raise ValueError("Không có train candidate.")
    eligible = [row for row in rows if bool(row["eligible"])]
    fallback_used = not eligible
    pool = eligible or rows
    selected = max(
        pool,
        key=lambda row: (
            finite(row["train_sharpe"], -999.0),
            finite(row["train_return_pct"], -999.0),
            finite(row["train_drawdown_pct"], -999.0),
            int(row["score"]),
        ),
    )
    return selected, fallback_used


def research_gates(summary: dict) -> dict:
    gates = {
        "gate_profitable_folds_at_least_half": (
            int(summary["profitable_folds"]) >= 6
        ),
        "gate_median_non_negative": (
            float(summary["median_test_return_pct"]) >= 0.0
        ),
        "gate_recent_3_folds_non_negative": (
            float(summary["recent_3_folds_return_pct"]) >= 0.0
        ),
        "gate_drawdown_within_15pct": (
            float(summary["chained_max_drawdown_pct"]) >= -15.0
        ),
    }
    return {**gates, "research_gate_passed": all(gates.values())}


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()
    scores = validate_scores(args.scores)
    if args.minimum_train_trades < 1:
        raise ValueError("minimum_train_trades phải từ 1 trở lên.")

    paper = PaperExecutionConfig.from_env()
    parity = BacktestPaperParityConfig.from_paper_config(
        paper,
        sell_tax_rate=args.sell_tax_rate,
    )
    symbols = (
        list(HOLDOUT20_SYMBOLS)
        if args.symbols is None
        else [
            value.strip().upper()
            for value in args.symbols
            if value.strip()
        ]
    )
    folds = build_walk_forward_folds(WalkForwardConfig(
        start_date=args.start,
        end_date=args.end,
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
    ))

    train_rows: list[dict] = []
    test_rows: list[dict] = []
    selected_scores: list[int] = []
    current_capital = float(parity.initial_cash)
    test_equity_curves: list[pd.DataFrame] = []

    for fold in folds:
        print("\n" + "=" * 96)
        print(
            f"NESTED SCORE WFO FOLD {fold.fold}: "
            f"train {fold.train_start.date()} → {fold.train_end.date()} | "
            f"test {fold.test_start.date()} → {fold.test_end.date()}"
        )
        print("=" * 96)
        fold_candidates: list[dict] = []

        for score in scores:
            _, metrics, _ = run_backtest(
                **build_backtest_kwargs(
                    score=score,
                    paper=paper,
                    parity=parity,
                    symbols=symbols,
                    hold=args.hold,
                    db_path=args.db,
                ),
                start_date=str(fold.train_start.date()),
                end_date=str(fold.train_end.date()),
                initial_capital=parity.initial_cash,
                verbose=False,
            )
            train_trades = int(metrics.get("total_trades", 0))
            row = {
                "fold": fold.fold,
                "train_start": fold.train_start.date(),
                "train_end": fold.train_end.date(),
                "test_start": fold.test_start.date(),
                "test_end": fold.test_end.date(),
                "score": score,
                "eligible": train_trades >= args.minimum_train_trades,
                "train_trades": train_trades,
                "train_return_pct": finite(metrics.get("total_return_pct")),
                "train_sharpe": finite(metrics.get("sharpe_ratio"), -999.0),
                "train_drawdown_pct": finite(
                    metrics.get("max_drawdown_pct"),
                    -999.0,
                ),
                "train_profit_factor": finite(
                    metrics.get("profit_factor")
                ),
            }
            fold_candidates.append(row)
            train_rows.append(row)

        selected, fallback_used = select_best_score(fold_candidates)
        selected_score = int(selected["score"])
        selected_scores.append(selected_score)
        trades, metrics, test_equity = run_backtest(
            **build_backtest_kwargs(
                score=selected_score,
                paper=paper,
                parity=parity,
                symbols=symbols,
                hold=args.hold,
                db_path=args.db,
            ),
            start_date=str(fold.test_start.date()),
            end_date=str(fold.test_end.date()),
            initial_capital=current_capital,
            verbose=False,
        )
        if isinstance(test_equity, pd.DataFrame):
            test_equity_curves.append(test_equity.copy())
        final_equity = finite(metrics.get("final_equity"), current_capital)
        test_return = finite(metrics.get("total_return_pct"))
        test_rows.append({
            **fold.to_dict(),
            "selected_score": selected_score,
            "selection_fallback_used": fallback_used,
            "selection_train_trades": selected["train_trades"],
            "selection_train_return_pct": selected["train_return_pct"],
            "selection_train_sharpe": selected["train_sharpe"],
            "selection_train_drawdown_pct": selected["train_drawdown_pct"],
            "test_initial_capital": current_capital,
            "test_final_equity": final_equity,
            "test_trades": len(trades),
            "test_return_pct": test_return,
            "test_sharpe": finite(metrics.get("sharpe_ratio")),
            "test_drawdown_pct": finite(metrics.get("max_drawdown_pct")),
            "test_profitable": test_return > 0,
        })
        current_capital = final_equity
        print(
            f"Selected score={selected_score} | "
            f"train Sharpe={selected['train_sharpe']:.3f} | "
            f"test={test_return:+.2f}% | trades={len(trades)}"
        )

    folds_df = pd.DataFrame(test_rows)
    train_df = pd.DataFrame(train_rows)
    returns = folds_df["test_return_pct"]
    counts = Counter(selected_scores)
    switches = sum(
        left != right
        for left, right in zip(selected_scores, selected_scores[1:])
    )
    summary = {
        "optimization_performed": True,
        "optimized_parameter": "min_hybrid_score",
        "score_grid": "|".join(str(score) for score in scores),
        "minimum_train_trades": args.minimum_train_trades,
        "folds": len(folds_df),
        "initial_capital": parity.initial_cash,
        "final_equity": current_capital,
        "walk_forward_return_pct": (
            current_capital / parity.initial_cash - 1.0
        ) * 100.0,
        "profitable_folds": int(folds_df["test_profitable"].sum()),
        "profitable_fold_pct": float(folds_df["test_profitable"].mean() * 100),
        "median_test_return_pct": float(returns.median()),
        "worst_test_return_pct": float(returns.min()),
        "recent_3_folds_return_pct": compound_return(returns.iloc[-3:]),
        "average_test_sharpe": float(folds_df["test_sharpe"].mean()),
        "worst_test_drawdown_pct": float(folds_df["test_drawdown_pct"].min()),
        "chained_max_drawdown_pct": (
            calculate_chained_drawdown_pct(test_equity_curves)
        ),
        "total_test_trades": int(folds_df["test_trades"].sum()),
        "selection_switches": switches,
        "unique_selected_scores": len(counts),
        "selection_counts": "|".join(
            f"{score}:{counts.get(score, 0)}" for score in scores
        ),
        "fallback_folds": int(folds_df["selection_fallback_used"].sum()),
    }
    summary.update(research_gates(summary))

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    train_path = output_dir / "nested_score_train_search.csv"
    folds_path = output_dir / "nested_score_folds.csv"
    summary_path = output_dir / "nested_score_summary.csv"
    train_df.to_csv(train_path, index=False, encoding="utf-8-sig")
    folds_df.to_csv(folds_path, index=False, encoding="utf-8-sig")
    pd.DataFrame([summary]).to_csv(
        summary_path,
        index=False,
        encoding="utf-8-sig",
    )

    print("\n" + "=" * 140)
    print("NESTED SCORE WFO SUMMARY")
    print("=" * 140)
    print(pd.DataFrame([summary]).to_string(index=False))
    print(f"\nSaved: {train_path}")
    print(f"Saved: {folds_path}")
    print(f"Saved: {summary_path}")


if __name__ == "__main__":
    main()
