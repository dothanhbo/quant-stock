from __future__ import annotations

"""Paired, fixed-policy OOS evaluation for the frozen production Q70 policy."""

import argparse
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from hashlib import sha256
import json
import math
import re
import shutil
import sqlite3
import sys
from typing import Any

import pandas as pd

# Permit the documented ``python research/run_frozen_q70_wfo.py`` invocation
# while retaining normal package imports under pytest and ``python -m``.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtesting.frozen_q70_evaluator import run_frozen_q70_backtest
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.trade import Trade
from backtesting.walk_forward import (
    WalkForwardConfig,
    WalkForwardFold,
    build_walk_forward_folds,
    calculate_chained_drawdown_pct,
)
from config.strategy_config import Q70_FROZEN
from core.database_coverage import CoverageUniverseIndex, build_database_coverage_index
from core.historical_breadth import HistoricalBreadthIndex, build_historical_breadth_index
from core.paths import resolve_market_database_path
from core.universe import get_vn100_symbols
from execution.signal_executor import PaperExecutionConfig


DEFAULT_START_DATE = "2018-08-07"

_REQUIRED_TEST_METRICS = (
    "final_equity",
    "total_return_pct",
    "total_trades",
    "win_rate_pct",
    "profit_factor",
    "max_drawdown_pct",
    "gross_profit",
    "gross_loss",
    "gross_trading_pnl",
    "net_trading_pnl",
    "total_buy_commission",
    "total_sell_commission",
    "total_sell_tax",
    "total_transaction_cost",
)

_DECISION_COLUMNS = (
    "fold", "test_start", "test_end", "phase", "symbol", "signal_date",
    "candidate_key", "score", "relative_strength_20d", "adx",
    "percentile_score", "percentile_relative_strength_20d", "percentile_adx",
    "quality_score", "quality_threshold", "market_state", "breadth_ema50_pct",
    "breadth_ema50_change_10d", "gate_accepted", "gate_reason",
    "execution_disposition", "executed_trade_key",
)


def _normalized_symbols(symbols: Any) -> list[str]:
    return sorted(
        {
            str(symbol).strip().upper()
            for symbol in symbols
            if str(symbol).strip() and str(symbol).strip().upper() != "VNINDEX"
        }
    )


