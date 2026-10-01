from __future__ import annotations

"""Explicit methodology and provenance contracts for historical research.

This module is deliberately evidence-first.  It describes what a result used;
it does not acquire constituent history, infer ticker mappings, or infer price
adjustments from numeric observations.
"""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Mapping

from quantlab.identity import canonical_identity_value, canonical_json


CONTRACT = "quantlab.historical_research_provenance"
VERSION = "v1"


def _identity(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


class HistoricalUniverseMethodology(str, Enum):
    CURRENT_INDEX_MEMBERSHIP = "CURRENT_INDEX_MEMBERSHIP"
    DATABASE_COVERAGE = "DATABASE_COVERAGE"
    POINT_IN_TIME_INDEX_MEMBERSHIP = "POINT_IN_TIME_INDEX_MEMBERSHIP"
    EXPLICIT_SYMBOL_SET = "EXPLICIT_SYMBOL_SET"


class PITMembershipCapability(str, Enum):
    SUPPORTED = "SUPPORTED"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"


class HistoricalBiasStatus(str, Enum):
    SURVIVORSHIP_AND_FUTURE_MEMBERSHIP_RISK = "SURVIVORSHIP_AND_FUTURE_MEMBERSHIP_RISK"
    DATABASE_AVAILABILITY_ONLY_NOT_HISTORICAL_INDEX = "DATABASE_AVAILABILITY_ONLY_NOT_HISTORICAL_INDEX"
    EXPLICIT_SYMBOL_SCOPE = "EXPLICIT_SYMBOL_SCOPE"
    PIT_MEMBERSHIP_UNAVAILABLE = "PIT_MEMBERSHIP_UNAVAILABLE"


class ResearchWarning(str, Enum):
    CURRENT_MEMBERSHIP_RETROSPECTIVE = "CURRENT_MEMBERSHIP_RETROSPECTIVE"
    PIT_MEMBERSHIP_UNAVAILABLE = "PIT_MEMBERSHIP_UNAVAILABLE"
    DATABASE_COVERAGE_NOT_HISTORICAL_VN100 = "DATABASE_COVERAGE_NOT_HISTORICAL_VN100"
    CORPORATE_ACTION_PROVENANCE_UNAVAILABLE = "CORPORATE_ACTION_PROVENANCE_UNAVAILABLE"
    PRICE_ADJUSTMENT_UNKNOWN = "PRICE_ADJUSTMENT_UNKNOWN"
    TICKER_IDENTITY_HISTORY_UNAVAILABLE = "TICKER_IDENTITY_HISTORY_UNAVAILABLE"
    EXECUTION_INTERPRETATION_LIMITED = "EXECUTION_INTERPRETATION_LIMITED"


@dataclass(frozen=True, slots=True)
class PointInTimeMembershipRecord:
    """Minimum future record shape; no records are fabricated by this package."""

    effective_from: str
    effective_to: str | None
    index_id: str
    symbol: str
    canonical_security_id: str | None
    source: str
    source_timestamp: str | None


@dataclass(frozen=True, slots=True)
class UniverseMethodologyContract:
    methodology: HistoricalUniverseMethodology
    causal_with_respect_to: str
    historical_index_membership: bool
    pit_membership_capability: PITMembershipCapability
    bias_status: HistoricalBiasStatus
    eligibility_basis: str
    bias_warnings: tuple[ResearchWarning, ...]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        warnings = tuple(self.bias_warnings)
        object.__setattr__(self, "bias_warnings", warnings)
        object.__setattr__(self, "identity", _identity({
            "contract": [CONTRACT, VERSION, "universe"],
            "methodology": self.methodology.value,
            "causal_with_respect_to": self.causal_with_respect_to,
            "historical_index_membership": self.historical_index_membership,
            "pit_membership_capability": self.pit_membership_capability.value,
            "bias_status": self.bias_status.value,
            "eligibility_basis": self.eligibility_basis,
            "bias_warnings": tuple(item.value for item in warnings),
        }))


@dataclass(frozen=True, slots=True)
class TickerIdentityContract:
    symbol_semantics: str = "historical_observation_label_only"
    canonical_security_identity_available: bool = False
    ticker_change_history_available: bool = False
    delisted_security_history_available: bool = False
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "identity", _identity({
            "contract": [CONTRACT, VERSION, "ticker_identity"],
            "symbol_semantics": self.symbol_semantics,
            "canonical_security_identity_available": self.canonical_security_identity_available,
            "ticker_change_history_available": self.ticker_change_history_available,
            "delisted_security_history_available": self.delisted_security_history_available,
        }))


