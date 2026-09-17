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


EXHAUSTION_RETURN_3D = 6.0
EXHAUSTION_VOLUME = 2.5
DEFAULT_OUTPUT = Path("research_results/exhaustion_risk_adjustment_wfo")


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

    def is_exhaustion(self, candidate: Any) -> bool:
        ret3 = self.return_3d_at_signal(candidate)
        volume = getattr(candidate, "volume_ratio", None)

        if ret3 is None or volume is None:
            return False

        try:
            return (
                float(ret3) >= EXHAUSTION_RETURN_3D
                and float(volume) >= EXHAUSTION_VOLUME
            )
        except (TypeError, ValueError):
            return False


class ExhaustionSizeSizer:
    """PositionSizer wrapper: apply a size multiplier to exhaustion quantities."""

    def __init__(self, base: PositionSizer, store: ExhaustionFeatureStore, factor: float):
        self.base = base
        self.store = store
        self.factor = float(factor)
        self.name = f"exhaustion_size_x{self.factor:g}"

    def calculate_quantity(self, context) -> int:
        quantity = int(self.base.calculate_quantity(context))
        if not self.store.is_exhaustion(context.candidate):
            return quantity

        scaled = int(quantity * self.factor)
        lot = int(context.lot_size)
        if lot <= 0:
            return max(scaled, 0)

        return (scaled // lot) * lot


def make_simulator_class(
    *,
    mode: str,
    store: ExhaustionFeatureStore,
):
    class ExhaustionAwarePortfolioSimulator(BasePortfolioSimulator):
        def __init__(self, *args, **kwargs):
            position_sizer = kwargs.get("position_sizer")

            if mode.startswith("size_x") and position_sizer is not None:
                factor = float(mode.removeprefix("size_x"))
                kwargs["position_sizer"] = ExhaustionSizeSizer(
                    position_sizer,
                    store,
                    factor,
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
            exhausted = store.is_exhaustion(candidate)

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
    *,
    mode: str,
    cache,
    folds,
    paper,
    output_dir: Path,
):
    store = ExhaustionFeatureStore(cache.db_path)
    simulator_cls = make_simulator_class(mode=mode, store=store)

    original_simulator = grid.PortfolioSimulator
    grid.PortfolioSimulator = simulator_cls
    try:
        case = grid.build_cases()[0]  # baseline_no_regime_policy
        frame, summary = grid.evaluate_case(
            case=case,
            folds=folds,
            cache=cache,
            paper=paper,
        )
    finally:
        grid.PortfolioSimulator = original_simulator

    frame = frame.copy()
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

    variants = (
        "baseline",
        "size_x0.75",
        "size_x0.50",
        "size_x0.25",
        "size_x0",
    )

    summaries = []
    fold_frames = []

    for mode in variants:
        print("\n" + "=" * 90)
        print(f"VARIANT: {mode}")
        print("=" * 90)

        frame, summary = run_variant(
            mode=mode,
            cache=cache,
            folds=folds,
            paper=paper,
            output_dir=output,
        )

        summaries.append({
            "variant": mode,
            **summary,
        })
        fold_frames.append(frame)

        print(
            f"return={summary['walk_forward_return_pct']:+.2f}% | "
            f"median={summary['median_test_return_pct']:+.2f}% | "
            f"recent3={summary['recent_3_folds_return_pct']:+.2f}% | "
            f"DD={summary['chained_max_drawdown_pct']:+.2f}% | "
            f"gates={summary['gate_count']}/6"
        )

    summary_df = pd.DataFrame(summaries)

    preferred = [
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
    summary_df[preferred].to_csv(
        output / "summary.csv",
        index=False,
        encoding="utf-8-sig",
    )

    pd.concat(fold_frames, ignore_index=True).to_csv(
        output / "folds.csv",
        index=False,
        encoding="utf-8-sig",
    )

    (output / "README.md").write_text(
        """# Exhaustion Risk Adjustment WFO

Research-only. No production configuration changed.

Exhaustion definition:
- signal 3D return >= 6%
- volume ratio >= 2.5x

Variants:
- baseline / 100% size
- size_x0.75
- size_x0.50
- size_x0.25
- size_x0 / hard reject

The experiment reuses `research.run_regime_policy_grid` and therefore the
same candidate generation, production-trailing exit, paper execution
configuration, portfolio simulator, ranking, transaction costs and WFO
gates.

Do not promote a variant from full-sample performance alone. Require
stable OOS/WFO evidence and inspect fold-level results.
""",
        encoding="utf-8",
    )

    print("\n" + "=" * 90)
    print("FINAL COMPARISON")
    print("=" * 90)
    print(
        summary_df[preferred]
        .sort_values(
            ["research_gate_passed", "gate_count", "median_test_return_pct"],
            ascending=[False, False, False],
        )
        .to_string(index=False)
    )
    print(f"\nSaved: {output.resolve()}")


if __name__ == "__main__":
    main()
