from __future__ import annotations

"""Post-hoc, fixed-policy NEUTRAL-entry sensitivity for frozen Q70.

This runner is intentionally separate from the canonical paired experiment:
it is sensitivity evidence selected after reviewing prior OOS results, not a
new independent validation.
"""

import argparse
from dataclasses import asdict, replace
from pathlib import Path
import json
import math
import sys
from typing import Any

import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtesting.frozen_q70_evaluator import run_frozen_q70_backtest
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.walk_forward import (
    WalkForwardConfig,
    WalkForwardFold,
    build_walk_forward_folds,
    calculate_chained_drawdown_pct,
)
from config.strategy_config import Q70_FROZEN
from core.database_coverage import build_database_coverage_index
from core.historical_breadth import build_historical_breadth_index
from core.paths import resolve_market_database_path
from execution.signal_executor import PaperExecutionConfig
from research.run_frozen_q70_wfo import (
    DEFAULT_START_DATE,
    _decision_summary,
    _fold_row,
    _latest_vnindex_date,
    _prepare_output,
    _require_test_metrics,
    _trade_rows,
)


_ALLOWED_NON_NEUTRAL_STATES = frozenset({
    "RECOVERY", "HEALTHY_BULL", "FRAGILE_BULL",
})

_DECISION_COLUMNS = (
    "fold", "test_start", "test_end", "phase", "symbol", "signal_date",
    "candidate_key", "score", "relative_strength_20d", "adx",
    "percentile_score", "percentile_relative_strength_20d", "percentile_adx",
    "quality_score", "quality_threshold", "market_state", "breadth_ema50_pct",
    "breadth_ema50_change_10d", "gate_accepted", "gate_reason",
    "state_policy_eligible", "state_policy_reason", "execution_disposition",
    "executed_trade_key",
)


def _decision_rows(decisions: tuple[Any, ...], *, fold: WalkForwardFold) -> list[dict[str, Any]]:
    return [{
        "fold": fold.fold, "test_start": str(fold.test_start.date()),
        "test_end": str(fold.test_end.date()), "phase": decision.phase,
        "symbol": decision.symbol, "signal_date": decision.signal_date,
        "candidate_key": decision.candidate_key, "score": decision.score,
        "relative_strength_20d": decision.relative_strength_20d, "adx": decision.adx,
        "percentile_score": decision.percentile_score,
        "percentile_relative_strength_20d": decision.percentile_relative_strength_20d,
        "percentile_adx": decision.percentile_adx, "quality_score": decision.quality_score,
        "quality_threshold": decision.quality_threshold, "market_state": decision.market_state,
        "breadth_ema50_pct": decision.breadth_ema50_pct,
        "breadth_ema50_change_10d": decision.breadth_ema50_change_10d,
        "gate_accepted": decision.gate_accepted, "gate_reason": decision.gate_reason,
        "state_policy_eligible": decision.state_policy_eligible,
        "state_policy_reason": decision.state_policy_reason,
        "execution_disposition": decision.execution_disposition,
        "executed_trade_key": decision.executed_trade_key,
    } for decision in decisions]


def _output_path(output_root: str | Path | None, start: str, end: str) -> Path:
    return (
        Path("research_results") / f"frozen_q70_neutral_exclusion_{start}_{end}"
        if output_root is None else Path(output_root)
    )


def _fold_row_with_policy(
    fold: WalkForwardFold,
    train_metrics: dict[str, Any],
    test_metrics: dict[str, Any],
    initial_equity: float,
) -> dict[str, Any]:
    row = _fold_row(fold, train_metrics, test_metrics, initial_equity)
    row.update({
        "state_policy_rejected_candidates": test_metrics.get(
            "state_policy_rejected_candidates", 0
        ),
        "state_policy_rejection_state_counts": json.dumps(
            test_metrics.get("state_policy_rejection_state_counts", {}), sort_keys=True
        ),
    })
    return row