def _canonical_hash(value: Any) -> str:
    """SHA-256 of deterministic UTF-8 JSON (sorted keys, compact separators)."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _load_legacy_universe_manifest(path: str | Path) -> tuple[tuple[str, ...], dict[str, str]]:
    """Load and validate a prior audited legacy-universe snapshot offline."""
    source_path = Path(path).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"legacy universe manifest not found: {source_path}")
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"legacy universe manifest is invalid JSON: {source_path}") from exc
    symbols = payload.get("resolved_at_run_symbols") if isinstance(payload, dict) else None
    if not isinstance(symbols, list):
        raise ValueError("legacy universe manifest requires resolved_at_run_symbols list")
    normalized = _normalized_symbols(symbols)
    if not normalized:
        raise ValueError("legacy universe manifest resolves to an empty symbol list")
    expected_count = payload.get("symbol_count")
    if expected_count is not None:
        if isinstance(expected_count, bool) or not isinstance(expected_count, int):
            raise ValueError("legacy universe manifest symbol_count must be an integer")
        if expected_count != len(normalized):
            raise ValueError("legacy universe manifest symbol_count does not match normalized symbols")
    actual_hash = _canonical_hash(normalized)
    expected_hash = payload.get("symbol_list_hash")
    if expected_hash is not None:
        if not isinstance(expected_hash, str):
            raise ValueError("legacy universe manifest symbol_list_hash must be a string")
        if expected_hash != actual_hash:
            raise ValueError("legacy universe manifest symbol_list_hash does not match normalized symbols")
    return tuple(normalized), {
        "source": "persisted_manifest",
        "source_manifest_path": str(source_path),
        "validated_source_hash": actual_hash,
    }


def _fold_membership_record(
    fold: WalkForwardFold,
    *,
    memberships: list[tuple[str, list[str]]],
) -> dict[str, Any]:
    set_dictionary: dict[str, list[str]] = {}
    sessions: list[dict[str, Any]] = []
    for session_date, symbols in memberships:
        normalized = _normalized_symbols(symbols)
        membership_hash = _canonical_hash(normalized)
        set_dictionary.setdefault(membership_hash, normalized)
        sessions.append(
            {
                "session_date": session_date,
                "member_count": len(normalized),
                "membership_hash": membership_hash,
            }
        )
    session_sets = [set(set_dictionary[row["membership_hash"]]) for row in sessions]
    union = sorted(set().union(*session_sets)) if session_sets else []
    intersection = sorted(set.intersection(*session_sets)) if session_sets else []
    return {
        "fold": fold.fold,
        "test_start": str(fold.test_start.date()),
        "test_end": str(fold.test_end.date()),
        "sessions": sessions,
        "membership_sets": set_dictionary,
        "fold_membership_hash": _canonical_hash(
            [{"session_date": row["session_date"], "membership_hash": row["membership_hash"]} for row in sessions]
        ),
        "member_count_min": min((row["member_count"] for row in sessions), default=0),
        "member_count_max": max((row["member_count"] for row in sessions), default=0),
        "union_symbols": union,
        "union_symbol_count": len(union),
        "union_hash": _canonical_hash(union),
        "intersection_symbols": intersection,
        "intersection_symbol_count": len(intersection),
        "intersection_hash": _canonical_hash(intersection),
    }


def _arm_universe_manifest(
    *,
    arm: str,
    folds: list[WalkForwardFold],
    session_dates: tuple[str, ...],
    legacy_symbols: tuple[str, ...] | None,
    coverage_index: CoverageUniverseIndex | None,
    legacy_provenance: dict[str, str] | None = None,
) -> dict[str, Any]:
    is_legacy = arm == "legacy_current_vn100_retroactive"
    if is_legacy:
        resolved_symbols = _normalized_symbols(legacy_symbols or [])
        manifest: dict[str, Any] = {
            "universe_mode": arm,
            "retrospective_current_membership_warning": True,
            "resolved_at_run_symbols": resolved_symbols,
            "symbol_count": len(resolved_symbols),
            "symbol_list_hash": _canonical_hash(resolved_symbols),
            **(legacy_provenance or {"source": "live_current_vn100"}),
        }
    else:
        if coverage_index is None:
            raise ValueError("database coverage manifest requires coverage_index")
        candidates = _normalized_symbols(coverage_index.candidate_symbols)
        manifest = {
            "universe_mode": arm,
            "minimum_history_sessions": coverage_index.minimum_history_sessions,
            "maximum_staleness_sessions": coverage_index.maximum_staleness_sessions,
            "coverage_index_start_date": coverage_index.start_date,
            "coverage_index_end_date": coverage_index.end_date,
            "candidate_symbols": candidates,
            "candidate_symbol_count": len(candidates),
            "candidate_symbol_list_hash": _canonical_hash(candidates),
            "database_coverage_warning": "Database availability coverage is not historical VN100.",
        }

    fold_records: list[dict[str, Any]] = []
    for fold in folds:
        dates = [
            session_date
            for session_date in session_dates
            if str(fold.test_start.date()) <= session_date <= str(fold.test_end.date())
        ]
        memberships = [
            (
                session_date,
                (resolved_symbols if is_legacy else _normalized_symbols(
                    coverage_index.members_as_of(session_date)  # type: ignore[union-attr]
                )),
            )
            for session_date in dates
        ]
        fold_records.append(_fold_membership_record(fold, memberships=memberships))
    manifest["oos_fold_memberships"] = fold_records
    return manifest


def _universe_comparison(
    legacy: dict[str, Any],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for left, right in zip(legacy["oos_fold_memberships"], coverage["oos_fold_memberships"]):
        left_sessions = [(row["session_date"], row["membership_hash"]) for row in left["sessions"]]
        right_sessions = [(row["session_date"], row["membership_hash"]) for row in right["sessions"]]
        left_union, right_union = set(left["union_symbols"]), set(right["union_symbols"])
        left_intersection = set(left["intersection_symbols"])
        right_intersection = set(right["intersection_symbols"])
        rows.append(
            {
                "fold": left["fold"],
                "test_start": left["test_start"],
                "test_end": left["test_end"],
                "legacy_fold_membership_hash": left["fold_membership_hash"],
                "coverage_fold_membership_hash": right["fold_membership_hash"],
                "legacy_member_count_range": [left["member_count_min"], left["member_count_max"]],
                "coverage_member_count_range": [right["member_count_min"], right["member_count_max"]],
                "exact_membership_equality_every_oos_session": left_sessions == right_sessions,
                "legacy_union_hash": left["union_hash"],
                "coverage_union_hash": right["union_hash"],
                "legacy_intersection_hash": left["intersection_hash"],
                "coverage_intersection_hash": right["intersection_hash"],
                "symbols_only_in_legacy_union": sorted(left_union - right_union),
                "symbols_only_in_coverage_union": sorted(right_union - left_union),
                "symbols_only_in_legacy_intersection": sorted(left_intersection - right_intersection),
                "symbols_only_in_coverage_intersection": sorted(right_intersection - left_intersection),
            }
        )
    return {
        "canonical_hashing": "SHA-256 over UTF-8 JSON with sorted keys and compact separators.",
        "legacy_universe_mode": legacy["universe_mode"],
        "coverage_universe_mode": coverage["universe_mode"],
        "oos_fold_comparisons": rows,
    }


def _latest_vnindex_date(database_path: Path) -> str:
    with sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True) as connection:
        row = connection.execute(
            """
            SELECT MAX(date(time)) FROM prices
            WHERE UPPER(TRIM(symbol)) = 'VNINDEX'
            """
        ).fetchone()
    if not row or row[0] is None:
        raise ValueError("canonical market database has no VNINDEX sessions")
    return str(row[0])


def _output_path(output_root: str | Path | None, start_date: str, end_date: str) -> Path:
    if output_root is None:
        return Path("research_results") / f"frozen_q70_paired_{start_date}_{end_date}"
    return Path(output_root)


def _prepare_output(path: Path, *, overwrite: bool) -> None:
    resolved = path.resolve()
    project_root = Path(__file__).resolve().parent.parent
    if resolved in {project_root, project_root.parent, Path(resolved.anchor)}:
        raise ValueError("output_root must be a dedicated experiment directory")
    if resolved.exists():
        if not overwrite:
            raise FileExistsError(f"output directory already exists: {resolved}")
        if not resolved.is_dir():
            raise ValueError("output_root exists but is not a directory")
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True, exist_ok=False)


def _trade_rows(trades: list[Trade], fold: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for trade in trades:
        rows.append(
            {
                "fold": fold,
                "symbol": trade.symbol,
                "signal_date": trade.signal_date,
                "entry_date": trade.entry_date,
                "exit_date": trade.exit_date,
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "quantity": trade.quantity,
                "return_pct": trade.return_pct,
                "net_pnl": trade.net_pnl,
                "buy_commission": trade.buy_commission,
                "sell_commission": trade.sell_commission,
                "sell_tax": trade.sell_tax,
                "total_transaction_cost": trade.total_transaction_cost,
                "exit_reason": getattr(trade.exit_reason, "value", trade.exit_reason),
            }
        )
    return rows


def _decision_rows(
    decisions: tuple[Any, ...],
    *,
    fold: WalkForwardFold,
) -> list[dict[str, Any]]:
    """Flatten immutable evaluator audit records into the OOS-only CSV schema."""
    return [
        {
            "fold": fold.fold,
            "test_start": str(fold.test_start.date()),
            "test_end": str(fold.test_end.date()),
            "phase": decision.phase,
            "symbol": decision.symbol,
            "signal_date": decision.signal_date,
            "candidate_key": decision.candidate_key,
            "score": decision.score,
            "relative_strength_20d": decision.relative_strength_20d,
            "adx": decision.adx,
            "percentile_score": decision.percentile_score,
            "percentile_relative_strength_20d": decision.percentile_relative_strength_20d,
            "percentile_adx": decision.percentile_adx,
            "quality_score": decision.quality_score,
            "quality_threshold": decision.quality_threshold,
            "market_state": decision.market_state,
            "breadth_ema50_pct": decision.breadth_ema50_pct,
            "breadth_ema50_change_10d": decision.breadth_ema50_change_10d,
            "gate_accepted": decision.gate_accepted,
            "gate_reason": decision.gate_reason,
            "execution_disposition": decision.execution_disposition,
            "executed_trade_key": decision.executed_trade_key,
        }
        for decision in decisions
    ]


def _audit_column(prefix: str, value: str) -> str:
    return prefix + re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _decision_summary(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    accepted = [row for row in decisions if row["gate_accepted"]]
    executed = [row for row in accepted if row["execution_disposition"] == "executed"]
    summary: dict[str, Any] = {
        "decision_base_entry_count": len(decisions),
        "decision_q70_accepted_count": len(accepted),
        "decision_executed_count": len(executed),
        "decision_accepted_not_executed_count": len(accepted) - len(executed),
    }
    for prefix, values in (
        ("decision_gate_reason_", (str(row["gate_reason"]) for row in decisions)),
        ("decision_simulator_disposition_", (str(row["execution_disposition"]) for row in accepted)),
        ("decision_accepted_state_", (str(row["market_state"]) for row in accepted)),
        ("decision_executed_state_", (str(row["market_state"]) for row in executed)),
    ):
        for value, count in Counter(values).items():
            summary[_audit_column(prefix, value)] = count
    return summary


def _require_test_metrics(metrics: dict[str, Any]) -> None:
    """Reject incomplete evaluator output instead of publishing invented zeros."""
    for name in _REQUIRED_TEST_METRICS:
        if name not in metrics:
            raise ValueError(f"frozen evaluator missing required metric: {name}")
        try:
            value = float(metrics[name])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"frozen evaluator metric must be numeric: {name}"
            ) from exc
        if not math.isfinite(value):
            raise ValueError(f"frozen evaluator metric must be finite: {name}")


def _fold_row(
    fold: WalkForwardFold,
    train_metrics: dict[str, Any],
    test_metrics: dict[str, Any],
    initial_equity: float,
) -> dict[str, Any]:
    return {
        "fold": fold.fold,
        **{key: str(value) for key, value in fold.to_dict().items() if key != "fold"},
        "test_initial_equity": initial_equity,
        "test_final_equity": test_metrics.get("final_equity"),
        "test_return_pct": test_metrics.get("total_return_pct"),
        "test_trades": test_metrics["total_trades"],
        "test_win_rate_pct": test_metrics["win_rate_pct"],
        "test_profit_factor": test_metrics["profit_factor"],
        "test_max_drawdown_pct": test_metrics["max_drawdown_pct"],
        "test_total_transaction_cost": test_metrics["total_transaction_cost"],
        "test_total_buy_commission": test_metrics["total_buy_commission"],
        "test_total_sell_commission": test_metrics["total_sell_commission"],
        "test_total_sell_tax": test_metrics["total_sell_tax"],
        "train_trades_diagnostic": train_metrics.get("total_trades", 0),
        "train_return_pct_diagnostic": train_metrics.get("total_return_pct"),
        "evaluation_rows": test_metrics.get("total_evaluation_rows", 0),
        "base_entry_candidates": test_metrics.get("base_entry_candidates", 0),
        "q70_accepted_candidates": test_metrics.get("q70_accepted_candidates", 0),
        "q70_rejection_counts": json.dumps(test_metrics.get("q70_rejection_counts", {}), sort_keys=True),
        "q70_rejection_state_counts": json.dumps(test_metrics.get("q70_rejection_state_counts", {}), sort_keys=True),
        "eligible_count_min": test_metrics.get("coverage_eligible_count_min"),
        "eligible_count_max": test_metrics.get("coverage_eligible_count_max"),
        "eligible_count_mean": test_metrics.get("coverage_eligible_count_mean"),
    }


def _run_arm(
    *,
    arm: str,
    folds: list[WalkForwardFold],
    database_path: Path,
    current_symbols: tuple[str, ...] | None,
    coverage_index: CoverageUniverseIndex | None,
    breadth_index: HistoricalBreadthIndex,
    paper: PaperExecutionConfig,
    parity: BacktestPaperParityConfig,
    decision_ledger: bool = False,
) -> dict[str, Any]:
    current_capital = parity.initial_cash
    fold_rows: list[dict[str, Any]] = []
    oos_trades: list[dict[str, Any]] = []
    oos_decisions: list[dict[str, Any]] = []
    equity_curves: list[pd.DataFrame] = []
    for fold in folds:
        common = {
            "db_path": str(database_path),
            "coverage_index": coverage_index,
            "breadth_index": breadth_index,
            "paper_execution_config": paper,
            "universe_mode": "database_coverage" if arm == "database_coverage_50_history_5_staleness" else "current_vn100",
            "minimum_history_sessions": 50,
            "maximum_staleness_sessions": 5,
            "current_vn100_symbols": current_symbols,
        }
        # Diagnostic only: no parameter is fitted, selected, or fed forward.
        _, train_metrics, _ = run_frozen_q70_backtest(
            start_date=str(fold.train_start.date()), end_date=str(fold.train_end.date()),
            parity_config=parity, **common,
        )
        test_parity = replace(parity, initial_cash=current_capital)
        trades, test_metrics, equity = run_frozen_q70_backtest(
            start_date=str(fold.test_start.date()), end_date=str(fold.test_end.date()),
            parity_config=test_parity,
            collect_decision_ledger=decision_ledger,
            decision_phase="test",
            **common,
        )
        _require_test_metrics(test_metrics)
        fold_rows.append(_fold_row(fold, train_metrics, test_metrics, current_capital))
        oos_trades.extend(_trade_rows(trades, fold.fold))
        if decision_ledger:
            decisions = test_metrics.get("decision_ledger")
            if decisions is None:
                raise ValueError("frozen evaluator missing decision ledger")
            oos_decisions.extend(_decision_rows(decisions, fold=fold))
        equity_curves.append(equity)
        current_capital = float(test_metrics["final_equity"])

    fold_frame = pd.DataFrame(fold_rows).sort_values("fold")
    trade_frame = pd.DataFrame(oos_trades).sort_values(
        ["fold", "signal_date", "symbol"], kind="stable"
    ) if oos_trades else pd.DataFrame(columns=["fold", "symbol", "signal_date"])
    decision_frame = pd.DataFrame(oos_decisions, columns=_DECISION_COLUMNS).sort_values(
        ["fold", "signal_date", "symbol", "candidate_key"], kind="stable"
    ) if oos_decisions else pd.DataFrame(columns=_DECISION_COLUMNS)
    summary = {
        "arm": arm,
        "policy_fingerprint": "Q70_FROZEN/hybrid_trend_donchian/ATR2x5/fixed",
        "initial_equity": parity.initial_cash,
        "final_equity": current_capital,
        "compounded_chained_oos_return_pct": (current_capital / parity.initial_cash - 1.0) * 100.0,
        "total_oos_trades": int(len(trade_frame)),
        "total_oos_transaction_cost": float(fold_frame["test_total_transaction_cost"].sum()),
        "total_evaluation_rows": int(fold_frame["evaluation_rows"].sum()),
        "total_base_entry_candidates": int(fold_frame["base_entry_candidates"].sum()),
        "total_q70_accepted_candidates": int(fold_frame["q70_accepted_candidates"].sum()),
        "chained_oos_max_drawdown_pct": calculate_chained_drawdown_pct(equity_curves),
        "universe_label": arm,
        "retrospective_current_vn100_warning": arm.startswith("legacy_current"),
        "database_coverage_limitation": arm.startswith("database_coverage"),
    }
    if decision_ledger:
        summary.update(_decision_summary(oos_decisions))
    return {
        "folds": fold_frame,
        "trades": trade_frame,
        "decisions": decision_frame,
        "summary": summary,
    }


def run_paired_frozen_q70_wfo(
    *,
    start_date: str = DEFAULT_START_DATE,
    end_date: str | None = None,
    train_months: int = 24,
    test_months: int = 6,
    step_months: int = 6,
    minimum_history_sessions: int = 50,
    maximum_staleness_sessions: int = 5,
    output_root: str | Path | None = None,
    overwrite: bool = False,
    db_path: str | Path | None = None,
    legacy_universe_manifest: str | Path | None = None,
    decision_ledger: bool = False,
) -> dict[str, Any]:
    """Run paired fixed-Q70 folds and write a new, self-contained experiment."""
    if (minimum_history_sessions, maximum_staleness_sessions) != (50, 5):
        raise ValueError("frozen paired Q70 protocol requires 50 history and 5 staleness sessions")
    # Validate the offline source before any database/index work or output path.
    if legacy_universe_manifest is not None:
        current_symbols, legacy_provenance = _load_legacy_universe_manifest(
            legacy_universe_manifest
        )
    else:
        current_symbols = tuple(_normalized_symbols(get_vn100_symbols()))
        legacy_provenance = {"source": "live_current_vn100"}
    database_path = resolve_market_database_path(db_path)
    resolved_end_date = end_date or _latest_vnindex_date(database_path)
    folds = build_walk_forward_folds(WalkForwardConfig(
        start_date=start_date, end_date=resolved_end_date,
        train_months=train_months, test_months=test_months, step_months=step_months,
    ))
    # Build every dependency before creating any result path.
    coverage = build_database_coverage_index(
        start_date, resolved_end_date, minimum_history_sessions=50,
        maximum_staleness_sessions=5, database_path=database_path,
    )
    current_breadth = build_historical_breadth_index(
        start_date, resolved_end_date, universe_mode="current_vn100",
        symbols=current_symbols, database_path=database_path,
    )
    coverage_breadth = build_historical_breadth_index(
        start_date, resolved_end_date, universe_mode="database_coverage",
        coverage_index=coverage, database_path=database_path,
    )
    paper = PaperExecutionConfig()
    parity = BacktestPaperParityConfig.from_paper_config(paper)
    if (
        Q70_FROZEN.entry_model != "hybrid_trend_donchian"
        or not Q70_FROZEN.quality_enabled
        or Q70_FROZEN.quality_threshold != 0.70
        or Q70_FROZEN.stop_atr_multiplier != 2.0
        or Q70_FROZEN.target_atr_multiplier != 5.0
        or paper.atr_stop_multiplier != 2.0
        or paper.target_atr_multiplier != 5.0
        or parity.atr_stop_multiplier != 2.0
    ):
        raise ValueError("frozen Q70 policy/parity configuration is inconsistent")

    output = _output_path(output_root, start_date, resolved_end_date)
    _prepare_output(output, overwrite=overwrite)
    arm_specs = (
        ("legacy_current_vn100_retroactive", current_symbols, None, current_breadth),
        ("database_coverage_50_history_5_staleness", None, coverage, coverage_breadth),
    )
    universe_manifests = {
        "legacy_current_vn100_retroactive": _arm_universe_manifest(
            arm="legacy_current_vn100_retroactive",
            folds=folds,
            session_dates=coverage.session_dates,
            legacy_symbols=current_symbols,
            coverage_index=None,
            legacy_provenance=legacy_provenance,
        ),
        "database_coverage_50_history_5_staleness": _arm_universe_manifest(
            arm="database_coverage_50_history_5_staleness",
            folds=folds,
            session_dates=coverage.session_dates,
            legacy_symbols=None,
            coverage_index=coverage,
        ),
    }
    universe_comparison = _universe_comparison(
        universe_manifests["legacy_current_vn100_retroactive"],
        universe_manifests["database_coverage_50_history_5_staleness"],
    )
    arms: dict[str, dict[str, Any]] = {}
    for arm, arm_symbols, arm_coverage, arm_breadth in arm_specs:
        arms[arm] = _run_arm(
            arm=arm, folds=folds, database_path=database_path,
            current_symbols=arm_symbols, coverage_index=arm_coverage,
            breadth_index=arm_breadth, paper=paper, parity=parity,
            decision_ledger=decision_ledger,
        )
        arm_dir = output / arm
        arm_dir.mkdir()
        arms[arm]["folds"].to_csv(arm_dir / "folds.csv", index=False, encoding="utf-8")
        pd.DataFrame([arms[arm]["summary"]]).to_csv(arm_dir / "summary.csv", index=False, encoding="utf-8")
        arms[arm]["trades"].to_csv(arm_dir / "trade_level_oos.csv", index=False, encoding="utf-8")
        if decision_ledger:
            arms[arm]["decisions"].to_csv(
                arm_dir / "candidate_decision_oos.csv", index=False, encoding="utf-8"
            )
        (arm_dir / "policy_fingerprint.json").write_text(json.dumps({
            "policy": asdict(Q70_FROZEN), "paper_execution": asdict(paper),
            "parity": asdict(parity), "universe_label": arm,
        }, indent=2, default=str), encoding="utf-8")
        (arm_dir / "universe_manifest.json").write_text(
            json.dumps(universe_manifests[arm], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (arm_dir / "assumptions.md").write_text(
            "# Assumptions\n\n"
            "Q70 is frozen; train folds are diagnostic only and never fit parameters.\n\n"
            f"Universe label: `{arm}`.\n\n"
            + (
                "This arm applies current VN100 membership retrospectively and is survivorship-biased.\n"
                if arm.startswith("legacy_current")
                else "Database coverage is not historical VN100.\n"
            )
            + "Historical execution remains a backtest approximation.\n",
            encoding="utf-8",
        )
    comparison = pd.DataFrame([arms[name]["summary"] for name, *_ in arm_specs])
    comparison.to_csv(output / "comparison.csv", index=False, encoding="utf-8")
    manifest = {
        "database_path": str(database_path), "latest_vnindex_date": resolved_end_date,
        "folds": [fold.to_dict() for fold in folds], "q70_threshold": 0.70,
        "policy_fingerprint": "Q70_FROZEN/hybrid_trend_donchian/ATR2x5/fixed",
        "arms": [name for name, *_ in arm_specs],
        "warning": "legacy_current_vn100_retroactive is survivorship-biased; database coverage is not historical VN100.",
        "legacy_universe_provenance": legacy_provenance,
    }
    (output / "experiment_manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    (output / "universe_comparison.json").write_text(
        json.dumps(universe_comparison, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {
        "output_root": output,
        "folds": folds,
        "arms": arms,
        "manifest": manifest,
        "universe_manifests": universe_manifests,
        "universe_comparison": universe_comparison,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date")
    parser.add_argument("--train-months", type=int, default=24)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument("--minimum-history-sessions", type=int, default=50)
    parser.add_argument("--maximum-staleness-sessions", type=int, default=5)
    parser.add_argument("--output-root")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--db-path")
    parser.add_argument("--legacy-universe-manifest")
    parser.add_argument("--decision-ledger", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_paired_frozen_q70_wfo(**vars(args))
