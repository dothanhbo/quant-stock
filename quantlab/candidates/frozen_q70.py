from __future__ import annotations

"""Pure Phase 3.7-decision to Phase 3.8 research-candidate conversion."""

from typing import TYPE_CHECKING, Any, Iterable, Mapping

from quantlab.alpha import FrozenQ70Decision, FrozenQ70EvaluationRow, FrozenQ70Policy

from .contracts import FrozenQ70CandidateBatch, FrozenQ70CandidateRecord

if TYPE_CHECKING:
    from quantlab.adapters.frozen_q70_candidate_decisions import HistoricalCandidateDecisionResult


_CONTEXT_MAP = {
    "signal_close": "close",
    "atr14": "ATR14",
    "atr_percent": "ATR_Percent",
    "rsi14": "RSI",
    "volume_ratio": "Vol_Ratio",
    "ema10": "EMA10",
    "ema20": "EMA20",
    "ema50": "EMA50",
    "previous_20d_high": "Previous_20D_High",
    "donchian_breakout": "Breakout_20D",
    "market_regime": "Market_Regime",
    "breadth_ema50_pct": "breadth_ema50_pct",
    "breadth_ema50_change_10d": "breadth_ema50_change_10d",
    "breadth_universe_count": "breadth_universe_count",
}


def _unique_evaluations(rows: Iterable[FrozenQ70EvaluationRow]) -> dict[str, FrozenQ70EvaluationRow]:
    result: dict[str, FrozenQ70EvaluationRow] = {}
    for row in rows:
        key = row.evaluation_key or f"{row.symbol}:{row.signal_date}"
        if key in result:
            raise ValueError(f"duplicate evaluation key: {key}")
        result[key] = row
    return result


def _accepted_decisions(result: HistoricalCandidateDecisionResult) -> dict[str, tuple[FrozenQ70Decision, float]]:
    accepted: dict[str, tuple[FrozenQ70Decision, float]] = {}
    for date_text in sorted(result.batches):
        batch = result.batches[date_text]
        for decision in batch.decisions:
            if not decision.accepted:
                continue
            if decision.evaluation_key in accepted:
                raise ValueError(f"duplicate accepted candidate key: {decision.evaluation_key}")
            accepted[decision.evaluation_key] = (decision, batch.policy.threshold)
    accepted_keys = tuple(result.accepted_candidate_keys)
    if len(set(accepted_keys)) != len(accepted_keys):
        raise ValueError("duplicate accepted candidate key in Phase 3.7 result")
    if set(accepted_keys) != set(accepted):
        missing = sorted(set(accepted_keys) - set(accepted))
        unexpected = sorted(set(accepted) - set(accepted_keys))
        raise ValueError(f"accepted candidate key mismatch; missing={missing}, unexpected={unexpected}")
    return accepted


def candidate_batch_from_decisions(
    result: HistoricalCandidateDecisionResult,
    *,
    requested_symbols: Iterable[str],
    available_symbols: Iterable[str],
    start_date: str,
    through_date: str,
) -> FrozenQ70CandidateBatch:
    """Build accepted research records without evaluating or loading data again."""
    metadata: Mapping[str, Any] = result.metadata
    evaluations = _unique_evaluations(result.evaluations)
    accepted = _accepted_decisions(result)
    policy_fingerprint = str(metadata.get("q70_policy_fingerprint") or FrozenQ70Policy().fingerprint)
    records: list[FrozenQ70CandidateRecord] = []
    for key, (decision, threshold) in accepted.items():
        evaluation = evaluations.get(key)
        if evaluation is None:
            raise ValueError(f"missing accepted evaluation row: {key}")
        if not evaluation.base_entry_passed:
            raise ValueError(f"accepted candidate is not a base-entry pass: {key}")
        if (evaluation.symbol, evaluation.signal_date) != (decision.symbol, key.rsplit(":", 1)[-1]):
            raise ValueError(f"decision/evaluation identity mismatch: {key}")
        try:
            context = result.signal_context_for(key)
        except KeyError as exc:
            raise ValueError(f"missing accepted signal context: {key}") from exc
        missing_context = tuple(column for column in _CONTEXT_MAP.values() if column not in context)
        if missing_context:
            raise ValueError(f"missing accepted signal fields for {key}: {', '.join(missing_context)}")
        paper_state = context.get("paper_v2_state")
        if paper_state is None or str(paper_state) != decision.market_state:
            raise ValueError(f"decision/signal-state mismatch: {key}")
        context_values = {field: context[column] for field, column in _CONTEXT_MAP.items()}
        records.append(FrozenQ70CandidateRecord(
            candidate_key=key,
            evaluation_key=key,
            symbol=evaluation.symbol,
            signal_date=evaluation.signal_date,
            snapshot_id=str(metadata["snapshot_id"]),
            prepared_v6_identity=result.prepared_identity,
            phase_3_7_run_identity=result.identity,
            entry_policy_identity=str(metadata["entry_policy_identity"]),
            q70_policy_fingerprint=policy_fingerprint,
            universe_membership_identity=str(metadata["universe_membership_identity"]),
            score=evaluation.score,
            relative_strength_20d=evaluation.relative_strength_20d,
            adx=evaluation.adx,
            component_percentiles=decision.component_percentiles,
            quality_score=decision.quality_score,
            q70_threshold=threshold,
            acceptance_reason=decision.reason,
            paper_v2_state=decision.market_state,
            **context_values,
        ))
    return FrozenQ70CandidateBatch(
        candidates=tuple(records),
        requested_symbols=tuple(requested_symbols),
        available_symbols=tuple(available_symbols),
        start_date=start_date,
        through_date=through_date,
        snapshot_id=str(metadata["snapshot_id"]),
        prepared_v6_identity=result.prepared_identity,
        phase_3_7_run_identity=result.identity,
        entry_policy_identity=str(metadata["entry_policy_identity"]),
        q70_policy_fingerprint=policy_fingerprint,
        universe_membership_identity=str(metadata["universe_membership_identity"]),
    )
