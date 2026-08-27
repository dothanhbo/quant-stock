"""Validate two pre-registered BULL entry brakes with production-parity WFO.

This script deliberately does not optimize thresholds.  The audit nominated
only two hypotheses, both evaluated on the causal signal row before next-open
execution:

1. Do not open a BULL position after VNINDEX has risen >= 7% in 20 sessions.
2. Additionally reject a BULL signal whose stock volume is >= 2.5x MA20.

The portfolio simulator is rerun for each case; this is not a post-hoc filter
of executed trades, so capital, candidate competition and risk limits remain
honest.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*args, **kwargs) -> bool:
        return False

from backtesting.engine import BacktestConfig, generate_candidate_trades
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.portfolio_metrics import calculate_portfolio_metrics
from backtesting.portfolio_simulator import PortfolioSimulator
from backtesting.prepared_data import prepare_backtest_dataset
from backtesting.regime_policy import RegimePortfolioPolicy
from backtesting.transaction_cost import TransactionCostConfig
from backtesting.walk_forward import WalkForwardConfig, build_walk_forward_folds
from config.trading_policy import TradingPolicy
from execution.signal_executor import PaperExecutionConfig
from research.run_exit_policy_matrix import (
    ExitPolicyCase,
    PeriodResult,
    build_exit_model,
    build_fold_row,
    rank_summary,
    summarize_case,
    trade_rows,
)
from research.universes import HOLDOUT20_SYMBOLS


DEFAULT_OUTPUT_DIR = Path("research_results/bull_filter_wfo")


@dataclass(frozen=True, slots=True)
class BullFilterCase:
    case_id: str
    description: str
    block_market_return_20d_ge: float | None = None
    block_volume_ratio_ge: float | None = None


def build_cases() -> list[BullFilterCase]:
    """Pre-registered cases; do not extend from a successful WFO output."""
    return [
        BullFilterCase(
            case_id="baseline_fixed_atr_2_4",
            description="No BULL entry brake; fixed ATR 2/4 baseline.",
        ),
        BullFilterCase(
            case_id="bull_skip_market_return20_ge_7",
            description=(
                "In BULL only, block new entries when VNINDEX return over "
                "the signal-day trailing 20 sessions is >= 7%."
            ),
            block_market_return_20d_ge=7.0,
        ),
        BullFilterCase(
            case_id="bull_skip_market_return20_ge_7_or_volume_2_5x",
            description=(
                "In BULL only, also block stock volume spikes >= 2.5x MA20."
            ),
            block_market_return_20d_ge=7.0,
            block_volume_ratio_ge=2.5,
        ),
    ]


def production_symbols(db_path: str) -> list[str]:
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT UPPER(symbol)
            FROM prices
            WHERE UPPER(symbol) <> 'VNINDEX'
            ORDER BY UPPER(symbol)
            """
        ).fetchall()
    return [str(row[0]) for row in rows]


