
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from backtesting.engine import BacktestConfig, build_exit_model, generate_candidate_trades, run_backtest
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.walk_forward import WalkForwardConfig, build_walk_forward_folds
from config.trading_policy import TradingPolicy
from execution.signal_executor import PaperExecutionConfig
from research.universes import HOLDOUT20_SYMBOLS


# Controlled Q70 architecture ablation.
#
# A: base signal contains RS + Q70 contains RS
# B: base signal contains RS + Q70 does NOT contain RS
# C: base signal does NOT contain RS + Q70 contains RS
# D: base signal does NOT contain RS + Q70 does NOT contain RS
#
# "Base signal does NOT contain RS" is implemented by passing 0.0 as the
# relative_strength input to the existing entry model. This removes RS from
# the entry model's score/RS condition while leaving the real RS value available
# to the Q70 feature when that feature is enabled.
VARIANTS = {
    "A_current": ("base_rs", "q70_rs"),
    "B_no_q70_rs": ("base_rs", "no_q70_rs"),
    "C_no_base_rs": ("no_base_rs", "q70_rs"),
    "D_no_rs": ("no_base_rs", "no_q70_rs"),
}

Q70_FEATURES_WITH_RS = ("signal_score", "relative_strength", "adx", "volume_ratio")
Q70_FEATURES_NO_RS = ("signal_score", "adx", "volume_ratio")


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
class FrozenVariantQuality:
    features: tuple[str, ...]
    refs: dict[str, pd.Series]

    @classmethod
    def fit(cls, candidates: list[Any], features: tuple[str, ...]) -> "FrozenVariantQuality":
        rows = [{
            "signal_score": getattr(t, "signal_score", None),
            "relative_strength": getattr(t, "relative_strength", None),
            "adx": getattr(t, "adx", None),
            "volume_ratio": getattr(t, "volume_ratio", None),
        } for t in candidates]
        df = pd.DataFrame(rows)
        refs = {
            name: pd.to_numeric(df[name], errors="coerce").dropna()
            if name in df.columns else pd.Series(dtype=float)
            for name in features
        }
        return cls(features=features, refs=refs)

    def score(self, values: dict[str, Any]) -> float:
        return float(np.mean([
            frozen_percentile(values.get(name), self.refs[name])
            for name in self.features
        ]))


class VariantEntryModel:
    """Thin wrapper around the existing entry model.

    For C/D, the real market RS is withheld from the base entry model by
    passing 0.0. For A/B, the real RS is passed unchanged.
    """

    def __init__(self, base_model: Any, use_base_rs: bool):
        self.base_model = base_model
        self.use_base_rs = bool(use_base_rs)

    @property
    def name(self) -> str:
        suffix = "with_rs" if self.use_base_rs else "without_rs"
        return f"{self.base_model.name}__{suffix}"

    def evaluate(self, latest, relative_strength, market_config):
        rs_for_base = relative_strength if self.use_base_rs else 0.0
        return self.base_model.evaluate(
            latest=latest,
            relative_strength=rs_for_base,
            market_config=market_config,
        )