def _run_arm(
    *,
    arm: str,
    allowed_entry_states: frozenset[str] | None,
    folds: list[WalkForwardFold],
    database_path: Path,
    coverage_index: Any,
    breadth_index: Any,
    paper: PaperExecutionConfig,
    parity: BacktestPaperParityConfig,
) -> dict[str, Any]:
    capital = parity.initial_cash
    fold_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    decision_rows: list[dict[str, Any]] = []
    curves: list[pd.DataFrame] = []
    common = {
        "db_path": str(database_path),
        "universe_mode": "database_coverage",
        "minimum_history_sessions": 50,
        "maximum_staleness_sessions": 5,
        "coverage_index": coverage_index,
        "breadth_index": breadth_index,
        "paper_execution_config": paper,
        "allowed_entry_states": allowed_entry_states,
    }
    for fold in folds:
        _, train_metrics, _ = run_frozen_q70_backtest(
            start_date=str(fold.train_start.date()),
            end_date=str(fold.train_end.date()),
            parity_config=parity,
            **common,
        )
        test_parity = replace(parity, initial_cash=capital)
        trades, test_metrics, curve = run_frozen_q70_backtest(
            start_date=str(fold.test_start.date()),
            end_date=str(fold.test_end.date()),
            parity_config=test_parity,
            collect_decision_ledger=True,
            decision_phase="test",
            **common,
        )
        _require_test_metrics(test_metrics)
        decisions = test_metrics.get("decision_ledger")
        if decisions is None:
            raise ValueError("frozen evaluator missing decision ledger")
        fold_rows.append(_fold_row_with_policy(fold, train_metrics, test_metrics, capital))
        trade_rows.extend(_trade_rows(trades, fold.fold))
        decision_rows.extend(_decision_rows(decisions, fold=fold))
        curves.append(curve)
        capital = float(test_metrics["final_equity"])

    fold_frame = pd.DataFrame(fold_rows).sort_values("fold", kind="stable")
    trade_frame = pd.DataFrame(trade_rows).sort_values(
        ["fold", "signal_date", "symbol"], kind="stable"
    ) if trade_rows else pd.DataFrame()
    decision_frame = pd.DataFrame(decision_rows, columns=_DECISION_COLUMNS).sort_values(
        ["fold", "signal_date", "symbol", "candidate_key"], kind="stable"
    ) if decision_rows else pd.DataFrame(columns=_DECISION_COLUMNS)
    positive_pnl = float(trade_frame.loc[trade_frame["net_pnl"] > 0, "net_pnl"].sum()) if not trade_frame.empty else 0.0
    negative_pnl = float(trade_frame.loc[trade_frame["net_pnl"] < 0, "net_pnl"].sum()) if not trade_frame.empty else 0.0
    summary: dict[str, Any] = {
        "arm": arm,
        "initial_equity": parity.initial_cash,
        "final_equity": capital,
        "compounded_chained_oos_return_pct": (capital / parity.initial_cash - 1.0) * 100.0,
        "total_oos_trades": int(len(trade_frame)),
        "win_rate_pct": (
            float((trade_frame["net_pnl"] > 0).mean() * 100.0)
            if not trade_frame.empty else 0.0
        ),
        "profit_factor": (
            positive_pnl / abs(negative_pnl) if negative_pnl < 0
            else (math.inf if positive_pnl > 0 else 0.0)
        ),
        "total_oos_transaction_cost": float(fold_frame["test_total_transaction_cost"].sum()),
        "total_evaluation_rows": int(fold_frame["evaluation_rows"].sum()),
        "total_base_entry_candidates": int(fold_frame["base_entry_candidates"].sum()),
        "total_q70_accepted_candidates": int(fold_frame["q70_accepted_candidates"].sum()),
        "total_state_policy_rejected_candidates": int(
            fold_frame["state_policy_rejected_candidates"].sum()
        ),
        "chained_oos_max_drawdown_pct": calculate_chained_drawdown_pct(curves),
        "universe_mode": "database_coverage",
        "minimum_history_sessions": 50,
        "maximum_staleness_sessions": 5,
        "allowed_entry_states": (
            None if allowed_entry_states is None else sorted(allowed_entry_states)
        ),
    }
    summary.update(_decision_summary(decision_rows))
    return {"folds": fold_frame, "trades": trade_frame, "decisions": decision_frame, "summary": summary}


def _cagr(initial: float, final: float, folds: list[WalkForwardFold]) -> float:
    days = (folds[-1].test_end - folds[0].test_start).days
    return (final / initial) ** (365.25 / days) - 1.0 if days > 0 else 0.0


def _comparison_row(
    payload: dict[str, Any], *, best_baseline_fold: int, folds: list[WalkForwardFold]
) -> dict[str, Any]:
    summary = dict(payload["summary"])
    rows = payload["folds"]
    excluded = rows.loc[rows["fold"] != best_baseline_fold, "test_return_pct"]
    summary.update({
        "cagr_pct": 100.0 * _cagr(summary["initial_equity"], summary["final_equity"], folds),
        "profitable_folds": int((rows["test_return_pct"] > 0).sum()),
        "best_fold": int(rows.loc[rows["test_return_pct"].idxmax(), "fold"]),
        "worst_fold": int(rows.loc[rows["test_return_pct"].idxmin(), "fold"]),
        "baseline_identified_best_fold": best_baseline_fold,
        "return_excluding_best_baseline_fold_pct": 100.0 * (
            math.prod(1.0 + value / 100.0 for value in excluded) - 1.0
        ),
    })
    return summary


