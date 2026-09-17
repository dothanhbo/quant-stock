"""Production-parity regime-policy grid and nested walk-forward validation.

Entry, exit, ranking, costs, sizing and global risk limits are frozen to the
paper-trading policy.  Only BULL/SIDEWAY/BEAR portfolio rules vary.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*args, **kwargs) -> bool:
        return False

from backtesting.engine import (
    BacktestConfig,
    calculate_metrics,
    generate_candidate_trades,
)
from backtesting.exit_models import TrailingATRExitModel
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.portfolio_metrics import calculate_portfolio_metrics
from backtesting.portfolio_simulator import PortfolioSimulator
from backtesting.regime_policy import (
    RegimePortfolioPolicy,
    RegimePortfolioRule,
)
from backtesting.transaction_cost import TransactionCostConfig
from backtesting.walk_forward import (
    WalkForwardConfig,
    build_walk_forward_folds,
    calculate_chained_drawdown_pct,
)
from config.trading_policy import TradingPolicy
from execution.signal_executor import PaperExecutionConfig
from research.universes import HOLDOUT20_SYMBOLS


DEFAULT_OUTPUT_DIR = Path(
    "research_results/regime_policy_grid_current"
)

BULL_POSITION_GRID = (5, 7, 10)
SIDEWAY_POSITION_GRID = (3, 4, 5)
BEAR_MODE_GRID = ("block", "one")
BULL_HEAT_GRID = (4.0, 5.0, 6.0)
SIDEWAY_HEAT_GRID = (3.0, 4.0, 5.0)
BEAR_ONE_HEAT_PCT = 1.0


@dataclass(frozen=True, slots=True)
class RegimeGridCase:
    case_id: str
    baseline: bool
    bull_max_positions: int | None
    bull_heat_pct: float | None
    sideway_max_positions: int | None
    sideway_heat_pct: float | None
    bear_mode: str
    is_current_production: bool = False


@dataclass(slots=True)
class PeriodResult:
    trades: list
    metrics: dict[str, Any]
    equity: pd.DataFrame


def build_cases() -> list[RegimeGridCase]:
    cases = [
        RegimeGridCase(
            case_id="baseline_no_regime_policy",
            baseline=True,
            bull_max_positions=None,
            bull_heat_pct=None,
            sideway_max_positions=None,
            sideway_heat_pct=None,
            bear_mode="unrestricted",
        )
    ]

    for bull_positions in BULL_POSITION_GRID:
        for sideway_positions in SIDEWAY_POSITION_GRID:
            for bear_mode in BEAR_MODE_GRID:
                for bull_heat in BULL_HEAT_GRID:
                    for sideway_heat in SIDEWAY_HEAT_GRID:
                        case_id = (
                            f"bull{bull_positions}_h{bull_heat:g}__"
                            f"side{sideway_positions}_h{sideway_heat:g}__"
                            f"bear_{bear_mode}"
                        )
                        cases.append(
                            RegimeGridCase(
                                case_id=case_id,
                                baseline=False,
                                bull_max_positions=bull_positions,
                                bull_heat_pct=bull_heat,
                                sideway_max_positions=sideway_positions,
                                sideway_heat_pct=sideway_heat,
                                bear_mode=bear_mode,
                                is_current_production=(
                                    bull_positions == 5
                                    and bull_heat == 5.0
                                    and sideway_positions == 3
                                    and sideway_heat == 4.0
                                    and bear_mode == "block"
                                ),
                            )
                        )
    return cases


def build_policy(
    case: RegimeGridCase,
) -> RegimePortfolioPolicy | None:
    if case.baseline:
        return None

    assert case.bull_max_positions is not None
    assert case.bull_heat_pct is not None
    assert case.sideway_max_positions is not None
    assert case.sideway_heat_pct is not None

    bear_allows_one = case.bear_mode == "one"
    return RegimePortfolioPolicy(
        rules={
            "BULL": RegimePortfolioRule(
                allow_new_positions=True,
                max_positions=case.bull_max_positions,
                max_portfolio_heat_pct=case.bull_heat_pct,
            ),
            "SIDEWAY": RegimePortfolioRule(
                allow_new_positions=True,
                max_positions=case.sideway_max_positions,
                max_portfolio_heat_pct=case.sideway_heat_pct,
            ),
            "BEAR": RegimePortfolioRule(
                allow_new_positions=bear_allows_one,
                max_positions=1 if bear_allows_one else 0,
                max_portfolio_heat_pct=(
                    BEAR_ONE_HEAT_PCT if bear_allows_one else None
                ),
            ),
        }
    )


class CandidateTradeCache:
    """Generate signal/exit candidates once per period, then reuse safely."""

    def __init__(
        self,
        *,
        symbols: list[str],
        db_path: str,
        trading_policy: TradingPolicy,
        paper: PaperExecutionConfig,
        max_holding_days: int,
        exit_mode: str = "production_trailing",
    ) -> None:
        self.symbols = sorted({value.strip().upper() for value in symbols})
        self.db_path = db_path
        self.trading_policy = trading_policy
        self.paper = paper
        self.max_holding_days = max_holding_days
        self.exit_mode = exit_mode
        self._cache: dict[tuple[str, str], list] = {}

    def _candidate_config(self) -> BacktestConfig:
        return BacktestConfig(
            max_holding_days=self.max_holding_days,
            initial_capital=self.paper.initial_cash,
            buy_commission_pct=self.paper.commission_rate * 100,
            sell_commission_pct=self.paper.commission_rate * 100,
            sell_tax_pct=self.paper.sell_tax_rate * 100,
            buy_slippage_pct=self.paper.slippage_bps / 100,
            sell_slippage_pct=self.paper.slippage_bps / 100,
            ranking_method="signal_score",
        )

    def get(self, start_date: str, end_date: str) -> list:
        key = (start_date, end_date)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        config = self._candidate_config()
        entry_model = self.trading_policy.build_entry_model()
        if self.exit_mode == "production_trailing":
            exit_model = TrailingATRExitModel(
                stop_atr_multiplier=(
                    self.trading_policy.stop_atr_multiplier
                ),
                target_atr_multiplier=(
                    self.trading_policy.target_atr_multiplier
                ),
                trailing_atr_multiplier=(
                    self.trading_policy.trailing_atr_multiplier
                ),
            )
        else:
            from backtesting.engine import build_exit_model

            exit_model = build_exit_model(
                name="atr",
                stop_atr_multiplier=(
                    self.trading_policy.stop_atr_multiplier
                ),
                target_atr_multiplier=(
                    self.trading_policy.target_atr_multiplier
                ),
            )
        candidates = []
        for symbol in self.symbols:
            candidates.extend(
                generate_candidate_trades(
                    symbol=symbol,
                    config=config,
                    db_path=self.db_path,
                    warmup_bars=60,
                    verbose=False,
                    start_date=start_date,
                    end_date=end_date,
                    entry_model=entry_model,
                    exit_model=exit_model,
                )
            )
        self._cache[key] = candidates
        return candidates

    def metadata(self) -> pd.DataFrame:
        rows = [
            {
                "start_date": start,
                "end_date": end,
                "candidate_trades": len(trades),
            }
            for (start, end), trades in sorted(self._cache.items())
        ]
        return pd.DataFrame(rows)


def simulate_period(
    *,
    cache: CandidateTradeCache,
    case: RegimeGridCase,
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
        regime_policy=build_policy(case),
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
    )
    metrics = calculate_metrics(result.executed_trades, config)
    metrics.update(
        calculate_portfolio_metrics(
            result.equity_curve,
            final_equity=result.final_equity,
        )
    )
    metrics["final_equity"] = float(result.final_equity)
    metrics["total_return_pct"] = float(
        (result.final_equity / initial_capital - 1.0) * 100.0
    )
    metrics["rejected_trades"] = len(result.rejected_trades)
    return PeriodResult(
        trades=result.executed_trades,
        metrics=metrics,
        equity=result.equity_curve,
    )


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def compound_return(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return 0.0
    return float(((1.0 + numeric / 100.0).prod() - 1.0) * 100.0)


def build_fold_row(
    *,
    case: RegimeGridCase,
    fold,
    train: PeriodResult,
    test: PeriodResult,
    test_initial_capital: float,
) -> dict[str, Any]:
    train_return = safe_float(train.metrics.get("total_return_pct"))
    test_return = safe_float(test.metrics.get("total_return_pct"))
    train_sharpe = safe_float(train.metrics.get("sharpe_ratio"))
    test_sharpe = safe_float(test.metrics.get("sharpe_ratio"))
    return {
        "case_id": case.case_id,
        "is_current_production": case.is_current_production,
        **fold.to_dict(),
        "train_trades": len(train.trades),
        "train_return_pct": train_return,
        "train_sharpe_ratio": train_sharpe,
        "train_max_drawdown_pct": safe_float(
            train.metrics.get("max_drawdown_pct")
        ),
        "test_initial_capital": test_initial_capital,
        "test_final_equity": safe_float(
            test.metrics.get("final_equity"), test_initial_capital
        ),
        "test_trades": len(test.trades),
        "test_return_pct": test_return,
        "test_sharpe_ratio": test_sharpe,
        "test_max_drawdown_pct": safe_float(
            test.metrics.get("max_drawdown_pct")
        ),
        "test_profit_factor": safe_float(
            test.metrics.get("profit_factor")
        ),
        "test_win_rate_pct": safe_float(
            test.metrics.get("win_rate_pct")
        ),
        "return_degradation_pct": test_return - train_return,
        "sharpe_degradation": test_sharpe - train_sharpe,
        "test_profitable": test_return > 0,
    }


def research_gates(summary: dict[str, Any]) -> dict[str, Any]:
    required_profitable = math.ceil(int(summary["folds"]) / 2)
    gates = {
        "gate_profitable_folds_at_least_half": (
            int(summary["profitable_folds"]) >= required_profitable
        ),
        "gate_median_non_negative": (
            float(summary["median_test_return_pct"]) >= 0.0
        ),
        "gate_excluding_first_fold_non_negative": (
            float(summary["return_excluding_first_fold_pct"]) >= 0.0
        ),
        "gate_excluding_first_two_folds_non_negative": (
            float(summary["return_excluding_first_two_folds_pct"]) >= 0.0
        ),
        "gate_recent_3_folds_non_negative": (
            float(summary["recent_3_folds_return_pct"]) >= 0.0
        ),
        "gate_chained_drawdown_within_15pct": (
            float(summary["chained_max_drawdown_pct"]) >= -15.0
        ),
    }
    gate_count = sum(bool(value) for value in gates.values())
    return {
        **gates,
        "gate_count": gate_count,
        "research_gate_passed": gate_count == len(gates),
    }


def summarize_case(
    *,
    case: RegimeGridCase,
    folds_frame: pd.DataFrame,
    equity_curves: list[pd.DataFrame],
    initial_capital: float,
) -> dict[str, Any]:
    final_equity = float(folds_frame.iloc[-1]["test_final_equity"])
    returns = folds_frame["test_return_pct"]
    profitable_folds = int((returns > 0).sum())
    summary = {
        **asdict(case),
        "optimization_performed": True,
        "folds": len(folds_frame),
        "initial_capital": initial_capital,
        "final_equity": final_equity,
        "walk_forward_return_pct": (
            final_equity / initial_capital - 1.0
        ) * 100.0,
        "profitable_folds": profitable_folds,
        "losing_folds": len(folds_frame) - profitable_folds,
        "profitable_fold_pct": profitable_folds / len(folds_frame) * 100.0,
        "total_test_trades": int(folds_frame["test_trades"].sum()),
        "average_test_return_pct": float(returns.mean()),
        "median_test_return_pct": float(returns.median()),
        "worst_test_return_pct": float(returns.min()),
        "best_test_return_pct": float(returns.max()),
        "recent_3_folds_return_pct": compound_return(returns.iloc[-3:]),
        "return_excluding_first_fold_pct": compound_return(returns.iloc[1:]),
        "return_excluding_first_two_folds_pct": compound_return(
            returns.iloc[2:]
        ),
        "average_test_sharpe": float(
            folds_frame["test_sharpe_ratio"].mean()
        ),
        "average_test_drawdown_pct": float(
            folds_frame["test_max_drawdown_pct"].mean()
        ),
        "worst_test_drawdown_pct": float(
            folds_frame["test_max_drawdown_pct"].min()
        ),
        "chained_max_drawdown_pct": calculate_chained_drawdown_pct(
            equity_curves
        ),
        "average_return_degradation_pct": float(
            folds_frame["return_degradation_pct"].mean()
        ),
        "average_sharpe_degradation": float(
            folds_frame["sharpe_degradation"].mean()
        ),
    }
    summary.update(research_gates(summary))
    return summary


def evaluate_case(
    *,
    case: RegimeGridCase,
    folds,
    cache: CandidateTradeCache,
    paper: PaperExecutionConfig,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    current_capital = float(paper.initial_cash)
    rows = []
    equity_curves = []
    for fold in folds:
        train = simulate_period(
            cache=cache,
            case=case,
            paper=paper,
            initial_capital=paper.initial_cash,
            start_date=str(fold.train_start.date()),
            end_date=str(fold.train_end.date()),
        )
        test_initial = current_capital
        test = simulate_period(
            cache=cache,
            case=case,
            paper=paper,
            initial_capital=test_initial,
            start_date=str(fold.test_start.date()),
            end_date=str(fold.test_end.date()),
        )
        current_capital = safe_float(
            test.metrics.get("final_equity"), test_initial
        )
        equity_curves.append(test.equity.copy())
        rows.append(
            build_fold_row(
                case=case,
                fold=fold,
                train=train,
                test=test,
                test_initial_capital=test_initial,
            )
        )
    frame = pd.DataFrame(rows)
    return frame, summarize_case(
        case=case,
        folds_frame=frame,
        equity_curves=equity_curves,
        initial_capital=paper.initial_cash,
    )


def rank_summary(frame: pd.DataFrame) -> pd.DataFrame:
    ranked = frame.sort_values(
        by=[
            "research_gate_passed",
            "gate_count",
            "median_test_return_pct",
            "recent_3_folds_return_pct",
            "chained_max_drawdown_pct",
            "walk_forward_return_pct",
        ],
        ascending=[False, False, False, False, False, False],
    ).reset_index(drop=True)
    ranked.insert(0, "robust_rank", range(1, len(ranked) + 1))
    return ranked


def run_nested_selection(
    *,
    cases: list[RegimeGridCase],
    folds,
    grid_folds: pd.DataFrame,
    cache: CandidateTradeCache,
    paper: PaperExecutionConfig,
    minimum_train_trades: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    current_capital = float(paper.initial_cash)
    rows = []
    equity_curves = []
    case_map = {case.case_id: case for case in cases}

    for fold in folds:
        pool = grid_folds[grid_folds["fold"] == fold.fold].copy()
        eligible = pool[pool["train_trades"] >= minimum_train_trades]
        if eligible.empty:
            eligible = pool
        eligible = eligible.sort_values(
            by=[
                "train_sharpe_ratio",
                "train_return_pct",
                "train_max_drawdown_pct",
            ],
            ascending=[False, False, False],
        )
        selected_row = eligible.iloc[0]
        selected = case_map[str(selected_row["case_id"])]
        test_initial = current_capital
        test = simulate_period(
            cache=cache,
            case=selected,
            paper=paper,
            initial_capital=test_initial,
            start_date=str(fold.test_start.date()),
            end_date=str(fold.test_end.date()),
        )
        current_capital = safe_float(
            test.metrics.get("final_equity"), test_initial
        )
        equity_curves.append(test.equity.copy())
        rows.append(
            {
                **fold.to_dict(),
                **asdict(selected),
                "selection_train_trades": int(selected_row["train_trades"]),
                "selection_train_return_pct": float(
                    selected_row["train_return_pct"]
                ),
                "selection_train_sharpe": float(
                    selected_row["train_sharpe_ratio"]
                ),
                "test_initial_capital": test_initial,
                "test_final_equity": current_capital,
                "test_trades": len(test.trades),
                "test_return_pct": safe_float(
                    test.metrics.get("total_return_pct")
                ),
                "test_sharpe_ratio": safe_float(
                    test.metrics.get("sharpe_ratio")
                ),
                "test_max_drawdown_pct": safe_float(
                    test.metrics.get("max_drawdown_pct")
                ),
            }
        )

    frame = pd.DataFrame(rows)
    returns = frame["test_return_pct"]
    profitable = int((returns > 0).sum())
    summary = {
        "optimization_performed": True,
        "selection_method": "train_sharpe_then_return_then_drawdown",
        "minimum_train_trades": minimum_train_trades,
        "parameter_combinations": len(cases),
        "folds": len(frame),
        "initial_capital": paper.initial_cash,
        "final_equity": current_capital,
        "walk_forward_return_pct": (
            current_capital / paper.initial_cash - 1.0
        ) * 100.0,
        "profitable_folds": profitable,
        "profitable_fold_pct": profitable / len(frame) * 100.0,
        "median_test_return_pct": float(returns.median()),
        "worst_test_return_pct": float(returns.min()),
        "recent_3_folds_return_pct": compound_return(returns.iloc[-3:]),
        "average_test_sharpe": float(frame["test_sharpe_ratio"].mean()),
        "worst_test_drawdown_pct": float(
            frame["test_max_drawdown_pct"].min()
        ),
        "chained_max_drawdown_pct": calculate_chained_drawdown_pct(
            equity_curves
        ),
        "total_test_trades": int(frame["test_trades"].sum()),
        "selection_switches": int(
            (frame["case_id"] != frame["case_id"].shift(1)).sum() - 1
        ),
        "unique_selected_cases": int(frame["case_id"].nunique()),
    }
    return frame, pd.DataFrame([summary])


def build_parameter_stability(
    *,
    ranked_summary: pd.DataFrame,
    nested_selections: pd.DataFrame,
    top_n: int = 20,
) -> pd.DataFrame:
    top = ranked_summary.head(top_n)
    rows = []
    columns = (
        "bull_max_positions",
        "bull_heat_pct",
        "sideway_max_positions",
        "sideway_heat_pct",
        "bear_mode",
    )
    for column in columns:
        top_counts = top[column].value_counts(dropna=False)
        selected_counts = nested_selections[column].value_counts(dropna=False)
        values = list(dict.fromkeys([*top_counts.index, *selected_counts.index]))
        for value in values:
            top_count = int(top_counts.get(value, 0))
            selected_count = int(selected_counts.get(value, 0))
            rows.append(
                {
                    "parameter": column,
                    "value": value,
                    "top_n": min(top_n, len(ranked_summary)),
                    "top_count": top_count,
                    "top_pct": top_count / max(len(top), 1) * 100.0,
                    "nested_selected_count": selected_count,
                    "nested_selected_pct": (
                        selected_count / max(len(nested_selections), 1) * 100.0
                    ),
                }
            )
    return pd.DataFrame(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Full production-parity regime policy grid + nested WFO."
    )
    parser.add_argument("--db", default="data/market.db")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--start", default="2018-08-07")
    parser.add_argument("--end", default="2026-08-25")
    parser.add_argument("--train-months", type=int, default=24)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument("--hold", type=int, default=30)
    parser.add_argument("--minimum-train-trades", type=int, default=20)
    parser.add_argument(
        "--exit-mode",
        choices=("production_trailing", "fixed_atr_control"),
        default="production_trailing",
        help="Production trailing exit or a fixed-ATR diagnostic control.",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--only",
        nargs="*",
        default=None,
        help="Optional case_id list for smoke/debug runs.",
    )
    return parser


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    paper = PaperExecutionConfig.from_env()
    trading_policy = TradingPolicy.from_env()
    symbols = (
        list(HOLDOUT20_SYMBOLS)
        if args.symbols is None
        else [value.strip().upper() for value in args.symbols if value.strip()]
    )
    walk_config = WalkForwardConfig(
        start_date=args.start,
        end_date=args.end,
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
    )
    folds = build_walk_forward_folds(walk_config)
    all_cases = build_cases()
    case_map = {case.case_id: case for case in all_cases}
    if args.only:
        unknown = sorted(set(args.only) - set(case_map))
        if unknown:
            raise ValueError("Unknown case_id: " + ", ".join(unknown))
        cases = [case_map[value] for value in args.only]
    else:
        cases = all_cases

    cache = CandidateTradeCache(
        symbols=symbols,
        db_path=args.db,
        trading_policy=trading_policy,
        paper=paper,
        max_holding_days=args.hold,
        exit_mode=args.exit_mode,
    )

    print(f"Precomputing candidates for {len(folds) * 2} periods...")
    periods = []
    for fold in folds:
        periods.extend(
            [
                (str(fold.train_start.date()), str(fold.train_end.date())),
                (str(fold.test_start.date()), str(fold.test_end.date())),
            ]
        )
    for index, (start, end) in enumerate(periods, start=1):
        candidates = cache.get(start, end)
        print(
            f"[{index:02d}/{len(periods):02d}] {start} -> {end}: "
            f"{len(candidates)} candidates"
        )
    cache.metadata().to_csv(
        output_dir / "candidate_cache_metadata.csv",
        index=False,
        encoding="utf-8-sig",
    )

    summary_rows = []
    fold_frames = []
    for index, case in enumerate(cases, start=1):
        frame, summary = evaluate_case(
            case=case,
            folds=folds,
            cache=cache,
            paper=paper,
        )
        summary_rows.append(summary)
        fold_frames.append(frame)
        print(
            f"CASE {index:03d}/{len(cases):03d} {case.case_id}: "
            f"return={summary['walk_forward_return_pct']:+.2f}% "
            f"median={summary['median_test_return_pct']:+.2f}% "
            f"gates={summary['gate_count']}/6"
        )
        rank_summary(pd.DataFrame(summary_rows)).to_csv(
            output_dir / "grid_summary_partial.csv",
            index=False,
            encoding="utf-8-sig",
        )

    grid_summary = rank_summary(pd.DataFrame(summary_rows))
    grid_folds = pd.concat(fold_frames, ignore_index=True)
    grid_summary.to_csv(
        output_dir / "grid_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    grid_folds.to_csv(
        output_dir / "grid_folds.csv",
        index=False,
        encoding="utf-8-sig",
    )

    nested_selections, nested_summary = run_nested_selection(
        cases=cases,
        folds=folds,
        grid_folds=grid_folds,
        cache=cache,
        paper=paper,
        minimum_train_trades=args.minimum_train_trades,
    )
    nested_selections.to_csv(
        output_dir / "nested_selections.csv",
        index=False,
        encoding="utf-8-sig",
    )
    nested_summary.to_csv(
        output_dir / "nested_summary.csv",
        index=False,
        encoding="utf-8-sig",
    )
    build_parameter_stability(
        ranked_summary=grid_summary,
        nested_selections=nested_selections,
    ).to_csv(
        output_dir / "parameter_stability.csv",
        index=False,
        encoding="utf-8-sig",
    )

    config = {
        "data": {
            "db": args.db,
            "symbols": symbols,
            "start": args.start,
            "end": args.end,
            "train_months": args.train_months,
            "test_months": args.test_months,
            "step_months": args.step_months,
        },
        "production_parity": {
            "entry_model": trading_policy.entry_model,
            "exit_model": args.exit_mode,
            "stop_atr_multiplier": trading_policy.stop_atr_multiplier,
            "target_atr_multiplier": trading_policy.target_atr_multiplier,
            "trailing_atr_multiplier": trading_policy.trailing_atr_multiplier,
            "maximum_holding_sessions": args.hold,
            "position_sizer": paper.position_sizer,
            "risk_per_trade_pct": paper.risk_per_trade_pct,
            "maximum_position_pct": paper.maximum_position_pct,
            "maximum_gross_exposure_pct": paper.maximum_gross_exposure_pct,
            "minimum_cash_buffer_pct": paper.minimum_cash_buffer_pct,
            "maximum_orders_per_scan": paper.maximum_orders_per_scan,
            "commission_rate": paper.commission_rate,
            "sell_tax_rate": paper.sell_tax_rate,
            "slippage_bps": paper.slippage_bps,
        },
        "grid": {
            "bull_positions": BULL_POSITION_GRID,
            "sideway_positions": SIDEWAY_POSITION_GRID,
            "bear_modes": BEAR_MODE_GRID,
            "bull_heat_pct": BULL_HEAT_GRID,
            "sideway_heat_pct": SIDEWAY_HEAT_GRID,
            "bear_one_heat_pct": BEAR_ONE_HEAT_PCT,
            "cases_including_baseline": len(cases),
        },
    }
    (output_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\nTOP 10 ROBUST FIXED POLICIES")
    columns = [
        "robust_rank",
        "case_id",
        "research_gate_passed",
        "gate_count",
        "walk_forward_return_pct",
        "median_test_return_pct",
        "recent_3_folds_return_pct",
        "chained_max_drawdown_pct",
    ]
    print(grid_summary[columns].head(10).to_string(index=False))
    print("\nNESTED WFO")
    print(nested_summary.to_string(index=False))
    print(f"\nSaved: {output_dir.resolve()}")


if __name__ == "__main__":
    main()
