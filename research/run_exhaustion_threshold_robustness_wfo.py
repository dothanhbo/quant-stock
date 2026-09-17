"""Exhaustion risk-adjustment research.

Research-only. Does NOT modify production config/code.

Runs a production-parity WFO sensitivity curve using the existing
research.run_regime_policy_grid machinery:
1) baseline / 100% size
2) exhaustion position size x0.75
3) exhaustion position size x0.50
4) exhaustion position size x0.25
5) hard reject / x0.00

The exhaustion state is recomputed point-in-time from the same market.db
using the signal bar immediately before the candidate entry bar.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pandas as pd

import research.run_regime_policy_grid as grid
from backtesting.engine import load_price_data
from backtesting.portfolio_simulator import PortfolioSimulator as BasePortfolioSimulator
from backtesting.position_sizers import PositionSizer
from execution.signal_executor import PaperExecutionConfig
from config.trading_policy import TradingPolicy
from research.universes import HOLDOUT20_SYMBOLS


RETURN_THRESHOLDS = (5.0, 6.0, 7.0)
VOLUME_THRESHOLDS = (2.0, 2.5, 3.0)
SIZE_FACTOR = 0.50
DEFAULT_OUTPUT = Path("research_results/exhaustion_threshold_robustness_wfo")


class ExhaustionFeatureStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._symbol_cache: dict[str, pd.DataFrame] = {}

    def _load(self, symbol: str) -> pd.DataFrame:
        symbol = str(symbol).upper()
        if symbol not in self._symbol_cache:
            self._symbol_cache[symbol] = load_price_data(symbol, self.db_path)
        return self._symbol_cache[symbol]

    def return_3d_at_signal(self, candidate: Any) -> float | None:
        symbol = str(candidate.symbol).upper()
        df = self._load(symbol)
        if df.empty:
            return None

        entry_date = pd.Timestamp(candidate.entry_date)
        matches = df.index[df["time"].eq(entry_date)]
        if len(matches) == 0:
            # Fallback for timestamp normalization.
            dates = pd.to_datetime(df["time"]).dt.normalize()
            matches = df.index[dates.eq(entry_date.normalize())]
        if len(matches) == 0:
            return None

        entry_idx = int(matches[0])
        signal_idx = entry_idx - 1

        if signal_idx < 3:
            return None

        current_close = float(df.iloc[signal_idx]["close"])
        prior_close = float(df.iloc[signal_idx - 3]["close"])

        if prior_close <= 0:
            return None

        return (current_close / prior_close - 1.0) * 100.0

    def is_exhaustion(
        self,
        candidate: Any,
        return_threshold: float,
        volume_threshold: float,
    ) -> bool:
        ret3 = self.return_3d_at_signal(candidate)
        volume = getattr(candidate, "volume_ratio", None)
        if ret3 is None or volume is None:
            return False
        try:
            return (
                float(ret3) >= float(return_threshold)
                and float(volume) >= float(volume_threshold)
            )
        except (TypeError, ValueError):
            return False


class ExhaustionSizeSizer:
    """PositionSizer wrapper: apply a size multiplier to exhaustion quantities."""

    def __init__(
        self, base: PositionSizer, store: ExhaustionFeatureStore, factor: float,
        return_threshold: float, volume_threshold: float,
    ):
        self.base = base
        self.store = store
        self.factor = float(factor)
        self.return_threshold = float(return_threshold)
        self.volume_threshold = float(volume_threshold)
        self.name = (f"exhaustion_{self.return_threshold:g}pct_"
                      f"{self.volume_threshold:g}x_size_x{self.factor:g}")

    def calculate_quantity(self, context) -> int:
        quantity = int(self.base.calculate_quantity(context))
        if not self.store.is_exhaustion(
            context.candidate, self.return_threshold, self.volume_threshold
        ):
            return quantity

        scaled = int(quantity * self.factor)
        lot = int(context.lot_size)
        if lot <= 0:
            return max(scaled, 0)

        return (scaled // lot) * lot


def make_simulator_class(
    *, mode: str, store: ExhaustionFeatureStore,
    return_threshold: float, volume_threshold: float,
):
    class ExhaustionAwarePortfolioSimulator(BasePortfolioSimulator):
        def __init__(self, *args, **kwargs):
            position_sizer = kwargs.get("position_sizer")

            if mode.startswith("size_x") and position_sizer is not None:
                factor = float(mode.removeprefix("size_x"))
                kwargs["position_sizer"] = ExhaustionSizeSizer(
                    position_sizer, store, factor, return_threshold, volume_threshold
                )

            super().__init__(*args, **kwargs)

        def _open_candidate(
            self,
            *,
            candidate,
            event_date,
            active_trades,
            rejected_trades,
            replacement_opportunities,
        ):
            exhausted = store.is_exhaustion(
                candidate, return_threshold, volume_threshold
            )

            if mode == "size_x0" and exhausted:
                self._reject_trade(
                    rejected_trades=rejected_trades,
                    candidate=candidate,
                    reason="momentum_exhaustion_filter",
                )
                return

            return super()._open_candidate(
                candidate=candidate,
                event_date=event_date,
                active_trades=active_trades,
                rejected_trades=rejected_trades,
                replacement_opportunities=replacement_opportunities,
            )

    ExhaustionAwarePortfolioSimulator.__name__ = (
        f"ExhaustionAwarePortfolioSimulator_{mode}"
    )
    return ExhaustionAwarePortfolioSimulator


def run_variant(
    *, mode: str, return_threshold: float, volume_threshold: float,
    cache, folds, paper,
):
    store = ExhaustionFeatureStore(cache.db_path)
    simulator_cls = make_simulator_class(
        mode=mode, store=store,
        return_threshold=return_threshold, volume_threshold=volume_threshold,
    )
    original_simulator = grid.PortfolioSimulator
    grid.PortfolioSimulator = simulator_cls
    try:
        case = grid.build_cases()[0]
        frame, summary = grid.evaluate_case(
            case=case, folds=folds, cache=cache, paper=paper,
        )
    finally:
        grid.PortfolioSimulator = original_simulator
    frame = frame.copy()
    frame.insert(0, "volume_threshold_x", volume_threshold)
    frame.insert(0, "return_threshold_pct", return_threshold)
    frame.insert(0, "variant", mode)
    return frame, summary

def build_parser():
    parser = argparse.ArgumentParser(
        description="WFO test of exhaustion risk-adjustment variants."
    )
    parser.add_argument("--db", default="data/market.db")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--start", default="2018-08-07")
    parser.add_argument("--end", default="2026-08-25")
    parser.add_argument("--train-months", type=int, default=24)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument("--hold", type=int, default=30)
    parser.add_argument(
        "--output",
        default="research_results/exhaustion_risk_adjustment_sensitivity_wfo",
    )
    return parser


def main():
    args = build_parser().parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    paper = PaperExecutionConfig.from_env()
    trading_policy = TradingPolicy.from_env()

    symbols = (
        list(HOLDOUT20_SYMBOLS)
        if args.symbols is None
        else [s.strip().upper() for s in args.symbols if s.strip()]
    )

    walk_config = grid.WalkForwardConfig(
        start_date=args.start,
        end_date=args.end,
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
    )
    folds = grid.build_walk_forward_folds(walk_config)

    cache = grid.CandidateTradeCache(
        symbols=symbols,
        db_path=args.db,
        trading_policy=trading_policy,
        paper=paper,
        max_holding_days=args.hold,
        exit_mode="production_trailing",
    )

    periods = []
    for fold in folds:
        periods.extend([
            (str(fold.train_start.date()), str(fold.train_end.date())),
            (str(fold.test_start.date()), str(fold.test_end.date())),
        ])

    print(f"Precomputing candidates for {len(periods)} periods...")
    for i, (start, end) in enumerate(periods, 1):
        candidates = cache.get(start, end)
        print(f"[{i:02d}/{len(periods):02d}] {start} -> {end}: {len(candidates)} candidates")

    threshold_pairs = [(r, v) for r in RETURN_THRESHOLDS for v in VOLUME_THRESHOLDS]
    summaries = []
    fold_frames = []

    for return_threshold, volume_threshold in threshold_pairs:
        for mode in ("baseline", f"size_x{SIZE_FACTOR:g}"):
            print("\n" + "=" * 90)
            print(
                f"THRESHOLD: 3D>={return_threshold:g}% | "
                f"VOL>={volume_threshold:g}x | VARIANT: {mode}"
            )
            print("=" * 90)

            frame, summary = run_variant(
                mode=mode,
                return_threshold=return_threshold,
                volume_threshold=volume_threshold,
                cache=cache,
                folds=folds,
                paper=paper,
            )

            summaries.append({
                "return_threshold_pct": return_threshold,
                "volume_threshold_x": volume_threshold,
                "variant": mode,
                **summary,
            })
            fold_frames.append(frame)

            print(
                f"return={summary.get('walk_forward_return_pct', float('nan')):+.2f}% | "
                f"median={summary.get('median_test_return_pct', float('nan')):+.2f}% | "
                f"recent3={summary.get('recent_3_folds_return_pct', float('nan')):+.2f}% | "
                f"DD={summary.get('chained_max_drawdown_pct', float('nan')):+.2f}% | "
                f"gates={summary.get('gate_count', 'n/a')}/6"
            )

    summary_df = pd.DataFrame(summaries)

    preferred = [
        "return_threshold_pct",
        "volume_threshold_x",
        "variant",
        "research_gate_passed",
        "gate_count",
        "walk_forward_return_pct",
        "median_test_return_pct",
        "recent_3_folds_return_pct",
        "chained_max_drawdown_pct",
        "average_test_sharpe",
        "total_test_trades",
        "profitable_folds",
        "losing_folds",
        "worst_test_return_pct",
        "best_test_return_pct",
    ]
    available = [c for c in preferred if c in summary_df.columns]
    summary_df[available].to_csv(
        output / "summary.csv",
        index=False,
        encoding="utf-8-sig",
    )

    folds_df = pd.concat(fold_frames, ignore_index=True)
    folds_df.to_csv(
        output / "folds.csv",
        index=False,
        encoding="utf-8-sig",
    )

    base = summary_df[summary_df["variant"] == "baseline"].copy()
    overlay = summary_df[summary_df["variant"] == f"size_x{SIZE_FACTOR:g}"].copy()

    compare = overlay.merge(
        base,
        on=["return_threshold_pct", "volume_threshold_x"],
        suffixes=("_overlay", "_baseline"),
    )

    for metric in [
        "walk_forward_return_pct",
        "median_test_return_pct",
        "recent_3_folds_return_pct",
        "chained_max_drawdown_pct",
        "average_test_sharpe",
        "profitable_folds",
    ]:
        compare[f"{metric}_delta"] = (
            compare[f"{metric}_overlay"] - compare[f"{metric}_baseline"]
        )

    compare.to_csv(
        output / "threshold_comparison.csv",
        index=False,
        encoding="utf-8-sig",
    )

    # Compact matrices for quick robustness inspection.
    compare.pivot(
        index="return_threshold_pct",
        columns="volume_threshold_x",
        values="walk_forward_return_pct_delta",
    ).to_csv(
        output / "return_delta_matrix.csv",
        encoding="utf-8-sig",
    )

    compare.pivot(
        index="return_threshold_pct",
        columns="volume_threshold_x",
        values="chained_max_drawdown_pct_delta",
    ).to_csv(
        output / "drawdown_delta_matrix.csv",
        encoding="utf-8-sig",
    )

    print("\n" + "=" * 90)
    print("THRESHOLD ROBUSTNESS — FINAL WFO DELTAS")
    print("=" * 90)
    display_cols = [
        "return_threshold_pct", "volume_threshold_x",
        "walk_forward_return_pct_delta", "median_test_return_pct_delta",
        "recent_3_folds_return_pct_delta", "chained_max_drawdown_pct_delta",
        "average_test_sharpe_delta", "profitable_folds_delta",
    ]
    print(
        compare[display_cols]
        .sort_values(["walk_forward_return_pct_delta", "recent_3_folds_return_pct_delta"], ascending=False)
        .to_string(index=False)
    )
    print(f"\nSaved: {output.resolve()}")


if __name__ == "__main__":
    main()
