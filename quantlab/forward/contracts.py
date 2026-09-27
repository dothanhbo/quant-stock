from __future__ import annotations

"""Immutable contracts for prospective, forward-only validation evidence."""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import math
from types import MappingProxyType
from typing import Any, Mapping

from quantlab.identity import canonical_identity_value, canonical_json


CONTRACT = "quantlab.prospective_forward_validation"
CONTRACT_VERSION = "v1"


def identity(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


class MaturityStatus(str, Enum):
    PENDING = "PENDING"
    MATURED = "MATURED"
    OUTCOME_UNAVAILABLE = "OUTCOME_UNAVAILABLE"
    INVALIDATED = "INVALIDATED"


class OutcomeAvailability(str, Enum):
    AVAILABLE = "AVAILABLE"
    MISSING_STOCK_FORMATION_CLOSE = "MISSING_STOCK_FORMATION_CLOSE"
    MISSING_STOCK_TARGET_CLOSE = "MISSING_STOCK_TARGET_CLOSE"
    MISSING_BENCHMARK_FORMATION_CLOSE = "MISSING_BENCHMARK_FORMATION_CLOSE"
    MISSING_BENCHMARK_TARGET_CLOSE = "MISSING_BENCHMARK_TARGET_CLOSE"


class AuditEventType(str, Enum):
    MISSING_FORMATION = "MISSING_FORMATION"


@dataclass(frozen=True, slots=True)
class ForwardValidationProtocol:
    protocol_version: str
    source_phase8_manifest_identity: str
    source_phase8_manifest_sha256: str
    source_phase8_result_identity: str
    selection_policy: str
    selection_policy_identity: str
    weighting_policy: str
    budget: int
    tracked_horizons: tuple[int, ...]
    benchmark: str
    activation_market_session_boundary: str
    historical_research_boundary_source: str
    operational_activation_cutoff_semantics: str
    formation_rule: str
    maturity_rule: str
    outcome_semantics: tuple[str, ...]
    no_backfill_invariant: str
    construction_semantics: str
    immutability_rules: tuple[str, ...]
    protocol_fingerprint: str = field(init=False)
    protocol_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.protocol_version or not self.protocol_version.strip():
            raise ValueError("prospective protocol version is required")
        if self.selection_policy != "ADX_ONLY" or self.weighting_policy != "EQUAL_WEIGHT":
            raise ValueError("prospective policy must preserve frozen selection and weighting")
        if self.budget != 5 or self.tracked_horizons != (5, 10) or self.benchmark != "VNINDEX":
            raise ValueError("prospective scenarios must match Phase 8 authorization")
        payload = {
            "contract": {"name": CONTRACT, "version": CONTRACT_VERSION},
            "protocol_version": self.protocol_version,
            "source_phase8_manifest_identity": self.source_phase8_manifest_identity,
            "source_phase8_manifest_sha256": self.source_phase8_manifest_sha256,
            "source_phase8_result_identity": self.source_phase8_result_identity,
            "selection_policy": self.selection_policy,
            "selection_policy_identity": self.selection_policy_identity,
            "weighting_policy": self.weighting_policy,
            "budget": self.budget,
            "tracked_horizons": self.tracked_horizons,
            "benchmark": self.benchmark,
            "activation_market_session_boundary": self.activation_market_session_boundary,
            "historical_research_boundary_source": self.historical_research_boundary_source,
            "operational_activation_cutoff_semantics": self.operational_activation_cutoff_semantics,
            "formation_rule": self.formation_rule,
            "maturity_rule": self.maturity_rule,
            "outcome_semantics": self.outcome_semantics,
            "no_backfill_invariant": self.no_backfill_invariant,
            "construction_semantics": self.construction_semantics,
            "immutability_rules": self.immutability_rules,
        }
        fingerprint = identity(payload)
        object.__setattr__(self, "protocol_fingerprint", fingerprint)
        object.__setattr__(self, "protocol_id", f"QV-FWD-{self.protocol_version}-{fingerprint[:16]}")


@dataclass(frozen=True, slots=True)
class ForwardProtocolActivation:
    protocol_id: str
    protocol_fingerprint: str
    protocol_version: str
    activated_at_utc: str
    activation_market_session_boundary: str
    operational_start_after_session: str
    source_phase8_manifest_identity: str
    source_phase8_manifest_sha256: str
    selection_policy: str
    weighting_policy: str
    budget: int
    tracked_horizons: tuple[int, ...]
    benchmark: str
    activation_identity: str


@dataclass(frozen=True, slots=True)
class ForwardPosition:
    symbol: str
    rank: int
    weight: float
    formation_close: float | None
    position_identity: str


@dataclass(frozen=True, slots=True)
class ForwardFormation:
    protocol_id: str
    protocol_fingerprint: str
    formation_session: str
    recorded_at_utc: str
    source_snapshot_identity: str
    completed_session_set_identity: str
    selection_policy_identity: str
    eligible_universe_identity: str
    selection_identity: str
    ordered_selected_symbols: tuple[str, ...]
    actual_selected_count: int
    budget: int
    weighting_policy: str
    positions: tuple[ForwardPosition, ...]
    gross_weight: float
    cash_weight: float
    benchmark_formation_close: float | None
    formation_identity: str


@dataclass(frozen=True, slots=True)
class ForwardMaturity:
    protocol_id: str
    formation_identity: str
    formation_session: str
    horizon_sessions: int
    target_session: str | None
    status: MaturityStatus
    reason_code: str
    recorded_at_utc: str
    maturity_identity: str


@dataclass(frozen=True, slots=True)
class ForwardOutcome:
    protocol_id: str
    formation_identity: str
    horizon_sessions: int
    target_session: str
    symbol: str
    weight: float
    availability: OutcomeAvailability
    stock_forward_return_pct: float | None
    benchmark_forward_return_pct: float | None
    excess_forward_return_pct_points: float | None
    recorded_at_utc: str
    outcome_identity: str


@dataclass(frozen=True, slots=True)
class ForwardAuditEvent:
    protocol_id: str
    event_type: AuditEventType
    market_session: str
    reason_code: str
    recorded_at_utc: str
    event_identity: str


@dataclass(frozen=True, slots=True)
class ForwardValidationStatus:
    protocol_id: str
    active: bool
    latest_completed_market_session: str | None
    latest_recorded_formation: str | None
    formation_count: int
    pending_maturity_count: int
    matured_count_by_horizon: Mapping[int, int]
    outcome_unavailable_count: int
    missing_formation_sessions: tuple[str, ...]
    status_identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "matured_count_by_horizon", MappingProxyType(dict(self.matured_count_by_horizon)))


def finite_positive_or_none(value: float | None, *, name: str) -> float | None:
    if value is None:
        return None
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be finite and positive when supplied")
    return result
