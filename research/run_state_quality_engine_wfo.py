from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtesting.engine import build_exit_model, run_backtest
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.walk_forward import WalkForwardConfig, build_walk_forward_folds
from config.trading_policy import TradingPolicy
from execution.signal_executor import PaperExecutionConfig
from research.universes import HOLDOUT20_SYMBOLS


FEATURES = ("signal_score", "relative_strength", "adx", "volume_ratio")


def _finite(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if np.isfinite(x) else None


def _percentile(value: Any, reference: pd.Series) -> float:
    x = _finite(value)
    ref = pd.to_numeric(reference, errors="coerce").dropna()
    if x is None or ref.empty:
        return 0.5
    # Frozen empirical CDF: causal, no test-period fitting.
    return float((ref <= x).mean())


def load_market_health(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {
        "time",
        "breadth_ema50_pct",
        "breadth_ema50_change_10d",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Market-health file missing: {sorted(missing)}")
    df["time"] = pd.to_datetime(df["time"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["time"]).sort_values("time")
    return df.set_index("time")


@dataclass(frozen=True)
class FrozenQuality:
    references: dict[str, pd.Series]

    @classmethod
    def fit(cls, trades: list[Any]) -> "FrozenQuality":
        rows = []
        for t in trades:
            rows.append({
                f: getattr(t, f, None)
                for f in FEATURES
            })
        df = pd.DataFrame(rows)
        refs = {
            f: pd.to_numeric(df[f], errors="coerce").dropna()
            for f in FEATURES
        }
        return cls(refs)

    def score(self, evaluation: dict[str, Any]) -> float:
        values = {
            "signal_score": evaluation.get("score"),
            "relative_strength": evaluation.get("relative_strength_20d"),
            "adx": evaluation.get("adx"),
            "volume_ratio": evaluation.get("volume_ratio"),
        }
        return float(np.mean([
            _percentile(values[f], self.references[f])
            for f in FEATURES
        ]))


class StateQualityGateStrategy:
    """
    Wrapper around the policy's normal entry model.

    The wrapper does NOT learn from test data.  It receives:
      - a frozen quality distribution learned from the fold's training trades
      - the PIT daily market-health table
    """

    def __init__(
        self,
        base_strategy,
        quality: FrozenQuality,
        market_health: pd.DataFrame,
        quality_threshold: float = 0.75,
    ):
        self.base_strategy = base_strategy
        self.quality = quality
        self.market_health = market_health
        self.quality_threshold = quality_threshold

    @property
    def name(self) -> str:
        return self.base_strategy.name + "__state_quality_gate"

    def _state(self, latest: pd.Series, regime: str) -> str:
        date = pd.Timestamp(latest["time"]).normalize()
        if date not in self.market_health.index:
            return "NEUTRAL"

        row = self.market_health.loc[date]
        breadth = _finite(row.get("breadth_ema50_pct"))
        change = _finite(row.get("breadth_ema50_change_10d"))

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
            if breadth is not None and change is not None:
                if breadth >= 60 and change > 0:
                    return "RECOVERY"
            return "NEUTRAL"

        return "NEUTRAL"

    def evaluate(self, latest, relative_strength, market_config):
        decision = self.base_strategy.evaluate(
            latest=latest,
            relative_strength=relative_strength,
            market_config=market_config,
        )

        if decision.get("status") != "PASSED":
            return decision

        quality_score = self.quality.score(decision)
        state = self._state(
            latest,
            str(decision.get("regime", market_config.get("regime", "UNKNOWN"))),
        )

        # State + Quality Gate policy from the prior diagnostic:
        # DIVERGENT_BULL rejected; FRAGILE_BULL requires Q4;
        # other states require Q4 as well.
        allowed = (
            state != "DIVERGENT_BULL"
            and quality_score >= self.quality_threshold
        )

        decision = dict(decision)
        decision["market_state"] = state
        decision["quality_composite"] = quality_score

        if not allowed:
            decision["status"] = "FILTERED"
            decision["reason"] = (
                f"state_quality_gate:{state}:q={quality_score:.3f}"
            )

        return decision


def make_backtest_kwargs(
    paper: PaperExecutionConfig,
    policy: TradingPolicy,
    parity: BacktestPaperParityConfig,
    symbols: list[str],
    entry_model,
    db_path: str,
) -> dict[str, Any]:
    return {
        "symbols": symbols,
        "max_holding_days": policy.maximum_holding_days,
        "entry_model": entry_model,
        "exit_model": build_exit_model(
            name="atr",
            stop_atr_multiplier=policy.stop_atr_multiplier,
            target_atr_multiplier=policy.target_atr_multiplier,
            break_even_trigger=policy.target_atr_multiplier,
            trailing_atr_multiplier=policy.stop_atr_multiplier,
        ),
        "ranking_method": "signal_score",
        "position_sizer": parity.build_position_sizer(),
        "buy_commission_pct": parity.commission_pct,
        "sell_commission_pct": parity.commission_pct,
        "sell_tax_pct": parity.sell_tax_pct,
        "buy_slippage_pct": parity.slippage_pct,
        "sell_slippage_pct": parity.slippage_pct,
        "max_positions": parity.maximum_open_positions,
        "lot_size": parity.lot_size,
        "max_new_positions_per_day": paper.maximum_orders_per_scan,
        "maximum_gross_exposure_pct": parity.maximum_gross_exposure_pct,
        "minimum_cash_buffer_pct": parity.minimum_cash_buffer_pct,
        "db_path": db_path,
    }


def metrics_row(
    fold: int,
    policy_name: str,
    train_metrics: dict[str, Any],
    test_metrics: dict[str, Any],
    test_trades: list[Any],
    initial_capital: float,
    final_capital: float,
) -> dict[str, Any]:
    returns = [float(t.net_return_pct) for t in test_trades if t.is_closed]
    return {
        "fold": fold,
        "policy": policy_name,
        "train_return_pct": float(train_metrics.get("total_return_pct", 0.0)),
        "test_return_pct": float(test_metrics.get("total_return_pct", 0.0)),
        "test_final_equity": final_capital,
        "trade_count": len(test_trades),
        "win_rate_pct": (
            float(np.mean([t.is_win for t in test_trades]) * 100)
            if test_trades else 0.0
        ),
        "avg_trade_return_pct": float(np.mean(returns)) if returns else 0.0,
        "median_trade_return_pct": float(np.median(returns)) if returns else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Proper engine-level WFO: State + Quality Gate vs baseline."
    )
    parser.add_argument("--market-health", required=True)
    parser.add_argument(
        "--db-path",
        default="data/market.db",
        help="Path to market price database.",
    )
    parser.add_argument("--start", default="2018-08-07")
    parser.add_argument("--end", default="2026-08-21")
    parser.add_argument("--train-months", type=int, default=24)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument("--quality-threshold", type=float, default=0.75)
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="Default: HOLDOUT20.",
    )
    parser.add_argument(
        "--output",
        default="research_results/state_quality_engine_wfo",
    )
    args = parser.parse_args()

    paper = PaperExecutionConfig.from_env()
    policy = TradingPolicy.from_env()
    parity = BacktestPaperParityConfig.from_paper_config(paper)
    symbols = (
        list(HOLDOUT20_SYMBOLS)
        if args.symbols is None
        else [x.strip().upper() for x in args.symbols if x.strip()]
    )
    health = load_market_health(args.market_health)

    wf = WalkForwardConfig(
        start_date=args.start,
        end_date=args.end,
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
    )

    base_strategy = policy.build_entry_model()
    folds = build_walk_forward_folds(wf)

    rows = []
    all_trade_rows = []
    current_capital = parity.initial_cash

    for fold in folds:
        print(f"\n{'=' * 90}")
        print(f"ENGINE WFO FOLD {fold.fold}")
        print(
            f"Train: {fold.train_start.date()} -> {fold.train_end.date()} | "
            f"Test: {fold.test_start.date()} -> {fold.test_end.date()}"
        )

        # TRAIN: unchanged baseline engine. These trades are used only
        # to freeze the empirical quality distribution for this fold.
        train_kwargs = make_backtest_kwargs(
            paper, policy, parity, symbols, base_strategy,db_path=args.db_path,
        )
        train_trades, train_metrics, _ = run_backtest(
            **train_kwargs,
            start_date=str(fold.train_start.date()),
            end_date=str(fold.train_end.date()),
            initial_capital=parity.initial_cash,
            verbose=False,
        )

        frozen_quality = FrozenQuality.fit(train_trades)

        # Baseline TEST.
        baseline_trades, baseline_metrics, _ = run_backtest(
            **train_kwargs,
            start_date=str(fold.test_start.date()),
            end_date=str(fold.test_end.date()),
            initial_capital=current_capital,
            verbose=False,
        )

        # State + Quality TEST, with frozen train-only thresholds.
        gate_strategy = StateQualityGateStrategy(
            base_strategy=policy.build_entry_model(),
            quality=frozen_quality,
            market_health=health,
            quality_threshold=args.quality_threshold,
        )
        gate_kwargs = make_backtest_kwargs(
            paper, policy, parity, symbols, gate_strategy,db_path=args.db_path,
        )
        gate_trades, gate_metrics, _ = run_backtest(
            **gate_kwargs,
            start_date=str(fold.test_start.date()),
            end_date=str(fold.test_end.date()),
            initial_capital=current_capital,
            verbose=False,
        )

        rows.append(
            metrics_row(
                fold.fold, "baseline", train_metrics, baseline_metrics,
                baseline_trades, current_capital,
                float(baseline_metrics.get("final_equity", current_capital)),
            )
        )
        rows.append(
            metrics_row(
                fold.fold, "state_quality_gate", train_metrics, gate_metrics,
                gate_trades, current_capital,
                float(gate_metrics.get("final_equity", current_capital)),
            )
        )

        for policy_name, trades in (
            ("baseline", baseline_trades),
            ("state_quality_gate", gate_trades),
        ):
            for t in trades:
                d = t.to_dict()
                d["fold"] = fold.fold
                d["policy"] = policy_name
                all_trade_rows.append(d)

        # Chain capital using BASELINE only. This keeps the comparison
        # fold-local and avoids one policy contaminating the other's capital.
        current_capital = float(
            baseline_metrics.get("final_equity", current_capital)
        )

        print(
            f"Baseline: {float(baseline_metrics.get('total_return_pct', 0.0)):.2f}% "
            f"({len(baseline_trades)} trades)"
        )
        print(
            f"Gate    : {float(gate_metrics.get('total_return_pct', 0.0)):.2f}% "
            f"({len(gate_trades)} trades)"
        )

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    fold_df = pd.DataFrame(rows)
    trade_df = pd.DataFrame(all_trade_rows)

    fold_df.to_csv(out / "fold_policy_summary.csv", index=False, encoding="utf-8-sig")
    trade_df.to_csv(out / "trade_level_policy_returns.csv", index=False, encoding="utf-8-sig")

    summary_rows = []
    for policy_name, g in fold_df.groupby("policy", sort=False):
        test_returns = pd.to_numeric(g["test_return_pct"], errors="coerce").dropna()
        summary_rows.append({
            "policy": policy_name,
            "folds": len(g),
            "profitable_folds": int((test_returns > 0).sum()),
            "mean_fold_return_pct": float(test_returns.mean()),
            "median_fold_return_pct": float(test_returns.median()),
            "best_fold_pct": float(test_returns.max()),
            "worst_fold_pct": float(test_returns.min()),
            "total_test_trades": int(g["trade_count"].sum()),
            "mean_trades_per_fold": float(g["trade_count"].mean()),
        })

    pd.DataFrame(summary_rows).to_csv(
        out / "policy_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )

    print("\nDONE")
    print(f"Output: {out}")
    print(f"  {out / 'policy_summary.csv'}")
    print(f"  {out / 'fold_policy_summary.csv'}")
    print(f"  {out / 'trade_level_policy_returns.csv'}")


if __name__ == "__main__":
    main()
