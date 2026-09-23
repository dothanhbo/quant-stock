from __future__ import annotations

"""Immutable contracts for outcome-free candidate-factor diagnostics."""

from dataclasses import dataclass, field
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Mapping

from quantlab.features.contracts import canonical_json


DIAGNOSTICS_CONTRACT_NAME = "quantlab.candidate_factor_diagnostics"
DIAGNOSTICS_CONTRACT_VERSION = "v1"
QUANTILE_METHOD = "linear_index_n_minus_1_v1"
STANDARD_DEVIATION_METHOD = "population_ddof_0_v1"
TIE_METHOD = "excess_duplicates_v1"
PEARSON_METHOD = "pairwise_finite_pearson_v1"
SPEARMAN_METHOD = "pairwise_finite_average_rank_v1"

DIAGNOSTIC_NUMERIC_FIELDS = frozenset({
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


def _identity(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _identity(value[key]) for key in sorted(value)}
    if isinstance(value, (tuple, list)):
        return [_identity(item) for item in value]
    if hasattr(value, "canonical"):
        return value.canonical()
    raise TypeError(f"unsupported diagnostics identity value: {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class CandidateFactorSet:
    name: str
    version: str
    fields: tuple[str, ...]
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        name, version = str(self.name).strip(), str(self.version).strip()
        factor_fields = tuple(str(item).strip() for item in self.fields)
        if not name or not version:
            raise ValueError("factor-set name and version are required")
        if not factor_fields:
            raise ValueError("factor set requires at least one field")
        unsupported = tuple(item for item in factor_fields if item not in DIAGNOSTIC_NUMERIC_FIELDS)
        if unsupported:
            raise ValueError("unsupported diagnostic field: " + ", ".join(unsupported))
        if len(set(factor_fields)) != len(factor_fields):
            raise ValueError("duplicate diagnostic factor field")
        methods = {
            "quantile": QUANTILE_METHOD,
            "standard_deviation": STANDARD_DEVIATION_METHOD,
            "ties": TIE_METHOD,
            "pearson": PEARSON_METHOD,
            "spearman": SPEARMAN_METHOD,
        }
        payload = {"name": name, "version": version, "fields": list(factor_fields), "methods": methods}
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "fields", factor_fields)
        object.__setattr__(self, "fingerprint", sha256(canonical_json(payload)).hexdigest())


@dataclass(frozen=True, slots=True)
class FactorDescriptiveDiagnostics:
    total_candidate_count: int
    finite_count: int
    missing_nonfinite_count: int
    finite_coverage_pct: float
    minimum: float | None
    maximum: float | None
    mean: float | None
    population_std: float | None
    median: float | None
    percentile_25: float | None
    percentile_75: float | None
    interquartile_range: float | None
    unique_finite_count: int
    tie_count: int
    tie_rate: float | None
    constant: bool

    def canonical(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class FactorAssociationDiagnostics:
    first_factor: str
    second_factor: str
    pairwise_finite_count: int
    pearson_correlation: float | None
    spearman_correlation: float | None
    undefined_reason: str | None

    @property
    def pair_key(self) -> str:
        return f"{self.first_factor}|{self.second_factor}"

    def canonical(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class FactorDateStabilityDiagnostics:
    dates_with_finite_observations: int
    constant_date_count: int
    mean_date_finite_coverage_pct: float | None
    mean_date_median: float | None
    population_std_date_median: float | None
    mean_date_iqr: float | None

    def canonical(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class FactorDateDiagnostics:
    signal_date: str
    candidate_count: int
    factor_set_fingerprint: str
    source_candidate_content_identity: str
    per_factor: Mapping[str, FactorDescriptiveDiagnostics]
    pairwise_associations: tuple[FactorAssociationDiagnostics, ...]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        per_factor = MappingProxyType(dict(self.per_factor))
        associations = tuple(self.pairwise_associations)
        payload = {
            "signal_date": self.signal_date,
            "candidate_count": self.candidate_count,
            "factor_set_fingerprint": self.factor_set_fingerprint,
            "source_candidate_content_identity": self.source_candidate_content_identity,
            "per_factor": {key: value.canonical() for key, value in per_factor.items()},
            "pairwise_associations": [value.canonical() for value in associations],
        }
        object.__setattr__(self, "per_factor", per_factor)
        object.__setattr__(self, "pairwise_associations", associations)
        object.__setattr__(self, "identity", sha256(canonical_json(_identity(payload))).hexdigest())

    def association_for(self, first_factor: str, second_factor: str) -> FactorAssociationDiagnostics:
        for association in self.pairwise_associations:
            if (association.first_factor, association.second_factor) == (first_factor, second_factor):
                return association
        raise KeyError(f"unknown canonical factor pair: {first_factor}|{second_factor}")


@dataclass(frozen=True, slots=True)
class FactorAggregateDiagnostics:
    date_count: int
    total_candidate_observations: int
    per_factor: Mapping[str, FactorDescriptiveDiagnostics]
    pairwise_associations: tuple[FactorAssociationDiagnostics, ...]
    date_level_stability: Mapping[str, FactorDateStabilityDiagnostics]
    scope: str = "pooled_descriptive_only_not_cross_sectional_ranking"
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        per_factor = MappingProxyType(dict(self.per_factor))
        associations = tuple(self.pairwise_associations)
        stability = MappingProxyType(dict(self.date_level_stability))
        payload = {
            "date_count": self.date_count,
            "total_candidate_observations": self.total_candidate_observations,
            "per_factor": {key: value.canonical() for key, value in per_factor.items()},
            "pairwise_associations": [value.canonical() for value in associations],
            "date_level_stability": {key: value.canonical() for key, value in stability.items()},
            "scope": self.scope,
        }
        object.__setattr__(self, "per_factor", per_factor)
        object.__setattr__(self, "pairwise_associations", associations)
        object.__setattr__(self, "date_level_stability", stability)
        object.__setattr__(self, "identity", sha256(canonical_json(_identity(payload))).hexdigest())

    def association_for(self, first_factor: str, second_factor: str) -> FactorAssociationDiagnostics:
        for association in self.pairwise_associations:
            if (association.first_factor, association.second_factor) == (first_factor, second_factor):
                return association
        raise KeyError(f"unknown canonical factor pair: {first_factor}|{second_factor}")


@dataclass(frozen=True, slots=True)
class CandidateFactorDiagnosticsResult:
    source_candidate_batch_identity: str
    factor_set_fingerprint: str
    per_date: tuple[FactorDateDiagnostics, ...]
    aggregate: FactorAggregateDiagnostics
    contract_name: str = DIAGNOSTICS_CONTRACT_NAME
    contract_version: str = DIAGNOSTICS_CONTRACT_VERSION
    result_identity: str = field(init=False)

    def __post_init__(self) -> None:
        per_date = tuple(self.per_date)
        if tuple(item.signal_date for item in per_date) != tuple(sorted(item.signal_date for item in per_date)):
            raise ValueError("per-date diagnostics must be chronological")
        payload = {
            "contract": {"name": self.contract_name, "version": self.contract_version},
            "source_candidate_batch_identity": self.source_candidate_batch_identity,
            "factor_set_fingerprint": self.factor_set_fingerprint,
            "per_date_identities": [item.identity for item in per_date],
            "aggregate": self.aggregate.identity,
        }
        object.__setattr__(self, "per_date", per_date)
        object.__setattr__(self, "result_identity", sha256(canonical_json(payload)).hexdigest())

