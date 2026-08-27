"""Frozen-entry diagnostic for BULL/SIDEWAY portfolio admission rules.

This is a diagnosis, not a production optimizer. It fixes hybrid+Donchian,
next-open execution, VND price conversion, ATR Risk, lot 100, costs, 30 trading
sessions and fixed ATR 2/4 exit. Only the pre-declared BULL admission bracket
varies, and a nested WFO report prevents choosing a policy by hindsight.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*args, **kwargs) -> bool:
        return False

from backtesting.regime_policy import (
    RegimePortfolioPolicy,
    RegimePortfolioRule,
)
from backtesting.walk_forward import (
    WalkForwardConfig,
    build_walk_forward_folds,
    calculate_chained_drawdown_pct,
)
from config.trading_policy import TradingPolicy
from execution.signal_executor import PaperExecutionConfig
from research.run_exit_policy_matrix import (
    CandidateTradeCache,
    ExitPolicyCase,
    compound_return,
    grouped_diagnostics,
    production_symbols,
    safe_float,
    simulate_period,
)
from research.universes import HOLDOUT20_SYMBOLS


DEFAULT_OUTPUT_DIR = Path("research_results/regime_entry_diagnostic")
FIXED_EXIT_CASE = ExitPolicyCase(
    case_id="fixed_atr_2_4_diagnostic",
    exit_family="fixed",
    stop_atr=2.0,
    target_atr=4.0,
)


@dataclass(frozen=True, slots=True)
class RegimeDiagnosticCase:
    case_id: str
    bull_max_positions: int
    bull_heat_pct: float | None
    sideway_max_positions: int
    sideway_heat_pct: float | None
    is_current_production: bool = False

    @property
    def bull_allows_entries(self) -> bool:
        return self.bull_max_positions > 0

    @property
    def sideway_allows_entries(self) -> bool:
        return self.sideway_max_positions > 0


def build_cases() -> list[RegimeDiagnosticCase]:
    """Pre-declared bracket: current, two softer caps, and two attribution controls."""
    return [
        RegimeDiagnosticCase(
            "current_bull5_h5__side3_h4", 5, 5.0, 3, 4.0, True
        ),
        RegimeDiagnosticCase(
            "bull3_h3__side3_h4", 3, 3.0, 3, 4.0
        ),
        RegimeDiagnosticCase(
            "bull1_h1__side3_h4", 1, 1.0, 3, 4.0
        ),
        RegimeDiagnosticCase(
            "sideway_only__side3_h4", 0, None, 3, 4.0
        ),
        RegimeDiagnosticCase(
            "bull_only__bull5_h5", 5, 5.0, 0, None
        ),
    ]


def build_policy(case: RegimeDiagnosticCase) -> RegimePortfolioPolicy:
    return RegimePortfolioPolicy(
        rules={
            "BULL": RegimePortfolioRule(
                allow_new_positions=case.bull_allows_entries,
                max_positions=case.bull_max_positions,
                max_portfolio_heat_pct=case.bull_heat_pct,
            ),
            "SIDEWAY": RegimePortfolioRule(
                allow_new_positions=case.sideway_allows_entries,
                max_positions=case.sideway_max_positions,
                max_portfolio_heat_pct=case.sideway_heat_pct,
            ),
            "BEAR": RegimePortfolioRule(False, 0, None),
        }
    )


def gate_summary(summary: dict) -> dict:
    required_profitable = math.ceil(int(summary["folds"]) / 2)
    gates = {
        "gate_profitable_folds_at_least_half": (
            summary["profitable_folds"] >= required_profitable
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


def make_fold_row(
    *,
    case: RegimeDiagnosticCase,
    fold,
    train,
    test,
    test_initial_capital: float,
) -> dict:
    train_return = safe_float(train.metrics.get("total_return_pct"))
    test_return = safe_float(test.metrics.get("total_return_pct"))
    return {
        **asdict(case),
        **fold.to_dict(),
        "train_trades": len(train.trades),
        "train_return_pct": train_return,
        "train_sharpe_ratio": safe_float(train.metrics.get("sharpe_ratio")),
        "train_max_drawdown_pct": safe_float(
            train.metrics.get("max_drawdown_pct")
        ),
        "test_initial_capital": test_initial_capital,
        "test_final_equity": safe_float(
            test.metrics.get("final_equity"), test_initial_capital
        ),
        "test_trades": len(test.trades),
        "test_return_pct": test_return,
        "test_sharpe_ratio": safe_float(test.metrics.get("sharpe_ratio")),
        "test_max_drawdown_pct": safe_float(
            test.metrics.get("max_drawdown_pct")
        ),
        "test_profit_factor": safe_float(test.metrics.get("profit_factor")),
        "test_win_rate_pct": safe_float(test.metrics.get("win_rate_pct")),
        "return_degradation_pct": test_return - train_return,
        "test_profitable": test_return > 0,
    }


def summarize_case(
    *,
    case: RegimeDiagnosticCase,
    folds_frame: pd.DataFrame,
    equity_curves: list[pd.DataFrame],
    initial_capital: float,
) -> dict:
    returns = folds_frame["test_return_pct"]
    without_best = returns.drop(index=returns.idxmax()) if len(returns) > 1 else returns
    final_equity = float(folds_frame.iloc[-1]["test_final_equity"])
    summary = {
        **asdict(case),
        "diagnostic_only": True,
        "fixed_exit": "ATR stop 2 / target 4",
        "folds": len(folds_frame),
        "initial_capital": initial_capital,
        "final_equity": final_equity,
        "walk_forward_return_pct": (final_equity / initial_capital - 1) * 100,
        "profitable_folds": int((returns > 0).sum()),
        "losing_folds": int((returns <= 0).sum()),
        "profitable_fold_pct": float((returns > 0).mean() * 100),
        "total_test_trades": int(folds_frame["test_trades"].sum()),
        "median_test_return_pct": float(returns.median()),
        "best_test_return_pct": float(returns.max()),
        "worst_test_return_pct": float(returns.min()),
        "recent_3_folds_return_pct": compound_return(returns.iloc[-3:]),
        "return_excluding_first_two_folds_pct": compound_return(returns.iloc[2:]),
        "return_excluding_best_fold_pct": compound_return(without_best),
        "average_test_sharpe": float(folds_frame["test_sharpe_ratio"].mean()),
        "chained_max_drawdown_pct": calculate_chained_drawdown_pct(equity_curves),
    }
    summary.update(gate_summary(summary))
    return summary


def evaluate_case(*, case, folds, cache, paper):
    policy = build_policy(case)
    current_capital = float(paper.initial_cash)
    rows, trades, curves = [], [], []
    for fold in folds:
        train = simulate_period(
            cache=cache, paper=paper, initial_capital=paper.initial_cash,
            start_date=str(fold.train_start.date()),
            end_date=str(fold.train_end.date()), regime_policy=policy,
        )
        test_initial = current_capital
        test = simulate_period(
            cache=cache, paper=paper, initial_capital=test_initial,
            start_date=str(fold.test_start.date()),
            end_date=str(fold.test_end.date()), regime_policy=policy,
        )
        current_capital = safe_float(test.metrics.get("final_equity"), test_initial)
        curves.append(test.equity.copy())
        rows.append(make_fold_row(
            case=case, fold=fold, train=train, test=test,
            test_initial_capital=test_initial,
        ))
        for trade in test.trades:
            row = trade.to_dict()
            row["case_id"] = case.case_id
            row["fold"] = fold.fold
            trades.append(row)
    frame = pd.DataFrame(rows)
    return frame, summarize_case(
        case=case, folds_frame=frame, equity_curves=curves,
        initial_capital=paper.initial_cash,
    ), pd.DataFrame(trades)


def rank_summary(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.sort_values(
        by=[
            "research_gate_passed", "gate_count", "median_test_return_pct",
            "recent_3_folds_return_pct", "chained_max_drawdown_pct",
            "walk_forward_return_pct",
        ],
        ascending=[False, False, False, False, False, False],
    ).reset_index(drop=True)
    result.insert(0, "robust_rank", range(1, len(result) + 1))
    return result


def run_nested_selection(*, cases, folds, grid_folds, cache, paper, minimum_train_trades):
    case_map = {case.case_id: case for case in cases}
    current_capital = float(paper.initial_cash)
    rows, curves = [], []
    for fold in folds:
        pool = grid_folds[grid_folds["fold"] == fold.fold]
        eligible = pool[pool["train_trades"] >= minimum_train_trades]
        selected_row = (eligible if not eligible.empty else pool).sort_values(
            by=["train_sharpe_ratio", "train_return_pct", "train_max_drawdown_pct"],
            ascending=[False, False, False],
        ).iloc[0]
        case = case_map[str(selected_row["case_id"])]
        test_initial = current_capital
        test = simulate_period(
            cache=cache, paper=paper, initial_capital=test_initial,
            start_date=str(fold.test_start.date()),
            end_date=str(fold.test_end.date()), regime_policy=build_policy(case),
        )
        current_capital = safe_float(test.metrics.get("final_equity"), test_initial)
        curves.append(test.equity.copy())
        rows.append({
            **fold.to_dict(), **asdict(case),
            "selection_train_trades": int(selected_row["train_trades"]),
            "selection_train_return_pct": float(selected_row["train_return_pct"]),
            "selection_train_sharpe": float(selected_row["train_sharpe_ratio"]),
            "test_initial_capital": test_initial,
            "test_final_equity": current_capital,
            "test_trades": len(test.trades),
            "test_return_pct": safe_float(test.metrics.get("total_return_pct")),
            "test_sharpe_ratio": safe_float(test.metrics.get("sharpe_ratio")),
            "test_max_drawdown_pct": safe_float(test.metrics.get("max_drawdown_pct")),
        })
    frame = pd.DataFrame(rows)
    returns = frame["test_return_pct"]
    without_best = returns.drop(index=returns.idxmax()) if len(returns) > 1 else returns
    summary = {
        "diagnostic_only": True,
        "selection_method": "train_sharpe_then_return_then_drawdown",
        "minimum_train_trades": minimum_train_trades,
        "parameter_combinations": len(cases),
        "folds": len(frame), "initial_capital": paper.initial_cash,
        "final_equity": current_capital,
        "walk_forward_return_pct": (current_capital / paper.initial_cash - 1) * 100,
        "profitable_folds": int((returns > 0).sum()),
        "median_test_return_pct": float(returns.median()),
        "worst_test_return_pct": float(returns.min()),
        "recent_3_folds_return_pct": compound_return(returns.iloc[-3:]),
        "return_excluding_first_two_folds_pct": compound_return(returns.iloc[2:]),
        "return_excluding_best_fold_pct": compound_return(without_best),
        "chained_max_drawdown_pct": calculate_chained_drawdown_pct(curves),
        "total_test_trades": int(frame["test_trades"].sum()),
        "selection_switches": int((frame["case_id"] != frame["case_id"].shift(1)).sum() - 1),
        "unique_selected_cases": int(frame["case_id"].nunique()),
    }
    summary.update(gate_summary(summary))
    return frame, pd.DataFrame([summary])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Frozen-entry BULL admission diagnostic + nested WFO."
    )
    parser.add_argument("--db", default="data/market.db")
    parser.add_argument(
        "--universe",
        choices=("holdout20", "production"),
        default="holdout20",
    )
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--start", default="2018-08-07")
    parser.add_argument("--end", default="2026-08-26")
    parser.add_argument("--train-months", type=int, default=24)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument("--hold", type=int, default=30)
    parser.add_argument("--market-price-scale", type=float, default=1000.0)
    parser.add_argument("--minimum-train-trades", type=int, default=20)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--only",
        nargs="*",
        default=None,
        help="Optional pre-declared policy case_id list.",
    )
    return parser


def main() -> None:
    load_dotenv()
    args = build_parser().parse_args()
    if args.market_price_scale <= 0:
        raise ValueError("--market-price-scale must be greater than 0")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    paper = PaperExecutionConfig.from_env()
    policy = TradingPolicy.from_env()
    if args.symbols is not None:
        symbols = [
            value.strip().upper()
            for value in args.symbols
            if value.strip()
        ]
    elif args.universe == "production":
        symbols = production_symbols(args.db)
    else:
        symbols = list(HOLDOUT20_SYMBOLS)
    folds = build_walk_forward_folds(WalkForwardConfig(
        start_date=args.start, end_date=args.end,
        train_months=args.train_months, test_months=args.test_months,
        step_months=args.step_months,
    ))
    cache = CandidateTradeCache(
        case=FIXED_EXIT_CASE, symbols=symbols, db_path=args.db,
        trading_policy=policy, paper=paper, max_holding_days=args.hold,
        market_price_scale=args.market_price_scale,
    )
    periods = [
        (str(date.date()), str(end.date()))
        for fold in folds
        for date, end in ((fold.train_start, fold.train_end), (fold.test_start, fold.test_end))
    ]
    print(f"Precomputing fixed ATR 2/4 candidates for {len(periods)} periods...")
    for index, (start, end) in enumerate(periods, start=1):
        print(f"[{index:02d}/{len(periods):02d}] {start} -> {end}: {len(cache.get(start, end))} candidates")
    cache.metadata().to_csv(output / "candidate_cache_metadata.csv", index=False, encoding="utf-8-sig")

    all_cases = build_cases()
    case_map = {case.case_id: case for case in all_cases}
    if args.only:
        unknown = sorted(set(args.only) - set(case_map))
        if unknown:
            raise ValueError("Unknown case_id: " + ", ".join(unknown))
        cases = [case_map[value] for value in args.only]
    else:
        cases = all_cases
    summaries, frames, trade_frames = [], [], []
    for index, case in enumerate(cases, start=1):
        frame, summary, trades = evaluate_case(
            case=case, folds=folds, cache=cache, paper=paper,
        )
        summaries.append(summary); frames.append(frame); trade_frames.append(trades)
        print(f"CASE {index}/{len(cases)} {case.case_id}: return={summary['walk_forward_return_pct']:+.2f}% gates={summary['gate_count']}/{summary['gate_total']}")

    summary_frame = rank_summary(pd.DataFrame(summaries))
    folds_frame = pd.concat(frames, ignore_index=True)
    trades_frame = pd.concat(trade_frames, ignore_index=True)
    nested_selections, nested_summary = run_nested_selection(
        cases=cases, folds=folds, grid_folds=folds_frame, cache=cache,
        paper=paper, minimum_train_trades=args.minimum_train_trades,
    )
    outputs = {
        "regime_entry_summary.csv": summary_frame,
        "regime_entry_folds.csv": folds_frame,
        "nested_selections.csv": nested_selections,
        "nested_summary.csv": nested_summary,
        "trade_level_oos.csv": trades_frame,
        "entry_regime_attribution.csv": grouped_diagnostics(trades_frame, "market_regime"),
        "exit_reason_diagnostics.csv": grouped_diagnostics(trades_frame, "exit_reason"),
    }
    for filename, frame in outputs.items():
        frame.to_csv(output / filename, index=False, encoding="utf-8-sig")
    (output / "config.json").write_text(json.dumps({
        "diagnostic_only": True,
        "fixed_entry": policy.entry_model,
        "fixed_exit": "ATR stop 2 / target 4",
        "price_scale": args.market_price_scale,
        "universe": args.universe,
        "symbols": symbols,
        "cases": [asdict(case) for case in cases],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nRANKING")
    print(summary_frame[["robust_rank", "case_id", "walk_forward_return_pct", "median_test_return_pct", "chained_max_drawdown_pct", "gate_count"]].to_string(index=False))
    print("\nNESTED WFO")
    print(nested_summary.to_string(index=False))
    print(f"\nSaved: {output.resolve()}")


if __name__ == "__main__":
    main()
