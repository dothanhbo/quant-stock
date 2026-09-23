from __future__ import annotations

"""Opt-in historical evaluation-to-Q70 adapter; deliberately no trade execution."""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from hashlib import sha256
import math
from types import MappingProxyType
from typing import Any, Iterable, Mapping

import pandas as pd

from quantlab.adapters.historical_prepared_features import prepare_historical_paper_state_subset
from quantlab.alpha import FrozenQ70BatchResult, FrozenQ70EvaluationRow, score_frozen_q70_batch
from quantlab.features.universe_context import PointInTimeUniverseContext
from quantlab.identity import canonical_identity_value, canonical_json


HISTORICAL_CANDIDATE_DECISION_CONTRACT = "quantlab.historical_candidate_decision_result"
HISTORICAL_CANDIDATE_DECISION_CONTRACT_VERSION = "v2"


@dataclass(frozen=True, slots=True)
class HistoricalCandidateDecisionResult:
    identity: str
    prepared_identity: str
    evaluations: tuple[FrozenQ70EvaluationRow, ...]
    batches: Mapping[str, FrozenQ70BatchResult]
    accepted_candidate_keys: tuple[str, ...]
    metadata: Mapping[str, object]
    _signal_contexts: Mapping[str, Mapping[str, object]] = field(
        default_factory=lambda: MappingProxyType({}),
        repr=False,
    )

    def signal_context_for(self, evaluation_key: str) -> Mapping[str, object]:
        """Return immutable causal v6 scalars retained for the evaluated row."""
        try:
            return self._signal_contexts[str(evaluation_key)]
        except KeyError as exc:
            raise KeyError(f"missing signal context for evaluation key: {evaluation_key}") from exc

    @property
    def signal_context_keys(self) -> tuple[str, ...]:
        return tuple(self._signal_contexts)


_SIGNAL_CONTEXT_COLUMNS = (
    "close",
    "ATR14",
    "ATR_Percent",
    "RSI",
    "Vol_Ratio",
    "EMA10",
    "EMA20",
    "EMA50",
    "Previous_20D_High",
    "Breakout_20D",
    "Market_Regime",
    "breadth_ema50_pct",
    "breadth_ema50_change_10d",
    "breadth_universe_count",
    "paper_v2_state",
)


def _immutable_scalar(value: Any) -> object:
    """Detach a pandas/numpy scalar without changing NaN/null meaning."""
    if value is None or value is pd.NA:
        return None
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(value, float) and math.isnan(value):
        return float("nan")
    return value


def _canonical_daily_states(
    grouped: Mapping[str, list[FrozenQ70EvaluationRow]],
    signal_contexts: Mapping[str, Mapping[str, object]],
) -> tuple[dict[str, str], dict[str, Mapping[str, object]]]:
    """Resolve one causal market-wide state for each evaluation date.

    The prepared v6 frame carries a convenience ``paper_v2_state`` join on
    every symbol row.  Sparse symbol calendars can leave that joined value
    missing even though the row's market-regime and breadth inputs are
    present.  A cross-sectional Q70 batch must not use an arbitrary symbol's
    missing joined value as its market state.  Reuse the registered
    authoritative classifier on the retained same-date inputs, require all
    symbol rows to agree, and expose that canonical state in every retained
    signal context.
    """
    # Delayed import preserves this adapter's import-time side-effect boundary.
    from quantlab.features.builtins import _classify_paper_v2_state

    states: dict[str, str] = {}
    normalized_contexts = dict(signal_contexts)
    for date_text in sorted(grouped):
        resolved: set[str] = set()
        for row in grouped[date_text]:
            key = row.evaluation_key or f"{row.symbol}:{date_text}"
            context = signal_contexts[key]
            resolved.add(_classify_paper_v2_state({
                "regime": context["Market_Regime"],
                "breadth_ema50_pct": context["breadth_ema50_pct"],
                "breadth_ema50_change_10d": context["breadth_ema50_change_10d"],
            }))
        if len(resolved) != 1:
            raise ValueError(
                f"inconsistent market-state inputs for signal date {date_text}: "
                f"{sorted(resolved)}"
            )
        state = resolved.pop()
        states[date_text] = state
        for row in grouped[date_text]:
            key = row.evaluation_key or f"{row.symbol}:{date_text}"
            normalized_contexts[key] = MappingProxyType({
                **signal_contexts[key],
                "paper_v2_state": state,
            })
    return states, normalized_contexts