def finite_number(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def allow_entry(
    case: BullFilterCase,
    latest: pd.Series,
    evaluation: dict[str, Any] | None = None,
) -> bool:
    """Return whether a passed signal may become a next-open candidate."""
    evaluation = evaluation or {}
    regime = str(
        evaluation.get("regime", latest.get("Market_Regime", "UNKNOWN"))
    ).upper()
    if regime != "BULL":
        return True

    market_return = finite_number(latest.get("Index_Return_20D"))
    if (
        case.block_market_return_20d_ge is not None
        and market_return is not None
        and market_return >= case.block_market_return_20d_ge
    ):
        return False

    volume_ratio = finite_number(
        evaluation.get("volume_ratio", latest.get("Volume_Ratio"))
    )
    if (
        case.block_volume_ratio_ge is not None
        and volume_ratio is not None
        and volume_ratio >= case.block_volume_ratio_ge
    ):
        return False

    return True


class FilteredCandidateTradeCache:
    """Candidate cache that applies a named research gate before simulation."""

    def __init__(
        self,
        *,
        case: BullFilterCase,
        symbols: list[str],
        db_path: str,
        trading_policy: TradingPolicy,
        paper: PaperExecutionConfig,
        max_holding_days: int,
        market_price_scale: float,
    ) -> None:
        self.case = case
        self.symbols = sorted({value.strip().upper() for value in symbols})
        self.db_path = db_path
        self.trading_policy = trading_policy
        self.paper = paper
        self.max_holding_days = max_holding_days
        self.market_price_scale = market_price_scale
        self._cache: dict[tuple[str, str], list] = {}
        self._metadata: dict[tuple[str, str], dict[str, Any]] = {}
        self._prepared_by_symbol: dict[str, pd.DataFrame] = {}

    def _config(self) -> BacktestConfig:
        return BacktestConfig(
            max_holding_days=self.max_holding_days,
            initial_capital=self.paper.initial_cash,
            buy_commission_pct=self.paper.commission_rate * 100,
            sell_commission_pct=self.paper.commission_rate * 100,
            sell_tax_pct=self.paper.sell_tax_rate * 100,
            buy_slippage_pct=self.paper.slippage_bps / 100,
            sell_slippage_pct=self.paper.slippage_bps / 100,
            ranking_method="signal_score",
            market_price_scale=self.market_price_scale,
        )

    def _prepared_data(self, symbol: str) -> pd.DataFrame:
        if symbol not in self._prepared_by_symbol:
            data = prepare_backtest_dataset(
                symbol,
                db_path=self.db_path,
            ).copy()
            if not data.empty:
                data["time"] = pd.to_datetime(data["time"], errors="coerce")
                data = (
                    data.dropna(subset=["time"])
                    .sort_values("time")
                    .reset_index(drop=True)
                )
            self._prepared_by_symbol[symbol] = data
        return self._prepared_by_symbol[symbol]

    def _signal_row_before_entry(self, trade) -> pd.Series | None:
        data = self._prepared_data(str(trade.symbol).upper())
        if data.empty:
            return None
        entry_date = pd.Timestamp(trade.entry_date)
        prior = data[data["time"] < entry_date]
        if prior.empty:
            return None
        return prior.iloc[-1]

    def _allow_trade(self, trade) -> bool:
        # The candidate holds the strategy evaluation values.  Market return
        # is reconstructed from the strictly preceding signal row, never from
        # entry-day or exit-day data.
        if (
            self.case.block_market_return_20d_ge is None
            and self.case.block_volume_ratio_ge is None
        ):
            return True
        if str(getattr(trade, "market_regime", "UNKNOWN")).upper() != "BULL":
            return True
        latest = self._signal_row_before_entry(trade)
        if latest is None:
            # Preserve the candidate rather than silently applying a filter
            # with missing data; metadata makes this visible for review.
            return True
        return allow_entry(
            self.case,
            latest,
            {
                "regime": getattr(trade, "market_regime", "UNKNOWN"),
                "volume_ratio": getattr(trade, "volume_ratio", None),
            },
        )

    def get(self, start_date: str, end_date: str) -> list:
        key = (start_date, end_date)
        if key in self._cache:
            return self._cache[key]
        config = self._config()
        entry_model = self.trading_policy.build_entry_model()
        fixed_exit = ExitPolicyCase(
            case_id="fixed_atr_2_4",
            exit_family="fixed",
            stop_atr=2.0,
            target_atr=4.0,
        )
        raw_candidates = []
        for symbol in self.symbols:
            raw_candidates.extend(
                generate_candidate_trades(
                    symbol=symbol,
                    config=config,
                    db_path=self.db_path,
                    warmup_bars=60,
                    verbose=False,
                    start_date=start_date,
                    end_date=end_date,
                    entry_model=entry_model,
                    exit_model=build_exit_model(fixed_exit),
                )
            )
        candidates = [
            trade for trade in raw_candidates if self._allow_trade(trade)
        ]
        self._cache[key] = candidates
        self._metadata[key] = {
            "case_id": self.case.case_id,
            "start_date": start_date,
            "end_date": end_date,
            "raw_candidate_trades": len(raw_candidates),
            "candidate_trades": len(candidates),
            "blocked_candidates": len(raw_candidates) - len(candidates),
        }
        return candidates

    def metadata(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                self._metadata[(start, end)]
                for start, end in sorted(self._cache)
            ]
        )