class VariantQualityGate:
    def __init__(
        self,
        base_model: Any,
        use_base_rs: bool,
        quality_model: FrozenVariantQuality,
        market_health: pd.DataFrame,
        threshold: float,
        use_q70_rs: bool,
    ):
        self.base_model = VariantEntryModel(base_model, use_base_rs)
        self.quality_model = quality_model
        self.market_health = market_health
        self.threshold = float(threshold)
        self.use_q70_rs = bool(use_q70_rs)

    @property
    def name(self) -> str:
        q = "q70_rs" if self.use_q70_rs else "q70_no_rs"
        return f"{self.base_model.name}__{q}"

    def evaluate(self, latest, relative_strength, market_config):
        decision = self.base_model.evaluate(
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
        quality = self.quality_model.score(values)

        state = classify_market_state(
            latest.get("time"),
            decision.get("regime", market_config.get("regime", "UNKNOWN")),
            self.market_health,
        )

        result = dict(decision)
        result["market_state"] = state
        result["quality_composite"] = quality

        if state == "DIVERGENT_BULL" or quality < self.threshold:
            result["status"] = "FILTERED"
            result["reason"] = (
                f"q70_ablation:{state}:quality={quality:.4f}"
            )
        return result


def load_market_health(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"time", "breadth_ema50_pct", "breadth_ema50_change_10d"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"market-health missing: {sorted(missing)}")
    df["time"] = pd.to_datetime(df["time"], errors="coerce").dt.normalize()
    return (
        df.dropna(subset=["time"])
        .sort_values("time")
        .drop_duplicates("time", keep="last")
        .set_index("time")
    )


def classify_market_state(signal_date: Any, regime: str, health: pd.DataFrame) -> str:
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
        if breadth is not None and change is not None and breadth >= 60 and change > 0:
            return "RECOVERY"
        return "NEUTRAL"
    return "NEUTRAL"


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


def generate_candidates_exact(kwargs: dict[str, Any], symbols: list[str], start: str, end: str):
    params = set(BacktestConfig.__dataclass_fields__.keys())
    config_kwargs = {k: v for k, v in kwargs.items() if k in params}
    config = BacktestConfig(**config_kwargs)
    out = []
    for symbol in symbols:
        out.extend(generate_candidate_trades(
            symbol=symbol,
            config=config,
            db_path=kwargs["db_path"],
            entry_model=kwargs["entry_model"],
            exit_model=kwargs["exit_model"],
            start_date=start,
            end_date=end,
            verbose=False,
        ))
    return out


def summarize(values: list[float]) -> dict[str, float]:
    r = np.asarray(values, dtype=float)
    return {
        "folds": int(len(r)),
        "profitable_folds": int((r > 0).sum()),
        "mean_fold_return_pct": float(r.mean()) if len(r) else 0.0,
        "median_fold_return_pct": float(np.median(r)) if len(r) else 0.0,
        "compounded_oos_return_pct": float((np.prod(1 + r / 100) - 1) * 100) if len(r) else 0.0,
        "worst_fold_pct": float(r.min()) if len(r) else 0.0,
        "best_fold_pct": float(r.max()) if len(r) else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="V2 Q70 Market-RS double-count WFO ablation.")
    parser.add_argument("--market-health", required=True)
    parser.add_argument("--db-path", default="data/market.db")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--start", default="2018-08-07")
    parser.add_argument("--end", default="2026-08-21")
    parser.add_argument("--train-months", type=int, default=24)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument("--quality-threshold", type=float, default=0.70)
    parser.add_argument("--output", default="research_results/q70_market_rs_ablation_wfo")
    args = parser.parse_args()

    paper = PaperExecutionConfig.from_env()
    policy = TradingPolicy.from_env()
    parity = BacktestPaperParityConfig.from_paper_config(paper)
    symbols = list(HOLDOUT20_SYMBOLS) if args.symbols is None else [
        s.strip().upper() for s in args.symbols if s.strip()
    ]
    health = load_market_health(args.market_health)

    wf = WalkForwardConfig(
        start_date=args.start,
        end_date=args.end,
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
    )
    folds = build_walk_forward_folds(wf)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    fold_rows = []
    variant_returns = {name: [] for name in VARIANTS}
    variant_trades = {name: [] for name in VARIANTS}
    capital = {name: float(parity.initial_cash) for name in VARIANTS}

    for fold in folds:
        print("\n" + "=" * 100)
        print(f"Q70 RS ABLATION FOLD {fold.fold}")
        print(
            f"Train: {fold.train_start.date()} -> {fold.train_end.date()} | "
            f"Test: {fold.test_start.date()} -> {fold.test_end.date()}"
        )

        for variant, (base_kind, q_kind) in VARIANTS.items():
            use_base_rs = base_kind == "base_rs"
            use_q70_rs = q_kind == "q70_rs"
            features = Q70_FEATURES_WITH_RS if use_q70_rs else Q70_FEATURES_NO_RS

            train_model = VariantEntryModel(policy.build_entry_model(), use_base_rs)
            train_kwargs = build_kwargs(
                paper, policy, parity, symbols, train_model, args.db_path
            )
            train_candidates = generate_candidates_exact(
                train_kwargs, symbols,
                str(fold.train_start.date()),
                str(fold.train_end.date()),
            )
            quality_model = FrozenVariantQuality.fit(train_candidates, features)

            gate = VariantQualityGate(
                base_model=policy.build_entry_model(),
                use_base_rs=use_base_rs,
                quality_model=quality_model,
                market_health=health,
                threshold=args.quality_threshold,
                use_q70_rs=use_q70_rs,
            )
            test_kwargs = build_kwargs(
                paper, policy, parity, symbols, gate, args.db_path
            )
            trades, metrics, _ = run_backtest(
                **test_kwargs,
                start_date=str(fold.test_start.date()),
                end_date=str(fold.test_end.date()),
                initial_capital=capital[variant],
                verbose=False,
            )
            ret = float(metrics.get("total_return_pct", 0.0))
            final_equity = float(metrics.get("final_equity", capital[variant]))
            capital[variant] = final_equity
            variant_returns[variant].append(ret)
            variant_trades[variant].append(len(trades))

            fold_rows.append({
                "variant": variant,
                "fold": fold.fold,
                "train_start": fold.train_start.date(),
                "train_end": fold.train_end.date(),
                "test_start": fold.test_start.date(),
                "test_end": fold.test_end.date(),
                "train_candidates": len(train_candidates),
                "test_trades": len(trades),
                "test_return_pct": ret,
                "final_equity": final_equity,
                "base_rs_enabled": use_base_rs,
                "q70_rs_enabled": use_q70_rs,
                "q70_feature_count": len(features),
            })
            print(
                f"{variant:<15} return={ret:+7.2f}% | trades={len(trades):3d} | "
                f"train_candidates={len(train_candidates):4d}"
            )

    fold_df = pd.DataFrame(fold_rows)
    fold_df.to_csv(output / "fold_summary.csv", index=False, encoding="utf-8-sig")

    summaries = []
    for variant in VARIANTS:
        s = summarize(variant_returns[variant])
        s.update({
            "variant": variant,
            "total_trades": int(sum(variant_trades[variant])),
        })
        summaries.append(s)
    summary_df = pd.DataFrame(summaries)[[
        "variant", "folds", "profitable_folds", "mean_fold_return_pct",
        "median_fold_return_pct", "compounded_oos_return_pct",
        "total_trades", "worst_fold_pct", "best_fold_pct",
    ]]
    summary_df.to_csv(output / "summary.csv", index=False, encoding="utf-8-sig")

    print("\n=== V2 Q70 MARKET RS ABLATION ===")
    print(summary_df.to_string(index=False))
    print(f"\nOutput: {output.resolve()}")


if __name__ == "__main__":
    main()
