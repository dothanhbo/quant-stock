from __future__ import annotations

"""Public Phase 3.8 adapter from v6/Phase 3.7 to research candidates."""

from typing import Iterable

from quantlab.candidates import FrozenQ70CandidateBatch, candidate_batch_from_decisions
from quantlab.features.universe_context import PointInTimeUniverseContext

from .frozen_q70_candidate_decisions import evaluate_frozen_q70_candidates


def build_frozen_q70_candidate_records(
    snapshot,
    symbols: Iterable[str],
    *,
    benchmark_symbol: str,
    universe_context: PointInTimeUniverseContext,
    start_date: str,
    through_date: str,
    entry_model,
    entry_policy_identity: str,
    market_context_identity: str | None = None,
    feature_cache=None,
) -> FrozenQ70CandidateBatch:
    """Return accepted signal-date records; never construct trades or simulate."""
    requested = tuple(sorted({
        str(symbol).strip().upper()
        for symbol in symbols
        if str(symbol).strip() and str(symbol).strip().upper() != str(benchmark_symbol).strip().upper()
    }))
    decisions = evaluate_frozen_q70_candidates(
        snapshot,
        requested,
        benchmark_symbol=benchmark_symbol,
        universe_context=universe_context,
        start_date=start_date,
        through_date=through_date,
        entry_model=entry_model,
        entry_policy_identity=entry_policy_identity,
        market_context_identity=market_context_identity,
        feature_cache=feature_cache,
    )
    return candidate_batch_from_decisions(
        decisions,
        requested_symbols=requested,
        available_symbols=tuple(decisions.metadata.get("available_symbols", ())),
        start_date=start_date,
        through_date=through_date,
    )
