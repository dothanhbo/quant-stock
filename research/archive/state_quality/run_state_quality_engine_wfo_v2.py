from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> bool:
        return False

from backtesting.engine import build_exit_model, run_backtest
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.walk_forward import WalkForwardConfig, build_walk_forward_folds
from config.trading_policy import TradingPolicy
from execution.signal_executor import PaperExecutionConfig
from research.universes import HOLDOUT20_SYMBOLS


QUALITY_FEATURES = (
    "signal_score",
    "relative_strength",
    "adx",
    "volume_ratio",
)


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if np.isfinite(x) else default


def frozen_percentile(value: Any, ref: pd.Series) -> float:
    x = _num(value)
    ref = pd.to_numeric(ref, errors="coerce").dropna()
    if x is None or ref.empty:
        return 0.5
    return float((ref <= x).mean())


@dataclass(frozen=True)
class FrozenQualityModel:
    refs: dict[str, pd.Series]

    @classmethod
    def fit(cls, train_trades: list[Any]) -> "FrozenQualityModel":
        rows = []
        for trade in train_trades:
            rows.append({
                "signal_score": getattr(trade, "signal_score", None),
                "relative_strength": getattr(trade, "relative_strength", None),
                "adx": getattr(trade, "adx", None),
                "volume_ratio": getattr(trade, "volume_ratio", None),
            })
        df = pd.DataFrame(rows)
        refs = {
            name: pd.to_numeric(
                df[name], errors="coerce"
            ).dropna() if name in df.columns else pd.Series(dtype=float)
            for name in QUALITY_FEATURES
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
            frozen_percentile(values[name], self.refs[name])
            for name in QUALITY_FEATURES
        ]))