def _decision_identity_content(decision) -> dict[str, object]:
    return {
        "symbol": decision.symbol,
        "evaluation_key": decision.evaluation_key,
        "base_entry_passed": decision.base_entry_passed,
        "eligible": decision.eligible,
        "component_percentiles": dict(decision.component_percentiles),
        "quality_score": decision.quality_score,
        "market_state": decision.market_state,
        "accepted": decision.accepted,
        "reason": decision.reason,
    }


def _batch_identity_content(batch: FrozenQ70BatchResult) -> dict[str, object]:
    return {
        "policy_fingerprint": batch.policy.fingerprint,
        "batch_identity": batch.batch_identity,
        "signal_date": batch.signal_date,
        "market_state": batch.market_state,
        "universe_identity": batch.universe_identity,
        "decisions": [_decision_identity_content(item) for item in batch.decisions],
        "reference_records": [_decision_identity_content(item) for item in batch.reference_records],
        "counts": dict(batch.counts),
        "rejection_counts": dict(batch.rejection_counts),
    }


def _result_identity(
    *,
    prepared_identity: str,
    entry_policy_identity: str,
    rows: Iterable[FrozenQ70EvaluationRow],
    batches: Mapping[str, FrozenQ70BatchResult],
    accepted_candidate_keys: tuple[str, ...],
    signal_contexts: Mapping[str, Mapping[str, object]],
    semantic_metadata: Mapping[str, object],
) -> str:
    ordered_rows = tuple(sorted(rows, key=lambda row: (row.signal_date, row.symbol, row.evaluation_key or "")))
    ordered_context_keys = tuple(sorted(signal_contexts, key=lambda key: (key.rsplit(":", 1)[-1], key.rsplit(":", 1)[0])))
    payload = {
        "contract": {
            "name": HISTORICAL_CANDIDATE_DECISION_CONTRACT,
            "version": HISTORICAL_CANDIDATE_DECISION_CONTRACT_VERSION,
        },
        "prepared_identity": prepared_identity,
        "entry_policy_identity": entry_policy_identity,
        "evaluations": [row.canonical() for row in ordered_rows],
        "batches": {
            date_text: _batch_identity_content(batches[date_text])
            for date_text in sorted(batches)
        },
        "accepted_candidate_keys": list(accepted_candidate_keys),
        "semantic_metadata": dict(semantic_metadata),
        "signal_contexts": {
            key: dict(signal_contexts[key])
            for key in ordered_context_keys
        },
    }
    return sha256(canonical_json(canonical_identity_value(payload))).hexdigest()


