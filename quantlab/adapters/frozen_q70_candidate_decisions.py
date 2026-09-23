from __future__ import annotations

"""Opt-in historical evaluation-to-Q70 adapter; deliberately no trade execution."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Iterable, Mapping

import pandas as pd

from quantlab.adapters.historical_prepared_features import prepare_historical_paper_state_subset
from quantlab.alpha import FrozenQ70BatchResult, FrozenQ70EvaluationRow, score_frozen_q70_batch
from quantlab.features.contracts import canonical_json
from quantlab.features.universe_context import PointInTimeUniverseContext


@dataclass(frozen=True, slots=True)
class HistoricalCandidateDecisionResult:
    identity: str
    prepared_identity: str
    evaluations: tuple[FrozenQ70EvaluationRow, ...]
    batches: Mapping[str, FrozenQ70BatchResult]
    accepted_candidate_keys: tuple[str, ...]
    metadata: Mapping[str, object]


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
    for symbol in prepared.available_symbols:
        frame = prepared.frame_for(symbol)
        # Match the historical collector's no-next-bar boundary, while this
        # adapter intentionally stops before Trade/exit construction.
        for _, latest in frame.iloc[:-1].iterrows():
            date_text = pd.Timestamp(latest["time"]).date().isoformat()
            outcome = evaluate_prepared_row(symbol=symbol, latest=latest, market_config=build_market_config(str(latest["Market_Regime"])), entry_model=entry_model)
            rows.append(FrozenQ70EvaluationRow(symbol, date_text, outcome.get("score"), outcome.get("relative_strength_20d"), outcome.get("adx"), outcome.get("status") == "PASSED", f"{symbol}:{date_text}", outcome.get("reason")))
    grouped: dict[str, list[FrozenQ70EvaluationRow]] = defaultdict(list)
    states: dict[str, str] = {}
    for row in rows:
        grouped[row.signal_date].append(row)
    for date_text in grouped:
        # State is market-wide; any matching primary frame gives the same v6 value.
        for symbol in prepared.available_symbols:
            frame = prepared.frame_for(symbol); found = frame.loc[frame["time"] == pd.Timestamp(date_text), "paper_v2_state"]
            if not found.empty:
                states[date_text] = str(found.iloc[0]); break
    batches = {date_text: score_frozen_q70_batch(grouped[date_text], signal_date=date_text, market_state=states.get(date_text, "NEUTRAL"), universe_context=universe_context) for date_text in sorted(grouped)}
    accepted = tuple(decision.evaluation_key for batch in batches.values() for decision in batch.decisions if decision.accepted)
    identity = sha256(canonical_json({"prepared": prepared.computation_identity.sha256, "entry_policy_identity": entry_policy_identity, "rows": [row.canonical() for row in rows]})).hexdigest()
    metadata = {"snapshot_id": snapshot.snapshot_id, "benchmark_symbol": str(benchmark_symbol).upper(), "universe_membership_identity": universe_context.membership_identity, "entry_policy_identity": entry_policy_identity, "q70_policy_fingerprint": next(iter(batches.values())).policy.fingerprint if batches else None, "start_date": start_date, "through_date": through_date, "evaluation_count": len(rows), "reference_only_count": sum(not row.base_entry_passed for row in rows), "base_entry_candidate_keys": tuple(row.evaluation_key or f"{row.symbol}:{row.signal_date}" for row in rows if row.base_entry_passed), "accepted_count": len(accepted), "rejection_counts": dict(Counter(decision.reason for batch in batches.values() for decision in batch.decisions if not decision.accepted)), "cache": prepared.metadata.get("cache")}
    return HistoricalCandidateDecisionResult(identity, prepared.computation_identity.sha256, tuple(sorted(rows, key=lambda row: (row.signal_date, row.symbol))), MappingProxyType(batches), accepted, MappingProxyType(metadata))