@dataclass(frozen=True, slots=True)
class PriceProvenanceContract:
    provider_or_source: str
    source_dataset_semantics: str
    price_adjustment_state: str
    corporate_action_coverage: str
    price_unit_state: str
    volume_unit_state: str
    execution_suitable: bool
    evidence: tuple[str, ...]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "identity", _identity({
            "contract": [CONTRACT, VERSION, "price"],
            "provider_or_source": self.provider_or_source,
            "source_dataset_semantics": self.source_dataset_semantics,
            "price_adjustment_state": self.price_adjustment_state,
            "corporate_action_coverage": self.corporate_action_coverage,
            "price_unit_state": self.price_unit_state,
            "volume_unit_state": self.volume_unit_state,
            "execution_suitable": self.execution_suitable,
            "evidence": self.evidence,
        }))


def canonical_local_price_provenance() -> PriceProvenanceContract:
    """Return the fail-closed local market-db interpretation.

    The existing execution provenance contract is the evidence source.  Its
    adjustment and corporate-action conclusions are intentionally not
    strengthened here.
    """
    # Keep execution provenance lazy: importing the methodology contract must
    # not initialize unrelated execution integrations.
    from quantlab.execution.price_provenance import assess_price_provenance

    existing = assess_price_provenance(source_identities={"dataset": "local_market_db.prices"})
    return PriceProvenanceContract(
        provider_or_source="local_market_db; provider provenance not persisted",
        source_dataset_semantics="daily OHLCV prices table; source endpoint semantics are not persisted per row",
        price_adjustment_state=existing.adjustment_state,
        corporate_action_coverage="UNAVAILABLE",
        price_unit_state=existing.storage_unit_state,
        volume_unit_state="INTEGER_STORED_UNIT_UNRESOLVED",
        execution_suitable=False,
        evidence=(
            "prices has no raw/adjusted marker or adjustment factor",
            "no corporate-action table or event provenance is persisted",
            "stored volume is integer-valued but its provider unit is not independently verified",
        ),
    )


def universe_methodology_contract(
    methodology: HistoricalUniverseMethodology | str,
) -> UniverseMethodologyContract:
    if isinstance(methodology, HistoricalUniverseMethodology):
        method = methodology
    else:
        aliases = {
            "current_vn100": HistoricalUniverseMethodology.CURRENT_INDEX_MEMBERSHIP,
            "legacy_current_vn100_retroactive": HistoricalUniverseMethodology.CURRENT_INDEX_MEMBERSHIP,
            "database_coverage": HistoricalUniverseMethodology.DATABASE_COVERAGE,
            "point_in_time_index_membership": HistoricalUniverseMethodology.POINT_IN_TIME_INDEX_MEMBERSHIP,
            "explicit_symbols": HistoricalUniverseMethodology.EXPLICIT_SYMBOL_SET,
        }
        normalized = str(methodology).lower()
        if normalized in aliases:
            method = aliases[normalized]
        else:
            try:
                method = HistoricalUniverseMethodology(str(methodology))
            except ValueError as exc:
                raise ValueError(f"unsupported historical universe methodology: {methodology}") from exc
    if method is HistoricalUniverseMethodology.CURRENT_INDEX_MEMBERSHIP:
        return UniverseMethodologyContract(
            method, "none; current membership is reused retrospectively", False,
            PITMembershipCapability.UNSUPPORTED,
            HistoricalBiasStatus.SURVIVORSHIP_AND_FUTURE_MEMBERSHIP_RISK,
            "current VN100 membership retrieved at run time",
            (ResearchWarning.CURRENT_MEMBERSHIP_RETROSPECTIVE,
             ResearchWarning.PIT_MEMBERSHIP_UNAVAILABLE),
        )
    if method is HistoricalUniverseMethodology.DATABASE_COVERAGE:
        return UniverseMethodologyContract(
            method, "local database availability at the signal date", False,
            PITMembershipCapability.UNSUPPORTED,
            HistoricalBiasStatus.DATABASE_AVAILABILITY_ONLY_NOT_HISTORICAL_INDEX,
            "minimum observed history and VNINDEX-session staleness rules",
            (ResearchWarning.DATABASE_COVERAGE_NOT_HISTORICAL_VN100,
             ResearchWarning.PIT_MEMBERSHIP_UNAVAILABLE),
        )
    if method is HistoricalUniverseMethodology.POINT_IN_TIME_INDEX_MEMBERSHIP:
        return UniverseMethodologyContract(
            method, "dated constituent membership records", True,
            PITMembershipCapability.UNSUPPORTED,
            HistoricalBiasStatus.PIT_MEMBERSHIP_UNAVAILABLE,
            "unsupported until dated constituent records are available",
            (ResearchWarning.PIT_MEMBERSHIP_UNAVAILABLE,),
        )
    return UniverseMethodologyContract(
        method, "caller-defined symbol scope", False,
        PITMembershipCapability.UNSUPPORTED,
        HistoricalBiasStatus.EXPLICIT_SYMBOL_SCOPE,
        "explicit caller-supplied symbols",
        (ResearchWarning.PIT_MEMBERSHIP_UNAVAILABLE,),
    )


