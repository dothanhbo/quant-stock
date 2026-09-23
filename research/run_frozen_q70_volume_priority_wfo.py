from __future__ import annotations

"""Controlled, database-coverage-only frozen-Q70 priority sensitivity.

This runner changes only execution priority among already Q70-accepted
candidates belonging to the same signal-date subgroup and entry-date event.
It is sensitivity evidence, not a production policy or fresh OOS validation.
"""

import argparse
from collections import Counter
from dataclasses import asdict, replace
from hashlib import sha256
import json
import math
from pathlib import Path
import shutil
import tempfile
from io import StringIO
from typing import Any, Iterable
from uuid import uuid4

import pandas as pd

if __package__ in {None, ""}:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtesting.frozen_q70_evaluator import (
    CANONICAL_SIGNAL_SCORE_PRIORITY_FINGERPRINT,
    run_frozen_q70_backtest,
)
from backtesting.paper_parity import BacktestPaperParityConfig
from backtesting.trade import Trade
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
from quantlab.evaluation import FROZEN_Q70_VOLUME_RSI_TEMPORAL_STABILITY_V1
from quantlab.ranking import FROZEN_Q70_VOLUME_RATIO_RANK_V1
from research.run_frozen_q70_wfo import (
    _decision_rows,
    _decision_summary,
    _fold_row,
    _require_test_metrics,
    _trade_rows,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_START_DATE = "2018-08-07"
DEFAULT_END_DATE = "2026-09-17"
MINIMUM_HISTORY_SESSIONS = 50
MAXIMUM_STALENESS_SESSIONS = 5
CANONICAL_TRADE_SHA256 = "F6596DAE7F86D094542EA4564C8A264846F23D6EAD3FA85FC9825F5FE384116C"
CANONICAL_TRADE_PATH = (
    PROJECT_ROOT / "research_results" / "frozen_q70_paired_full_2018_2026_v4_instrumented" /
    "database_coverage_50_history_5_staleness" / "trade_level_oos.csv"
)

ARM_SPECS = (
    ("baseline_signal_score_priority", None),
    ("volume_ratio_priority", FROZEN_Q70_VOLUME_RATIO_RANK_V1),
)

TRADE_COLUMNS = (
    "fold", "symbol", "signal_date", "entry_date", "exit_date", "entry_price",
    "exit_price", "quantity", "return_pct", "net_pnl", "buy_commission",
    "sell_commission", "sell_tax", "total_transaction_cost", "exit_reason",
)
PRIORITY_COLUMNS = (
    "fold", "candidate_key", "symbol", "signal_date", "entry_date", "volume_ratio",
    "volume_ratio_finite", "q70_quality_score", "baseline_signal_score",
    "baseline_within_entry_date_ordinal", "signal_date_group_key",
    "signal_date_group_size", "signal_date_group_slot_ordinals",
    "variant_within_signal_date_ordinal", "final_simulator_priority_ordinal",
    "ranking_policy_fingerprint", "execution_disposition", "executed_trade_key",
)
ACCEPTED_PARITY_COLUMNS = (
    "fold", "baseline_accepted_count", "variant_accepted_count",
    "baseline_candidate_key_sha256", "variant_candidate_key_sha256",
    "baseline_raw_candidate_sha256", "variant_raw_candidate_sha256",
    "baseline_q70_decision_sha256", "variant_q70_decision_sha256",
    "baseline_exit_definition_sha256", "variant_exit_definition_sha256", "parity",
)
EXECUTED_COMPARISON_COLUMNS = (
    "fold", "candidate_key", "signal_date", "entry_date", "symbol",
    "baseline_executed", "variant_executed", "baseline_entry_ordinal",
    "variant_entry_ordinal", "volume_ratio", "q70_quality", "signal_score",
    "baseline_disposition", "variant_disposition",
    "divergence_classification", "causal_anchor_fold",
    "causal_anchor_entry_date", "causal_anchor_identity",
)
RANKING_CHANGE_COLUMNS = (
    "fold", "entry_date", "accepted_entry_event_count",
    "distinct_signal_date_subgroup_count", "changed_final_ordinal_count",
    "baseline_top_three_keys", "variant_top_three_keys", "top_three_overlap_count",
    "daily_order_cap_selection_changed", "baseline_executed_count",
    "variant_executed_count", "baseline_rejection_counts", "variant_rejection_counts",
    "ranking_change_identity", "direct_execution_divergence_count",
    "downstream_divergence_count", "unexpected_divergence_count",
)


def _identity_json_value(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return {"__nonfinite_float__": "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")}
    if isinstance(value, dict):
        return {str(key): _identity_json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_identity_json_value(item) for item in value]
    return value


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        _identity_json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str, allow_nan=False,
    ).encode("utf-8")


def _hash_payload(value: Any) -> str:
    return sha256(_canonical_json(value)).hexdigest()


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _trade_frame_csv_hash(frame: pd.DataFrame) -> str:
    stream = StringIO(newline="")
    frame.loc[:, TRADE_COLUMNS].to_csv(stream, index=False, lineterminator="\n")
    return sha256(stream.getvalue().encode("utf-8")).hexdigest().upper()


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _safe_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_safe_value(item) for item in value]
    if hasattr(value, "item"):
        return _safe_value(value.item())
    return str(value)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(_safe_value(payload), ensure_ascii=False, sort_keys=True,
                   indent=2, allow_nan=False) + "\n",
        encoding="utf-8", newline="\n",
    )


