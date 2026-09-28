"""Production-parity exit-policy matrix with nested walk-forward validation.

The entry model, next-open execution, ATR-risk sizing, costs and regime policy
are frozen.  Only the exit model varies.  Delayed trailing is research-only;
paper-trading code is changed only after a candidate passes robustness checks.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import sqlite3
from dataclasses import asdict, dataclass, replace
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
from backtesting.exit_models import (
    ATRExitModel,
    TrailingATRExitModel,
)
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.portfolio_metrics import calculate_portfolio_metrics
from backtesting.portfolio_simulator import PortfolioSimulator
from backtesting.regime_policy import RegimePortfolioPolicy
from backtesting.transaction_cost import TransactionCostConfig
from backtesting.walk_forward import (
    WalkForwardConfig,
    build_walk_forward_folds,
    calculate_chained_drawdown_pct,
)
from config.trading_policy import TradingPolicy
from execution.signal_executor import PaperExecutionConfig
from research.universes import HOLDOUT20_SYMBOLS


DEFAULT_OUTPUT_DIR = Path("research_results/exit_policy_matrix_current")


@dataclass(frozen=True, slots=True)
class ExitPolicyCase:
    case_id: str
    exit_family: str
    stop_atr: float
    target_atr: float
    trailing_atr: float | None = None
    activation_r: float | None = None
    is_current_production: bool = False


@dataclass(slots=True)
class PeriodResult:
    trades: list
    metrics: dict[str, Any]
    equity: pd.DataFrame


class DelayedTrailingATRExitModel(TrailingATRExitModel):
    """Activate the trailing stop only after the trade reaches N initial R."""

    def __init__(self, *, activation_r: float, **kwargs) -> None:
        if activation_r <= 0:
            raise ValueError("activation_r must be greater than 0")
        super().__init__(**kwargs)
        self.activation_r = float(activation_r)

    def update_levels(
        self,
        *,
        entry_price: float,
        current_row: Any,
        current_stop: float,
        current_target: float,
        highest_price: float,
        config: Any,
    ) -> tuple[float, float]:
        # The fixed target retains the entry ATR exactly:
        # target - entry = target_atr_multiplier * entry_ATR.
        entry_atr = (
            float(current_target) - float(entry_price)
        ) / self.target_atr_multiplier
        initial_risk = self.stop_atr_multiplier * entry_atr
        trigger = float(entry_price) + self.activation_r * initial_risk
        if highest_price < trigger:
            return current_stop, current_target
        return super().update_levels(
            entry_price=entry_price,
            current_row=current_row,
            current_stop=current_stop,
            current_target=current_target,
            highest_price=highest_price,
            config=config,
        )


def build_cases() -> list[ExitPolicyCase]:
    return [
        ExitPolicyCase("fixed_atr_2_4", "fixed", 2.0, 4.0),
        ExitPolicyCase("fixed_atr_2_5", "fixed", 2.0, 5.0),
        ExitPolicyCase("fixed_atr_2_6", "fixed", 2.0, 6.0),
        ExitPolicyCase(
            "trailing_atr_2_0_current",
            "trailing",
            2.0,
            5.0,
            trailing_atr=2.0,
            is_current_production=True,
        ),
        ExitPolicyCase(
            "trailing_atr_2_5",
            "trailing",
            2.0,
            5.0,
            trailing_atr=2.5,
        ),
        ExitPolicyCase(
            "trailing_atr_3_0",
            "trailing",
            2.0,
            5.0,
            trailing_atr=3.0,
        ),
        ExitPolicyCase(
            "trailing_atr_2_5_after_1r",
            "delayed_trailing",
            2.0,
            5.0,
            trailing_atr=2.5,
            activation_r=1.0,
        ),
        ExitPolicyCase(
            "trailing_atr_2_5_after_2r",
            "delayed_trailing",
            2.0,
            5.0,
            trailing_atr=2.5,
            activation_r=2.0,
        ),
    ]


def build_exit_model(case: ExitPolicyCase):
    if case.exit_family == "fixed":
        return ATRExitModel(
            stop_atr_multiplier=case.stop_atr,
            target_atr_multiplier=case.target_atr,
        )
    kwargs = {
        "stop_atr_multiplier": case.stop_atr,
        "target_atr_multiplier": case.target_atr,
        "trailing_atr_multiplier": float(case.trailing_atr),
    }
    if case.exit_family == "trailing":
        return TrailingATRExitModel(**kwargs)
    if case.exit_family == "delayed_trailing":
        return DelayedTrailingATRExitModel(
            activation_r=float(case.activation_r),
            **kwargs,
        )
    raise ValueError(f"Unsupported exit family: {case.exit_family}")


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


class CandidateTradeCache:
    def __init__(
        self,
        *,
        case: ExitPolicyCase,
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

    def get(self, start_date: str, end_date: str) -> list:
        key = (start_date, end_date)
        if key in self._cache:
            return self._cache[key]
        config = self._config()
        entry_model = self.trading_policy.build_entry_model()
        exit_model = build_exit_model(self.case)
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
        return pd.DataFrame(
            [
                {
                    "case_id": self.case.case_id,
                    "start_date": start,
                    "end_date": end,
                    "candidate_trades": len(trades),
                }
                for (start, end), trades in sorted(self._cache.items())
            ]
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


def simulate_period(
    *,
    cache: CandidateTradeCache,
    paper: PaperExecutionConfig,
    initial_capital: float,
    start_date: str,
    end_date: str,
    regime_policy: RegimePortfolioPolicy | None = None,
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
        regime_policy=(
            regime_policy
            or RegimePortfolioPolicy()
        ),
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
    metrics["total_return_pct"] = (
        float(result.final_equity) / initial_capital - 1.0
    ) * 100.0
    metrics["rejected_trades"] = len(result.rejected_trades)
    return PeriodResult(
        trades=result.executed_trades,
        metrics=metrics,
        equity=result.equity_curve,
    )


def trade_rows(case: ExitPolicyCase, fold_number: int, trades: list) -> list[dict]:
    rows = []
    for trade in trades:
        row = trade.to_dict()
        row["case_id"] = case.case_id
        row["fold"] = fold_number
        rows.append(row)
    return rows


def build_fold_row(
    *,
    case: ExitPolicyCase,
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
        "test_profit_factor": safe_float(test.metrics.get("profit_factor")),
        "test_win_rate_pct": safe_float(test.metrics.get("win_rate_pct")),
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
        "gate_median_non_negative": summary["median_test_return_pct"] >= 0,
        "gate_recent_3_folds_non_negative": (
            summary["recent_3_folds_return_pct"] >= 0
        ),
        "gate_excluding_first_two_non_negative": (
            summary["return_excluding_first_two_folds_pct"] >= 0
        ),
        "gate_excluding_best_fold_non_negative": (
            summary["return_excluding_best_fold_pct"] >= 0
        ),
        "gate_chained_drawdown_within_15pct": (
            summary["chained_max_drawdown_pct"] >= -15
        ),
        "gate_at_least_200_oos_trades": summary["total_test_trades"] >= 200,
    }
    count = sum(bool(value) for value in gates.values())
    return {
        **gates,
        "gate_count": count,
        "gate_total": len(gates),
        "research_gate_passed": count == len(gates),
    }


def summarize_case(
    *,
    case: ExitPolicyCase,
    folds_frame: pd.DataFrame,
    equity_curves: list[pd.DataFrame],
    initial_capital: float,
) -> dict[str, Any]:
    returns = folds_frame["test_return_pct"]
    profitable = int((returns > 0).sum())
    without_best = returns.drop(index=returns.idxmax()) if len(returns) > 1 else returns
    final_equity = float(folds_frame.iloc[-1]["test_final_equity"])
    summary = {
        **asdict(case),
        "folds": len(folds_frame),
        "initial_capital": initial_capital,
        "final_equity": final_equity,
        "walk_forward_return_pct": (final_equity / initial_capital - 1) * 100,
        "profitable_folds": profitable,
        "losing_folds": len(folds_frame) - profitable,
        "profitable_fold_pct": profitable / len(folds_frame) * 100,
        "total_test_trades": int(folds_frame["test_trades"].sum()),
        "average_test_return_pct": float(returns.mean()),
        "median_test_return_pct": float(returns.median()),
        "worst_test_return_pct": float(returns.min()),
        "best_test_return_pct": float(returns.max()),
        "recent_3_folds_return_pct": compound_return(returns.iloc[-3:]),
        "return_excluding_first_two_folds_pct": compound_return(returns.iloc[2:]),
        "return_excluding_best_fold_pct": compound_return(without_best),
        "average_test_sharpe": float(folds_frame["test_sharpe_ratio"].mean()),
        "worst_test_drawdown_pct": float(
            folds_frame["test_max_drawdown_pct"].min()
        ),
        "chained_max_drawdown_pct": calculate_chained_drawdown_pct(equity_curves),
        "average_return_degradation_pct": float(
            folds_frame["return_degradation_pct"].mean()
        ),
    }
    summary.update(research_gates(summary))
    return summary


def evaluate_case(
    *,
    case: ExitPolicyCase,
    folds,
    cache: CandidateTradeCache,
    paper: PaperExecutionConfig,
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    current_capital = float(paper.initial_cash)
    rows = []
    all_trade_rows = []
    equity_curves = []
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
        all_trade_rows.extend(trade_rows(case, fold.fold, test.trades))
    frame = pd.DataFrame(rows)
    return (
        frame,
        summarize_case(
            case=case,
            folds_frame=frame,
            equity_curves=equity_curves,
            initial_capital=paper.initial_cash,
        ),
        pd.DataFrame(all_trade_rows),
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
    cases: list[ExitPolicyCase],
    folds,
    grid_folds: pd.DataFrame,
    caches: dict[str, CandidateTradeCache],
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
        selected_row = eligible.sort_values(
            by=[
                "train_sharpe_ratio",
                "train_return_pct",
                "train_max_drawdown_pct",
            ],
            ascending=[False, False, False],
        ).iloc[0]
        selected = case_map[str(selected_row["case_id"])]
        test_initial = current_capital
        test = simulate_period(
            cache=caches[selected.case_id],
            paper=paper,
            initial_capital=test_initial,
            start_date=str(fold.test_start.date()),
            end_date=str(fold.test_end.date()),
        )
        current_capital = safe_float(test.metrics.get("final_equity"), test_initial)
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
                "test_return_pct": safe_float(test.metrics.get("total_return_pct")),
                "test_sharpe_ratio": safe_float(test.metrics.get("sharpe_ratio")),
                "test_max_drawdown_pct": safe_float(
                    test.metrics.get("max_drawdown_pct")
                ),
            }
        )
    frame = pd.DataFrame(rows)
    returns = frame["test_return_pct"]
    profitable = int((returns > 0).sum())
    without_best = returns.drop(index=returns.idxmax()) if len(returns) > 1 else returns
    summary = {
        "selection_method": "train_sharpe_then_return_then_drawdown",
        "minimum_train_trades": minimum_train_trades,
        "parameter_combinations": len(cases),
        "folds": len(frame),
        "initial_capital": paper.initial_cash,
        "final_equity": current_capital,
        "walk_forward_return_pct": (current_capital / paper.initial_cash - 1) * 100,
        "profitable_folds": profitable,
        "median_test_return_pct": float(returns.median()),
        "worst_test_return_pct": float(returns.min()),
        "recent_3_folds_return_pct": compound_return(returns.iloc[-3:]),
        "return_excluding_first_two_folds_pct": compound_return(returns.iloc[2:]),
        "return_excluding_best_fold_pct": compound_return(without_best),
        "chained_max_drawdown_pct": calculate_chained_drawdown_pct(equity_curves),
        "total_test_trades": int(frame["test_trades"].sum()),
        "selection_switches": int(
            (frame["case_id"] != frame["case_id"].shift(1)).sum() - 1
        ),
        "unique_selected_cases": int(frame["case_id"].nunique()),
    }
    summary.update(research_gates(summary))
    return frame, pd.DataFrame([summary])


def grouped_diagnostics(trades: pd.DataFrame, column: str) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    data = trades.copy()
    data["is_win_numeric"] = data["is_win"].astype(int)
    return (
        data.groupby(["case_id", column], dropna=False)
        .agg(
            trades=("symbol", "size"),
            total_net_pnl=("pnl", "sum"),
            average_return_pct=("return_pct", "mean"),
            median_return_pct=("return_pct", "median"),
            win_rate_pct=("is_win_numeric", "mean"),
            average_holding_days=("holding_days", "mean"),
        )
        .reset_index()
        .assign(win_rate_pct=lambda frame: frame["win_rate_pct"] * 100)
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Frozen-policy exit matrix + nested WFO."
    )
    parser.add_argument("--db", default="data/market.db")
    parser.add_argument("--universe", choices=("holdout20", "production"), default="holdout20")
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--start", default="2018-08-07")
    parser.add_argument("--end", default="2026-08-25")
    parser.add_argument("--train-months", type=int, default=24)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument("--hold", type=int, default=30)
    parser.add_argument(
        "--market-price-scale",
        type=float,
        default=1000.0,
        help=(
            "Scale raw market-db prices into VND before sizing; "
            "market.db currently stores thousand VND."
        ),
    )
    parser.add_argument("--risk-per-trade-pct", type=float, default=None)
    parser.add_argument("--slippage-bps", type=float, default=None)
    parser.add_argument("--minimum-train-trades", type=int, default=20)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--only", nargs="*", default=None)
    return parser


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    paper = PaperExecutionConfig.from_env()
    overrides = {}
    if args.risk_per_trade_pct is not None:
        overrides["risk_per_trade_pct"] = args.risk_per_trade_pct
    if args.slippage_bps is not None:
        overrides["slippage_bps"] = args.slippage_bps
    if overrides:
        paper = replace(paper, **overrides)
    if args.market_price_scale <= 0:
        raise ValueError("--market-price-scale must be greater than 0")
    trading_policy = TradingPolicy.from_env()
    if args.symbols is not None:
        symbols = [value.strip().upper() for value in args.symbols if value.strip()]
    elif args.universe == "production":
        symbols = production_symbols(args.db)
    else:
        symbols = list(HOLDOUT20_SYMBOLS)

    folds = build_walk_forward_folds(
        WalkForwardConfig(
            start_date=args.start,
            end_date=args.end,
            train_months=args.train_months,
            test_months=args.test_months,
            step_months=args.step_months,
        )
    )
    all_cases = build_cases()
    case_map = {case.case_id: case for case in all_cases}
    if args.only:
        unknown = sorted(set(args.only) - set(case_map))
        if unknown:
            raise ValueError("Unknown case_id: " + ", ".join(unknown))
        cases = [case_map[value] for value in args.only]
    else:
        cases = all_cases

    caches = {
        case.case_id: CandidateTradeCache(
            case=case,
            symbols=symbols,
            db_path=args.db,
            trading_policy=trading_policy,
            paper=paper,
            max_holding_days=args.hold,
            market_price_scale=args.market_price_scale,
        )
        for case in cases
    }
    periods = []
    for fold in folds:
        periods.extend(
            [
                (str(fold.train_start.date()), str(fold.train_end.date())),
                (str(fold.test_start.date()), str(fold.test_end.date())),
            ]
        )

    summary_rows = []
    fold_frames = []
    trade_frames = []
    metadata_frames = []
    for case_index, case in enumerate(cases, start=1):
        cache = caches[case.case_id]
        print(f"PRECOMPUTE {case_index:02d}/{len(cases):02d} {case.case_id}")
        for period_index, (start, end) in enumerate(periods, start=1):
            candidates = cache.get(start, end)
            print(
                f"  [{period_index:02d}/{len(periods):02d}] "
                f"{start} -> {end}: {len(candidates)}"
            )
        metadata_frames.append(cache.metadata())
        frame, summary, trades = evaluate_case(
            case=case,
            folds=folds,
            cache=cache,
            paper=paper,
        )
        summary_rows.append(summary)
        fold_frames.append(frame)
        trade_frames.append(trades)
        print(
            f"RESULT {case.case_id}: "
            f"return={summary['walk_forward_return_pct']:+.2f}% "
            f"median={summary['median_test_return_pct']:+.2f}% "
            f"gates={summary['gate_count']}/{summary['gate_total']}"
        )
        rank_summary(pd.DataFrame(summary_rows)).to_csv(
            output_dir / "matrix_summary_partial.csv",
            index=False,
            encoding="utf-8-sig",
        )

    matrix_summary = rank_summary(pd.DataFrame(summary_rows))
    matrix_folds = pd.concat(fold_frames, ignore_index=True)
    trades = pd.concat(trade_frames, ignore_index=True)
    metadata = pd.concat(metadata_frames, ignore_index=True)
    nested_selections, nested_summary = run_nested_selection(
        cases=cases,
        folds=folds,
        grid_folds=matrix_folds,
        caches=caches,
        paper=paper,
        minimum_train_trades=args.minimum_train_trades,
    )
    regime_diagnostics = grouped_diagnostics(trades, "market_regime")
    exit_diagnostics = grouped_diagnostics(trades, "exit_reason")

    frames = {
        "matrix_summary.csv": matrix_summary,
        "matrix_folds.csv": matrix_folds,
        "nested_selections.csv": nested_selections,
        "nested_summary.csv": nested_summary,
        "trade_level_oos.csv": trades,
        "regime_diagnostics.csv": regime_diagnostics,
        "exit_reason_diagnostics.csv": exit_diagnostics,
        "candidate_cache_metadata.csv": metadata,
    }
    for name, frame in frames.items():
        frame.to_csv(output_dir / name, index=False, encoding="utf-8-sig")

    config = {
        "data": {
            "db": args.db,
            "universe": args.universe,
            "symbols": symbols,
            "start": args.start,
            "end": args.end,
            "folds": len(folds),
            "train_months": args.train_months,
            "test_months": args.test_months,
            "step_months": args.step_months,
        },
        "frozen_policy": {
            "entry_model": trading_policy.entry_model,
            "execution_timing": trading_policy.execution_timing,
            "maximum_holding_sessions": args.hold,
            "regime_policy": "BULL 5/5%, SIDEWAY 3/4%, BEAR block new entries",
            "position_sizer": paper.position_sizer,
            "risk_per_trade_pct": paper.risk_per_trade_pct,
            "maximum_position_pct": paper.maximum_position_pct,
            "maximum_gross_exposure_pct": paper.maximum_gross_exposure_pct,
            "minimum_cash_buffer_pct": paper.minimum_cash_buffer_pct,
            "maximum_orders_per_scan": paper.maximum_orders_per_scan,
            "commission_rate": paper.commission_rate,
            "sell_tax_rate": paper.sell_tax_rate,
            "slippage_bps": paper.slippage_bps,
            "market_price_scale": args.market_price_scale,
        },
        "cases": [asdict(case) for case in cases],
        "integrity": {
            "candidate_stop_for_sizing": "initial_stop_price",
            "delayed_trailing_is_research_only": True,
        },
    }
    (output_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\nROBUST RANKING")
    print(
        matrix_summary[
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
    print("\nNESTED WFO")
    print(nested_summary.to_string(index=False))
    print(f"\nSaved: {output_dir.resolve()}")


if __name__ == "__main__":
    main()
