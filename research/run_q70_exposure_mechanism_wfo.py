
import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from backtesting.engine import run_backtest
from backtesting.walk_forward import WalkForwardConfig, build_walk_forward_folds
from backtesting.paper_parity import BacktestPaperParityConfig
from config.paper_config import PaperExecutionConfig
from config.trading_policy import TradingPolicy


def num(x, default=np.nan):
    try:
        if x is None or pd.isna(x):
            return default
        return float(x)
    except Exception:
        return default


def load_health(path):
    d = pd.read_csv(path)
    required = ["time", "breadth_ema50_pct", "breadth_ema50_change_10d"]
    missing = [c for c in required if c not in d.columns]
    if missing:
        raise ValueError(f"Missing market-health columns {missing}; found {d.columns.tolist()}")
    d["time"] = pd.to_datetime(d["time"]).dt.normalize()
    return d.drop_duplicates("time").set_index("time").sort_index()


def state_from_row(row):
    regime = str(row.get("market_regime", row.get("regime", ""))).upper()
    breadth = num(row.get("breadth_ema50_pct"))
    change = num(row.get("breadth_ema50_change_10d"))

    if regime == "BEAR":
        return "BEAR"
    if regime == "BULL":
        if breadth < 50 and change < 0:
            return "DIVERGENT_BULL"
        if breadth >= 70 and change >= 0:
            return "HEALTHY_BULL"
        return "FRAGILE_BULL"
    if regime == "SIDEWAY" and breadth >= 60 and change > 0:
        return "RECOVERY"
    return "NEUTRAL"


def frozen_quality(train_trades):
    """
    Fit an empirical percentile model on TRAIN candidates only.
    Uses the same four quality dimensions as the previous Q70 research:
    signal score, relative strength, ADX, volume ratio.
    """
    features = ["signal_score", "relative_strength", "adx", "volume_ratio"]
    arrays = {}
    for f in features:
        vals = []
        for t in train_trades:
            v = num(getattr(t, f, np.nan))
            if np.isfinite(v):
                vals.append(v)
        arrays[f] = np.sort(np.asarray(vals, dtype=float))

    def percentile(v, arr):
        if not np.isfinite(v) or len(arr) == 0:
            return 0.5
        return float(np.searchsorted(arr, v, side="right") / len(arr))

    def score(t):
        return float(np.mean([
            percentile(num(getattr(t, "signal_score", np.nan)), arrays["signal_score"]),
            percentile(num(getattr(t, "relative_strength", np.nan)), arrays["relative_strength"]),
            percentile(num(getattr(t, "adx", np.nan)), arrays["adx"]),
            percentile(num(getattr(t, "volume_ratio", np.nan)), arrays["volume_ratio"]),
        ]))

    return score


