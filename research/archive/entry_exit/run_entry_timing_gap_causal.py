from __future__ import annotations

import argparse
import copy
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from backtesting.engine import BacktestConfig, generate_candidate_trades, load_price_data
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.portfolio_metrics import calculate_portfolio_metrics
from backtesting.portfolio_simulator import PortfolioSimulator
from backtesting.transaction_cost import TransactionCostConfig
from backtesting.walk_forward import WalkForwardConfig, build_walk_forward_folds
from config.trading_policy import TradingPolicy
from execution.signal_executor import PaperExecutionConfig
from research.universes import HOLDOUT20_SYMBOLS

# Phase 1: causal gap-aware entry. The existing candidate already executes
# at the next session open. We only reject a candidate when that T+1 open
# gaps too far above the T signal close. No T+1 information is used to
# manufacture a better entry price; the actual candidate entry price is kept.
GAP_THRESHOLDS = (None, 0.5, 1.0, 1.5, 2.0, 3.0)

def build_candidates(symbols, db_path, policy, paper, start, end):
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
    entry = policy.build_entry_model()
    # Keep current production trailing exit fixed for the entry experiment.
    from backtesting.exit_models import TrailingATRExitModel
    exit_model = TrailingATRExitModel(
        stop_atr_multiplier=policy.stop_atr_multiplier,
        target_atr_multiplier=policy.target_atr_multiplier,
        trailing_atr_multiplier=policy.trailing_atr_multiplier,
    )
    out = []
    for symbol in symbols:
        out.extend(generate_candidate_trades(
            symbol=symbol, config=cfg, db_path=db_path, warmup_bars=60,
            verbose=False, start_date=start, end_date=end,
            entry_model=entry, exit_model=exit_model,
        ))
    return out

def signal_close_map(symbols, db_path):
    cache = {}
    for symbol in symbols:
        df = load_price_data(symbol, db_path)
        if df.empty:
            continue
        df["time"] = pd.to_datetime(df["time"]).dt.normalize()
        cache[symbol] = df.set_index("time")["close"].astype(float)
    return cache

def gap_pct(candidate, prices):
    sd = pd.Timestamp(candidate.signal_date).normalize()
    close = prices.get(candidate.symbol, pd.Series(dtype=float)).get(sd, np.nan)
    entry = float(candidate.entry_price)
    if not np.isfinite(close) or close <= 0:
        return np.nan
    return (entry / close - 1.0) * 100.0

def simulate(candidates, paper, parity, cost_mult):
    costs = TransactionCostConfig(
        buy_commission_pct=paper.commission_rate * 100 * cost_mult,
        sell_commission_pct=paper.commission_rate * 100 * cost_mult,
        sell_tax_pct=paper.sell_tax_rate * 100 * cost_mult,
        buy_slippage_pct=paper.slippage_bps / 100 * cost_mult,
        sell_slippage_pct=paper.slippage_bps / 100 * cost_mult,
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
    return result, metrics

def main():
    p = argparse.ArgumentParser(description="Causal gap-aware Entry Timing Phase 1.")
    p.add_argument("--db-path", default="data/market.db")
    p.add_argument("--symbols", nargs="*", default=None)
    p.add_argument("--start", default="2018-08-07")
    p.add_argument("--end", default="2026-08-06")
    p.add_argument("--train-months", type=int, default=24)
    p.add_argument("--test-months", type=int, default=6)
    p.add_argument("--step-months", type=int, default=6)
    p.add_argument("--cost-multipliers", nargs="+", type=float, default=[1.0, 1.5, 2.0])
    p.add_argument("--output", default="research_results/entry_timing_gap_causal")
    args = p.parse_args()

    paper = PaperExecutionConfig.from_env()
    policy = TradingPolicy.from_env()
    parity = BacktestPaperParityConfig.from_paper_config(
        paper, sell_tax_rate=paper.sell_tax_rate
    )
    symbols = list(HOLDOUT20_SYMBOLS) if args.symbols is None else sorted(
        {s.strip().upper() for s in args.symbols if s.strip()}
    )
    prices = signal_close_map(symbols, args.db_path)
    folds = build_walk_forward_folds(WalkForwardConfig(
        start_date=args.start, end_date=args.end,
        train_months=args.train_months, test_months=args.test_months,
        step_months=args.step_months,
    ))
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    rows = []
    trade_rows = []

    for fold in folds:
        candidates = build_candidates(
            symbols, args.db_path, policy, paper,
            str(fold.test_start.date()), str(fold.test_end.date())
        )
        gap_by_id = {id(c): gap_pct(c, prices) for c in candidates}

        print(f"\nFOLD {fold.fold}: candidates={len(candidates)}")
        for threshold in GAP_THRESHOLDS:
            if threshold is None:
                selected = candidates
                label = "no_gap_filter"
            else:
                selected = [
                    c for c in candidates
                    if np.isfinite(gap_by_id.get(id(c), np.nan))
                    and gap_by_id.get(id(c)) <= threshold
                ]
                label = f"max_gap_{threshold:g}pct"

            for mult in args.cost_multipliers:
                result, metrics = simulate(selected, paper, parity, mult)
                rows.append({
                    "policy": label,
                    "max_gap_pct": threshold,
                    "cost_multiplier": mult,
                    "fold": fold.fold,
                    "test_start": fold.test_start.date(),
                    "test_end": fold.test_end.date(),
                    "candidate_trades": len(candidates),
                    "selected_candidates": len(selected),
                    "executed_trades": len(result.executed_trades),
                    "fold_return_pct": float(metrics.get("total_return_pct", 0)),
                    "max_drawdown_pct": float(metrics.get("max_drawdown_pct", 0)),
                    "sharpe_ratio": float(metrics.get("sharpe_ratio", 0)),
                })
                for t in result.executed_trades:
                    trade_rows.append({
                        "policy": label,
                        "cost_multiplier": mult,
                        "fold": fold.fold,
                        "symbol": t.symbol,
                        "signal_date": t.signal_date,
                        "entry_date": t.entry_date,
                        "gap_pct": gap_pct(t, prices),
                        "net_return_pct": getattr(t, "net_return_pct", np.nan),
                    })

    df = pd.DataFrame(rows)
    td = pd.DataFrame(trade_rows)
    df.to_csv(output / "entry_timing_gap_fold_summary.csv", index=False, encoding="utf-8-sig")
    td.to_csv(output / "entry_timing_gap_trade_level.csv", index=False, encoding="utf-8-sig")

    summary = []
    for (policy_name, mult), g in df.groupby(["policy", "cost_multiplier"], sort=False):
        r = g.fold_return_pct.to_numpy(float)
        summary.append({
            "policy": policy_name,
            "cost_multiplier": mult,
            "folds": len(r),
            "profitable_folds": int((r > 0).sum()),
            "mean_fold_return_pct": float(r.mean()),
            "median_fold_return_pct": float(np.median(r)),
            "compounded_oos_return_pct": float((np.prod(1+r/100)-1)*100),
            "worst_fold_pct": float(r.min()),
            "best_fold_pct": float(r.max()),
            "total_executed_trades": int(g.executed_trades.sum()),
            "mean_max_drawdown_pct": float(g.max_drawdown_pct.mean()),
            "mean_sharpe": float(g.sharpe_ratio.mean()),
        })
    sdf = pd.DataFrame(summary)
    sdf.to_csv(output / "entry_timing_gap_summary.csv", index=False, encoding="utf-8-sig")
    print("\n=== ENTRY GAP TIMING PHASE 1 ===")
    print(sdf.to_string(index=False))
    print("\nSaved:", output.resolve())

if __name__ == "__main__":
    main()
