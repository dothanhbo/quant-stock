from __future__ import annotations

"""Pure same-date ranking of accepted immutable Frozen-Q70 candidates.

Finite percentiles use a weak empirical CDF.  For higher-is-better the value is
``count(reference <= value) / N``; lower-is-better uses
``count(reference >= value) / N``.  Equal raw values therefore receive the
same contribution.  Missing values never enter the finite reference sample.
"""

from collections import defaultdict
import math
from types import MappingProxyType
from typing import Any

from quantlab.candidates import FrozenQ70CandidateBatch, FrozenQ70CandidateRecord

from .contracts import (
    CandidateRankingBatch,
    CandidateRankingPolicy,
    MissingValuePolicy,
    RankedCandidate,
    RankingDirection,
    RankingFactor,
)


FROZEN_Q70_QUALITY_RANK_V1 = CandidateRankingPolicy(
    name="FROZEN_Q70_QUALITY_RANK_V1",
    version="1",
    factors=(RankingFactor(
        field_name="quality_score",
        weight=1.0,
        direction=RankingDirection.HIGHER_IS_BETTER,
        missing_value_policy=MissingValuePolicy.WORST,
    ),),
)


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _percentile(value: float, references: tuple[float, ...], direction: RankingDirection) -> float:
    if not references:
        raise ValueError("finite percentile requires a non-empty reference sample")
    if direction is RankingDirection.HIGHER_IS_BETTER:
        return sum(reference <= value for reference in references) / len(references)
    return sum(reference >= value for reference in references) / len(references)


def _quality_tie_break(candidate: FrozenQ70CandidateRecord) -> float:
    value = _finite(candidate.quality_score)
    return float("-inf") if value is None else value


def _rank_date(
    candidates: tuple[FrozenQ70CandidateRecord, ...],
    *,
    signal_date: str,
    source_identity: str,
    policy: CandidateRankingPolicy,
) -> CandidateRankingBatch:
    keys = tuple(candidate.candidate_key for candidate in candidates)
    if len(set(keys)) != len(keys):
        raise ValueError(f"duplicate candidate key in source batch for {signal_date}")
    if any(candidate.signal_date != signal_date for candidate in candidates):
        raise ValueError("candidate signal date does not agree with its ranking group")

    raw_by_key = {
        candidate.candidate_key: {
            factor.field_name: getattr(candidate, factor.field_name)
            for factor in policy.factors
        }
        for candidate in candidates
    }
    exclusions: dict[str, str] = {}
    for candidate in candidates:
        missing = tuple(
            factor.field_name
            for factor in policy.factors
            if factor.missing_value_policy is MissingValuePolicy.EXCLUDE
            and _finite(raw_by_key[candidate.candidate_key][factor.field_name]) is None
        )
        if missing:
            exclusions[candidate.candidate_key] = "missing_nonfinite:" + ",".join(missing)
    included = tuple(candidate for candidate in candidates if candidate.candidate_key not in exclusions)
    references = {
        factor.field_name: tuple(
            value
            for candidate in included
            if (value := _finite(raw_by_key[candidate.candidate_key][factor.field_name])) is not None
        )
        for factor in policy.factors
    }
    total_weight = sum(factor.weight for factor in policy.factors)
    provisional: list[tuple[FrozenQ70CandidateRecord, MappingProxyType, MappingProxyType, MappingProxyType, float]] = []
    for candidate in included:
        percentiles: dict[str, float] = {}
        contributions: dict[str, float] = {}
        for factor in policy.factors:
            value = _finite(raw_by_key[candidate.candidate_key][factor.field_name])
            if value is not None:
                percentile = _percentile(value, references[factor.field_name], factor.direction)
            elif factor.missing_value_policy is MissingValuePolicy.NEUTRAL:
                percentile = 0.5
            elif factor.missing_value_policy is MissingValuePolicy.WORST:
                percentile = 0.0
            else:  # EXCLUDE candidates were removed before reference construction.
                raise AssertionError("excluded candidate reached percentile calculation")
            percentiles[factor.field_name] = float(percentile)
            contributions[factor.field_name] = factor.weight * float(percentile)
        composite = sum(contributions.values()) / total_weight
        provisional.append((
            candidate,
            MappingProxyType(dict(raw_by_key[candidate.candidate_key])),
            MappingProxyType(percentiles),
            MappingProxyType(contributions),
            composite,
        ))
    provisional.sort(key=lambda item: (-item[4], -_quality_tie_break(item[0]), item[0].symbol, item[0].candidate_key))
    ranked = tuple(
        RankedCandidate(
            candidate=candidate,
            raw_values=raw,
            factor_percentiles=percentiles,
            factor_contributions=contributions,
            composite_score=composite,
            ordinal=ordinal,
            tie_break_evidence=MappingProxyType({
                "composite_score_desc": composite,
                "quality_score_desc": _quality_tie_break(candidate),
                "symbol_asc": candidate.symbol,
                "candidate_key_asc": candidate.candidate_key,
            }),
        )
        for ordinal, (candidate, raw, percentiles, contributions, composite)
        in enumerate(provisional, start=1)
    )
    return CandidateRankingBatch(
        signal_date=signal_date,
        policy_fingerprint=policy.fingerprint,
        source_candidate_batch_identity=source_identity,
        ranked_candidates=ranked,
        excluded_candidate_keys=tuple(exclusions),
        exclusion_reasons=MappingProxyType(exclusions),
    )


def rank_candidate_batch(
    candidate_batch: FrozenQ70CandidateBatch,
    policy: CandidateRankingPolicy = FROZEN_Q70_QUALITY_RANK_V1,
) -> tuple[CandidateRankingBatch, ...]:
    """Rank each signal date independently without loading or recomputing data."""
    if not isinstance(candidate_batch, FrozenQ70CandidateBatch):
        raise TypeError("candidate_batch must be FrozenQ70CandidateBatch")
    if not isinstance(policy, CandidateRankingPolicy):
        raise TypeError("policy must be CandidateRankingPolicy")
    grouped: dict[str, list[FrozenQ70CandidateRecord]] = defaultdict(list)
    seen: set[str] = set()
    for candidate in candidate_batch.candidates:
        if candidate.candidate_key in seen:
            raise ValueError(f"duplicate candidate key: {candidate.candidate_key}")
        seen.add(candidate.candidate_key)
        grouped[candidate.signal_date].append(candidate)
    return tuple(
        _rank_date(
            tuple(grouped[signal_date]),
            signal_date=signal_date,
            source_identity=candidate_batch.batch_identity,
            policy=policy,
        )
        for signal_date in sorted(grouped)
    )