def evaluate_frozen_q70_candidates(snapshot, symbols: Iterable[str], *, benchmark_symbol: str, universe_context: PointInTimeUniverseContext, start_date: str, through_date: str, entry_model, entry_policy_identity: str, market_context_identity: str | None = None, feature_cache=None) -> HistoricalCandidateDecisionResult:
    """Use v6 frames and the authoritative one-row evaluator, never a simulator.

    ``entry_policy_identity`` is a caller-owned immutable declaration: callers
    must supply a different value whenever mutable entry-model semantics change.
    Generic strategy instances cannot be safely introspected for that contract.
    """
    if entry_model is None or not hasattr(entry_model, "evaluate"):
        raise TypeError("entry_model must be an explicit authoritative entry model")
    if not isinstance(entry_policy_identity, str) or not entry_policy_identity.strip():
        raise ValueError("entry_policy_identity must be a non-empty stable string")
    prepared = prepare_historical_paper_state_subset(snapshot, symbols, benchmark_symbol=benchmark_symbol, universe_context=universe_context, start_date=start_date, through_date=through_date, market_context_identity=market_context_identity, cache=feature_cache)
    # Delayed imports avoid importing scanner/configuration at package import.
    from strategy.market_regime import build_market_config
    from strategy.scanner import evaluate_prepared_row
    rows: list[FrozenQ70EvaluationRow] = []
    signal_contexts: dict[str, Mapping[str, object]] = {}
    for symbol in prepared.available_symbols:
        frame = prepared.frame_for(symbol)
        # Match the historical collector's no-next-bar boundary, while this
        # adapter intentionally stops before Trade/exit construction.
        for _, latest in frame.iloc[:-1].iterrows():
            date_text = pd.Timestamp(latest["time"]).date().isoformat()
            evaluation_key = f"{symbol}:{date_text}"
            if evaluation_key in signal_contexts:
                raise ValueError(f"duplicate historical evaluation key: {evaluation_key}")
            signal_contexts[evaluation_key] = MappingProxyType({
                column: _immutable_scalar(latest[column])
                for column in _SIGNAL_CONTEXT_COLUMNS
            })
            outcome = evaluate_prepared_row(symbol=symbol, latest=latest, market_config=build_market_config(str(latest["Market_Regime"])), entry_model=entry_model)
            rows.append(FrozenQ70EvaluationRow(symbol, date_text, outcome.get("score"), outcome.get("relative_strength_20d"), outcome.get("adx"), outcome.get("status") == "PASSED", evaluation_key, outcome.get("reason")))
    grouped: dict[str, list[FrozenQ70EvaluationRow]] = defaultdict(list)
    for row in rows:
        grouped[row.signal_date].append(row)
    states, signal_contexts = _canonical_daily_states(grouped, signal_contexts)
    batches = {date_text: score_frozen_q70_batch(grouped[date_text], signal_date=date_text, market_state=states[date_text], universe_context=universe_context) for date_text in sorted(grouped)}
    accepted = tuple(decision.evaluation_key for batch in batches.values() for decision in batch.decisions if decision.accepted)
    semantic_metadata = {"snapshot_id": snapshot.snapshot_id, "benchmark_symbol": str(benchmark_symbol).upper(), "universe_membership_identity": universe_context.membership_identity, "entry_policy_identity": entry_policy_identity, "q70_policy_fingerprint": next(iter(batches.values())).policy.fingerprint if batches else None, "start_date": start_date, "through_date": through_date, "requested_symbols": tuple(sorted(prepared.metadata.get("primary_symbols", ()))), "available_symbols": tuple(sorted(prepared.available_symbols)), "missing_symbols": tuple(sorted(prepared.missing_symbols)), "evaluation_count": len(rows), "reference_only_count": sum(not row.base_entry_passed for row in rows), "base_entry_candidate_keys": tuple(sorted(row.evaluation_key or f"{row.symbol}:{row.signal_date}" for row in rows if row.base_entry_passed)), "accepted_count": len(accepted), "rejection_counts": dict(Counter(decision.reason for batch in batches.values() for decision in batch.decisions if not decision.accepted))}
    identity = _result_identity(
        prepared_identity=prepared.computation_identity.sha256,
        entry_policy_identity=entry_policy_identity,
        rows=rows,
        batches=batches,
        accepted_candidate_keys=accepted,
        signal_contexts=signal_contexts,
        semantic_metadata=semantic_metadata,
    )
    metadata = {"identity_contract_name": HISTORICAL_CANDIDATE_DECISION_CONTRACT, "identity_contract_version": HISTORICAL_CANDIDATE_DECISION_CONTRACT_VERSION, **semantic_metadata, "cache": prepared.metadata.get("cache")}
    ordered_contexts = {
        key: signal_contexts[key]
        for key in sorted(signal_contexts, key=lambda item: (item.rsplit(":", 1)[1], item.rsplit(":", 1)[0]))
    }
    return HistoricalCandidateDecisionResult(
        identity,
        prepared.computation_identity.sha256,
        tuple(sorted(rows, key=lambda row: (row.signal_date, row.symbol))),
        MappingProxyType(batches),
        accepted,
        MappingProxyType(metadata),
        MappingProxyType(ordered_contexts),
    )
