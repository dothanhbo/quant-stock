from __future__ import annotations

"""
Portfolio-level candidate State × Quality WFO.

Purpose
-------
Validate whether the candidate-level State+Quality filter survives the REAL
portfolio simulator: position sizing, ranking, overlap, max positions,
exposure, cash buffer, commissions, slippage, sell tax and exits.

Important:
- Quality model is fitted from ALL eligible TRAIN CANDIDATES, not executed trades.
- Test is run through the existing run_backtest() portfolio engine.
- Market state is causal and comes from the PIT market-health file.
- No test-period fitting.
- Baseline and treatment start each fold from the same chained capital.
- This is a research script; do not change production config from this output alone.
"""

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtesting.engine import build_exit_model, generate_candidate_trades, run_backtest
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.walk_forward import build_walk_forward_folds
from config.trading_policy import TradingPolicy
from execution.signal_executor import PaperExecutionConfig


QUALITY_FEATURES = (
    "signal_score",
    "relative_strength",
    "adx",
    "volume_ratio",
)

DEFAULT_THRESHOLDS = (0.60, 0.65, 0.70, 0.75)


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
        rows = []
        for t in candidates:
            rows.append({
                "signal_score": getattr(t, "signal_score", None),
                "relative_strength": getattr(t, "relative_strength", None),
                "adx": getattr(t, "adx", None),
                "volume_ratio": getattr(t, "volume_ratio", None),
            })

        df = pd.DataFrame(rows)
        refs = {}
        for name in QUALITY_FEATURES:
            if name in df.columns:
                refs[name] = pd.to_numeric(
                    df[name], errors="coerce"
                ).dropna()
            else:
                refs[name] = pd.Series(dtype=float)

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
    df = (
        df.dropna(subset=["time"])
          .sort_values("time")
          .drop_duplicates("time", keep="last")
          .set_index("time")
    )
    return df


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
        if breadth is not None and change is not None:
            if breadth >= 60 and change > 0:
                return "RECOVERY"
        return "NEUTRAL"

    return "NEUTRAL"


class StateQualityGateStrategy:
    """
    Entry-only filter.

    Everything downstream remains the existing production/backtest engine:
    exits, sizing, ranking, overlap, max positions, exposure and costs.
    """

    def __init__(self, base_strategy, quality_model, market_health, threshold):
        self.base_strategy = base_strategy
        self.quality_model = quality_model
        self.market_health = market_health
        self.threshold = float(threshold)

    @property
    def name(self):
        return self.base_strategy.name + f"__state_quality_{self.threshold:.2f}"

    def evaluate(self, latest, relative_strength, market_config):
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
            adx = _num(latest.get("ADX14"), 0.0)

        volume_ratio = _num(decision.get("volume_ratio"))
        if volume_ratio is None:
            volume_ratio = _num(latest.get("Vol_Ratio"), 0.0)

        rs = _num(relative_strength, 0.0)

        quality = self.quality_model.score({
            "signal_score": score,
            "relative_strength": rs,
            "adx": adx,
            "volume_ratio": volume_ratio,
        })

        state = classify_market_state(
            latest.get("time"),
            decision.get("regime", market_config.get("regime", "UNKNOWN")),
            self.market_health,
        )

        allowed = state != "DIVERGENT_BULL" and quality >= self.threshold

        result = dict(decision)
        result["market_state"] = state
        result["quality_composite"] = quality

        if not allowed:
            result["status"] = "FILTERED"
            result["reason"] = f"state_quality:{state}:q={quality:.4f}"

        return result


def build_parity(paper: PaperExecutionConfig) -> BacktestPaperParityConfig:
    """
    Build parity config using only constructor fields supported by the
    installed project version.

    Important:
    maximum_orders_per_scan is a PAPER execution setting, not necessarily a
    BacktestPaperParityConfig constructor field. It is therefore deliberately
    not forced into the parity object; the portfolio backtest receives the
    corresponding limit separately via build_kwargs().
    """
    import inspect

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
    accepted = {
        k: v for k, v in values.items()
        if k in params
    }

    return BacktestPaperParityConfig(**accepted)