def _write_frame(path: Path, frame: pd.DataFrame, columns: tuple[str, ...]) -> None:
    missing = sorted(set(columns) - set(frame.columns))
    if missing:
        raise ValueError(f"artifact is missing required columns for {path.name}: {missing}")
    frame.loc[:, columns].to_csv(path, index=False, encoding="utf-8", lineterminator="\n")


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _resolve_output(path: str | Path | None) -> Path:
    candidate = (
        PROJECT_ROOT / "research_results" / "frozen_q70_volume_priority_2018_2026"
        if path is None else Path(path).expanduser()
    )
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.absolute()


def _validate_output(path: Path, *, database_path: Path, overwrite: bool) -> None:
    if path.is_symlink():
        raise ValueError("output_root must not be a symlink")
    resolved, root = path.resolve(), PROJECT_ROOT.resolve()
    forbidden_exact = {
        Path(resolved.anchor), root, root.parent, root / "research_results",
        database_path.parent.resolve(),
    }
    forbidden_trees = (root / ".git", root / "data", root / "research")
    if resolved in forbidden_exact or any(_is_within(resolved, item.resolve()) for item in forbidden_trees):
        raise ValueError("output_root must be a dedicated experiment directory")
    if resolved.exists():
        if not resolved.is_dir():
            raise ValueError("output_root exists but is not a directory")
        if not overwrite:
            raise FileExistsError(f"output directory already exists: {resolved}")
    resolved.parent.mkdir(parents=True, exist_ok=True)


def _publish_atomic(temporary: Path, output: Path) -> None:
    if not output.exists():
        temporary.replace(output)
        return
    backup = output.with_name(f".{output.name}.backup-{uuid4().hex}")
    output.replace(backup)
    try:
        temporary.replace(output)
    except Exception:
        if output.exists():
            shutil.rmtree(output)
        backup.replace(output)
        raise
    try:
        shutil.rmtree(backup)
    except Exception:
        if output.exists():
            shutil.rmtree(output)
        backup.replace(output)
        raise


def _candidate_key(trade: Trade) -> str:
    return f"{trade.symbol.strip().upper()}|{trade.signal_date.isoformat()}"


def _audit_numeric(value: Any) -> Any:
    if value is None:
        return None
    number = float(value)
    if math.isnan(number):
        return "NaN"
    if math.isinf(number):
        return "Infinity" if number > 0 else "-Infinity"
    return number


def _priority_rows(ledger: tuple[Any, ...], decisions: tuple[Any, ...], fold: int) -> list[dict[str, Any]]:
    decision_by_key = {item.candidate_key: item for item in decisions}
    rows = []
    for item in ledger:
        decision = decision_by_key[item.candidate_key]
        rows.append({
            "fold": fold, "candidate_key": item.candidate_key, "symbol": item.symbol,
            "signal_date": item.signal_date, "entry_date": item.entry_date,
            "volume_ratio": _audit_numeric(item.volume_ratio), "volume_ratio_finite": item.volume_ratio_finite,
            "q70_quality_score": item.q70_quality_score,
            "baseline_signal_score": item.baseline_signal_score,
            "baseline_within_entry_date_ordinal": item.baseline_within_entry_date_ordinal,
            "signal_date_group_key": item.signal_date_group_key,
            "signal_date_group_size": item.signal_date_group_size,
            "signal_date_group_slot_ordinals": json.dumps(item.signal_date_group_slot_ordinals),
            "variant_within_signal_date_ordinal": item.variant_within_signal_date_ordinal,
            "final_simulator_priority_ordinal": item.final_simulator_priority_ordinal,
            "ranking_policy_fingerprint": item.ranking_policy_fingerprint,
            "execution_disposition": decision.execution_disposition,
            "executed_trade_key": decision.executed_trade_key,
        })
    return rows


def _audit_hashes(audit: tuple[Any, ...], decisions: tuple[Any, ...]) -> dict[str, Any]:
    ordered = sorted(audit, key=lambda item: item.candidate_key)
    decision_by_key = {item.candidate_key: item for item in decisions}
    keys = [item.candidate_key for item in ordered]
    raw = [{
        "candidate_key": item.candidate_key, "symbol": item.symbol,
        "signal_date": item.signal_date.isoformat(), "entry_date": item.entry_date.isoformat(),
        "entry_price": item.entry_price, "signal_score": item.signal_score,
        "relative_strength_20d": item.relative_strength_20d, "adx": item.adx,
        "volume_ratio": item.volume_ratio, "atr": item.atr, "stop_price": item.stop_price,
        "market_regime": item.market_regime, "entry_model": item.entry_model,
    } for item in ordered]
    q70 = [{
        "candidate_key": item.candidate_key,
        "quality_score": decision_by_key[item.candidate_key].quality_score,
        "gate_accepted": decision_by_key[item.candidate_key].gate_accepted,
        "gate_reason": decision_by_key[item.candidate_key].gate_reason,
        "market_state": decision_by_key[item.candidate_key].market_state,
    } for item in ordered]
    exits = [{
        "candidate_key": item.candidate_key, "exit_date": item.exit_date.isoformat(),
        "exit_price": item.exit_price, "exit_reason": item.exit_reason,
        "execution": item.execution, "atr": item.atr, "stop_price": item.stop_price,
    } for item in ordered]
    return {
        "count": len(ordered), "key_hash": _hash_payload(keys),
        "raw_hash": _hash_payload(raw), "q70_hash": _hash_payload(q70),
        "exit_hash": _hash_payload(exits),
    }