class FixedQ70Exposure:
    """
    Candidate gate with Q70 and an exposure multiplier by state.

    Crucial mechanism test:
    - candidate eligibility is determined ONLY by Q70
    - exposure multiplier is applied after eligibility
    - no separate state gate is used
    This isolates whether Fragile exposure itself is useful.
    """
    def __init__(self, base_sizer, quality_fn, health, fragile_scale):
        self.base_sizer = base_sizer
        self.quality_fn = quality_fn
        self.health = health
        self.fragile_scale = fragile_scale

    def calculate_quantity(self, context):
        candidate = context.candidate
        q = self.quality_fn(candidate)
        if q < 0.70:
            return 0

        q0 = self.base_sizer.calculate_quantity(context)
        if q0 <= 0:
            return 0

        d = getattr(candidate, "entry_date", None)
        if d is None:
            d = getattr(candidate, "signal_date", None)
        try:
            d = pd.Timestamp(d).normalize()
        except Exception:
            return q0

        if d in self.health.index:
            row = self.health.loc[d]
            # Candidate carries market regime from the backtest.
            state = state_from_row({
                "market_regime": getattr(candidate, "market_regime", ""),
                "breadth_ema50_pct": row["breadth_ema50_pct"],
                "breadth_ema50_change_10d": row["breadth_ema50_change_10d"],
            })
            if state == "DIVERGENT_BULL":
                return 0
            if state == "FRAGILE_BULL":
                q0 = int(q0 * self.fragile_scale)
                return (q0 // context.lot_size) * context.lot_size
        return q0


class Q70Only:
    def __init__(self, base_sizer, quality_fn):
        self.base_sizer = base_sizer
        self.quality_fn = quality_fn

    def calculate_quantity(self, context):
        if self.quality_fn(context.candidate) < 0.70:
            return 0
        return self.base_sizer.calculate_quantity(context)


def make_parity(paper):
    # Compatible with current PaperExecutionConfig variants.
    fields = {
        "initial_cash": paper.initial_cash,
        "commission_rate": paper.commission_rate,
        "sell_tax_rate": getattr(paper, "sell_tax_rate", 0.001),
        "slippage_bps": paper.slippage_bps,
        "lot_size": paper.lot_size,
        "atr_stop_multiplier": getattr(paper, "atr_stop_multiplier", 2.0),
    }
    try:
        return BacktestPaperParityConfig(**fields)
    except TypeError:
        # Fallback for older config constructor.
        return BacktestPaperParityConfig(
            initial_cash=paper.initial_cash,
            commission_rate=paper.commission_rate,
            sell_tax_rate=getattr(paper, "sell_tax_rate", 0.001),
            slippage_bps=paper.slippage_bps,
            lot_size=paper.lot_size,
        )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market-health", required=True)
    ap.add_argument("--db-path", required=True)
    ap.add_argument("--out", default="research_results/q70_exposure_mechanism_wfo")
    ap.add_argument("--start-date", default="2018-08-07")
    ap.add_argument("--end-date", default="2026-08-21")
    ap.add_argument("--fragile-scales", nargs="+", type=float, default=[1.0, 0.75, 0.50, 0.25, 0.0])
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    health = load_health(args.market_health)

    paper = PaperExecutionConfig.from_env()
    policy = TradingPolicy.from_env()
    parity = make_parity(paper)

    con = sqlite3.connect(args.db_path)
    symbols = sorted(pd.read_sql(
        "select distinct symbol from prices where upper(symbol) <> 'VNINDEX'", con
    )["symbol"].tolist())
    con.close()

    folds = build_walk_forward_folds(
        WalkForwardConfig(args.start_date, args.end_date, 24, 6, 6)
    )

    # Try to use the project's existing candidate-generation function.
    from backtesting.engine import generate_candidate_trades

    def build_kwargs():
        return dict(
            db_path=args.db_path,
            initial_capital=paper.initial_cash,
            entry_model=policy.build_entry_model(),
            exit_model=policy.build_exit_model(),
            position_sizer=parity.build_position_sizer(),
            lot_size=paper.lot_size,
            start_date=args.start_date,
            end_date=args.end_date,
            maximum_gross_exposure_pct=getattr(policy, "maximum_gross_exposure_pct", None),
            minimum_cash_buffer_pct=getattr(policy, "minimum_cash_buffer_pct", 0.0),
        )

    policies = [("q70", 1.0), *[
        (f"q70_f{str(x).replace('.', '')}", x) for x in args.fragile_scales if x != 1.0
    ]]

    rows = []
    trade_rows = []

    for fold in folds:
        print(f"\nFOLD {fold.fold}: {fold.train_start.date()} -> {fold.test_end.date()}")

        # Train candidate pool: ALL market symbols, train window only.
        train_trades = []
        for symbol in symbols:
            kw = build_kwargs()
            try:
                ts = generate_candidate_trades(
                    symbol,
                    kw.get("config") if "config" in kw else None,
                    db_path=args.db_path,
                    entry_model=kw["entry_model"],
                    exit_model=kw["exit_model"],
                    start_date=fold.train_start,
                    end_date=fold.train_end,
                )
            except TypeError:
                # Current signature expects BacktestConfig, so construct via the
                # same run_backtest configuration path when needed.
                try:
                    from backtesting.config import BacktestConfig
                    cfg = BacktestConfig(
                        db_path=args.db_path,
                        initial_capital=paper.initial_cash,
                        lot_size=paper.lot_size,
                    )
                    ts = generate_candidate_trades(
                        symbol, cfg, db_path=args.db_path,
                        entry_model=kw["entry_model"],
                        exit_model=kw["exit_model"],
                        start_date=fold.train_start,
                        end_date=fold.train_end,
                    )
                except Exception as e:
                    print("candidate generation failed:", symbol, repr(e))
                    ts = []
            train_trades.extend(ts)

        quality_fn = frozen_quality(train_trades)
        print("train candidates:", len(train_trades))

        for name, fragile_scale in policies:
            # The actual engine WFO remains the authority. This script deliberately
            # keeps the same Q70 candidate gate and changes only sizing by state.
            if name == "q70":
                sizer_factory = lambda: Q70Only(parity.build_position_sizer(), quality_fn)
            else:
                sizer_factory = lambda fs=fragile_scale: FixedQ70Exposure(
                    parity.build_position_sizer(), quality_fn, health, fs
                )

            # Run test portfolio. The exact project API may differ across revisions;
            # keep all arguments centralized for easy adaptation.
            try:
                result = run_backtest(
                    symbols=symbols,
                    db_path=args.db_path,
                    initial_capital=paper.initial_cash,
                    start_date=fold.test_start,
                    end_date=fold.test_end,
                    entry_model=policy.build_entry_model(),
                    exit_model=policy.build_exit_model(),
                    position_sizer=sizer_factory(),
                    lot_size=paper.lot_size,
                    maximum_gross_exposure_pct=getattr(policy, "maximum_gross_exposure_pct", None),
                    minimum_cash_buffer_pct=getattr(policy, "minimum_cash_buffer_pct", 0.0),
                    max_positions=getattr(policy, "max_open_positions", 10),
                )
            except TypeError as e:
                print(f"{name} fold {fold.fold}: engine API mismatch: {e}")
                raise

            trades = getattr(result, "trades", result if isinstance(result, list) else [])
            closed = [t for t in trades if getattr(t, "is_closed", False)]
            returns = np.array([num(getattr(t, "net_return_pct", np.nan)) for t in closed])
            returns = returns[np.isfinite(returns)]

            if len(returns):
                gains = returns[returns > 0].sum()
                losses = -returns[returns < 0].sum()
                pf = gains / losses if losses else np.inf
                fold_return = returns.sum()
                win_rate = (returns > 0).mean() * 100
            else:
                pf = np.nan
                fold_return = 0.0
                win_rate = np.nan

            rows.append({
                "policy": name,
                "fold": fold.fold,
                "test_start": fold.test_start.date(),
                "test_end": fold.test_end.date(),
                "trades": len(returns),
                "fold_return_sum_pct": fold_return,
                "win_rate_pct": win_rate,
                "profit_factor": pf,
            })

            for t in closed:
                trade_rows.append({
                    "policy": name,
                    "fold": fold.fold,
                    "symbol": getattr(t, "symbol", ""),
                    "entry_date": getattr(t, "entry_date", ""),
                    "net_return_pct": getattr(t, "net_return_pct", np.nan),
                    "market_regime": getattr(t, "market_regime", ""),
                    "signal_score": getattr(t, "signal_score", np.nan),
                })

    fold_df = pd.DataFrame(rows)
    trade_df = pd.DataFrame(trade_rows)

    fold_df.to_csv(out / "q70_exposure_fold_summary.csv", index=False)
    trade_df.to_csv(out / "q70_exposure_trade_level.csv", index=False)

    summary = []
    for policy_name, g in fold_df.groupby("policy"):
        r = g["fold_return_sum_pct"].to_numpy(dtype=float)
        comp = np.prod(1 + r / 100) - 1 if len(r) else 0
        summary.append({
            "policy": policy_name,
            "folds": len(g),
            "profitable_folds": int((r > 0).sum()),
            "mean_fold_return_pct": r.mean() if len(r) else np.nan,
            "median_fold_return_pct": np.median(r) if len(r) else np.nan,
            "compounded_oos_return_pct": comp * 100,
            "worst_fold_pct": r.min() if len(r) else np.nan,
            "best_fold_pct": r.max() if len(r) else np.nan,
            "total_test_trades": int(g["trades"].sum()),
            "avg_profit_factor": g["profit_factor"].replace([np.inf, -np.inf], np.nan).mean(),
        })
    summary_df = pd.DataFrame(summary)
    summary_df.to_csv(out / "q70_exposure_summary.csv", index=False)

    print("\n=== SUMMARY ===")
    print(summary_df.to_string(index=False))
    print("\nWrote:", out.resolve())


if __name__ == "__main__":
    main()
