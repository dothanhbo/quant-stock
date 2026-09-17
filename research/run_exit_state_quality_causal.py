from __future__ import annotations

"""
Causal portfolio WFO: robust fixed-ATR exits x Market State/Quality gate.

Purpose:
- Test whether the newly discovered robust fixed-ATR exit region remains useful
  after applying the causal Market State + composite Quality gate.
- Compare ungated robust-exit baselines against Q0.65/Q0.70 gates.
- Keep production T+1 Open entry, production sizing/ranking/portfolio limits,
  signal_date causality, and transaction-cost parity.
- Stress 1x / 1.5x / 2x costs.

This is a research confirmation script. It does NOT modify production config.
"""

import argparse
import copy
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtesting.engine import BacktestConfig, build_exit_model, generate_candidate_trades
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.portfolio_metrics import calculate_portfolio_metrics
from backtesting.portfolio_simulator import PortfolioSimulator
from backtesting.transaction_cost import TransactionCostConfig
from backtesting.walk_forward import WalkForwardConfig, build_walk_forward_folds
from config.trading_policy import TradingPolicy
from execution.signal_executor import PaperExecutionConfig

QUALITY_FEATURES = ("signal_score", "relative_strength", "adx", "volume_ratio")
EXIT_CASES = (
    ("fixed_atr_4_8", 4.0, 8.0),
    ("fixed_atr_4_5_8", 4.5, 8.0),
    ("fixed_atr_4_5_9", 4.5, 9.0),
)
THRESHOLDS = (0.65, 0.70)
STATES = ("HEALTHY_BULL", "FRAGILE_BULL", "DIVERGENT_BULL", "RECOVERY", "NEUTRAL", "BEAR")


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if np.isfinite(x) else default


@dataclass(frozen=True)
class FrozenQualityModel:
    refs: dict[str, pd.Series]

    @classmethod
    def fit(cls, candidates: list[Any]) -> "FrozenQualityModel":
        rows = [{
            "signal_score": getattr(t, "signal_score", None),
            "relative_strength": getattr(t, "relative_strength", None),
            "adx": getattr(t, "adx", None),
            "volume_ratio": getattr(t, "volume_ratio", None),
        } for t in candidates]
        df = pd.DataFrame(rows)
        refs = {}
        for name in QUALITY_FEATURES:
            refs[name] = pd.to_numeric(df.get(name, pd.Series(dtype=float)), errors="coerce").dropna()
        return cls(refs)

    def score(self, values: dict[str, Any]) -> float:
        scores = []
        for name in QUALITY_FEATURES:
            x = _num(values.get(name))
            ref = self.refs[name]
            if x is None or ref.empty:
                scores.append(0.5)
            else:
                scores.append(float((ref <= x).mean()))
        return float(np.mean(scores))