def _state_and_block_counts(decisions: pd.DataFrame) -> tuple[dict[str, int], dict[str, int]]:
    executed = decisions.loc[decisions["execution_disposition"] == "executed"]
    state_counts = {name: 0 for name in (
        "BEAR", "DIVERGENT_BULL", "FRAGILE_BULL", "HEALTHY_BULL", "NEUTRAL", "RECOVERY",
    )}
    state_counts.update({str(key): int(value) for key, value in executed["market_state"].value_counts().items()})
    blocks = {item.name: 0 for item in FROZEN_Q70_VOLUME_RSI_TEMPORAL_STABILITY_V1.blocks}
    for date_text in executed["signal_date"].astype(str).str[:10]:
        for block in FROZEN_Q70_VOLUME_RSI_TEMPORAL_STABILITY_V1.blocks:
            if block.start_date <= date_text <= block.end_date:
                blocks[block.name] += 1
                break
    return state_counts, blocks


def _run_arm(
    *, arm: str, policy: Any, folds: list[WalkForwardFold], database_path: Path,
    coverage_index: Any, breadth_index: Any, paper: PaperExecutionConfig,
    parity: BacktestPaperParityConfig,
) -> dict[str, Any]:
    capital = parity.initial_cash
    fold_rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    priority: list[dict[str, Any]] = []
    curves: list[pd.DataFrame] = []
    audits: dict[int, dict[str, Any]] = {}
    common = {
        "db_path": str(database_path), "universe_mode": "database_coverage",
        "minimum_history_sessions": MINIMUM_HISTORY_SESSIONS,
        "maximum_staleness_sessions": MAXIMUM_STALENESS_SESSIONS,
        "coverage_index": coverage_index, "breadth_index": breadth_index,
        "paper_execution_config": paper,
    }
    for fold in folds:
        # Diagnostic train execution is deliberately not selected, optimized, or aggregated.
        _, train_metrics, _ = run_frozen_q70_backtest(
            start_date=str(fold.train_start.date()), end_date=str(fold.train_end.date()),
            parity_config=parity, candidate_priority_policy=policy, **common,
        )
        test_parity = replace(parity, initial_cash=capital)
        executed, metrics, curve = run_frozen_q70_backtest(
            start_date=str(fold.test_start.date()), end_date=str(fold.test_end.date()),
            parity_config=test_parity, candidate_priority_policy=policy,
            collect_decision_ledger=True, collect_priority_ledger=True,
            decision_phase="test", **common,
        )
        _require_test_metrics(metrics)
        ledger = metrics.get("candidate_priority_ledger")
        audit = metrics.get("accepted_candidate_audit")
        decision_ledger = metrics.get("decision_ledger")
        if ledger is None or audit is None or decision_ledger is None:
            raise ValueError("frozen evaluator omitted required priority audit output")
        fold_rows.append(_fold_row(fold, train_metrics, metrics, capital))
        trades.extend(_trade_rows(executed, fold.fold))
        decisions.extend(_decision_rows(decision_ledger, fold=fold))
        priority.extend(_priority_rows(ledger, decision_ledger, fold.fold))
        audits[fold.fold] = _audit_hashes(audit, decision_ledger)
        curves.append(curve)
        capital = float(metrics["final_equity"])

    folds_frame = pd.DataFrame(fold_rows).sort_values("fold", kind="stable").reset_index(drop=True)
    trades_frame = pd.DataFrame(trades, columns=TRADE_COLUMNS).sort_values(
        ["fold", "signal_date", "symbol"], kind="stable",
    ).reset_index(drop=True)
    decisions_frame = pd.DataFrame(decisions).sort_values(
        ["fold", "signal_date", "symbol", "candidate_key"], kind="stable",
    ).reset_index(drop=True)
    priority_frame = pd.DataFrame(priority, columns=PRIORITY_COLUMNS).sort_values(
        ["fold", "entry_date", "final_simulator_priority_ordinal", "candidate_key"], kind="stable",
    ).reset_index(drop=True)
    positive = float(trades_frame.loc[trades_frame["net_pnl"] > 0, "net_pnl"].sum())
    negative = float(trades_frame.loc[trades_frame["net_pnl"] < 0, "net_pnl"].sum())
    state_counts, block_counts = _state_and_block_counts(decisions_frame)
    summary = {
        "arm": arm, "initial_equity": parity.initial_cash, "final_equity": capital,
        "compounded_oos_return_pct": (capital / parity.initial_cash - 1.0) * 100.0,
        "total_trades": len(trades_frame),
        "win_rate_pct": 100.0 * float((trades_frame["net_pnl"] > 0).mean()) if len(trades_frame) else 0.0,
        "profit_factor": positive / abs(negative) if negative < 0 else (math.inf if positive > 0 else 0.0),
        "gross_trading_pnl": float(sum(float(row["net_pnl"]) + float(row["total_transaction_cost"]) for row in trades)),
        "net_trading_pnl": float(trades_frame["net_pnl"].sum()),
        "total_transaction_cost": float(folds_frame["test_total_transaction_cost"].sum()),
        "total_buy_commission": float(folds_frame["test_total_buy_commission"].sum()),
        "total_sell_commission": float(folds_frame["test_total_sell_commission"].sum()),
        "total_sell_tax": float(folds_frame["test_total_sell_tax"].sum()),
        "accepted_candidate_count": len(priority_frame),
        "executed_count": len(trades_frame),
        "accepted_to_executed_rate": len(trades_frame) / len(priority_frame) if len(priority_frame) else 0.0,
        "chained_max_drawdown_pct": calculate_chained_drawdown_pct(curves),
        "profitable_fold_count": int((folds_frame["test_return_pct"] > 0).sum()),
        "best_fold": int(folds_frame.loc[folds_frame["test_return_pct"].idxmax(), "fold"]),
        "worst_fold": int(folds_frame.loc[folds_frame["test_return_pct"].idxmin(), "fold"]),
        "ranking_policy_fingerprint": (
            policy.fingerprint if policy is not None else CANONICAL_SIGNAL_SCORE_PRIORITY_FINGERPRINT
        ),
        "simulator_rejection_counts": json.dumps(dict(Counter(
            decisions_frame.loc[
                decisions_frame["gate_accepted"] &
                (decisions_frame["execution_disposition"] != "executed"),
                "execution_disposition",
            ].astype(str)
        )), sort_keys=True),
        "executed_state_counts": json.dumps(state_counts, sort_keys=True),
        "executed_temporal_block_counts": json.dumps(block_counts, sort_keys=True),
    }
    summary.update(_decision_summary(decisions))
    return {
        "folds": folds_frame, "trades": trades_frame, "decisions": decisions_frame,
        "priority": priority_frame, "audits": audits, "summary": summary,
    }


