from __future__ import annotations

import argparse
import copy
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtesting.engine import BacktestConfig, build_exit_model, generate_candidate_trades
from backtesting.exit_models import TrailingATRExitModel
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.portfolio_metrics import calculate_portfolio_metrics
from backtesting.portfolio_simulator import PortfolioSimulator
from backtesting.transaction_cost import TransactionCostConfig
from backtesting.walk_forward import WalkForwardConfig, build_walk_forward_folds
from config.trading_policy import TradingPolicy
from execution.signal_executor import PaperExecutionConfig
from research.universes import HOLDOUT20_SYMBOLS

EXIT_CASES = (
    ("fixed_atr_2_4", "fixed", 2.0, 4.0, None),
    ("fixed_atr_2_5", "fixed", 2.0, 5.0, None),
    ("fixed_atr_2_6", "fixed", 2.0, 6.0, None),
    ("fixed_atr_2_5_5", "fixed", 2.5, 5.0, None),
    ("fixed_atr_3_5", "fixed", 3.0, 5.0, None),
    ("fixed_atr_3_6", "fixed", 3.0, 6.0, None),
    ("fixed_atr_3_7", "fixed", 3.0, 7.0, None),
    ("fixed_atr_3_8", "fixed", 3.0, 8.0, None),
    ("fixed_atr_4_8", "fixed", 4.0, 8.0, None),
    ("trailing_current", "trailing", None, None, "current"),
)

def make_exit_model(case, policy):
    _, family, stop, target, trailing = case
    if family == "fixed":
        return build_exit_model(
            name="atr",
            stop_atr_multiplier=stop,
            target_atr_multiplier=target,
        )
    return TrailingATRExitModel(
        stop_atr_multiplier=policy.stop_atr_multiplier,
        target_atr_multiplier=policy.target_atr_multiplier,
        trailing_atr_multiplier=policy.trailing_atr_multiplier,
    )

def generate_candidates(symbols, db_path, policy, paper, start, end, case):
    cfg = BacktestConfig(
        max_holding_days=policy.maximum_holding_days,
        initial_capital=paper.initial_cash,
        buy_commission_pct=paper.commission_rate * 100,
        sell_commission_pct=paper.commission_rate * 100,
        sell_tax_pct=paper.sell_tax_rate * 100,
        buy_slippage_pct=paper.slippage_bps / 100,
        sell_slippage_pct=paper.slippage_bps / 100,
        ranking_method="signal_score",
    )
    entry_model = policy.build_entry_model()
    exit_model = make_exit_model(case, policy)
    candidates = []
    for symbol in symbols:
        candidates.extend(generate_candidate_trades(
            symbol=symbol, config=cfg, db_path=db_path, warmup_bars=60,
            verbose=False, start_date=start, end_date=end,
            entry_model=entry_model, exit_model=exit_model,
        ))
    return candidates

def simulate(candidates, paper, parity):
    costs = TransactionCostConfig(
        buy_commission_pct=paper.commission_rate * 100,
        sell_commission_pct=paper.commission_rate * 100,
        sell_tax_pct=paper.sell_tax_rate * 100,
        buy_slippage_pct=paper.slippage_bps / 100,
        sell_slippage_pct=paper.slippage_bps / 100,
    )
    sim = PortfolioSimulator(
        initial_cash=paper.initial_cash,
        position_size_pct=paper.fixed_fraction_pct,
        position_sizer=parity.build_position_sizer(),
        ranking_method="signal_score",
        transaction_cost_config=costs,
        max_positions=paper.maximum_open_positions,
        lot_size=paper.lot_size,
        max_new_positions_per_day=paper.maximum_orders_per_scan,
        maximum_gross_exposure_pct=paper.maximum_gross_exposure_pct,
        minimum_cash_buffer_pct=paper.minimum_cash_buffer_pct,
    )
    result = sim.simulate(copy.deepcopy(candidates))
    metrics = calculate_portfolio_metrics(
        result.equity_curve, final_equity=float(result.final_equity)
    )
    return result.executed_trades, metrics