def load_market_health(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "time" not in df.columns:
        raise ValueError("market-health must contain 'time'")
    required = {"breadth_ema50_pct", "breadth_ema50_change_10d"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"market-health missing: {sorted(missing)}")
    df["time"] = pd.to_datetime(df["time"], errors="coerce").dt.normalize()
    return (df.dropna(subset=["time"]).sort_values("time")
              .drop_duplicates("time", keep="last").set_index("time"))


def classify_market_state(signal_date: Any, regime: str, health: pd.DataFrame) -> str:
    d = pd.Timestamp(signal_date).normalize()
    regime = str(regime).upper()
    if d not in health.index:
        return "NEUTRAL"
    row = health.loc[d]
    breadth = _num(row.get("breadth_ema50_pct"))
    change = _num(row.get("breadth_ema50_change_10d"))
    if regime == "BEAR":
        return "BEAR"
    if regime == "BULL":
        if breadth is not None and change is not None:
            if breadth < 50 and change < 0:
                return "DIVERGENT_BULL"
            if breadth >= 70 and change >= 0:
                return "HEALTHY_BULL"
        return "FRAGILE_BULL"
    if regime == "SIDEWAY":
        if breadth is not None and change is not None and breadth >= 60 and change > 0:
            return "RECOVERY"
        return "NEUTRAL"
    return "NEUTRAL"


class StateQualityGateStrategy:
    def __init__(self, base_strategy, quality_model, market_health, threshold):
        self.base_strategy = base_strategy
        self.quality_model = quality_model
        self.market_health = market_health
        self.threshold = float(threshold)

    @property
    def name(self):
        return self.base_strategy.name + f"__state_quality_{self.threshold:.2f}"

    def evaluate(self, latest, relative_strength, market_config):
        decision = self.base_strategy.evaluate(latest, relative_strength, market_config)
        if decision.get("status") != "PASSED":
            return decision
        score = _num(decision.get("score"), 0.0)
        adx = _num(decision.get("adx"))
        if adx is None:
            adx = _num(latest.get("ADX14"), 0.0)
        vol = _num(decision.get("volume_ratio"))
        if vol is None:
            vol = _num(latest.get("Vol_Ratio"), 0.0)
        rs = _num(relative_strength, 0.0)
        quality = self.quality_model.score({
            "signal_score": score,
            "relative_strength": rs,
            "adx": adx,
            "volume_ratio": vol,
        })
        state = classify_market_state(
            latest.get("time"), decision.get("regime", market_config.get("regime", "UNKNOWN")), self.market_health
        )
        allowed = state != "DIVERGENT_BULL" and quality >= self.threshold
        result = dict(decision)
        result["market_state"] = state
        result["quality_composite"] = quality
        if not allowed:
            result["status"] = "FILTERED"
            result["reason"] = f"state_quality:{state}:q={quality:.4f}"
        return result


def build_parity(paper):
    values = {
        "initial_cash": paper.initial_cash,
        "position_sizer": paper.position_sizer,
        "risk_per_trade_pct": paper.risk_per_trade_pct,
        "atr_stop_multiplier": paper.atr_stop_multiplier,
        "fixed_fraction_pct": paper.fixed_fraction_pct,
        "lot_size": paper.lot_size,
        "commission_rate": paper.commission_rate,
        "slippage_bps": paper.slippage_bps,
        "maximum_position_pct": paper.maximum_position_pct,
        "maximum_gross_exposure_pct": paper.maximum_gross_exposure_pct,
        "maximum_open_positions": paper.maximum_open_positions,
        "maximum_daily_loss_pct": paper.maximum_daily_loss_pct,
        "minimum_cash_buffer_pct": paper.minimum_cash_buffer_pct,
        "maximum_order_adtv20_pct": paper.maximum_order_adtv20_pct,
        "sell_tax_rate": paper.sell_tax_rate,
    }
    params = inspect.signature(BacktestPaperParityConfig).parameters
    return BacktestPaperParityConfig(**{k: v for k, v in values.items() if k in params})


def make_config(paper, policy, parity, entry_model, exit_model):
    values = {
        "max_holding_days": policy.maximum_holding_days,
        "initial_capital": paper.initial_cash,
        "buy_commission_pct": paper.commission_rate * 100,
        "sell_commission_pct": paper.commission_rate * 100,
        "sell_tax_pct": paper.sell_tax_rate * 100,
        "buy_slippage_pct": paper.slippage_bps / 100,
        "sell_slippage_pct": paper.slippage_bps / 100,
        "ranking_method": "signal_score",
    }
    params = inspect.signature(BacktestConfig).parameters
    return BacktestConfig(**{k: v for k, v in values.items() if k in params})


def generate_candidates(symbols, db_path, paper, policy, parity, start, end, stop, target, entry_model):
    exit_model = build_exit_model(name="atr", stop_atr_multiplier=stop, target_atr_multiplier=target)
    cfg = make_config(paper, policy, parity, entry_model, exit_model)
    out = []
    for symbol in symbols:
        try:
            out.extend(generate_candidate_trades(
                symbol=symbol, config=cfg, db_path=db_path, warmup_bars=60,
                verbose=False, start_date=str(start.date()), end_date=str(end.date()),
                entry_model=entry_model, exit_model=exit_model,
            ))
        except Exception as exc:
            print(f"[WARN] {symbol}: {exc}")
    return out


def simulate(candidates, paper, parity, cost_multiplier, initial_capital):
    costs = TransactionCostConfig(
        buy_commission_pct=paper.commission_rate * 100 * cost_multiplier,
        sell_commission_pct=paper.commission_rate * 100 * cost_multiplier,
        sell_tax_pct=paper.sell_tax_rate * 100 * cost_multiplier,
        buy_slippage_pct=paper.slippage_bps / 100 * cost_multiplier,
        sell_slippage_pct=paper.slippage_bps / 100 * cost_multiplier,
    )
    sim = PortfolioSimulator(
        initial_cash=initial_capital,
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
    metrics = calculate_portfolio_metrics(result.equity_curve, final_equity=float(result.final_equity))
    return result, metrics


def compound(xs):
    x = np.asarray(xs, dtype=float)
    return float((np.prod(1 + x / 100) - 1) * 100) if len(x) else 0.0


def main():
    p = argparse.ArgumentParser(description="Causal WFO: robust fixed ATR exit x market state/quality gate")
    p.add_argument("--db-path", default="data/market.db")
    p.add_argument("--market-health", default="research_results/market_state_diagnostic_pit/market_health_pit_daily.csv")
    p.add_argument("--start", default="2018-08-07")
    p.add_argument("--end", default="2026-08-06")
    p.add_argument("--train-months", type=int, default=24)
    p.add_argument("--test-months", type=int, default=6)
    p.add_argument("--step-months", type=int, default=6)
    p.add_argument("--cost-multipliers", nargs="+", type=float, default=[1.0, 1.5, 2.0])
    p.add_argument("--thresholds", nargs="+", type=float, default=list(THRESHOLDS))
    p.add_argument("--output", default="research_results/exit_state_quality_causal")
    args = p.parse_args()

    health = load_market_health(args.market_health)
    paper = PaperExecutionConfig.from_env()
    policy = TradingPolicy.from_env()
    parity = build_parity(paper)

    import sqlite3
    with sqlite3.connect(args.db_path) as con:
        rows = con.execute("SELECT DISTINCT symbol FROM prices WHERE symbol <> 'VNINDEX' ORDER BY symbol").fetchall()
    symbols = [str(r[0]).upper() for r in rows if r and r[0]]
    if not symbols:
        raise RuntimeError("No non-VNINDEX symbols found")
    print(f"Universe: FULL DB ({len(symbols)} symbols)")

    folds = build_walk_forward_folds(WalkForwardConfig(
        start_date=args.start, end_date=args.end,
        train_months=args.train_months, test_months=args.test_months, step_months=args.step_months,
    ))
    outdir = Path(args.output); outdir.mkdir(parents=True, exist_ok=True)

    fold_rows, trade_rows = [], []
    # Separate chained capital per arm.
    arms = [("baseline", e, None) for e, _, _ in EXIT_CASES]
    for e, _, _ in EXIT_CASES:
        for q in args.thresholds:
            arms.append((f"q{q:.2f}", e, q))
    capital = {(arm[0], arm[1], arm[2], float(mult)): float(parity.initial_cash) for arm in arms for mult in args.cost_multipliers}

    for fold in folds:
        print(f"\nFOLD {fold.fold}: {fold.test_start.date()} -> {fold.test_end.date()}")
        # Train pool is generated once per exit because ATR exit affects candidate path.
        train_models = {}
        test_models = {}
        train_candidates_by_exit = {}
        for case_id, stop, target in EXIT_CASES:
            train_entry = policy.build_entry_model()
            train_cands = generate_candidates(symbols, args.db_path, paper, policy, parity,
                                              fold.train_start, fold.train_end, stop, target, train_entry)
            if any(getattr(c, "signal_date", None) is None for c in train_cands):
                raise RuntimeError(f"{case_id} fold {fold.fold}: missing train signal_date")
            train_candidates_by_exit[case_id] = train_cands
            train_models[case_id] = FrozenQualityModel.fit(train_cands)
            print(f"  {case_id}: train_candidates={len(train_cands)}")

        for case_id, stop, target in EXIT_CASES:
            test_entry = policy.build_entry_model()
            test_cands = generate_candidates(symbols, args.db_path, paper, policy, parity,
                                             fold.test_start, fold.test_end, stop, target, test_entry)
            missing = sum(getattr(c, "signal_date", None) is None for c in test_cands)
            if missing:
                raise RuntimeError(f"{case_id} fold {fold.fold}: missing {missing}/{len(test_cands)} test signal_date")

            # Baseline and gated arms use the same candidate pool for a given exit.
            gate_models = {q: StateQualityGateStrategy(policy.build_entry_model(), train_models[case_id], health, q)
                           for q in args.thresholds}

            for mult in args.cost_multipliers:
                # Baseline
                arm = ("baseline", case_id, None)
                capital_key = (arm[0], arm[1], arm[2], float(mult))
                start_cap = capital[capital_key]
                result, metrics = simulate(test_cands, paper, parity, mult, start_cap)
                ret = float(metrics.get("total_return_pct", 0.0))
                final = float(metrics.get("final_equity", start_cap))
                capital[capital_key] = final
                fold_rows.append({
                    "fold": fold.fold, "test_start": fold.test_start.date(), "test_end": fold.test_end.date(),
                    "arm": "baseline", "case_id": case_id, "threshold": np.nan, "cost_multiplier": mult,
                    "starting_capital": start_cap, "final_equity": final,
                    "fold_return_pct": ret, "executed_trades": len(result.executed_trades),
                    "max_drawdown_pct": float(metrics.get("max_drawdown_pct", 0.0)),
                    "sharpe_ratio": float(metrics.get("sharpe_ratio", 0.0)),
                })
                for t in result.executed_trades:
                    trade_rows.append({"fold": fold.fold, "arm": "baseline", "case_id": case_id, "threshold": np.nan,
                                       "cost_multiplier": mult, "symbol": getattr(t, "symbol", None),
                                       "signal_date": getattr(t, "signal_date", None), "entry_date": getattr(t, "entry_date", None),
                                       "exit_date": getattr(t, "exit_date", None), "net_return_pct": getattr(t, "net_return_pct", None),
                                       "exit_reason": getattr(t, "exit_reason", None)})

                # Gate arms
                for q, gate in gate_models.items():
                    arm_key = (f"q{q:.2f}", case_id, q)
                    capital_key = (arm_key[0], arm_key[1], arm_key[2], float(mult))
                    # Evaluate gate over candidates by temporarily replacing the entry model.
                    # Candidate generation is repeated here so FILTERED decisions are reflected causally.
                    gate_cands = generate_candidates(symbols, args.db_path, paper, policy, parity,
                                                     fold.test_start, fold.test_end, stop, target, gate)
                    result, metrics = simulate(gate_cands, paper, parity, mult, capital[capital_key])
                    ret = float(metrics.get("total_return_pct", 0.0))
                    start_cap = capital[capital_key]
                    final = float(metrics.get("final_equity", start_cap))
                    capital[capital_key] = final
                    fold_rows.append({
                        "fold": fold.fold, "test_start": fold.test_start.date(), "test_end": fold.test_end.date(),
                        "arm": f"q{q:.2f}", "case_id": case_id, "threshold": q, "cost_multiplier": mult,
                        "starting_capital": start_cap, "final_equity": final, "fold_return_pct": ret,
                        "executed_trades": len(result.executed_trades),
                        "max_drawdown_pct": float(metrics.get("max_drawdown_pct", 0.0)),
                        "sharpe_ratio": float(metrics.get("sharpe_ratio", 0.0)),
                    })
                    for t in result.executed_trades:
                        trade_rows.append({"fold": fold.fold, "arm": f"q{q:.2f}", "case_id": case_id, "threshold": q,
                                           "cost_multiplier": mult, "symbol": getattr(t, "symbol", None),
                                           "signal_date": getattr(t, "signal_date", None), "entry_date": getattr(t, "entry_date", None),
                                           "exit_date": getattr(t, "exit_date", None), "net_return_pct": getattr(t, "net_return_pct", None),
                                           "exit_reason": getattr(t, "exit_reason", None)})

    fd = pd.DataFrame(fold_rows)
    td = pd.DataFrame(trade_rows)
    fd.to_csv(outdir / "exit_state_quality_fold_summary.csv", index=False, encoding="utf-8-sig")
    td.to_csv(outdir / "exit_state_quality_trade_level.csv", index=False, encoding="utf-8-sig")

    rows = []
    for (arm, case_id, threshold, mult), g in fd.groupby(["arm", "case_id", "threshold", "cost_multiplier"], dropna=False):
        r = g.fold_return_pct.astype(float).to_numpy()
        rows.append({
            "arm": arm, "case_id": case_id, "threshold": threshold, "cost_multiplier": mult,
            "folds": len(r), "profitable_folds": int((r > 0).sum()),
            "mean_fold_return_pct": float(r.mean()), "median_fold_return_pct": float(np.median(r)),
            "compounded_oos_return_pct": compound(r), "worst_fold_pct": float(r.min()), "best_fold_pct": float(r.max()),
            "total_executed_trades": int(g.executed_trades.sum()),
            "mean_max_drawdown_pct": float(g.max_drawdown_pct.mean()), "mean_sharpe": float(g.sharpe_ratio.mean()),
        })
    sd = pd.DataFrame(rows)
    sd.to_csv(outdir / "exit_state_quality_summary.csv", index=False, encoding="utf-8-sig")

    print("\n=== EXIT x STATE/QUALITY SUMMARY ===")
    print(sd.sort_values(["cost_multiplier", "case_id", "arm"]).to_string(index=False))
    print(f"\nSaved: {outdir.resolve()}")


if __name__ == "__main__":
    main()
