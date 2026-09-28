from __future__ import annotations

import argparse
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> bool:
        return False

from backtesting.engine import BacktestConfig, build_exit_model, generate_candidate_trades, run_backtest
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
    def fit(cls, candidates: list[Any]) -> "FrozenQualityModel":
        rows = []
        for trade in candidates:
            rows.append(
                {
                    "signal_score": getattr(trade, "signal_score", None),
                    "relative_strength": getattr(trade, "relative_strength", None),
                    "adx": getattr(trade, "adx", None),
                    "volume_ratio": getattr(trade, "volume_ratio", None),
                }
            )
        df = pd.DataFrame(rows)
        refs = {
            name: pd.to_numeric(df[name], errors="coerce").dropna()
            if name in df.columns
            else pd.Series(dtype=float)
            for name in QUALITY_FEATURES
        }
        return cls(refs)

    def score_trade(self, trade: Any) -> float:
        values = {
            "signal_score": getattr(trade, "signal_score", None),
            "relative_strength": getattr(trade, "relative_strength", None),
            "adx": getattr(trade, "adx", None),
            "volume_ratio": getattr(trade, "volume_ratio", None),
        }
        return float(
            np.mean(
                [
                    frozen_percentile(values[name], self.refs[name])
                    for name in QUALITY_FEATURES
                ]
            )
        )