def _accepted_parity(baseline: dict[str, Any], variant: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for fold in sorted(baseline["audits"]):
        left, right = baseline["audits"][fold], variant["audits"][fold]
        parity = left == right
        rows.append({
            "fold": fold, "baseline_accepted_count": left["count"],
            "variant_accepted_count": right["count"],
            "baseline_candidate_key_sha256": left["key_hash"],
            "variant_candidate_key_sha256": right["key_hash"],
            "baseline_raw_candidate_sha256": left["raw_hash"],
            "variant_raw_candidate_sha256": right["raw_hash"],
            "baseline_q70_decision_sha256": left["q70_hash"],
            "variant_q70_decision_sha256": right["q70_hash"],
            "baseline_exit_definition_sha256": left["exit_hash"],
            "variant_exit_definition_sha256": right["exit_hash"], "parity": parity,
        })
        if not parity:
            raise ValueError(f"pre-ranking accepted-candidate parity failed in fold {fold}")
    return pd.DataFrame(rows, columns=ACCEPTED_PARITY_COLUMNS)


def _executed_comparison(baseline: dict[str, Any], variant: dict[str, Any]) -> pd.DataFrame:
    def lookup(payload: dict[str, Any]) -> dict[tuple[int, str], dict[str, Any]]:
        result = {}
        for row in payload["priority"].to_dict("records"):
            result[(int(row["fold"]), str(row["candidate_key"]))] = row
        return result
    left, right = lookup(baseline), lookup(variant)
    rows = []
    for fold, key in sorted(set(left) | set(right)):
        a, b = left.get((fold, key)), right.get((fold, key))
        source = a or b
        if source is None:
            continue
        rows.append({
            "fold": fold, "candidate_key": key, "signal_date": source["signal_date"],
            "entry_date": source["entry_date"], "symbol": source["symbol"],
            "baseline_executed": bool(a and a["execution_disposition"] == "executed"),
            "variant_executed": bool(b and b["execution_disposition"] == "executed"),
            "baseline_entry_ordinal": None if a is None else a["final_simulator_priority_ordinal"],
            "variant_entry_ordinal": None if b is None else b["final_simulator_priority_ordinal"],
            "volume_ratio": source["volume_ratio"], "q70_quality": source["q70_quality_score"],
            "signal_score": source["baseline_signal_score"],
            "baseline_disposition": None if a is None else a["execution_disposition"],
            "variant_disposition": None if b is None else b["execution_disposition"],
        })
    return pd.DataFrame(rows, columns=EXECUTED_COMPARISON_COLUMNS)


def _ranking_changes(baseline: dict[str, Any], variant: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for (fold, entry_date), left_group in baseline["priority"].groupby(["fold", "entry_date"], sort=True):
        right_group = variant["priority"].loc[
            (variant["priority"]["fold"] == fold) & (variant["priority"]["entry_date"] == entry_date)
        ]
        left = left_group.set_index("candidate_key")
        right = right_group.set_index("candidate_key")
        if set(left.index) != set(right.index):
            raise ValueError("entry-date candidate membership changed between arms")
        left_order = list(left.sort_values("final_simulator_priority_ordinal").index)
        right_order = list(right.sort_values("final_simulator_priority_ordinal").index)
        changed = sum(
            int(left.loc[key, "final_simulator_priority_ordinal"] != right.loc[key, "final_simulator_priority_ordinal"])
            for key in left.index
        )
        # Offline invariant: every subgroup retains precisely its baseline slot set.
        for group_key, subgroup in right.groupby("signal_date_group_key"):
            expected = tuple(sorted(left.loc[subgroup.index, "baseline_within_entry_date_ordinal"].astype(int)))
            actual = tuple(sorted(subgroup["final_simulator_priority_ordinal"].astype(int)))
            if expected != actual:
                raise ValueError(f"signal-date subgroup slots changed: {fold}/{entry_date}/{group_key}")
        left_dispositions = Counter(left_group["execution_disposition"].astype(str))
        right_dispositions = Counter(right_group["execution_disposition"].astype(str))
        identity = _hash_payload({
            "fold": int(fold),
            "entry_date": pd.Timestamp(entry_date).isoformat(),
            "baseline_order": left_order,
            "variant_order": right_order,
            "signal_date_slot_sets": [
                {
                    "signal_date_group_key": str(group_key),
                    "baseline_slots": list(sorted(
                        left.loc[subgroup.index, "baseline_within_entry_date_ordinal"].astype(int)
                    )),
                    "variant_slots": list(sorted(
                        subgroup["final_simulator_priority_ordinal"].astype(int)
                    )),
                }
                for group_key, subgroup in right.groupby("signal_date_group_key", sort=True)
            ],
        })
        rows.append({
            "fold": int(fold), "entry_date": entry_date,
            "accepted_entry_event_count": len(left),
            "distinct_signal_date_subgroup_count": int(left["signal_date_group_key"].nunique()),
            "changed_final_ordinal_count": changed,
            "baseline_top_three_keys": json.dumps(left_order[:3]),
            "variant_top_three_keys": json.dumps(right_order[:3]),
            "top_three_overlap_count": len(set(left_order[:3]) & set(right_order[:3])),
            "daily_order_cap_selection_changed": set(left_order[:3]) != set(right_order[:3]),
            "baseline_executed_count": int((left_group["execution_disposition"] == "executed").sum()),
            "variant_executed_count": int((right_group["execution_disposition"] == "executed").sum()),
            "baseline_rejection_counts": json.dumps(dict(left_dispositions), sort_keys=True),
            "variant_rejection_counts": json.dumps(dict(right_dispositions), sort_keys=True),
            "ranking_change_identity": identity,
            "direct_execution_divergence_count": 0,
            "downstream_divergence_count": 0,
            "unexpected_divergence_count": 0,
        })
    return pd.DataFrame(rows, columns=RANKING_CHANGE_COLUMNS)


def _event_key(fold: Any, entry_date: Any) -> tuple[int, pd.Timestamp]:
    return int(fold), pd.Timestamp(entry_date)


def _has_disposition_divergence(row: pd.Series) -> bool:
    return (
        bool(row["baseline_executed"]) != bool(row["variant_executed"])
        or str(row["baseline_disposition"]) != str(row["variant_disposition"])
    )


def _classify_execution_divergence(
    executed: pd.DataFrame,
    ranking: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Classify arm divergence along the runner's chained OOS timeline.

    The audit is chronological/path-based.  It proves that all divergence
    begins on a permitted direct priority-change date; without simulator state
    snapshots it does not claim candidate-level counterfactual causality for
    later downstream rows.
    """
    classified = executed.copy()
    changes = ranking.copy()
    row_index = classified.index
    for column in (
        "divergence_classification", "causal_anchor_entry_date",
        "causal_anchor_identity",
    ):
        classified[column] = pd.Series(
            pd.array([pd.NA] * len(classified), dtype="string"),
            index=row_index,
        )
    classified["causal_anchor_fold"] = pd.Series(
        pd.array([pd.NA] * len(classified), dtype="Int64"),
        index=row_index,
    )

    direct_by_event = {
        _event_key(row.fold, row.entry_date): str(row.ranking_change_identity)
        for row in changes.itertuples()
        if int(row.changed_final_ordinal_count) > 0
    }
    divergent_indexes = [
        index for index, row in classified.iterrows()
        if _has_disposition_divergence(row)
    ]
    divergent_indexes.sort(key=lambda index: (
        *_event_key(classified.at[index, "fold"], classified.at[index, "entry_date"]),
        str(classified.at[index, "candidate_key"]),
    ))

    anchor_event: tuple[int, pd.Timestamp] | None = None
    anchor_identity: str | None = None
    for index in divergent_indexes:
        event = _event_key(classified.at[index, "fold"], classified.at[index, "entry_date"])
        if event in direct_by_event:
            classification = "direct_priority"
            if anchor_event is None:
                anchor_event, anchor_identity = event, direct_by_event[event]
            row_anchor, row_identity = event, direct_by_event[event]
        elif anchor_event is not None and event > anchor_event:
            classification = "downstream_portfolio_state"
            row_anchor, row_identity = anchor_event, anchor_identity
        else:
            classification = "unexpected"
            row_anchor, row_identity = None, None
        classified.at[index, "divergence_classification"] = classification
        if row_anchor is not None:
            classified.at[index, "causal_anchor_fold"] = row_anchor[0]
            classified.at[index, "causal_anchor_entry_date"] = row_anchor[1].isoformat()
            classified.at[index, "causal_anchor_identity"] = row_identity

    for column in (
        "direct_execution_divergence_count", "downstream_divergence_count",
        "unexpected_divergence_count",
    ):
        changes[column] = 0
    for index in divergent_indexes:
        event = _event_key(classified.at[index, "fold"], classified.at[index, "entry_date"])
        mask = (
            changes["fold"].astype(int).eq(event[0])
            & pd.to_datetime(changes["entry_date"]).eq(event[1])
        )
        classification = classified.at[index, "divergence_classification"]
        column = {
            "direct_priority": "direct_execution_divergence_count",
            "downstream_portfolio_state": "downstream_divergence_count",
            "unexpected": "unexpected_divergence_count",
        }[classification]
        if bool(mask.any()):
            changes.loc[mask, column] += 1

    counts = {
        name: int((classified["divergence_classification"] == name).sum())
        for name in ("direct_priority", "downstream_portfolio_state", "unexpected")
    }
    return classified.loc[:, EXECUTED_COMPARISON_COLUMNS], changes.loc[:, RANKING_CHANGE_COLUMNS], counts


def _validate_canonical_baseline(frame: pd.DataFrame, reference: Path = CANONICAL_TRADE_PATH) -> dict[str, Any]:
    if not reference.is_file():
        raise FileNotFoundError(f"canonical baseline trade artifact is missing: {reference}")
    expected_hash = _file_hash(reference).upper()
    if expected_hash != CANONICAL_TRADE_SHA256:
        raise ValueError("canonical reference artifact hash does not match the approved SHA-256")
    expected = pd.read_csv(reference)
    actual = frame.loc[:, expected.columns].copy()
    try:
        pd.testing.assert_frame_equal(actual.reset_index(drop=True), expected.reset_index(drop=True), check_dtype=False)
    except AssertionError as exc:
        raise ValueError("baseline trade content does not match canonical database-coverage arm") from exc
    emitted_hash = _trade_frame_csv_hash(frame)
    return {
        "reference_path": str(reference.resolve()), "reference_sha256": expected_hash,
        "emitted_trade_csv_sha256": emitted_hash,
        "raw_csv_hash_equal": emitted_hash == expected_hash,
        "parsed_exact_content_equal": True, "row_count": len(expected),
    }


def _comparison(
    arms: dict[str, dict[str, Any]], folds: list[WalkForwardFold],
    divergence_counts: dict[str, int],
) -> pd.DataFrame:
    baseline = arms["baseline_signal_score_priority"]
    variant = arms["volume_ratio_priority"]
    executed = _executed_comparison(baseline, variant)
    baseline_keys = set(executed.loc[executed["baseline_executed"], "candidate_key"])
    variant_keys = set(executed.loc[executed["variant_executed"], "candidate_key"])
    best = int(baseline["folds"].loc[baseline["folds"]["test_return_pct"].idxmax(), "fold"])
    days = (folds[-1].test_end - folds[0].test_start).days
    rows = []
    for name, payload in arms.items():
        summary = dict(payload["summary"])
        excluded = payload["folds"].loc[payload["folds"]["fold"] != best, "test_return_pct"]
        summary.update({
            "cagr_pct": 100.0 * ((summary["final_equity"] / summary["initial_equity"]) ** (365.25 / days) - 1.0),
            "baseline_best_fold": best,
            "return_excluding_baseline_best_fold_pct": 100.0 * (math.prod(1.0 + value / 100.0 for value in excluded) - 1.0),
            "executed_key_overlap_count": len(baseline_keys & variant_keys),
            "executed_keys_unique_to_baseline": len(baseline_keys - variant_keys),
            "executed_keys_unique_to_variant": len(variant_keys - baseline_keys),
            "direct_priority_divergence_count": divergence_counts["direct_priority"],
            "downstream_portfolio_state_divergence_count": divergence_counts["downstream_portfolio_state"],
            "unexpected_divergence_count": divergence_counts["unexpected"],
        })
        rows.append(summary)
    return pd.DataFrame(rows)


def _normalized_trade_audit(frame: pd.DataFrame, *, before: tuple[int, pd.Timestamp] | None) -> pd.DataFrame:
    selected = frame.copy()
    if before is not None and not selected.empty:
        entry_events = list(zip(selected["fold"].astype(int), pd.to_datetime(selected["entry_date"])))
        selected = selected.loc[[event < before for event in entry_events]]
    columns = (
        "fold", "symbol", "signal_date", "entry_date", "exit_date", "entry_price",
        "exit_price", "quantity", "net_pnl", "buy_commission", "sell_commission",
        "sell_tax", "total_transaction_cost", "exit_reason",
    )
    return selected.loc[:, columns].sort_values(
        ["fold", "signal_date", "symbol"], kind="stable",
    ).reset_index(drop=True)


def _validate_reconciliation(
    arms: dict[str, dict[str, Any]], accepted: pd.DataFrame,
    executed: pd.DataFrame, ranking: pd.DataFrame,
    divergence_counts: dict[str, int],
) -> None:
    if not bool(accepted["parity"].all()):
        raise ValueError("accepted-candidate parity is incomplete")
    for name, payload in arms.items():
        folds, trades, summary = payload["folds"], payload["trades"], payload["summary"]
        if not math.isclose(float(folds.iloc[0]["test_initial_equity"]), float(summary["initial_equity"]), rel_tol=0, abs_tol=1e-6):
            raise ValueError(f"initial capital mismatch: {name}")
        if not math.isclose(float(folds.iloc[-1]["test_final_equity"]), float(summary["final_equity"]), rel_tol=0, abs_tol=1e-6):
            raise ValueError(f"final capital mismatch: {name}")
        for previous, current in zip(folds.to_dict("records"), folds.to_dict("records")[1:]):
            if not math.isclose(float(previous["test_final_equity"]), float(current["test_initial_equity"]), rel_tol=0, abs_tol=1e-6):
                raise ValueError(f"fold capital chain mismatch: {name}")
        net = float(trades["net_pnl"].sum())
        if not math.isclose(float(summary["initial_equity"]) + net, float(summary["final_equity"]), rel_tol=0, abs_tol=1e-5):
            raise ValueError(f"trade net-PnL reconciliation failed: {name}")
        cost = float(trades["total_transaction_cost"].sum())
        if not math.isclose(cost, float(summary["total_transaction_cost"]), rel_tol=0, abs_tol=1e-5):
            raise ValueError(f"transaction-cost reconciliation failed: {name}")
    divergent = executed.loc[executed["divergence_classification"].notna()]
    direct = divergent.loc[divergent["divergence_classification"] == "direct_priority"]
    if not divergent.empty and direct.empty:
        raise ValueError("arm divergence has no direct priority causal anchor")
    if divergence_counts["unexpected"]:
        raise ValueError("unexpected arm divergence precedes any direct priority causal anchor")
    if not divergent.empty:
        first = divergent.assign(
            _entry=pd.to_datetime(divergent["entry_date"]),
        ).sort_values(["fold", "_entry", "candidate_key"], kind="stable").iloc[0]
        if first["divergence_classification"] != "direct_priority":
            raise ValueError("first chronological arm divergence is not a direct priority change")
        anchor = _event_key(first["fold"], first["entry_date"])
    else:
        anchor = None

    baseline = arms["baseline_signal_score_priority"]
    variant = arms["volume_ratio_priority"]
    try:
        pd.testing.assert_frame_equal(
            _normalized_trade_audit(baseline["trades"], before=anchor),
            _normalized_trade_audit(variant["trades"], before=anchor),
            check_dtype=False,
        )
    except AssertionError as exc:
        raise ValueError("trade quantity/cost divergence occurs before the first direct priority change") from exc
    if anchor is not None:
        left_folds = baseline["folds"].sort_values("fold", kind="stable")
        right_folds = variant["folds"].sort_values("fold", kind="stable")
        pre_columns = (
            "fold", "test_initial_equity", "test_final_equity", "test_return_pct",
            "test_trades", "test_total_transaction_cost", "test_total_buy_commission",
            "test_total_sell_commission", "test_total_sell_tax",
        )
        try:
            pd.testing.assert_frame_equal(
                left_folds.loc[left_folds["fold"] < anchor[0], list(pre_columns)].reset_index(drop=True),
                right_folds.loc[right_folds["fold"] < anchor[0], list(pre_columns)].reset_index(drop=True),
                check_dtype=False,
            )
        except AssertionError as exc:
            raise ValueError("completed-fold portfolio results diverge before the causal anchor") from exc
        left_start = float(left_folds.loc[left_folds["fold"] == anchor[0], "test_initial_equity"].iloc[0])
        right_start = float(right_folds.loc[right_folds["fold"] == anchor[0], "test_initial_equity"].iloc[0])
        if not math.isclose(left_start, right_start, rel_tol=0, abs_tol=1e-6):
            raise ValueError("causal-anchor fold starts from unequal capital")

    # Every direct classification must point to a real, slot-validated change.
    direct_events = {
        _event_key(row.fold, row.entry_date)
        for row in ranking.itertuples()
        if int(row.changed_final_ordinal_count) > 0
    }
    if any(_event_key(row.fold, row.entry_date) not in direct_events for row in direct.itertuples()):
        raise ValueError("direct divergence lacks a validated ranking-change event")


def _write_outputs(
    directory: Path, *, arms: dict[str, dict[str, Any]], comparison: pd.DataFrame,
    accepted: pd.DataFrame, executed: pd.DataFrame, ranking: pd.DataFrame,
    manifest: dict[str, Any], paper: PaperExecutionConfig, parity: BacktestPaperParityConfig,
) -> None:
    for name, policy in ARM_SPECS:
        arm_dir = directory / name
        arm_dir.mkdir()
        payload = arms[name]
        payload["folds"].to_csv(arm_dir / "folds.csv", index=False, encoding="utf-8", lineterminator="\n")
        pd.DataFrame([payload["summary"]]).to_csv(arm_dir / "summary.csv", index=False, encoding="utf-8", lineterminator="\n")
        _write_frame(arm_dir / "trade_level_oos.csv", payload["trades"], TRADE_COLUMNS)
        payload["decisions"].to_csv(arm_dir / "candidate_decision_oos.csv", index=False, encoding="utf-8", lineterminator="\n")
        _write_frame(arm_dir / "candidate_priority_oos.csv", payload["priority"], PRIORITY_COLUMNS)
        _write_json(arm_dir / "policy_fingerprint.json", {
            "frozen_q70": asdict(Q70_FROZEN), "paper_execution": asdict(paper),
            "paper_parity": asdict(parity), "universe_mode": "database_coverage",
            "minimum_history_sessions": MINIMUM_HISTORY_SESSIONS,
            "maximum_staleness_sessions": MAXIMUM_STALENESS_SESSIONS,
            "candidate_priority_policy": None if policy is None else asdict(policy),
            "candidate_priority_policy_fingerprint": payload["summary"]["ranking_policy_fingerprint"],
        })
    comparison.to_csv(directory / "comparison.csv", index=False, encoding="utf-8", lineterminator="\n")
    _write_frame(directory / "accepted_candidate_parity.csv", accepted, ACCEPTED_PARITY_COLUMNS)
    _write_frame(directory / "executed_candidate_comparison.csv", executed, EXECUTED_COMPARISON_COLUMNS)
    _write_frame(directory / "ranking_change_by_date.csv", ranking, RANKING_CHANGE_COLUMNS)
    _write_json(directory / "experiment_manifest.json", manifest)
    (directory / "assumptions.md").write_text(
        "# Assumptions and limitations\n\n"
        "- This is an opt-in post-hoc priority sensitivity, not a production policy or fresh OOS confirmation.\n"
        "- Database coverage is not historical VN100 and cannot measure historical-index survivorship bias.\n"
        "- Only priority within same-entry-date/same-signal-date slots differs; Q70 membership is frozen.\n"
        "- Historical fills, capacity, costs, and exits are simulator approximations.\n"
        "- Volume-ratio temporal association does not establish standalone alpha.\n",
        encoding="utf-8", newline="\n",
    )


def _validate_artifacts(directory: Path, arms: dict[str, dict[str, Any]]) -> None:
    expected_root = {
        "experiment_manifest.json", "comparison.csv", "accepted_candidate_parity.csv",
        "executed_candidate_comparison.csv", "ranking_change_by_date.csv", "assumptions.md",
        *(name for name, _ in ARM_SPECS),
    }
    if {item.name for item in directory.iterdir()} != expected_root:
        raise ValueError("root experiment artifact set is incomplete")
    expected_arm = {
        "folds.csv", "summary.csv", "trade_level_oos.csv", "candidate_decision_oos.csv",
        "candidate_priority_oos.csv", "policy_fingerprint.json",
    }
    for name, _ in ARM_SPECS:
        arm_dir = directory / name
        if {item.name for item in arm_dir.iterdir()} != expected_arm:
            raise ValueError(f"arm artifact set is incomplete: {name}")
        if len(pd.read_csv(arm_dir / "folds.csv")) != len(arms[name]["folds"]):
            raise ValueError(f"fold artifact row count mismatch: {name}")
        if len(pd.read_csv(arm_dir / "candidate_priority_oos.csv")) != len(arms[name]["priority"]):
            raise ValueError(f"priority artifact row count mismatch: {name}")


def run_frozen_q70_volume_priority_wfo(
    *, database_path: str | Path | None = None, output_root: str | Path | None = None,
    overwrite: bool = False, canonical_trade_path: str | Path | None = None,
) -> dict[str, Any]:
    db = resolve_market_database_path(database_path)
    output = _resolve_output(output_root)
    _validate_output(output, database_path=db, overwrite=overwrite)
    reference = CANONICAL_TRADE_PATH if canonical_trade_path is None else Path(canonical_trade_path).resolve()
    if not reference.is_file():
        raise FileNotFoundError(f"canonical baseline trade artifact is missing: {reference}")
    database_hash_before = _file_hash(db)
    folds = build_walk_forward_folds(WalkForwardConfig(
        start_date=DEFAULT_START_DATE, end_date=DEFAULT_END_DATE,
        train_months=24, test_months=6, step_months=6,
    ))
    if len(folds) != 13:
        raise ValueError("canonical frozen-Q70 protocol must resolve exactly 13 folds")
    coverage = build_database_coverage_index(
        DEFAULT_START_DATE, DEFAULT_END_DATE,
        minimum_history_sessions=MINIMUM_HISTORY_SESSIONS,
        maximum_staleness_sessions=MAXIMUM_STALENESS_SESSIONS,
        database_path=db,
    )
    breadth = build_historical_breadth_index(
        DEFAULT_START_DATE, DEFAULT_END_DATE, universe_mode="database_coverage",
        coverage_index=coverage, database_path=db,
    )
    paper = PaperExecutionConfig()
    parity = BacktestPaperParityConfig.from_paper_config(paper)
    if (Q70_FROZEN.entry_model, Q70_FROZEN.quality_threshold, Q70_FROZEN.stop_atr_multiplier,
        Q70_FROZEN.target_atr_multiplier) != ("hybrid_trend_donchian", .70, 2.0, 5.0):
        raise ValueError("frozen-Q70 policy drift detected")
    arms = {
        name: _run_arm(
            arm=name, policy=policy, folds=folds, database_path=db,
            coverage_index=coverage, breadth_index=breadth, paper=paper, parity=parity,
        )
        for name, policy in ARM_SPECS
    }
    accepted = _accepted_parity(arms[ARM_SPECS[0][0]], arms[ARM_SPECS[1][0]])
    executed = _executed_comparison(arms[ARM_SPECS[0][0]], arms[ARM_SPECS[1][0]])
    ranking = _ranking_changes(arms[ARM_SPECS[0][0]], arms[ARM_SPECS[1][0]])
    executed, ranking, divergence_counts = _classify_execution_divergence(executed, ranking)
    _validate_reconciliation(arms, accepted, executed, ranking, divergence_counts)
    canonical = _validate_canonical_baseline(arms[ARM_SPECS[0][0]]["trades"], reference)
    comparison = _comparison(arms, folds, divergence_counts)
    database_hash_after = _file_hash(db)
    if database_hash_after != database_hash_before:
        raise ValueError("market database changed during read-only experiment")
    manifest = {
        "experiment": "frozen_q70_volume_priority_wfo_v1",
        "database_path": str(db), "database_sha256": database_hash_after,
        "start_date": DEFAULT_START_DATE, "end_date": DEFAULT_END_DATE,
        "folds": [fold.to_dict() for fold in folds],
        "universe_mode": "database_coverage", "minimum_history_sessions": 50,
        "maximum_staleness_sessions": 5,
        "shared_dependency_identity": {
            "coverage": [coverage.start_date, coverage.end_date, 50, 5],
            "breadth": [breadth.start_date, breadth.end_date, breadth.universe_mode],
        },
        "arms": [name for name, _ in ARM_SPECS],
        "only_intended_difference": "slot-preserving volume_ratio priority within same entry-date and signal-date subgroup",
        "canonical_baseline_parity": canonical,
        "accepted_candidate_parity_sha256": _hash_payload(accepted.to_dict("records")),
        "executed_comparison_row_count": len(executed),
        "ranking_change_row_count": len(ranking),
        "direct_priority_divergence_count": divergence_counts["direct_priority"],
        "downstream_portfolio_state_divergence_count": divergence_counts["downstream_portfolio_state"],
        "unexpected_divergence_count": divergence_counts["unexpected"],
        "divergence_causality_scope": (
            "All arm divergence begins at a permitted direct priority change; "
            "later divergence is chronologically consistent with deterministic "
            "downstream portfolio path dependence. Candidate-level counterfactual "
            "causality is not claimed because simulator state snapshots are unavailable."
        ),
        "oos_capital_semantics": "capital is chained across every OOS fold; train diagnostics reset and are excluded",
        "temporal_block_specification_fingerprint": FROZEN_Q70_VOLUME_RSI_TEMPORAL_STABILITY_V1.fingerprint,
        "network_access": "forbidden",
    }
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent)).resolve()
    try:
        _write_outputs(
            temporary, arms=arms, comparison=comparison, accepted=accepted,
            executed=executed, ranking=ranking, manifest=manifest, paper=paper, parity=parity,
        )
        _validate_artifacts(temporary, arms)
        if _file_hash(db) != database_hash_before:
            raise ValueError("market database changed before publication")
        _publish_atomic(temporary, output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {
        "output_root": output, "folds": folds, "arms": arms,
        "comparison": comparison, "accepted_candidate_parity": accepted,
        "executed_candidate_comparison": executed, "ranking_change_by_date": ranking,
        "manifest": manifest,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-path")
    parser.add_argument("--output-root")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    run_frozen_q70_volume_priority_wfo(**vars(parse_args()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
