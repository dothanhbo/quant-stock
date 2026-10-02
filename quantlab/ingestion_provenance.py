from __future__ import annotations

"""Fail-closed provenance contracts for future market-data ingestion.

The contracts in this module are intentionally disconnected from the active
writers.  They do not open a database, call a provider, or upgrade historical
rows.  A future writer may create a manifest only from evidence available at
ingestion time and persist it atomically with (or durably link it to) the
committed normalized batch.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from hashlib import sha256
import re
from types import MappingProxyType
from typing import Any, Mapping

from quantlab.identity import canonical_identity_value, canonical_json


CONTRACT = "quantlab.market_data_ingestion_provenance"
VERSION = "v1"
LEGACY_UNVERIFIED = "LEGACY_UNVERIFIED"
EXPLICIT_INGESTION_MANIFEST = "EXPLICIT_INGESTION_MANIFEST"

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _identity(value: Any) -> str:
    return sha256(canonical_json(canonical_identity_value(value))).hexdigest()


def _nonempty(value: str, name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be non-empty")
    return normalized


def _sha256(value: str, name: str) -> str:
    normalized = str(value).strip().lower()
    if not _SHA256_PATTERN.fullmatch(normalized):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return normalized


def _strings(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    normalized = tuple(sorted({_nonempty(value, name) for value in values}))
    return normalized


class VerificationState(str, Enum):
    VERIFIED = "VERIFIED"
    UNKNOWN = "UNKNOWN"


class AdjustmentMode(str, Enum):
    VERIFIED_RAW = "VERIFIED_RAW"
    VERIFIED_ADJUSTED = "VERIFIED_ADJUSTED"
    UNKNOWN = "UNKNOWN"


class CorporateActionCoverage(str, Enum):
    VERIFIED_COMPLETE = "VERIFIED_COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class SecurityIdentityState(str, Enum):
    VERIFIED_STABLE = "VERIFIED_STABLE"
    UNKNOWN = "UNKNOWN"


class LabelExclusionReason(str, Enum):
    LEGACY_UNVERIFIED_SOURCE = "LEGACY_UNVERIFIED_SOURCE"
    INGESTION_NOT_SUCCESSFUL = "INGESTION_NOT_SUCCESSFUL"
    OHLCV_NOT_STRUCTURALLY_VALID = "OHLCV_NOT_STRUCTURALLY_VALID"
    FEATURE_WARMUP_INCOMPLETE = "FEATURE_WARMUP_INCOMPLETE"
    FEATURE_SESSION_COVERAGE_UNVERIFIED = "FEATURE_SESSION_COVERAGE_UNVERIFIED"
    FEATURE_INPUT_RANGE_NOT_COVERED = "FEATURE_INPUT_RANGE_NOT_COVERED"
    FEATURE_PRICE_UNIT_UNVERIFIED = "FEATURE_PRICE_UNIT_UNVERIFIED"
    FEATURE_PRICE_UNIT_INCONSISTENT = "FEATURE_PRICE_UNIT_INCONSISTENT"
    FEATURE_ADJUSTMENT_UNVERIFIED = "FEATURE_ADJUSTMENT_UNVERIFIED"
    FEATURE_ADJUSTMENT_INCONSISTENT = "FEATURE_ADJUSTMENT_INCONSISTENT"
    SECURITY_IDENTITY_UNVERIFIED = "SECURITY_IDENTITY_UNVERIFIED"
    SECURITY_IDENTITY_MISMATCH = "SECURITY_IDENTITY_MISMATCH"
    FORMATION_SESSION_NOT_COVERED = "FORMATION_SESSION_NOT_COVERED"
    EXACT_MATURITY_PRICE_MISSING = "EXACT_MATURITY_PRICE_MISSING"
    MATURITY_SESSION_NOT_COVERED = "MATURITY_SESSION_NOT_COVERED"
    RETURN_PRICE_UNIT_UNVERIFIED = "RETURN_PRICE_UNIT_UNVERIFIED"
    RETURN_PRICE_UNIT_INCONSISTENT = "RETURN_PRICE_UNIT_INCONSISTENT"
    RETURN_ADJUSTMENT_UNVERIFIED = "RETURN_ADJUSTMENT_UNVERIFIED"
    RETURN_ADJUSTMENT_INCONSISTENT = "RETURN_ADJUSTMENT_INCONSISTENT"
    CORPORATE_ACTION_COVERAGE_INCOMPLETE = "CORPORATE_ACTION_COVERAGE_INCOMPLETE"
    RETROSPECTIVE_EVIDENCE_SUBSTITUTION = "RETROSPECTIVE_EVIDENCE_SUBSTITUTION"


@dataclass(frozen=True, slots=True)
class PriceUnitSemantics:
    unit: str
    verification_state: VerificationState
    verification_basis: tuple[str, ...] = ()
    source_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "unit", _nonempty(self.unit, "price unit"))
        basis = _strings(self.verification_basis, "price-unit verification basis")
        sources = _strings(self.source_references, "price-unit source reference")
        if self.verification_state is VerificationState.VERIFIED and (not basis or not sources):
            raise ValueError("verified price-unit semantics require basis and source references")
        object.__setattr__(self, "verification_basis", basis)
        object.__setattr__(self, "source_references", sources)

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "verification_state": self.verification_state.value,
            "verification_basis": list(self.verification_basis),
            "source_references": list(self.source_references),
        }


@dataclass(frozen=True, slots=True)
class SecurityReference:
    symbol: str
    canonical_security_id: str | None
    identity_state: SecurityIdentityState
    source_references: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        symbol = _nonempty(self.symbol, "symbol").upper()
        security_id = None if self.canonical_security_id is None else _nonempty(
            self.canonical_security_id, "canonical security id"
        )
        sources = _strings(self.source_references, "security-identity source reference")
        limitations = _strings(self.limitations, "security-identity limitation")
        if self.identity_state is SecurityIdentityState.VERIFIED_STABLE and (
            security_id is None or not sources
        ):
            raise ValueError("verified stable security identity requires an id and source reference")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "canonical_security_id", security_id)
        object.__setattr__(self, "source_references", sources)
        object.__setattr__(self, "limitations", limitations)

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "canonical_security_id": self.canonical_security_id,
            "identity_state": self.identity_state.value,
            "source_references": list(self.source_references),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class NormalizationSpecification:
    version: str
    required_columns: tuple[str, ...]
    symbol_normalization: str
    session_normalization: str
    duplicate_policy: str
    numeric_coercion: str
    price_scale_transform: str

    def __post_init__(self) -> None:
        required = tuple(_nonempty(value, "required column") for value in self.required_columns)
        if len(required) != len(set(required)) or not required:
            raise ValueError("required columns must be non-empty and unique")
        object.__setattr__(self, "required_columns", required)
        for name in (
            "version", "symbol_normalization", "session_normalization",
            "duplicate_policy", "numeric_coercion", "price_scale_transform",
        ):
            object.__setattr__(self, name, _nonempty(getattr(self, name), name))

    @property
    def fingerprint(self) -> str:
        return _identity(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "required_columns": list(self.required_columns),
            "symbol_normalization": self.symbol_normalization,
            "session_normalization": self.session_normalization,
            "duplicate_policy": self.duplicate_policy,
            "numeric_coercion": self.numeric_coercion,
            "price_scale_transform": self.price_scale_transform,
        }


@dataclass(frozen=True, slots=True)
class CorporateActionProvenance:
    coverage: CorporateActionCoverage
    coverage_start: date | None = None
    coverage_end: date | None = None
    covered_event_types: tuple[str, ...] = ()
    source_references: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        events = _strings(self.covered_event_types, "corporate-action event type")
        sources = _strings(self.source_references, "corporate-action source reference")
        limitations = _strings(self.limitations, "corporate-action limitation")
        if (self.coverage_start is None) != (self.coverage_end is None):
            raise ValueError("corporate-action coverage dates must be supplied together")
        if self.coverage_start and self.coverage_start > self.coverage_end:
            raise ValueError("corporate-action coverage range is inverted")
        if self.coverage is CorporateActionCoverage.VERIFIED_COMPLETE and (
            self.coverage_start is None or not events or not sources
        ):
            raise ValueError("complete corporate-action coverage requires range, events and sources")
        object.__setattr__(self, "covered_event_types", events)
        object.__setattr__(self, "source_references", sources)
        object.__setattr__(self, "limitations", limitations)

    def covers(self, start: date, end: date) -> bool:
        return bool(
            self.coverage is CorporateActionCoverage.VERIFIED_COMPLETE
            and self.coverage_start is not None
            and self.coverage_start <= start
            and self.coverage_end is not None
            and self.coverage_end >= end
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "coverage": self.coverage.value,
            "coverage_start": None if self.coverage_start is None else self.coverage_start.isoformat(),
            "coverage_end": None if self.coverage_end is None else self.coverage_end.isoformat(),
            "covered_event_types": list(self.covered_event_types),
            "source_references": list(self.source_references),
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class MarketDataIngestionManifest:
    provider_identity: str
    endpoint_identity: str
    package_name: str
    package_version: str
    retrieval_timestamp_utc: datetime
    security: SecurityReference
    requested_start: date
    requested_end: date
    returned_start: date | None
    returned_end: date | None
    row_count: int
    raw_data_content_sha256: str
    normalized_data_content_sha256: str
    normalization: NormalizationSpecification
    input_price_unit: PriceUnitSemantics
    stored_price_unit: PriceUnitSemantics
    adjustment_mode: AdjustmentMode
    adjustment_verification_basis: tuple[str, ...]
    adjustment_source_references: tuple[str, ...]
    corporate_actions: CorporateActionProvenance
    ingestion_succeeded: bool
    ohlcv_structurally_valid: bool
    raw_archive_reference: str | None = None
    schema_version: str = VERSION
    ingestion_identity: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("provider_identity", "endpoint_identity", "package_name", "package_version"):
            object.__setattr__(self, name, _nonempty(getattr(self, name), name))
        if self.schema_version != VERSION:
            raise ValueError(f"unsupported ingestion manifest schema: {self.schema_version}")
        stamp = self.retrieval_timestamp_utc
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("retrieval timestamp must be timezone-aware")
        object.__setattr__(self, "retrieval_timestamp_utc", stamp.astimezone(timezone.utc))
        if self.requested_start > self.requested_end:
            raise ValueError("requested session range is inverted")
        if (self.returned_start is None) != (self.returned_end is None):
            raise ValueError("returned session range must be supplied together")
        if self.returned_start is not None:
            if self.returned_start > self.returned_end:
                raise ValueError("returned session range is inverted")
            if self.returned_start < self.requested_start or self.returned_end > self.requested_end:
                raise ValueError("returned sessions fall outside the requested range")
        if not isinstance(self.row_count, int) or isinstance(self.row_count, bool) or self.row_count < 0:
            raise ValueError("row_count must be a non-negative integer")
        if self.ohlcv_structurally_valid and (
            not self.ingestion_succeeded or self.row_count == 0 or self.returned_start is None
        ):
            raise ValueError("structurally valid OHLCV requires a successful non-empty ingestion")
        object.__setattr__(self, "raw_data_content_sha256", _sha256(
            self.raw_data_content_sha256, "raw data fingerprint"
        ))
        object.__setattr__(self, "normalized_data_content_sha256", _sha256(
            self.normalized_data_content_sha256, "normalized data fingerprint"
        ))
        basis = _strings(self.adjustment_verification_basis, "adjustment verification basis")
        sources = _strings(self.adjustment_source_references, "adjustment source reference")
        if self.adjustment_mode is not AdjustmentMode.UNKNOWN and (not basis or not sources):
            raise ValueError("verified adjustment mode requires basis and source references")
        object.__setattr__(self, "adjustment_verification_basis", basis)
        object.__setattr__(self, "adjustment_source_references", sources)
        archive = None if self.raw_archive_reference is None else _nonempty(
            self.raw_archive_reference, "raw archive reference"
        )
        object.__setattr__(self, "raw_archive_reference", archive)
        object.__setattr__(self, "ingestion_identity", _identity(self.as_dict(include_identity=False)))

    @property
    def corporate_action_safe(self) -> bool:
        return bool(
            self.returned_start is not None
            and self.returned_end is not None
            and self.corporate_actions.covers(self.returned_start, self.returned_end)
        )

    @property
    def economically_valid_return_semantics(self) -> bool:
        return bool(
            self.ingestion_succeeded
            and self.ohlcv_structurally_valid
            and self.adjustment_mode is not AdjustmentMode.UNKNOWN
            and self.input_price_unit.verification_state is VerificationState.VERIFIED
            and self.stored_price_unit.verification_state is VerificationState.VERIFIED
            and self.security.identity_state is SecurityIdentityState.VERIFIED_STABLE
            and self.corporate_action_safe
        )

    def covers(self, start: date, end: date) -> bool:
        return bool(
            self.returned_start is not None and self.returned_start <= start
            and self.returned_end is not None and self.returned_end >= end
        )

    def as_dict(self, *, include_identity: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "contract": CONTRACT,
            "schema_version": self.schema_version,
            "provider_identity": self.provider_identity,
            "endpoint_identity": self.endpoint_identity,
            "package": {"name": self.package_name, "version": self.package_version},
            "retrieval_timestamp_utc": self.retrieval_timestamp_utc.isoformat().replace("+00:00", "Z"),
            "security": self.security.as_dict(),
            "requested_range": [self.requested_start.isoformat(), self.requested_end.isoformat()],
            "returned_range": None if self.returned_start is None else [
                self.returned_start.isoformat(), self.returned_end.isoformat()
            ],
            "row_count": self.row_count,
            "raw_data_content_sha256": self.raw_data_content_sha256,
            "normalized_data_content_sha256": self.normalized_data_content_sha256,
            "normalization": {**self.normalization.as_dict(), "fingerprint": self.normalization.fingerprint},
            "input_price_unit": self.input_price_unit.as_dict(),
            "stored_price_unit": self.stored_price_unit.as_dict(),
            "adjustment": {
                "mode": self.adjustment_mode.value,
                "verification_basis": list(self.adjustment_verification_basis),
                "source_references": list(self.adjustment_source_references),
            },
            "corporate_actions": self.corporate_actions.as_dict(),
            "ingestion_succeeded": self.ingestion_succeeded,
            "ohlcv_structurally_valid": self.ohlcv_structurally_valid,
            "economically_valid_return_semantics": self.economically_valid_return_semantics,
            "corporate_action_safe": self.corporate_action_safe,
            "raw_archive_reference": self.raw_archive_reference,
        }
        if include_identity:
            payload["ingestion_identity"] = self.ingestion_identity
        return payload

    def to_json_bytes(self) -> bytes:
        return canonical_json(canonical_identity_value(self.as_dict()))


@dataclass(frozen=True, slots=True)
class ProspectiveLabelObservation:
    observation_id: str
    symbol: str
    feature_window_start: date
    formation_session: date
    maturity_session: date
    required_feature_sessions: int
    observed_feature_sessions: int
    feature_session_coverage_verified: bool
    feature_input_manifests: tuple[MarketDataIngestionManifest, ...]
    formation_manifest: MarketDataIngestionManifest
    maturity_manifest: MarketDataIngestionManifest
    exact_maturity_price_available: bool
    retrospective_evidence_substitution: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "observation_id", _nonempty(self.observation_id, "observation id"))
        object.__setattr__(self, "symbol", _nonempty(self.symbol, "symbol").upper())
        if not (self.feature_window_start <= self.formation_session < self.maturity_session):
            raise ValueError("feature, formation and maturity sessions must be ordered")
        for name in ("required_feature_sessions", "observed_feature_sessions"):
            value = getattr(self, name)
            if isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.required_feature_sessions == 0:
            raise ValueError("required_feature_sessions must be positive")
        manifests = tuple(sorted(self.feature_input_manifests, key=lambda item: item.ingestion_identity))
        if not manifests:
            raise ValueError("feature input manifests are required")
        object.__setattr__(self, "feature_input_manifests", manifests)


@dataclass(frozen=True, slots=True)
class LabelEligibilityAssessment:
    observation_id: str
    symbol: str
    eligible: bool
    exclusion_reasons: tuple[LabelExclusionReason, ...]
    ingestion_identities: tuple[str, ...]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        reasons = tuple(sorted(set(self.exclusion_reasons), key=lambda item: item.value))
        identities = tuple(sorted(set(self.ingestion_identities)))
        if self.eligible == bool(reasons):
            raise ValueError("eligible observations cannot have exclusion reasons")
        object.__setattr__(self, "exclusion_reasons", reasons)
        object.__setattr__(self, "ingestion_identities", identities)
        object.__setattr__(self, "identity", _identity({
            "contract": [CONTRACT, VERSION, "prospective_label_eligibility"],
            "observation_id": self.observation_id,
            "symbol": self.symbol,
            "eligible": self.eligible,
            "exclusion_reasons": tuple(item.value for item in reasons),
            "ingestion_identities": identities,
        }))


@dataclass(frozen=True, slots=True)
class ProspectiveLabelEligibilityBatch:
    assessments: tuple[LabelEligibilityAssessment, ...]
    formation_observation_denominator: int
    provenance_eligible_numerator: int
    exclusion_reason_counts: Mapping[str, int]
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        assessments = tuple(sorted(self.assessments, key=lambda item: item.observation_id))
        counts = dict(sorted(self.exclusion_reason_counts.items()))
        object.__setattr__(self, "assessments", assessments)
        object.__setattr__(self, "exclusion_reason_counts", MappingProxyType(counts))
        object.__setattr__(self, "identity", _identity({
            "contract": [CONTRACT, VERSION, "prospective_label_eligibility_batch"],
            "assessment_identities": tuple(item.identity for item in assessments),
            "formation_observation_denominator": self.formation_observation_denominator,
            "provenance_eligible_numerator": self.provenance_eligible_numerator,
            "exclusion_reason_counts": counts,
        }))


def classify_record_provenance(
    manifest: MarketDataIngestionManifest | None,
    *,
    manifest_captured_at_ingestion: bool,
) -> str:
    """Never upgrade an old row merely because a manifest was built later."""
    if manifest is None or not manifest_captured_at_ingestion:
        return LEGACY_UNVERIFIED
    return EXPLICIT_INGESTION_MANIFEST


def _assessment(observation: ProspectiveLabelObservation) -> LabelEligibilityAssessment:
    manifests = (*observation.feature_input_manifests, observation.formation_manifest, observation.maturity_manifest)
    reasons: set[LabelExclusionReason] = set()
    if any(not item.ingestion_succeeded for item in manifests):
        reasons.add(LabelExclusionReason.INGESTION_NOT_SUCCESSFUL)
    if any(not item.ohlcv_structurally_valid for item in manifests):
        reasons.add(LabelExclusionReason.OHLCV_NOT_STRUCTURALLY_VALID)
    if observation.observed_feature_sessions < observation.required_feature_sessions:
        reasons.add(LabelExclusionReason.FEATURE_WARMUP_INCOMPLETE)
    if not observation.feature_session_coverage_verified:
        reasons.add(LabelExclusionReason.FEATURE_SESSION_COVERAGE_UNVERIFIED)
    if not any(item.covers(observation.feature_window_start, observation.formation_session) for item in observation.feature_input_manifests):
        reasons.add(LabelExclusionReason.FEATURE_INPUT_RANGE_NOT_COVERED)
    if any(
        item.input_price_unit.verification_state is not VerificationState.VERIFIED
        or item.stored_price_unit.verification_state is not VerificationState.VERIFIED
        for item in observation.feature_input_manifests
    ):
        reasons.add(LabelExclusionReason.FEATURE_PRICE_UNIT_UNVERIFIED)
    if len({item.stored_price_unit.unit for item in observation.feature_input_manifests}) > 1:
        reasons.add(LabelExclusionReason.FEATURE_PRICE_UNIT_INCONSISTENT)
    if any(item.adjustment_mode is AdjustmentMode.UNKNOWN for item in observation.feature_input_manifests):
        reasons.add(LabelExclusionReason.FEATURE_ADJUSTMENT_UNVERIFIED)
    if len({item.adjustment_mode for item in observation.feature_input_manifests}) > 1:
        reasons.add(LabelExclusionReason.FEATURE_ADJUSTMENT_INCONSISTENT)
    if not observation.formation_manifest.covers(observation.formation_session, observation.formation_session):
        reasons.add(LabelExclusionReason.FORMATION_SESSION_NOT_COVERED)
    if not observation.maturity_manifest.covers(observation.maturity_session, observation.maturity_session):
        reasons.add(LabelExclusionReason.MATURITY_SESSION_NOT_COVERED)
    if not observation.exact_maturity_price_available:
        reasons.add(LabelExclusionReason.EXACT_MATURITY_PRICE_MISSING)
    return_manifests = (observation.formation_manifest, observation.maturity_manifest)
    if any(
        item.input_price_unit.verification_state is not VerificationState.VERIFIED
        or item.stored_price_unit.verification_state is not VerificationState.VERIFIED
        for item in return_manifests
    ):
        reasons.add(LabelExclusionReason.RETURN_PRICE_UNIT_UNVERIFIED)
    if len({item.stored_price_unit.unit for item in return_manifests}) > 1:
        reasons.add(LabelExclusionReason.RETURN_PRICE_UNIT_INCONSISTENT)
    if any(item.adjustment_mode is AdjustmentMode.UNKNOWN for item in return_manifests):
        reasons.add(LabelExclusionReason.RETURN_ADJUSTMENT_UNVERIFIED)
    modes = {item.adjustment_mode for item in return_manifests}
    if len(modes) > 1:
        reasons.add(LabelExclusionReason.RETURN_ADJUSTMENT_INCONSISTENT)
    if any(not item.corporate_actions.covers(observation.formation_session, observation.maturity_session) for item in return_manifests):
        reasons.add(LabelExclusionReason.CORPORATE_ACTION_COVERAGE_INCOMPLETE)
    securities = tuple(item.security for item in manifests)
    if any(item.identity_state is not SecurityIdentityState.VERIFIED_STABLE for item in securities):
        reasons.add(LabelExclusionReason.SECURITY_IDENTITY_UNVERIFIED)
    identities = {item.canonical_security_id for item in securities}
    symbols = {item.symbol for item in securities}
    if len(identities) != 1 or None in identities or symbols != {observation.symbol}:
        reasons.add(LabelExclusionReason.SECURITY_IDENTITY_MISMATCH)
    if observation.retrospective_evidence_substitution:
        reasons.add(LabelExclusionReason.RETROSPECTIVE_EVIDENCE_SUBSTITUTION)
    return LabelEligibilityAssessment(
        observation.observation_id,
        observation.symbol,
        not reasons,
        tuple(reasons),
        tuple(item.ingestion_identity for item in manifests),
    )


def assess_prospective_label_eligibility(
    observations: tuple[ProspectiveLabelObservation, ...],
) -> ProspectiveLabelEligibilityBatch:
    """Assess provenance only; no return, Rank IC, or research result is computed."""
    normalized = tuple(sorted(observations, key=lambda item: item.observation_id))
    if len({item.observation_id for item in normalized}) != len(normalized):
        raise ValueError("observation ids must be unique")
    assessments = tuple(_assessment(item) for item in normalized)
    reason_counts: dict[str, int] = {}
    for item in assessments:
        for reason in item.exclusion_reasons:
            reason_counts[reason.value] = reason_counts.get(reason.value, 0) + 1
    return ProspectiveLabelEligibilityBatch(
        assessments,
        len(assessments),
        sum(item.eligible for item in assessments),
        reason_counts,
    )


__all__ = [
    "CONTRACT", "VERSION", "LEGACY_UNVERIFIED", "EXPLICIT_INGESTION_MANIFEST",
    "VerificationState", "AdjustmentMode", "CorporateActionCoverage",
    "SecurityIdentityState", "LabelExclusionReason", "PriceUnitSemantics",
    "SecurityReference", "NormalizationSpecification", "CorporateActionProvenance",
    "MarketDataIngestionManifest", "ProspectiveLabelObservation",
    "LabelEligibilityAssessment", "ProspectiveLabelEligibilityBatch",
    "classify_record_provenance", "assess_prospective_label_eligibility",
]