def load_market_health(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "time" not in df.columns:
        raise ValueError("Market-health file must contain 'time'.")
    required = {"breadth_ema50_pct", "breadth_ema50_change_10d"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Market-health file missing columns: {sorted(missing)}"
        )

    df["time"] = pd.to_datetime(df["time"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["time"]).sort_values("time")

    # One row per market day is required for causal state lookup.
    if df["time"].duplicated().any():
        df = df.drop_duplicates("time", keep="last")

    return df.set_index("time")


def classify_market_state(
    signal_date: Any,
    regime: str,
    health: pd.DataFrame,
) -> str:
    date = pd.Timestamp(signal_date).normalize()
    regime = str(regime).upper()

    if date not in health.index:
        return "NEUTRAL"

    row = health.loc[date]
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
        if breadth is not None and change is not None:
            if breadth >= 60 and change > 0:
                return "RECOVERY"
        return "NEUTRAL"

    return "NEUTRAL"


class StateQualityGateStrategy:
    """
    Thin entry wrapper around the EXISTING entry model.

    It does not change scoring, indicators, execution, exit, sizing,
    ranking, or portfolio simulation. It only changes whether a candidate
    returned by the existing entry model is allowed to proceed.

    The quality distribution is frozen from the fold's TRAIN trades.
    Market state comes from the causal PIT daily health table.
    """

    def __init__(
        self,
        base_strategy,
        quality_model: FrozenQualityModel,
        market_health: pd.DataFrame,
        threshold: float = 0.75,
    ) -> None:
        self.base_strategy = base_strategy
        self.quality_model = quality_model
        self.market_health = market_health
        self.threshold = float(threshold)

    @property
    def name(self) -> str:
        return self.base_strategy.name + "__state_quality_gate"

    def evaluate(
        self,
        latest: pd.Series,
        relative_strength: float,
        market_config: dict,
    ) -> dict:
        decision = self.base_strategy.evaluate(
            latest=latest,
            relative_strength=relative_strength,
            market_config=market_config,
        )

        if decision.get("status") != "PASSED":
            return decision

        # The entry model returns the exact fields used by the production
        # scanner/evaluation path, including relative_strength_20d.
        quality = self.quality_model.score(decision)

        state = classify_market_state(
            latest.get("time"),
            decision.get("regime", market_config.get("regime", "UNKNOWN")),
            self.market_health,
        )

        allowed = (
            state != "DIVERGENT_BULL"
            and quality >= self.threshold
        )

        result = dict(decision)
        result["market_state"] = state
        result["quality_composite"] = quality

        if not allowed:
            # Any non-PASSED status is ignored by generate_candidate_trades.
            result["status"] = "FILTERED"
            result["reason"] = (
                f"state_quality_gate:{state}:quality={quality:.4f}"
            )

        return result


def build_backtest_kwargs(
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
            trailing_atr_multiplier=policy.trailing_atr_multiplier,
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


def summarize_trades(trades: list[Any]) -> dict[str, float]:
    returns = [
        float(t.net_return_pct)
        for t in trades
        if getattr(t, "is_closed", False)
    ]
    wins = [
        bool(t.is_win)
        for t in trades
        if getattr(t, "is_closed", False)
    ]
    return {
        "trades": float(len(trades)),
        "win_rate_pct": float(np.mean(wins) * 100) if wins else 0.0,
        "avg_trade_return_pct": float(np.mean(returns)) if returns else 0.0,
        "median_trade_return_pct": float(np.median(returns)) if returns else 0.0,
    }


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description=(
            "Engine-level rolling WFO comparing the exact current baseline "
            "against a train-frozen State + Quality Gate overlay."
        )
    )
    parser.add_argument(
        "--market-health",
        required=True,
        help="PIT market_health_pit_daily.csv",
    )
    parser.add_argument("--db-path", default="data/market.db")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--start", default="2018-08-07")
    parser.add_argument("--end", default="2026-08-21")
    parser.add_argument("--train-months", type=int, default=24)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument("--quality-threshold", type=float, default=0.75)
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
        else [s.strip().upper() for s in args.symbols if s.strip()]
    )

    health = load_market_health(args.market_health)

    wf = WalkForwardConfig(
        start_date=args.start,
        end_date=args.end,
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
    )
    folds = build_walk_forward_folds(wf)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    fold_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []

    # Baseline is chained exactly as the existing WFO runner does.
    baseline_capital = float(parity.initial_cash)

    for fold in folds:
        print("\n" + "=" * 90)
        print(f"ENGINE WFO FOLD {fold.fold}")
        print(
            f"Train: {fold.train_start.date()} -> {fold.train_end.date()} | "
            f"Test: {fold.test_start.date()} -> {fold.test_end.date()}"
        )

        # 1) EXACT CURRENT BASELINE TRAIN RUN.
        base_train_model = policy.build_entry_model()
        train_kwargs = build_backtest_kwargs(
            paper, policy, parity, symbols, base_train_model, args.db_path
        )
        train_trades, train_metrics, _ = run_backtest(
            **train_kwargs,
            start_date=str(fold.train_start.date()),
            end_date=str(fold.train_end.date()),
            initial_capital=parity.initial_cash,
            verbose=False,
        )

        # Freeze quality ONLY from TRAIN.
        frozen_quality = FrozenQualityModel.fit(train_trades)

        # 2) EXACT CURRENT BASELINE TEST RUN.
        baseline_model = policy.build_entry_model()
        baseline_kwargs = build_backtest_kwargs(
            paper, policy, parity, symbols, baseline_model, args.db_path
        )
        baseline_trades, baseline_metrics, _ = run_backtest(
            **baseline_kwargs,
            start_date=str(fold.test_start.date()),
            end_date=str(fold.test_end.date()),
            initial_capital=baseline_capital,
            verbose=False,
        )

        # 3) TREATMENT TEST RUN.
        gate_model = StateQualityGateStrategy(
            base_strategy=policy.build_entry_model(),
            quality_model=frozen_quality,
            market_health=health,
            threshold=args.quality_threshold,
        )
        gate_kwargs = build_backtest_kwargs(
            paper, policy, parity, symbols, gate_model, args.db_path
        )
        gate_trades, gate_metrics, _ = run_backtest(
            **gate_kwargs,
            start_date=str(fold.test_start.date()),
            end_date=str(fold.test_end.date()),
            initial_capital=baseline_capital,
            verbose=False,
        )

        baseline_return = float(
            baseline_metrics.get("total_return_pct", 0.0)
        )
        gate_return = float(
            gate_metrics.get("total_return_pct", 0.0)
        )

        base_s = summarize_trades(baseline_trades)
        gate_s = summarize_trades(gate_trades)

        fold_rows.append({
            "fold": fold.fold,
            "train_start": fold.train_start.date(),
            "train_end": fold.train_end.date(),
            "test_start": fold.test_start.date(),
            "test_end": fold.test_end.date(),
            "train_trades": len(train_trades),
            "baseline_test_trades": len(baseline_trades),
            "gate_test_trades": len(gate_trades),
            "baseline_test_return_pct": baseline_return,
            "gate_test_return_pct": gate_return,
            "delta_pp": gate_return - baseline_return,
            "baseline_final_equity": float(
                baseline_metrics.get("final_equity", baseline_capital)
            ),
            "gate_final_equity": float(
                gate_metrics.get("final_equity", baseline_capital)
            ),
            "baseline_win_rate_pct": base_s["win_rate_pct"],
            "gate_win_rate_pct": gate_s["win_rate_pct"],
            "baseline_avg_trade_return_pct": base_s["avg_trade_return_pct"],
            "gate_avg_trade_return_pct": gate_s["avg_trade_return_pct"],
            "baseline_median_trade_return_pct": base_s["median_trade_return_pct"],
            "gate_median_trade_return_pct": gate_s["median_trade_return_pct"],
        })

        for policy_name, trades in (
            ("baseline", baseline_trades),
            ("state_quality_gate", gate_trades),
        ):
            for trade in trades:
                row = trade.to_dict()
                row["fold"] = fold.fold
                row["policy"] = policy_name
                trade_rows.append(row)

        # Chain ONLY baseline capital so treatment never contaminates
        # the baseline trajectory.
        baseline_capital = float(
            baseline_metrics.get("final_equity", baseline_capital)
        )

        print(
            f"Baseline: {baseline_return:+.2f}% | "
            f"{len(baseline_trades)} trades"
        )
        print(
            f"Gate    : {gate_return:+.2f}% | "
            f"{len(gate_trades)} trades | "
            f"delta {gate_return - baseline_return:+.2f} pp"
        )

    fold_df = pd.DataFrame(fold_rows)
    trade_df = pd.DataFrame(trade_rows)

    fold_df.to_csv(
        output_dir / "fold_policy_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    trade_df.to_csv(
        output_dir / "trade_level_policy_returns.csv",
        index=False,
        encoding="utf-8-sig",
    )

    summary = []
    for name, g in fold_df.groupby(
        [
            "baseline_test_return_pct"
        ],
        dropna=False,
    ):
        pass

    for policy_name, col in (
        ("baseline", "baseline_test_return_pct"),
        ("state_quality_gate", "gate_test_return_pct"),
    ):
        values = pd.to_numeric(fold_df[col], errors="coerce").dropna()
        summary.append({
            "policy": policy_name,
            "folds": len(values),
            "profitable_folds": int((values > 0).sum()),
            "mean_fold_return_pct": float(values.mean()),
            "median_fold_return_pct": float(values.median()),
            "best_fold_pct": float(values.max()),
            "worst_fold_pct": float(values.min()),
            "total_test_trades": int(
                fold_df[
                    "baseline_test_trades"
                    if policy_name == "baseline"
                    else "gate_test_trades"
                ].sum()
            ),
        })

    summary_df = pd.DataFrame(summary)
    summary_df.to_csv(
        output_dir / "policy_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )

    print("\n" + "=" * 90)
    print("SUMMARY")
    print(summary_df.to_string(index=False))
    print(f"\nOutput: {output_dir.resolve()}")


if __name__ == "__main__":
    main()
