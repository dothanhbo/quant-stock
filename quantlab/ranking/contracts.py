from __future__ import annotations

"""Immutable contracts for pure cross-sectional research ranking."""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import math
from types import MappingProxyType
from typing import Any, Mapping

from quantlab.candidates import FrozenQ70CandidateRecord
from quantlab.features.contracts import canonical_json


RANKING_CONTRACT_NAME = "quantlab.cross_sectional_candidate_ranking"
RANKING_CONTRACT_VERSION = "v1"
PERCENTILE_METHOD = "finite_weak_empirical_cdf_right_v1"

RANKABLE_NUMERIC_FIELDS = frozenset({
    "quality_score",
    "score",
    "relative_strength_20d",
    "adx",
    "atr_percent",
    "rsi14",
    "volume_ratio",
    "breadth_ema50_pct",
    "breadth_ema50_change_10d",
})


class RankingDirection(str, Enum):
    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"


class MissingValuePolicy(str, Enum):
    NEUTRAL = "neutral"
    WORST = "worst"
    EXCLUDE = "exclude"


def _identity_value(value: Any) -> Any:
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (AttributeError, ValueError):
            pass
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return {"__nonfinite_float__": "NaN"}
        if math.isinf(value):
            return {"__nonfinite_float__": "Infinity" if value > 0 else "-Infinity"}
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("ranking identity mapping keys must be strings")
        return {key: _identity_value(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_identity_value(item) for item in value]
    raise TypeError(f"unsupported ranking identity value: {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class RankingFactor:
    field_name: str
    weight: float
    direction: RankingDirection
    missing_value_policy: MissingValuePolicy

    def __post_init__(self) -> None:
        name = str(self.field_name).strip()
        if name not in RANKABLE_NUMERIC_FIELDS:
            raise ValueError(f"candidate field is not rankable: {name}")
        if isinstance(self.weight, bool):
            raise ValueError("ranking factor weight must be a finite non-negative number")
        try:
            weight = float(self.weight)
        except (TypeError, ValueError) as exc:
            raise ValueError("ranking factor weight must be a finite non-negative number") from exc
        if not math.isfinite(weight) or weight < 0:
            raise ValueError("ranking factor weight must be a finite non-negative number")
        if not isinstance(self.direction, RankingDirection):
            raise ValueError("ranking factor direction must be RankingDirection")
        if not isinstance(self.missing_value_policy, MissingValuePolicy):
            raise ValueError("ranking factor missing policy must be MissingValuePolicy")
        object.__setattr__(self, "field_name", name)
        object.__setattr__(self, "weight", weight)

    def canonical(self) -> dict[str, Any]:
        return {
            "field_name": self.field_name,
            "weight": self.weight,
            "direction": self.direction.value,
            "missing_value_policy": self.missing_value_policy.value,
        }


@dataclass(frozen=True, slots=True)
class CandidateRankingPolicy:
    name: str
    version: str
    factors: tuple[RankingFactor, ...]
    percentile_method: str = PERCENTILE_METHOD
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        name, version = str(self.name).strip(), str(self.version).strip()
        factors = tuple(self.factors)
        if not name or not version:
            raise ValueError("ranking policy name and version are required")
        if not factors or not all(isinstance(item, RankingFactor) for item in factors):
            raise ValueError("ranking policy requires an ordered RankingFactor tuple")
        names = tuple(item.field_name for item in factors)
        if len(set(names)) != len(names):
            raise ValueError("duplicate ranking factor field")
        if not any(item.weight > 0 for item in factors):
            raise ValueError("ranking policy requires at least one positive weight")
        if self.percentile_method != PERCENTILE_METHOD:
            raise ValueError(f"unsupported percentile method: {self.percentile_method}")
        payload = {
            "name": name,
            "version": version,
            "factors": [item.canonical() for item in factors],
            "percentile_method": self.percentile_method,
        }
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "factors", factors)
        object.__setattr__(self, "fingerprint", sha256(canonical_json(payload)).hexdigest())


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    candidate: FrozenQ70CandidateRecord
    raw_values: Mapping[str, Any]
    factor_percentiles: Mapping[str, float]
    factor_contributions: Mapping[str, float]
    composite_score: float
    ordinal: int
    tie_break_evidence: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, FrozenQ70CandidateRecord):
            raise TypeError("ranked candidate must retain a FrozenQ70CandidateRecord")
        if not isinstance(self.ordinal, int) or self.ordinal < 1:
            raise ValueError("ranked candidate ordinal must be positive")
        if not math.isfinite(float(self.composite_score)):
            raise ValueError("ranked candidate composite score must be finite")
        object.__setattr__(self, "raw_values", MappingProxyType(dict(self.raw_values)))
        object.__setattr__(self, "factor_percentiles", MappingProxyType({key: float(value) for key, value in self.factor_percentiles.items()}))
        object.__setattr__(self, "factor_contributions", MappingProxyType({key: float(value) for key, value in self.factor_contributions.items()}))
        object.__setattr__(self, "composite_score", float(self.composite_score))
        object.__setattr__(self, "tie_break_evidence", MappingProxyType(dict(self.tie_break_evidence)))

    @property
    def candidate_key(self) -> str:
        return self.candidate.candidate_key

    def canonical(self) -> dict[str, Any]:
        return {
            "candidate_key": self.candidate_key,
            "raw_values": _identity_value(self.raw_values),
            "factor_percentiles": _identity_value(self.factor_percentiles),
            "factor_contributions": _identity_value(self.factor_contributions),
            "composite_score": self.composite_score,
            "ordinal": self.ordinal,
            "tie_break_evidence": _identity_value(self.tie_break_evidence),
        }