def run_frozen_q70_neutral_exclusion_wfo(
    *,
    start_date: str = DEFAULT_START_DATE,
    end_date: str | None = None,
    train_months: int = 24,
    test_months: int = 6,
    step_months: int = 6,
    output_root: str | Path | None = None,
    overwrite: bool = False,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Run the post-hoc, two-arm NEUTRAL-entry sensitivity only."""
    database_path = resolve_market_database_path(db_path)
    resolved_end = end_date or _latest_vnindex_date(database_path)
    folds = build_walk_forward_folds(WalkForwardConfig(
        start_date=start_date, end_date=resolved_end, train_months=train_months,
        test_months=test_months, step_months=step_months,
    ))
    coverage = build_database_coverage_index(
        start_date, resolved_end, minimum_history_sessions=50,
        maximum_staleness_sessions=5, database_path=database_path,
    )
    breadth = build_historical_breadth_index(
        start_date, resolved_end, universe_mode="database_coverage",
        coverage_index=coverage, database_path=database_path,
    )
    paper = PaperExecutionConfig()
    parity = BacktestPaperParityConfig.from_paper_config(paper)
    if not (Q70_FROZEN.quality_threshold == 0.70 and Q70_FROZEN.entry_model == "hybrid_trend_donchian"):
        raise ValueError("frozen Q70 policy configuration is inconsistent")

    output = _output_path(output_root, start_date, resolved_end)
    _prepare_output(output, overwrite=overwrite)
    specs = (("baseline_frozen_q70", None), ("exclude_neutral", _ALLOWED_NON_NEUTRAL_STATES))
    arms = {
        arm: _run_arm(arm=arm, allowed_entry_states=states, folds=folds,
                      database_path=database_path, coverage_index=coverage,
                      breadth_index=breadth, paper=paper, parity=parity)
        for arm, states in specs
    }
    baseline_rows = arms["baseline_frozen_q70"]["folds"]
    best_baseline_fold = int(baseline_rows.loc[baseline_rows["test_return_pct"].idxmax(), "fold"])
    comparison = pd.DataFrame([
        _comparison_row(arms[arm], best_baseline_fold=best_baseline_fold, folds=folds)
        for arm, _ in specs
    ])
    for arm, states in specs:
        directory = output / arm
        directory.mkdir()
        payload = arms[arm]
        payload["folds"].to_csv(directory / "folds.csv", index=False, encoding="utf-8")
        pd.DataFrame([payload["summary"]]).to_csv(directory / "summary.csv", index=False, encoding="utf-8")
        payload["trades"].to_csv(directory / "trade_level_oos.csv", index=False, encoding="utf-8")
        payload["decisions"].to_csv(directory / "candidate_decision_oos.csv", index=False, encoding="utf-8")
        (directory / "policy_fingerprint.json").write_text(json.dumps({
            "policy": asdict(Q70_FROZEN), "paper_execution": asdict(paper),
            "parity": asdict(parity), "universe_mode": "database_coverage",
            "minimum_history_sessions": 50, "maximum_staleness_sessions": 5,
            "allowed_entry_states": None if states is None else sorted(states),
        }, indent=2, default=str), encoding="utf-8")
        (directory / "assumptions.md").write_text(
            "# Assumptions\n\nThis is a post-hoc sensitivity experiment selected after reviewing prior OOS results; it is not fresh OOS confirmation.\n\n"
            "Only NEUTRAL entry eligibility differs. Database coverage is not historical VN100. Historical execution is a backtest approximation.\n",
            encoding="utf-8",
        )
    comparison.to_csv(output / "comparison.csv", index=False, encoding="utf-8")
    manifest = {
        "database_path": str(database_path), "latest_vnindex_date": resolved_end,
        "folds": [fold.to_dict() for fold in folds],
        "arms": [arm for arm, _ in specs], "universe_mode": "database_coverage",
        "minimum_history_sessions": 50, "maximum_staleness_sessions": 5,
        "policy_fingerprint": "Q70_FROZEN/hybrid_trend_donchian/ATR2x5/fixed",
        "sensitivity_warning": "Rule selected after reviewing prior OOS results; sensitivity evidence only, not fresh OOS confirmation.",
        "only_difference": "exclude_neutral rejects gate-accepted candidates whose actual PaperV2QualityGate state is NEUTRAL before simulation.",
        "shared_dependency_fingerprint": {"coverage": [coverage.start_date, coverage.end_date, 50, 5], "breadth": [breadth.start_date, breadth.end_date]},
    }
    (output / "experiment_manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return {"output_root": output, "folds": folds, "arms": arms, "comparison": comparison, "manifest": manifest}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date")
    parser.add_argument("--train-months", type=int, default=24)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument("--output-root")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--db-path")
    return parser.parse_args()


if __name__ == "__main__":
    run_frozen_q70_neutral_exclusion_wfo(**vars(parse_args()))