def simulate_period(
    *,
    cache: FilteredCandidateTradeCache,
    paper: PaperExecutionConfig,
    initial_capital: float,
    start_date: str,
    end_date: str,
) -> PeriodResult:
    candidates = copy.deepcopy(cache.get(start_date, end_date))
    parity = BacktestPaperParityConfig.from_paper_config(
        paper,
        sell_tax_rate=paper.sell_tax_rate,
    )
    costs = TransactionCostConfig(
        buy_commission_pct=paper.commission_rate * 100,
        sell_commission_pct=paper.commission_rate * 100,
        sell_tax_pct=paper.sell_tax_rate * 100,
        buy_slippage_pct=paper.slippage_bps / 100,
        sell_slippage_pct=paper.slippage_bps / 100,
    )
    simulator = PortfolioSimulator(
        initial_cash=initial_capital,
        position_size_pct=paper.fixed_fraction_pct,
        position_sizer=parity.build_position_sizer(),
        ranking_method="signal_score",
        transaction_cost_config=costs,
        regime_policy=RegimePortfolioPolicy(),
        max_positions=paper.maximum_open_positions,
        lot_size=paper.lot_size,
        max_new_positions_per_day=paper.maximum_orders_per_scan,
        maximum_gross_exposure_pct=paper.maximum_gross_exposure_pct,
        minimum_cash_buffer_pct=paper.minimum_cash_buffer_pct,
    )
    result = simulator.simulate(candidates)
    config = BacktestConfig(
        max_holding_days=cache.max_holding_days,
        initial_capital=initial_capital,
        buy_commission_pct=paper.commission_rate * 100,
        sell_commission_pct=paper.commission_rate * 100,
        sell_tax_pct=paper.sell_tax_rate * 100,
        buy_slippage_pct=paper.slippage_bps / 100,
        sell_slippage_pct=paper.slippage_bps / 100,
        ranking_method="signal_score",
        market_price_scale=cache.market_price_scale,
    )
    from backtesting.engine import calculate_metrics

    metrics = calculate_metrics(result.executed_trades, config)
    metrics.update(
        calculate_portfolio_metrics(
            result.equity_curve,
            final_equity=result.final_equity,
        )
    )
    metrics["final_equity"] = float(result.final_equity)
    metrics["total_return_pct"] = (
        float(result.final_equity) / initial_capital - 1.0
    ) * 100.0
    metrics["rejected_trades"] = len(result.rejected_trades)
    return PeriodResult(
        trades=result.executed_trades,
        metrics=metrics,
        equity=result.equity_curve,
    )