@dataclass(frozen=True, slots=True)
class CandidateRankingBatch:
    signal_date: str
    policy_fingerprint: str
    source_candidate_batch_identity: str
    ranked_candidates: tuple[RankedCandidate, ...]
    excluded_candidate_keys: tuple[str, ...]
    exclusion_reasons: Mapping[str, str]
    contract_name: str = RANKING_CONTRACT_NAME
    contract_version: str = RANKING_CONTRACT_VERSION
    result_identity: str = field(init=False)

    def __post_init__(self) -> None:
        if (self.contract_name, self.contract_version) != (RANKING_CONTRACT_NAME, RANKING_CONTRACT_VERSION):
            raise ValueError("unsupported candidate-ranking contract")
        if not self.signal_date or not self.policy_fingerprint or not self.source_candidate_batch_identity:
            raise ValueError("ranking batch requires date, policy, and source identities")
        ranked = tuple(self.ranked_candidates)
        excluded = tuple(sorted(self.excluded_candidate_keys))
        reasons = {str(key): str(value) for key, value in self.exclusion_reasons.items()}
        keys = tuple(item.candidate_key for item in ranked)
        if len(set(keys)) != len(keys) or len(set(excluded)) != len(excluded):
            raise ValueError("duplicate candidate key in ranking result")
        if set(keys).intersection(excluded):
            raise ValueError("candidate cannot be both ranked and excluded")
        if set(reasons) != set(excluded):
            raise ValueError("excluded candidate keys and reasons must agree")
        if any(item.candidate.signal_date != self.signal_date for item in ranked):
            raise ValueError("candidate signal date does not agree with ranking batch")
        if tuple(item.ordinal for item in ranked) != tuple(range(1, len(ranked) + 1)):
            raise ValueError("ranked candidate ordinals must be contiguous")
        payload = {
            "contract": {"name": self.contract_name, "version": self.contract_version},
            "source_candidate_batch_identity": self.source_candidate_batch_identity,
            "signal_date": self.signal_date,
            "ranked_candidates": [item.canonical() for item in ranked],
            "exclusions": [[key, reasons[key]] for key in excluded],
            "policy_fingerprint": self.policy_fingerprint,
        }
        object.__setattr__(self, "ranked_candidates", ranked)
        object.__setattr__(self, "excluded_candidate_keys", excluded)
        object.__setattr__(self, "exclusion_reasons", MappingProxyType({key: reasons[key] for key in excluded}))
        object.__setattr__(self, "result_identity", sha256(canonical_json(payload)).hexdigest())