def build_kwargs(paper, policy, parity, symbols, entry_model, db_path):
    return {
        "symbols": symbols,
        "max_holding_days": policy.maximum_holding_days,
        "entry_model": entry_model,
        "exit_model": build_exit_model(
            name="atr",
            stop_atr_multiplier=policy.stop_atr_multiplier,
            target_atr_multiplier=policy.target_atr_multiplier,
            break_even_trigger=policy.target_atr_multiplier,
            # Canonical current-policy mapping:
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


def make_backtest_config(kwargs):
    """
    Build BacktestConfig only with parameters actually accepted by the local
    engine. This keeps the script compatible with the current project version.
    """
    from backtesting.engine import BacktestConfig
    import inspect

    params = inspect.signature(BacktestConfig).parameters
    return BacktestConfig(**{
        k: v for k, v in kwargs.items()
        if k in params
    })


def generate_candidates_exact(
    symbols,
    kwargs,
    start_date,
    end_date,
):
    """
    generate_candidate_trades() is symbol-level in the current engine.
    We therefore call it once per symbol and combine the candidates.
    """
    config = make_backtest_config(kwargs)
    out = []

    for symbol in symbols:
        try:
            candidates = generate_candidate_trades(
                symbol=symbol,
                config=config,
                db_path=kwargs["db_path"],
                warmup_bars=60,
                verbose=False,
                entry_model=kwargs["entry_model"],
                exit_model=kwargs["exit_model"],
                start_date=str(start_date.date()),
                end_date=str(end_date.date()),
            )
            out.extend(candidates)
        except Exception as exc:
            print(f"[WARN] candidate generation failed for {symbol}: {exc}")

    return out


def closed_summary(trades):
    closed = [t for t in trades if getattr(t, "is_closed", False)]
    returns = np.array(
        [float(getattr(t, "net_return_pct", 0.0)) for t in closed],
        dtype=float,
    )

    if len(returns) == 0:
        return {
            "trades": 0,
            "win_rate_pct": 0.0,
            "avg_trade_pct": 0.0,
            "median_trade_pct": 0.0,
            "profit_factor": 0.0,
        }

    gains = returns[returns > 0].sum()
    losses = -returns[returns < 0].sum()

    return {
        "trades": len(returns),
        "win_rate_pct": float((returns > 0).mean() * 100),
        "avg_trade_pct": float(returns.mean()),
        "median_trade_pct": float(np.median(returns)),
        "profit_factor": float(gains / losses) if losses > 0 else float("inf"),
    }


def compound(xs):
    x = np.asarray(xs, dtype=float)
    return float((np.prod(1.0 + x / 100.0) - 1.0) * 100.0) if len(x) else 0.0


def fold_drawdown(xs):
    eq = np.cumprod(1.0 + np.asarray(xs, dtype=float) / 100.0)
    if len(eq) == 0:
        return 0.0
    peak = np.maximum.accumulate(eq)
    return float(np.min((eq / peak - 1.0) * 100.0))


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--market-health",
        default="research_results/market_state_diagnostic_pit/market_health_pit_daily.csv",
    )
    p.add_argument("--db-path", default="data/market.db")
    p.add_argument(
        "--thresholds",
        nargs="+",
        type=float,
        default=list(DEFAULT_THRESHOLDS),
    )
    p.add_argument("--output-dir", default="research_results/state_quality_portfolio_wfo_fullmarket")
    p.add_argument("--train-months", type=int, default=24)
    p.add_argument("--test-months", type=int, default=6)
    p.add_argument("--step-months", type=int, default=6)
    p.add_argument("--start-date", default="2018-08-07")
    p.add_argument("--end-date", default="2026-08-21")
    args = p.parse_args()

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    health = load_market_health(args.market_health)

    paper = PaperExecutionConfig.from_env()
    policy = TradingPolicy.from_env()
    parity = build_parity(paper)

    # FULL-MARKET DISCOVERY universe: every non-index symbol present in the
    # database. This is intentionally broader than HOLDOUT20.
    import sqlite3
    with sqlite3.connect(args.db_path) as con:
        rows = con.execute(
            "SELECT DISTINCT symbol FROM prices WHERE symbol <> 'VNINDEX' ORDER BY symbol"
        ).fetchall()
    symbols = [str(r[0]).upper() for r in rows if r and r[0]]
    if not symbols:
        raise RuntimeError("No non-VNINDEX symbols found in database.")
    print(f"Research universe: FULL DB ({len(symbols)} symbols)")

    # The local project version exposes build_walk_forward_folds() with
    # positional arguments rather than the keyword names used by this
    # research wrapper. Keep the canonical fold definition unchanged.
    from backtesting.walk_forward import WalkForwardConfig

    wf_config = WalkForwardConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
    )
    folds = build_walk_forward_folds(wf_config)

    all_rows = []
    fold_rows = []
    treatment_capital = {float(t): float(parity.initial_cash) for t in args.thresholds}

    for fold in folds:
        print("\n" + "=" * 100)
        print(
            f"FOLD {fold.fold}: "
            f"TRAIN {fold.train_start.date()} -> {fold.train_end.date()} | "
            f"TEST {fold.test_start.date()} -> {fold.test_end.date()}"
        )

        # -------- TRAIN CANDIDATES --------
        base_train_model = policy.build_entry_model()
        train_kwargs = build_kwargs(
            paper, policy, parity, symbols, base_train_model, args.db_path
        )

        train_candidates = generate_candidates_exact(
            symbols,
            train_kwargs,
            fold.train_start,
            fold.train_end,
        )

        print(f"Train candidates: {len(train_candidates)}")

        quality = FrozenQualityModel.fit(train_candidates)

        # -------- BASELINE PORTFOLIO TEST --------
        base_test_model = policy.build_entry_model()
        base_kwargs = build_kwargs(
            paper, policy, parity, symbols, base_test_model, args.db_path
        )

        # Baseline and each treatment arm have their own chained capital path.
        # This preserves true portfolio-level WFO because position sizing depends
        # on current equity.
        if fold.fold == 1:
            starting_capital = float(parity.initial_cash)
        else:
            starting_capital = float(previous_baseline_capital)

        baseline_trades, baseline_metrics, baseline_equity = run_backtest(
            **base_kwargs,
            start_date=str(fold.test_start.date()),
            end_date=str(fold.test_end.date()),
            initial_capital=starting_capital,
            verbose=False,
        )

        previous_baseline_capital = float(
            baseline_metrics.get("final_equity", starting_capital)
        )

        base_ret = float(baseline_metrics.get("total_return_pct", 0.0))
        base_sum = closed_summary(baseline_trades)

        row = {
            "fold": fold.fold,
            "test_start": str(fold.test_start.date()),
            "test_end": str(fold.test_end.date()),
            "policy": "baseline",
            "threshold": np.nan,
            "starting_capital": starting_capital,
            "final_equity": previous_baseline_capital,
            "fold_return_pct": base_ret,
            **base_sum,
            "train_candidates": len(train_candidates),
        }
        fold_rows.append(row)

        # -------- TREATMENTS --------
        for threshold in args.thresholds:
            gate_model = StateQualityGateStrategy(
                base_strategy=policy.build_entry_model(),
                quality_model=quality,
                market_health=health,
                threshold=threshold,
            )

            gate_kwargs = build_kwargs(
                paper, policy, parity, symbols, gate_model, args.db_path
            )

            treatment_start = float(treatment_capital[float(threshold)])
            gate_trades, gate_metrics, gate_equity = run_backtest(
                **gate_kwargs,
                start_date=str(fold.test_start.date()),
                end_date=str(fold.test_end.date()),
                initial_capital=treatment_start,
                verbose=False,
            )

            gate_ret = float(gate_metrics.get("total_return_pct", 0.0))
            gate_final = float(
                gate_metrics.get("final_equity", treatment_start)
            )
            treatment_capital[float(threshold)] = gate_final
            gate_sum = closed_summary(gate_trades)

            fold_rows.append({
                "fold": fold.fold,
                "test_start": str(fold.test_start.date()),
                "test_end": str(fold.test_end.date()),
                "policy": "candidate_state_quality_gate",
                "threshold": threshold,
                "starting_capital": treatment_start,
                "final_equity": gate_final,
                "fold_return_pct": gate_ret,
                **gate_sum,
                "train_candidates": len(train_candidates),
            })

            for t in gate_trades:
                all_rows.append({
                    "fold": fold.fold,
                    "threshold": threshold,
                    "symbol": getattr(t, "symbol", None),
                    "entry_date": getattr(t, "entry_date", None),
                    "exit_date": getattr(t, "exit_date", None),
                    "net_return_pct": getattr(t, "net_return_pct", None),
                    "is_win": getattr(t, "is_win", None),
                    "signal_score": getattr(t, "signal_score", None),
                    "relative_strength": getattr(t, "relative_strength", None),
                    "adx": getattr(t, "adx", None),
                    "volume_ratio": getattr(t, "volume_ratio", None),
                })

        print(
            f"Baseline: {base_ret:+.3f}% | "
            f"trades={base_sum['trades']} | "
            f"PF={base_sum['profit_factor']:.3f}"
        )

    folds_df = pd.DataFrame(fold_rows)
    trades_df = pd.DataFrame(all_rows)

    # Summary by policy.
    summaries = []
    for (policy_name, threshold), g in folds_df.groupby(
        ["policy", "threshold"], dropna=False
    ):
        returns = g["fold_return_pct"].astype(float).to_numpy()
        summaries.append({
            "policy": policy_name,
            "threshold": threshold,
            "folds": len(g),
            "profitable_folds": int((returns > 0).sum()),
            "mean_fold_return_pct": float(np.mean(returns)),
            "median_fold_return_pct": float(np.median(returns)),
            "compounded_return_pct": compound(returns),
            "fold_max_dd_pct": fold_drawdown(returns),
            "best_fold_pct": float(np.max(returns)),
            "worst_fold_pct": float(np.min(returns)),
            "total_test_trades": int(g["trades"].sum()),
            "mean_win_rate_pct": float(g["win_rate_pct"].mean()),
            "mean_profit_factor": float(g["profit_factor"].replace(np.inf, np.nan).mean()),
        })

    summary_df = pd.DataFrame(summaries)

    folds_df.to_csv(outdir / "portfolio_fold_summary.csv", index=False)
    summary_df.to_csv(outdir / "portfolio_wfo_summary.csv", index=False)
    if not trades_df.empty:
        trades_df.to_csv(outdir / "portfolio_gate_trade_level.csv", index=False)

    print("\n" + "=" * 100)
    print("PORTFOLIO WFO SUMMARY")
    print(
        summary_df.sort_values(
            ["policy", "threshold"], na_position="first"
        ).to_string(index=False)
    )

    print(f"\nSaved to: {outdir.resolve()}")
    print("\nDecision rule:")
    print(
        "Do NOT promote a threshold from this report unless it beats baseline "
        "on out-of-sample portfolio return/DD with stability across folds, "
        "and then survives a separate untouched holdout / nested validation."
    )


if __name__ == "__main__":
    main()
