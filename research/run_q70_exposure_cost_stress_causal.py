from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtesting.engine import BacktestConfig, generate_candidate_trades
from backtesting.exit_models import TrailingATRExitModel
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.portfolio_metrics import calculate_portfolio_metrics
from backtesting.portfolio_simulator import PortfolioSimulator
from backtesting.transaction_cost import TransactionCostConfig
from backtesting.walk_forward import WalkForwardConfig, build_walk_forward_folds
from config.trading_policy import TradingPolicy
from execution.signal_executor import PaperExecutionConfig
from research.universes import HOLDOUT20_SYMBOLS


QUALITY_FEATURES = ("signal_score", "relative_strength", "adx", "volume_ratio")


def num(value: Any, default=np.nan) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if np.isfinite(x) else default


def load_health(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"time", "breadth_ema50_pct", "breadth_ema50_change_10d"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Market-health file missing columns: {sorted(missing)}")
    df["time"] = pd.to_datetime(df["time"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["time"]).sort_values("time")
    return df.drop_duplicates("time", keep="last").set_index("time")


def state_from_candidate(candidate: Any, health: pd.DataFrame) -> str:
    signal_date = getattr(candidate, "signal_date", None)
    if signal_date is None:
        raise ValueError(
            f"Candidate {getattr(candidate, 'symbol', '?')} has no signal_date; "
            "causal WFO refuses to fall back to entry_date."
        )
    date = pd.Timestamp(signal_date).normalize()
    regime = str(getattr(candidate, "market_regime", "")).upper()
    if date not in health.index:
        return "NEUTRAL"
    row = health.loc[date]
    breadth = num(row.get("breadth_ema50_pct"))
    change = num(row.get("breadth_ema50_change_10d"))
    if regime == "BEAR":
        return "BEAR"
    if regime == "BULL":
        if np.isfinite(breadth) and np.isfinite(change):
            if breadth < 50 and change < 0:
                return "DIVERGENT_BULL"
            if breadth >= 70 and change >= 0:
                return "HEALTHY_BULL"
        return "FRAGILE_BULL"
    if regime == "SIDEWAY" and np.isfinite(breadth) and np.isfinite(change):
        if breadth >= 60 and change > 0:
            return "RECOVERY"
    return "NEUTRAL"


def fit_quality(train_candidates: list[Any]):
    refs: dict[str, np.ndarray] = {}
    for feature in QUALITY_FEATURES:
        values = [num(getattr(t, feature, np.nan)) for t in train_candidates]
        values = [v for v in values if np.isfinite(v)]
        refs[feature] = np.sort(np.asarray(values, dtype=float))

    def percentile(value: float, ref: np.ndarray) -> float:
        if not np.isfinite(value) or len(ref) == 0:
            return 0.5
        return float(np.searchsorted(ref, value, side="right") / len(ref))

    def score(candidate: Any) -> float:
        return float(np.mean([
            percentile(num(getattr(candidate, "signal_score", np.nan)), refs["signal_score"]),
            percentile(num(getattr(candidate, "relative_strength", np.nan)), refs["relative_strength"]),
            percentile(num(getattr(candidate, "adx", np.nan)), refs["adx"]),
            percentile(num(getattr(candidate, "volume_ratio", np.nan)), refs["volume_ratio"]),
        ]))

    return score


@dataclass(frozen=True)
class CandidateCache:
    candidates: list[Any]


def generate_period_candidates(
    symbols: list[str],
    db_path: str,
    policy: TradingPolicy,
    paper: PaperExecutionConfig,
    start_date: str,
    end_date: str,
) -> CandidateCache:
    config = BacktestConfig(
        max_holding_days=policy.maximum_holding_days,
        initial_capital=paper.initial_cash,
        buy_commission_pct=paper.commission_rate * 100,
        sell_commission_pct=paper.commission_rate * 100,
        sell_tax_pct=paper.sell_tax_rate * 100,
        buy_slippage_pct=paper.slippage_bps / 100,
        sell_slippage_pct=paper.slippage_bps / 100,
        ranking_method="signal_score",
    )
    exit_model = TrailingATRExitModel(
        stop_atr_multiplier=policy.stop_atr_multiplier,
        target_atr_multiplier=policy.target_atr_multiplier,
        trailing_atr_multiplier=policy.trailing_atr_multiplier,
    )
    entry_model = policy.build_entry_model()
    candidates: list[Any] = []
    for symbol in symbols:
        candidates.extend(generate_candidate_trades(
            symbol=symbol,
            config=config,
            db_path=db_path,
            warmup_bars=60,
            verbose=False,
            start_date=start_date,
            end_date=end_date,
            entry_model=entry_model,
            exit_model=exit_model,
        ))
    return CandidateCache(candidates)


def simulate(
    candidates: list[Any],
    *,
    paper: PaperExecutionConfig,
    parity: BacktestPaperParityConfig,
    initial_capital: float,
    sizer,
    cost_multiplier: float = 1.0,
) -> tuple[list[Any], dict[str, Any], pd.DataFrame]:
    """Run the portfolio simulator with an explicit cost multiplier."""
    if cost_multiplier <= 0:
        raise ValueError("cost_multiplier must be > 0")

    costs = TransactionCostConfig(
        buy_commission_pct=paper.commission_rate * 100 * cost_multiplier,
        sell_commission_pct=paper.commission_rate * 100 * cost_multiplier,
        sell_tax_pct=paper.sell_tax_rate * 100 * cost_multiplier,
        buy_slippage_pct=paper.slippage_bps / 100 * cost_multiplier,
        sell_slippage_pct=paper.slippage_bps / 100 * cost_multiplier,
    )
    simulator = PortfolioSimulator(
        initial_cash=initial_capital,
        position_size_pct=paper.fixed_fraction_pct,
        position_sizer=sizer,
        ranking_method="signal_score",
        transaction_cost_config=costs,
        max_positions=paper.maximum_open_positions,
        lot_size=paper.lot_size,
        max_new_positions_per_day=paper.maximum_orders_per_scan,
        maximum_gross_exposure_pct=paper.maximum_gross_exposure_pct,
        minimum_cash_buffer_pct=paper.minimum_cash_buffer_pct,
    )
    result = simulator.simulate(copy.deepcopy(candidates))
    equity = result.equity_curve
    final_equity = float(result.final_equity)
    metrics = calculate_portfolio_metrics(
        equity,
        final_equity=final_equity,
    )
    return result.executed_trades, metrics, equity


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Causal Q70 exposure-mechanism WFO using signal_date only."
    )
    parser.add_argument("--market-health", required=True)
    parser.add_argument("--db-path", default="data/market.db")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--start", default="2018-08-07")
    parser.add_argument("--end", default="2026-08-21")
    parser.add_argument("--train-months", type=int, default=24)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument("--quality-threshold", type=float, default=0.70)
    parser.add_argument(
        "--fragile-scales", nargs="+", type=float,
        default=[0.20, 0.25, 0.30, 0.40, 0.50],
    )
    parser.add_argument(
        "--cost-multipliers", nargs="+", type=float,
        default=[1.0, 1.5, 2.0],
        help="Scale commission, tax and slippage costs.",
    )
    parser.add_argument(
        "--output",
        default="research_results/q70_exposure_cost_stress_causal",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    health = load_health(args.market_health)
    paper = PaperExecutionConfig.from_env()
    policy = TradingPolicy.from_env()
    parity = BacktestPaperParityConfig.from_paper_config(
        paper,
        sell_tax_rate=paper.sell_tax_rate,
    )
    symbols = (
        list(HOLDOUT20_SYMBOLS)
        if args.symbols is None
        else sorted({s.strip().upper() for s in args.symbols if s.strip()})
    )
    folds = build_walk_forward_folds(WalkForwardConfig(
        start_date=args.start,
        end_date=args.end,
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
    ))
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    exposure_policies = [("q70_f1", 1.0)] + [
        (f"q70_f{str(scale).replace('.', '')}", scale)
        for scale in args.fragile_scales
        if scale != 1.0
    ]
    cost_multipliers = [float(x) for x in args.cost_multipliers]
    if any(x <= 0 for x in cost_multipliers):
        raise ValueError("--cost-multipliers values must be > 0")

    rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []

    for fold in folds:
        print("\n" + "=" * 90)
        print(
            f"FOLD {fold.fold}: "
            f"{fold.train_start.date()} -> {fold.test_end.date()}"
        )

        train_cache = generate_period_candidates(
            symbols, args.db_path, policy, paper,
            str(fold.train_start.date()), str(fold.train_end.date()),
        )
        quality = fit_quality(train_cache.candidates)

        test_cache = generate_period_candidates(
            symbols, args.db_path, policy, paper,
            str(fold.test_start.date()), str(fold.test_end.date()),
        )
        q70_candidates = [
            t for t in test_cache.candidates
            if quality(t) >= args.quality_threshold
        ]

        print(
            f"train_candidates={len(train_cache.candidates)} "
            f"test_candidates={len(test_cache.candidates)} "
            f"q70_candidates={len(q70_candidates)}"
        )

        for exposure_name, fragile_scale in exposure_policies:
            class ExposureSizer:
                def __init__(self, base_sizer):
                    self.base_sizer = base_sizer

                @property
                def name(self):
                    return (
                        f"{self.base_sizer.name}"
                        f"__q70_fragile_{fragile_scale:g}"
                    )

                def calculate_quantity(self, context):
                    candidate = context.candidate
                    state = state_from_candidate(candidate, health)
                    q0 = self.base_sizer.calculate_quantity(context)
                    if q0 <= 0:
                        return 0
                    if state == "DIVERGENT_BULL":
                        return 0
                    if state == "FRAGILE_BULL":
                        scaled = int(q0 * fragile_scale)
                        return (scaled // context.lot_size) * context.lot_size
                    return q0

            for cost_multiplier in cost_multipliers:
                trades, metrics, equity = simulate(
                    q70_candidates,
                    paper=paper,
                    parity=parity,
                    initial_capital=paper.initial_cash,
                    sizer=ExposureSizer(parity.build_position_sizer()),
                    cost_multiplier=cost_multiplier,
                )

                fold_return = float(metrics.get("total_return_pct", 0.0))

                # Portfolio-level PF: use dollar P&L where available,
                # because net_return_pct is not exposure-scaled.
                pnl_values = []
                for trade in trades:
                    pnl = num(getattr(trade, "pnl", np.nan))
                    if np.isfinite(pnl):
                        pnl_values.append(pnl)

                if pnl_values:
                    gross_profit = sum(x for x in pnl_values if x > 0)
                    gross_loss = -sum(x for x in pnl_values if x < 0)
                    portfolio_pf = (
                        gross_profit / gross_loss
                        if gross_loss > 0
                        else (np.inf if gross_profit > 0 else 0.0)
                    )
                else:
                    portfolio_pf = np.nan

                rows.append({
                    "exposure_policy": exposure_name,
                    "fragile_scale": fragile_scale,
                    "cost_multiplier": cost_multiplier,
                    "fold": fold.fold,
                    "test_start": fold.test_start.date(),
                    "test_end": fold.test_end.date(),
                    "candidate_pool": len(q70_candidates),
                    "trades": len(trades),
                    "fold_return_pct": fold_return,
                    "max_drawdown_pct": float(
                        metrics.get("max_drawdown_pct", 0.0)
                    ),
                    "sharpe_ratio": float(metrics.get("sharpe_ratio", 0.0)),
                    "portfolio_profit_factor": portfolio_pf,
                })

                for trade in trades:
                    trade_rows.append({
                        "exposure_policy": exposure_name,
                        "fragile_scale": fragile_scale,
                        "cost_multiplier": cost_multiplier,
                        "fold": fold.fold,
                        "symbol": getattr(trade, "symbol", ""),
                        "signal_date": getattr(trade, "signal_date", ""),
                        "entry_date": getattr(trade, "entry_date", ""),
                        "market_state": state_from_candidate(trade, health),
                        "net_return_pct": getattr(trade, "net_return_pct", np.nan),
                        "pnl": getattr(trade, "pnl", np.nan),
                    })

    fold_df = pd.DataFrame(rows)
    trade_df = pd.DataFrame(trade_rows)

    fold_df.to_csv(
        output / "q70_exposure_cost_stress_fold_summary.csv", index=False
    )
    trade_df.to_csv(
        output / "q70_exposure_cost_stress_trade_level.csv", index=False
    )

    summary_rows = []
    for (exposure_name, fragile_scale, cost_multiplier), group in fold_df.groupby(
        ["exposure_policy", "fragile_scale", "cost_multiplier"], sort=False
    ):
        returns = group["fold_return_pct"].to_numpy(dtype=float)
        pf = group["portfolio_profit_factor"].replace(
            [np.inf, -np.inf], np.nan
        )

        summary_rows.append({
            "exposure_policy": exposure_name,
            "fragile_scale": fragile_scale,
            "cost_multiplier": cost_multiplier,
            "folds": len(returns),
            "profitable_folds": int((returns > 0).sum()),
            "mean_fold_return_pct": float(returns.mean()),
            "median_fold_return_pct": float(np.median(returns)),
            "compounded_oos_return_pct": float(
                (np.prod(1 + returns / 100) - 1) * 100
            ),
            "worst_fold_pct": float(returns.min()),
            "best_fold_pct": float(returns.max()),
            "total_test_trades": int(group["trades"].sum()),
            "mean_fold_max_drawdown_pct": float(
                group["max_drawdown_pct"].mean()
            ),
            "mean_fold_sharpe": float(group["sharpe_ratio"].mean()),
            "mean_portfolio_profit_factor": float(pf.mean())
            if pf.notna().any() else np.nan,
        })

    summary_df = pd.DataFrame(summary_rows)

    matrix_rows = []
    for (exposure_name, fragile_scale), group in summary_df.groupby(
        ["exposure_policy", "fragile_scale"], sort=False
    ):
        row = {
            "exposure_policy": exposure_name,
            "fragile_scale": fragile_scale,
        }
        for _, r in group.iterrows():
            suffix = f"x{float(r['cost_multiplier']):g}"
            row[f"return_{suffix}_pct"] = r["compounded_oos_return_pct"]
            row[f"mean_fold_{suffix}_pct"] = r["mean_fold_return_pct"]
            row[f"median_fold_{suffix}_pct"] = r["median_fold_return_pct"]
            row[f"worst_fold_{suffix}_pct"] = r["worst_fold_pct"]
            row[f"profitable_folds_{suffix}"] = r["profitable_folds"]
            row[f"mean_dd_{suffix}_pct"] = r["mean_fold_max_drawdown_pct"]
            row[f"mean_sharpe_{suffix}"] = r["mean_fold_sharpe"]
            row[f"pf_{suffix}"] = r["mean_portfolio_profit_factor"]
        matrix_rows.append(row)

    matrix_df = pd.DataFrame(matrix_rows)

    summary_df.to_csv(
        output / "q70_exposure_cost_stress_summary.csv", index=False
    )
    matrix_df.to_csv(
        output / "q70_exposure_cost_stress_matrix.csv", index=False
    )

    print("\n=== CAUSAL Q70 COST STRESS SUMMARY ===")
    print(summary_df.to_string(index=False))
    print("\n=== COST STRESS MATRIX ===")
    print(matrix_df.to_string(index=False))
    print("\nWrote:", output.resolve())


if __name__ == "__main__":
    main()