@dataclass(frozen=True, slots=True)
class HistoricalResearchProvenance:
    universe: UniverseMethodologyContract
    price: PriceProvenanceContract
    ticker_identity: TickerIdentityContract
    warnings: tuple[ResearchWarning, ...]
    suitable_for_exploratory_research: bool
    suitable_for_causal_historical_comparison: bool
    suitable_for_execution_interpretation: bool
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        warnings = tuple(dict.fromkeys(self.warnings))
        if ResearchWarning.TICKER_IDENTITY_HISTORY_UNAVAILABLE not in warnings:
            warnings += (ResearchWarning.TICKER_IDENTITY_HISTORY_UNAVAILABLE,)
        if ResearchWarning.CORPORATE_ACTION_PROVENANCE_UNAVAILABLE not in warnings:
            warnings += (ResearchWarning.CORPORATE_ACTION_PROVENANCE_UNAVAILABLE,)
        if ResearchWarning.PRICE_ADJUSTMENT_UNKNOWN not in warnings:
            warnings += (ResearchWarning.PRICE_ADJUSTMENT_UNKNOWN,)
        if ResearchWarning.EXECUTION_INTERPRETATION_LIMITED not in warnings:
            warnings += (ResearchWarning.EXECUTION_INTERPRETATION_LIMITED,)
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(self, "identity", _identity(self.as_mapping(include_identity=False)))

    def as_mapping(self, *, include_identity: bool = True) -> Mapping[str, Any]:
        data = {
            "contract": CONTRACT,
            "version": VERSION,
            "universe_methodology": self.universe.methodology.value,
            "universe": {
                "methodology": self.universe.methodology.value,
                "causal_with_respect_to": self.universe.causal_with_respect_to,
                "historical_index_membership": self.universe.historical_index_membership,
                "pit_membership_capability": self.universe.pit_membership_capability.value,
                "bias_status": self.universe.bias_status.value,
                "eligibility_basis": self.universe.eligibility_basis,
                "identity": self.universe.identity,
            },
            "price": {
                "provider_or_source": self.price.provider_or_source,
                "source_dataset_semantics": self.price.source_dataset_semantics,
                "price_adjustment_state": self.price.price_adjustment_state,
                "corporate_action_coverage": self.price.corporate_action_coverage,
                "price_unit_state": self.price.price_unit_state,
                "volume_unit_state": self.price.volume_unit_state,
                "execution_suitable": self.price.execution_suitable,
                "identity": self.price.identity,
            },
            "ticker_identity": {
                "symbol_semantics": self.ticker_identity.symbol_semantics,
                "canonical_security_identity_available": self.ticker_identity.canonical_security_identity_available,
                "ticker_change_history_available": self.ticker_identity.ticker_change_history_available,
                "delisted_security_history_available": self.ticker_identity.delisted_security_history_available,
                "identity": self.ticker_identity.identity,
            },
            "warnings": tuple(item.value for item in self.warnings),
            "suitability": {
                "exploratory_research": self.suitable_for_exploratory_research,
                "causal_historical_comparison": self.suitable_for_causal_historical_comparison,
                "execution_interpretation": self.suitable_for_execution_interpretation,
            },
        }
        if include_identity:
            data["identity"] = self.identity
        return _freeze(data)

    def as_dict(self, *, include_identity: bool = True) -> dict[str, Any]:
        """Return a JSON-compatible copy for result/artifact metadata."""
        return _thaw(self.as_mapping(include_identity=include_identity))


def build_research_provenance(
    methodology: HistoricalUniverseMethodology | str,
) -> HistoricalResearchProvenance:
    universe = universe_methodology_contract(methodology)
    price = canonical_local_price_provenance()
    warnings = list(universe.bias_warnings)
    causal_ok = universe.methodology is HistoricalUniverseMethodology.DATABASE_COVERAGE
    if universe.methodology is HistoricalUniverseMethodology.POINT_IN_TIME_INDEX_MEMBERSHIP:
        causal_ok = False
    return HistoricalResearchProvenance(
        universe=universe,
        price=price,
        ticker_identity=TickerIdentityContract(),
        warnings=tuple(warnings),
        suitable_for_exploratory_research=True,
        suitable_for_causal_historical_comparison=causal_ok,
        suitable_for_execution_interpretation=False,
    )


def classify_legacy_provenance(metadata: Mapping[str, Any] | None) -> str:
    """Classify old artifacts without upgrading their methodology claims."""
    if not metadata or not metadata.get("research_provenance"):
        return "LEGACY_UNKNOWN"
    return "EXPLICIT_PROVENANCE"


__all__ = [
    "CONTRACT", "VERSION", "HistoricalUniverseMethodology", "PITMembershipCapability",
    "HistoricalBiasStatus", "ResearchWarning", "PointInTimeMembershipRecord",
    "UniverseMethodologyContract", "TickerIdentityContract", "PriceProvenanceContract",
    "HistoricalResearchProvenance", "canonical_local_price_provenance",
    "universe_methodology_contract", "build_research_provenance", "classify_legacy_provenance",
]