def evaluate_case(
    *,
    case: BullFilterCase,
    folds,
    cache: FilteredCandidateTradeCache,
    paper: PaperExecutionConfig,
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    report_case = ExitPolicyCase(
        case_id=case.case_id,
        exit_family="fixed",
        stop_atr=2.0,
        target_atr=4.0,
    )
    current_capital = float(paper.initial_cash)
    rows: list[dict[str, Any]] = []
    all_trade_rows: list[dict[str, Any]] = []
    equity_curves: list[pd.DataFrame] = []
    for fold in folds:
        train = simulate_period(
            cache=cache,
            paper=paper,
            initial_capital=paper.initial_cash,
            start_date=str(fold.train_start.date()),
            end_date=str(fold.train_end.date()),
        )
        test_initial = current_capital
        test = simulate_period(
            cache=cache,
            paper=paper,
            initial_capital=test_initial,
            start_date=str(fold.test_start.date()),
            end_date=str(fold.test_end.date()),
        )
        current_capital = float(test.metrics["final_equity"])
        equity_curves.append(test.equity.copy())
        rows.append(
            build_fold_row(
                case=report_case,
                fold=fold,
                train=train,
                test=test,
                test_initial_capital=test_initial,
            )
        )
        all_trade_rows.extend(trade_rows(report_case, fold.fold, test.trades))
    frame = pd.DataFrame(rows)
    summary = summarize_case(
        case=report_case,
        folds_frame=frame,
        equity_curves=equity_curves,
        initial_capital=paper.initial_cash,
    )
    summary["filter_description"] = case.description
    summary["block_market_return_20d_ge"] = case.block_market_return_20d_ge
    summary["block_volume_ratio_ge"] = case.block_volume_ratio_ge
    return frame, summary, pd.DataFrame(all_trade_rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pre-registered BULL entry-filter walk-forward test."
    )
    parser.add_argument("--db", default="data/market.db")
    parser.add_argument(
        "--universe",
        choices=("holdout20", "production"),
        default="holdout20",
    )
    parser.add_argument("--start", default="2018-08-07")
    parser.add_argument("--end", default="2026-08-25")
    parser.add_argument("--train-months", type=int, default=24)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument("--hold", type=int, default=30)
    parser.add_argument("--market-price-scale", type=float, default=1000.0)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()
    if args.market_price_scale <= 0:
        raise ValueError("--market-price-scale must be greater than 0")
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    paper = PaperExecutionConfig.from_env()
    trading_policy = TradingPolicy.from_env()
    symbols = (
        production_symbols(args.db)
        if args.universe == "production"
        else list(HOLDOUT20_SYMBOLS)
    )
    folds = build_walk_forward_folds(
        WalkForwardConfig(
            start_date=args.start,
            end_date=args.end,
            train_months=args.train_months,
            test_months=args.test_months,
            step_months=args.step_months,
        )
    )
    periods = [
        period
        for fold in folds
        for period in (
            (str(fold.train_start.date()), str(fold.train_end.date())),
            (str(fold.test_start.date()), str(fold.test_end.date())),
        )
    ]
    cases = build_cases()
    summaries = []
    fold_frames = []
    trade_frames = []
    metadata_frames = []
    for index, case in enumerate(cases, start=1):
        cache = FilteredCandidateTradeCache(
            case=case,
            symbols=symbols,
            db_path=args.db,
            trading_policy=trading_policy,
            paper=paper,
            max_holding_days=args.hold,
            market_price_scale=args.market_price_scale,
        )
        print(f"PRECOMPUTE {index:02d}/{len(cases):02d} {case.case_id}")
        for period_index, (start, end) in enumerate(periods, start=1):
            candidates = cache.get(start, end)
            print(f"  [{period_index:02d}/{len(periods):02d}] {start} -> {end}: {len(candidates)}")
        frame, summary, trades = evaluate_case(
            case=case,
            folds=folds,
            cache=cache,
            paper=paper,
        )
        summaries.append(summary)
        fold_frames.append(frame)
        trade_frames.append(trades)
        metadata_frames.append(cache.metadata())
        print(
            f"RESULT {case.case_id}: "
            f"return={summary['walk_forward_return_pct']:+.2f}% "
            f"median={summary['median_test_return_pct']:+.2f}% "
            f"gates={summary['gate_count']}/{summary['gate_total']}"
        )
        rank_summary(pd.DataFrame(summaries)).to_csv(
            output_dir / "matrix_summary_partial.csv",
            index=False,
            encoding="utf-8-sig",
        )
    summary_frame = rank_summary(pd.DataFrame(summaries))
    frames = {
        "matrix_summary.csv": summary_frame,
        "matrix_folds.csv": pd.concat(fold_frames, ignore_index=True),
        "trade_level_oos.csv": pd.concat(trade_frames, ignore_index=True),
        "candidate_cache_metadata.csv": pd.concat(metadata_frames, ignore_index=True),
    }
    for name, frame in frames.items():
        frame.to_csv(output_dir / name, index=False, encoding="utf-8-sig")
    (output_dir / "config.json").write_text(
        json.dumps(
            {
                "purpose": "pre-registered BULL filter validation, not optimization",
                "feature_timing": "signal session before next-open entry",
                "cases": [asdict(case) for case in cases],
                "frozen_exit": "fixed ATR stop 2, target 4",
                "universe_name": args.universe,
                "universe": symbols,
                "market_price_scale": args.market_price_scale,
                "walk_forward": {
                    "start": args.start,
                    "end": args.end,
                    "train_months": args.train_months,
                    "test_months": args.test_months,
                    "step_months": args.step_months,
                    "max_holding_sessions": args.hold,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("\nROBUST RANKING")
    print(
        summary_frame[
            [
                "robust_rank",
                "case_id",
                "gate_count",
                "walk_forward_return_pct",
                "median_test_return_pct",
                "recent_3_folds_return_pct",
                "chained_max_drawdown_pct",
                "total_test_trades",
            ]
        ].to_string(index=False)
    )
    print(f"\nSaved: {output_dir.resolve()}")


if __name__ == "__main__":
    main()