def main():
    p = argparse.ArgumentParser(description="Causal production-parity Exit Matrix V4.")
    p.add_argument("--db-path", default="data/market.db")
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--start", default="2018-08-07")
    p.add_argument("--end", default="2026-08-06")
    p.add_argument("--train-months", type=int, default=24)
    p.add_argument("--test-months", type=int, default=6)
    p.add_argument("--step-months", type=int, default=6)
    p.add_argument("--cost-multipliers", nargs="+", type=float, default=[1.0, 1.5, 2.0])
    p.add_argument("--output", default="research_results/exit_matrix_v4_causal")
    args = p.parse_args()

    paper = PaperExecutionConfig.from_env()
    policy = TradingPolicy.from_env()
    parity = BacktestPaperParityConfig.from_paper_config(
        paper, sell_tax_rate=paper.sell_tax_rate
    )
    symbols = list(HOLDOUT20_SYMBOLS) if args.symbols is None else sorted(
        {s.strip().upper() for s in args.symbols if s.strip()}
    )
    folds = build_walk_forward_folds(WalkForwardConfig(
        start_date=args.start, end_date=args.end,
        train_months=args.train_months, test_months=args.test_months,
        step_months=args.step_months,
    ))
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    rows = []
    for fold in folds:
        print(f"\nFOLD {fold.fold}: {fold.test_start.date()} -> {fold.test_end.date()}")
        for case in EXIT_CASES:
            candidates = generate_candidates(
                symbols, args.db_path, policy, paper,
                str(fold.test_start.date()), str(fold.test_end.date()), case
            )
            print(f"  {case[0]} candidates={len(candidates)}")
            for mult in args.cost_multipliers:
                stressed = TransactionCostConfig(
                    buy_commission_pct=paper.commission_rate * 100 * mult,
                    sell_commission_pct=paper.commission_rate * 100 * mult,
                    sell_tax_pct=paper.sell_tax_rate * 100 * mult,
                    buy_slippage_pct=paper.slippage_bps / 100 * mult,
                    sell_slippage_pct=paper.slippage_bps / 100 * mult,
                )
                sim = PortfolioSimulator(
                    initial_cash=paper.initial_cash,
                    position_size_pct=paper.fixed_fraction_pct,
                    position_sizer=parity.build_position_sizer(),
                    ranking_method="signal_score",
                    transaction_cost_config=stressed,
                    max_positions=paper.maximum_open_positions,
                    lot_size=paper.lot_size,
                    max_new_positions_per_day=paper.maximum_orders_per_scan,
                    maximum_gross_exposure_pct=paper.maximum_gross_exposure_pct,
                    minimum_cash_buffer_pct=paper.minimum_cash_buffer_pct,
                )
                result = sim.simulate(copy.deepcopy(candidates))
                metrics = calculate_portfolio_metrics(
                    result.equity_curve, final_equity=float(result.final_equity)
                )
                rows.append({
                    "case_id": case[0],
                    "exit_family": case[1],
                    "stop_atr": case[2],
                    "target_atr": case[3],
                    "cost_multiplier": mult,
                    "fold": fold.fold,
                    "test_start": fold.test_start.date(),
                    "test_end": fold.test_end.date(),
                    "candidate_trades": len(candidates),
                    "executed_trades": len(result.executed_trades),
                    "fold_return_pct": float(metrics.get("total_return_pct", 0)),
                    "max_drawdown_pct": float(metrics.get("max_drawdown_pct", 0)),
                    "sharpe_ratio": float(metrics.get("sharpe_ratio", 0)),
                })

    df = pd.DataFrame(rows)
    df.to_csv(output / "exit_matrix_v4_fold_summary.csv", index=False, encoding="utf-8-sig")

    summary = []
    for (case_id, mult), g in df.groupby(["case_id", "cost_multiplier"], sort=False):
        r = g.fold_return_pct.to_numpy(float)
        summary.append({
            "case_id": case_id,
            "cost_multiplier": mult,
            "folds": len(r),
            "profitable_folds": int((r > 0).sum()),
            "mean_fold_return_pct": float(r.mean()),
            "median_fold_return_pct": float(np.median(r)),
            "compounded_oos_return_pct": float((np.prod(1 + r/100)-1)*100),
            "worst_fold_pct": float(r.min()),
            "best_fold_pct": float(r.max()),
            "total_executed_trades": int(g.executed_trades.sum()),
            "mean_max_drawdown_pct": float(g.max_drawdown_pct.mean()),
            "mean_sharpe": float(g.sharpe_ratio.mean()),
        })
    sdf = pd.DataFrame(summary)
    sdf.to_csv(output / "exit_matrix_v4_summary.csv", index=False, encoding="utf-8-sig")
    print("\n=== EXIT MATRIX V4 ===")
    print(sdf.to_string(index=False))
    print("\nSaved:", output.resolve())

if __name__ == "__main__":
    main()