def load_market_health(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "time" not in df.columns:
        raise ValueError("Market-health file must contain 'time'.")
    required = {"breadth_ema50_pct", "breadth_ema50_change_10d"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Market-health file missing columns: {sorted(missing)}")

    df["time"] = pd.to_datetime(df["time"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["time"]).sort_values("time")
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


def trade_signal_date(trade: Any) -> Any:
    for name in ("signal_date", "date", "entry_date"):
        value = getattr(trade, name, None)
        if value is not None:
            return value
    return None


def trade_regime(trade: Any) -> str:
    for name in ("regime", "market_regime"):
        value = getattr(trade, name, None)
        if value is not None:
            return str(value)
    return "UNKNOWN"


def allowed_trade(
    trade: Any,
    quality_model: FrozenQualityModel,
    health: pd.DataFrame,
    threshold: float,
) -> tuple[bool, str, float]:
    quality = quality_model.score_trade(trade)
    state = classify_market_state(
        trade_signal_date(trade),
        trade_regime(trade),
        health,
    )
    allowed = state != "DIVERGENT_BULL" and quality >= threshold
    return allowed, state, quality


def build_kwargs(
    paper: PaperExecutionConfig,
    policy: TradingPolicy,
    parity: BacktestPaperParityConfig,
    symbols: list[str],
    entry_model: Any,
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
            # Keep the canonical current-WFO expression.
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


def generate_candidates_exact(
    kwargs: dict[str, Any],
    symbols: list[str],
    start_date: str,
    end_date: str,
) -> list[Any]:
    """
    Generate ALL eligible candidates symbol-by-symbol using the repository's
    actual generate_candidate_trades(symbol, config, ...) contract.

    The BacktestConfig is built from the exact kwargs already used by
    run_backtest, so research does not duplicate indicator/entry/exit logic.
    """
    config_params = inspect.signature(BacktestConfig).parameters

    config_kwargs: dict[str, Any] = {
        key: value
        for key, value in kwargs.items()
        if key in config_params
    }

    config = BacktestConfig(**config_kwargs)

    candidates: list[Any] = []
    for symbol in symbols:
        generated = generate_candidate_trades(
            symbol=symbol,
            config=config,
            db_path=kwargs["db_path"],
            entry_model=kwargs["entry_model"],
            exit_model=kwargs["exit_model"],
            start_date=start_date,
            end_date=end_date,
            verbose=False,
        )
        candidates.extend(generated)

    return candidates


class FrozenCandidateGateStrategy:
    """
    Research-only gate for the TEST run.

    The quality model is fitted from ALL TRAIN candidates before portfolio
    simulation. The test engine still performs the normal candidate generation,
    ranking, sizing, costs, execution and portfolio simulation.
    """

    def __init__(
        self,
        base_strategy: Any,
        quality_model: FrozenQualityModel,
        market_health: pd.DataFrame,
        threshold: float,
    ) -> None:
        self.base_strategy = base_strategy
        self.quality_model = quality_model
        self.market_health = market_health
        self.threshold = threshold

    @property
    def name(self) -> str:
        return self.base_strategy.name + "__candidate_state_quality_gate"

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

        score = _num(decision.get("score"), 0.0)
        adx = _num(decision.get("adx"))
        if adx is None:
            adx = _num(latest.get("ADX14"))

        volume_ratio = _num(decision.get("volume_ratio"))
        if volume_ratio is None:
            volume_ratio = _num(latest.get("Vol_Ratio"))

        values = {
            "signal_score": score,
            "relative_strength": _num(relative_strength),
            "adx": adx,
            "volume_ratio": volume_ratio,
        }

        quality = float(
            np.mean(
                [
                    frozen_percentile(values[name], self.quality_model.refs[name])
                    for name in QUALITY_FEATURES
                ]
            )
        )

        state = classify_market_state(
            latest.get("time"),
            decision.get("regime", market_config.get("regime", "UNKNOWN")),
            self.market_health,
        )

        if state == "DIVERGENT_BULL" or quality < self.threshold:
            result = dict(decision)
            result["status"] = "FILTERED"
            result["market_state"] = state
            result["quality_composite"] = quality
            result["reason"] = f"candidate_state_quality_gate:{state}:quality={quality:.4f}"
            return result

        result = dict(decision)
        result["market_state"] = state
        result["quality_composite"] = quality
        return result


def summarize(trades: list[Any]) -> dict[str, float]:
    closed = [t for t in trades if getattr(t, "is_closed", False)]
    returns = [float(t.net_return_pct) for t in closed]
    wins = [bool(t.is_win) for t in closed]
    return {
        "trades": float(len(closed)),
        "win_rate_pct": float(np.mean(wins) * 100) if wins else 0.0,
        "avg_trade_return_pct": float(np.mean(returns)) if returns else 0.0,
        "median_trade_return_pct": float(np.median(returns)) if returns else 0.0,
    }


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Candidate-level train-frozen State + Quality WFO."
    )
    parser.add_argument("--market-health", required=True)
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
        default="research_results/state_quality_candidate_wfo",
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

    rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []

    baseline_capital = float(parity.initial_cash)

    for fold in folds:
        print("\n" + "=" * 90)
        print(f"CANDIDATE WFO FOLD {fold.fold}")
        print(
            f"Train: {fold.train_start.date()} -> {fold.train_end.date()} | "
            f"Test: {fold.test_start.date()} -> {fold.test_end.date()}"
        )

        # Exact engine kwargs, including canonical trailing multiplier.
        train_model = policy.build_entry_model()
        train_kwargs = build_kwargs(
            paper, policy, parity, symbols, train_model, args.db_path
        )

        # CRITICAL DIFFERENCE FROM V2:
        # fit quality on ALL eligible TRAIN candidates, before portfolio
        # simulation has removed anything due to capital/ranking constraints.
        train_candidates = generate_candidates_exact(
            train_kwargs,
            symbols,
            str(fold.train_start.date()),
            str(fold.train_end.date()),
        )
        frozen_quality = FrozenQualityModel.fit(train_candidates)

        print(f"Train candidates: {len(train_candidates)}")

        # Exact baseline TEST.
        baseline_model = policy.build_entry_model()
        baseline_kwargs = build_kwargs(
            paper, policy, parity, symbols, baseline_model, args.db_path
        )
        baseline_trades, baseline_metrics, _ = run_backtest(
            **baseline_kwargs,
            start_date=str(fold.test_start.date()),
            end_date=str(fold.test_end.date()),
            initial_capital=baseline_capital,
            verbose=False,
        )

        # Candidate-level gate TEST.
        gate_model = FrozenCandidateGateStrategy(
            base_strategy=policy.build_entry_model(),
            quality_model=frozen_quality,
            market_health=health,
            threshold=args.quality_threshold,
        )
        gate_kwargs = build_kwargs(
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
        gate_return = float(gate_metrics.get("total_return_pct", 0.0))

        base_s = summarize(baseline_trades)
        gate_s = summarize(gate_trades)

        rows.append(
            {
                "fold": fold.fold,
                "train_start": fold.train_start.date(),
                "train_end": fold.train_end.date(),
                "test_start": fold.test_start.date(),
                "test_end": fold.test_end.date(),
                "train_candidates": len(train_candidates),
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
            }
        )

        for policy_name, trades in (
            ("baseline", baseline_trades),
            ("candidate_state_quality_gate", gate_trades),
        ):
            for trade in trades:
                row = trade.to_dict()
                row["fold"] = fold.fold
                row["policy"] = policy_name
                trade_rows.append(row)

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

    fold_df = pd.DataFrame(rows)
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
    for policy_name, col, trade_col in (
        ("baseline", "baseline_test_return_pct", "baseline_test_trades"),
        (
            "candidate_state_quality_gate",
            "gate_test_return_pct",
            "gate_test_trades",
        ),
    ):
        values = pd.to_numeric(fold_df[col], errors="coerce").dropna()
        summary.append(
            {
                "policy": policy_name,
                "folds": len(values),
                "profitable_folds": int((values > 0).sum()),
                "mean_fold_return_pct": float(values.mean()),
                "median_fold_return_pct": float(values.median()),
                "best_fold_pct": float(values.max()),
                "worst_fold_pct": float(values.min()),
                "total_test_trades": int(fold_df[trade_col].sum()),
            }
        )

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
