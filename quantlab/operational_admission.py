from __future__ import annotations

"""Pure operational admission policy layered after the unchanged D4A guard."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from quantlab.completed_session import CompletedSessionDecision, CompletedSessionReason
from quantlab.preupdate_market_data_guard import (
    GuardDecision,
    GuardReason,
    GuardResult,
)

if TYPE_CHECKING:
    from quantlab.transactional_market_data import PreparedPriceBatch


POLICY = "OPERATIONAL_APPEND_ONLY_V1"


class IngestionIntent(str, Enum):
    INCREMENTAL_UPDATE = "INCREMENTAL_UPDATE"
    BACKFILL = "BACKFILL"
    BOOTSTRAP = "BOOTSTRAP"
    UNSPECIFIED = "UNSPECIFIED"


class SymbolIdentityState(str, Enum):
    VERIFIED_STABLE = "VERIFIED_STABLE"
    CONSISTENT_REQUEST_SYMBOL = "CONSISTENT_REQUEST_SYMBOL"
    UNCERTAIN = "UNCERTAIN"
    CONTRADICTORY = "CONTRADICTORY"


class OperationalAdmission(str, Enum):
    NOT_REQUIRED_GUARD_PASS = "NOT_REQUIRED_GUARD_PASS"
    OPERATIONAL_APPEND_ONLY_ACCEPTED = "OPERATIONAL_APPEND_ONLY_ACCEPTED"
    OPERATIONAL_REJECTED = "OPERATIONAL_REJECTED"
    BACKFILL_REQUIRES_STAGING = "BACKFILL_REQUIRES_STAGING"
    BOOTSTRAP_PENDING = "BOOTSTRAP_PENDING"
    HISTORICAL_GAP_REQUIRES_REVIEW = "HISTORICAL_GAP_REQUIRES_REVIEW"


class OperationalReason(str, Enum):
    GUARD_PASS_CONTROLS_WRITE = "GUARD_PASS_CONTROLS_WRITE"
    APPEND_ONLY_CONDITIONS_MET = "APPEND_ONLY_CONDITIONS_MET"
    EXISTING_HISTORY_REQUIRED = "EXISTING_HISTORY_REQUIRED"
    BACKFILL_NOT_APPEND_ONLY = "BACKFILL_NOT_APPEND_ONLY"
    HISTORICAL_GAP_NOT_APPEND_ONLY = "HISTORICAL_GAP_NOT_APPEND_ONLY"
    GUARD_BLOCKED = "GUARD_BLOCKED"
    UNEXPECTED_GUARD_REASON = "UNEXPECTED_GUARD_REASON"
    PRICE_BASIS_IS_ONLY_ALLOWED_INSUFFICIENCY = "PRICE_BASIS_IS_ONLY_ALLOWED_INSUFFICIENCY"
    OVERLAP_ANCHORS_INSUFFICIENT = "OVERLAP_ANCHORS_INSUFFICIENT"
    OVERLAP_NOT_IDENTICAL = "OVERLAP_NOT_IDENTICAL"
    NO_NEW_SESSIONS = "NO_NEW_SESSIONS"
    NEW_SESSION_NOT_STRICT_APPEND = "NEW_SESSION_NOT_STRICT_APPEND"
    BOUNDARY_DISCONTINUITY_PRESENT = "BOUNDARY_DISCONTINUITY_PRESENT"
    SYMBOL_IDENTITY_UNSAFE = "SYMBOL_IDENTITY_UNSAFE"
    SOURCE_ATTRIBUTION_INCOMPLETE = "SOURCE_ATTRIBUTION_INCOMPLETE"
    COMPLETED_SESSION_COVERAGE_MISSING = "COMPLETED_SESSION_COVERAGE_MISSING"
    COMPLETED_SESSION_EVIDENCE_MISSING = "COMPLETED_SESSION_EVIDENCE_MISSING"
    COMPLETED_SESSION_UNRESOLVED = "COMPLETED_SESSION_UNRESOLVED"
    COMPLETED_SESSION_REJECTED = "COMPLETED_SESSION_REJECTED"
    SYMBOL_SESSION_EVIDENCE_MISSING = "SYMBOL_SESSION_EVIDENCE_MISSING"
    SYMBOL_SESSION_COVERAGE_MISSING = "SYMBOL_SESSION_COVERAGE_MISSING"
    SYMBOL_SESSION_EVIDENCE_INVALID = "SYMBOL_SESSION_EVIDENCE_INVALID"
    SYMBOL_SESSION_NOT_TRADING = "SYMBOL_SESSION_NOT_TRADING"
    SYMBOL_SESSION_UNKNOWN = "SYMBOL_SESSION_UNKNOWN"


class ShadowDailyStatus(str, Enum):
    GUARD_PASS_APPROVED = "GUARD_PASS_APPROVED"
    OPERATIONAL_APPEND_ACCEPTED = "OPERATIONAL_APPEND_ACCEPTED"
    REJECTED = "REJECTED"
    BACKFILL_STAGED = "BACKFILL_STAGED"
    BOOTSTRAP_STAGED = "BOOTSTRAP_STAGED"
    HISTORICAL_GAP_STAGED = "HISTORICAL_GAP_STAGED"


OPERATIONAL_ONLY = "OPERATIONAL_ONLY"
UNKNOWN = "UNKNOWN"

_ALLOWED_INSUFFICIENT_REASONS = frozenset({
    GuardReason.IDENTICAL_OVERLAP,
    GuardReason.PRICE_BASIS_UNVERIFIED,
    GuardReason.WHOLE_HISTORY_NOT_CERTIFIED,
})

_ANCHOR_LIMITATIONS = (
    "LEGACY_UNVERIFIED",
    "PRICE_ADJUSTMENT_UNKNOWN",
    "CORPORATE_ACTION_VERIFICATION_UNKNOWN",
    "OVERLAP_DOES_NOT_CERTIFY_WHOLE_HISTORY",
)


@dataclass(frozen=True, slots=True)
class OperationalAdmissionResult:
    policy: str
    admission: OperationalAdmission
    reasons: tuple[OperationalReason, ...]
    guard_decision: GuardDecision
    guard_reasons: tuple[GuardReason, ...]
    historical_anchor_dates: tuple[str, ...]
    new_session_dates: tuple[str, ...]
    limitations: tuple[str, ...]
    data_class: str = OPERATIONAL_ONLY
    research_eligible: bool = False
    adjustment_mode: str = UNKNOWN
    corporate_action_verification: str = UNKNOWN

    @property
    def admitted(self) -> bool:
        return self.admission is OperationalAdmission.OPERATIONAL_APPEND_ONLY_ACCEPTED

    @property
    def requires_staging(self) -> bool:
        return self.admission in {
            OperationalAdmission.BACKFILL_REQUIRES_STAGING,
            OperationalAdmission.BOOTSTRAP_PENDING,
            OperationalAdmission.HISTORICAL_GAP_REQUIRES_REVIEW,
        }


def _result(
    admission: OperationalAdmission,
    reasons: Sequence[OperationalReason],
    guard: GuardResult,
) -> OperationalAdmissionResult:
    return OperationalAdmissionResult(
        POLICY,
        admission,
        tuple(sorted(set(reasons), key=lambda item: item.value)),
        guard.decision,
        guard.reasons,
        tuple(value.isoformat() for value in guard.overlap_dates),
        tuple(value.isoformat() for value in guard.genuinely_new_dates),
        _ANCHOR_LIMITATIONS,
    )


def _symbol_session_reasons(
    completed_reasons: Sequence[CompletedSessionReason],
) -> tuple[OperationalReason, ...]:
    mapping = {
        CompletedSessionReason.SYMBOL_SESSION_EVIDENCE_MISSING:
            OperationalReason.SYMBOL_SESSION_EVIDENCE_MISSING,
        CompletedSessionReason.SYMBOL_SESSION_ATTRIBUTION_INCOMPLETE:
            OperationalReason.SYMBOL_SESSION_EVIDENCE_INVALID,
        CompletedSessionReason.SYMBOL_SESSION_IDENTITY_MISMATCH:
            OperationalReason.SYMBOL_SESSION_EVIDENCE_INVALID,
        CompletedSessionReason.SYMBOL_NOT_TRADING:
            OperationalReason.SYMBOL_SESSION_NOT_TRADING,
        CompletedSessionReason.SYMBOL_SESSION_UNKNOWN:
            OperationalReason.SYMBOL_SESSION_UNKNOWN,
    }
    return tuple(mapping[reason] for reason in completed_reasons if reason in mapping)


def evaluate_operational_admission(
    batch: PreparedPriceBatch,
    guard: GuardResult,
    existing_rows: Sequence[Mapping[str, Any]],
) -> OperationalAdmissionResult:
    """Evaluate operational storage only; never reinterpret ``guard``."""
    if not existing_rows:
        return _result(
            OperationalAdmission.BOOTSTRAP_PENDING,
            (OperationalReason.EXISTING_HISTORY_REQUIRED,),
            guard,
        )

    if guard.historical_insert_dates:
        return _result(
            OperationalAdmission.HISTORICAL_GAP_REQUIRES_REVIEW,
            (OperationalReason.HISTORICAL_GAP_NOT_APPEND_ONLY,),
            guard,
        )

    if batch.ingestion_intent in {IngestionIntent.BACKFILL, IngestionIntent.BOOTSTRAP}:
        return _result(
            OperationalAdmission.BACKFILL_REQUIRES_STAGING,
            (OperationalReason.BACKFILL_NOT_APPEND_ONLY,),
            guard,
        )

    completed = batch.completed_session_result
    if completed is None:
        return _result(
            OperationalAdmission.OPERATIONAL_REJECTED,
            (OperationalReason.COMPLETED_SESSION_EVIDENCE_MISSING,),
            guard,
        )
    if completed.decision is CompletedSessionDecision.UNRESOLVED:
        return _result(
            OperationalAdmission.OPERATIONAL_REJECTED,
            (
                OperationalReason.COMPLETED_SESSION_UNRESOLVED,
                *_symbol_session_reasons(completed.reasons),
            ),
            guard,
        )
    if completed.decision is CompletedSessionDecision.REJECTED:
        return _result(
            OperationalAdmission.OPERATIONAL_REJECTED,
            (
                OperationalReason.COMPLETED_SESSION_REJECTED,
                *_symbol_session_reasons(completed.reasons),
            ),
            guard,
        )

    if guard.genuinely_new_dates and set(guard.genuinely_new_dates) != {completed.target_session}:
        return _result(
            OperationalAdmission.OPERATIONAL_REJECTED,
            (OperationalReason.SYMBOL_SESSION_COVERAGE_MISSING,),
            guard,
        )

    if guard.decision is GuardDecision.PASS:
        return _result(
            OperationalAdmission.NOT_REQUIRED_GUARD_PASS,
            (OperationalReason.GUARD_PASS_CONTROLS_WRITE,),
            guard,
        )

    reasons: set[OperationalReason] = set()
    if guard.decision is GuardDecision.BLOCK:
        reasons.add(OperationalReason.GUARD_BLOCKED)
    if batch.ingestion_intent is not IngestionIntent.INCREMENTAL_UPDATE:
        reasons.add(OperationalReason.BACKFILL_NOT_APPEND_ONLY)
    if batch.symbol_identity_state not in {
        SymbolIdentityState.VERIFIED_STABLE,
        SymbolIdentityState.CONSISTENT_REQUEST_SYMBOL,
    }:
        reasons.add(OperationalReason.SYMBOL_IDENTITY_UNSAFE)
    if not batch.source_attributable:
        reasons.add(OperationalReason.SOURCE_ATTRIBUTION_INCOMPLETE)
    if batch.completed_through is None or any(
        row.session > batch.completed_through for row in batch.rows
    ):
        reasons.add(OperationalReason.COMPLETED_SESSION_COVERAGE_MISSING)
    if len(guard.overlap_dates) < 2:
        reasons.add(OperationalReason.OVERLAP_ANCHORS_INSUFFICIENT)
    if guard.revisions or GuardReason.IDENTICAL_OVERLAP not in guard.reasons:
        reasons.add(OperationalReason.OVERLAP_NOT_IDENTICAL)
    if not guard.genuinely_new_dates:
        reasons.add(OperationalReason.NO_NEW_SESSIONS)
    latest_existing = max(str(row["time"]) for row in existing_rows)
    if any(value.isoformat() <= latest_existing for value in guard.genuinely_new_dates):
        reasons.add(OperationalReason.NEW_SESSION_NOT_STRICT_APPEND)
    if guard.boundary is not None:
        reasons.add(OperationalReason.BOUNDARY_DISCONTINUITY_PRESENT)
    if set(guard.reasons) - _ALLOWED_INSUFFICIENT_REASONS:
        reasons.add(OperationalReason.UNEXPECTED_GUARD_REASON)
    if GuardReason.PRICE_BASIS_UNVERIFIED not in guard.reasons:
        reasons.add(OperationalReason.PRICE_BASIS_IS_ONLY_ALLOWED_INSUFFICIENCY)

    if reasons:
        return _result(OperationalAdmission.OPERATIONAL_REJECTED, tuple(reasons), guard)
    return _result(
        OperationalAdmission.OPERATIONAL_APPEND_ONLY_ACCEPTED,
        (OperationalReason.APPEND_ONLY_CONDITIONS_MET,),
        guard,
    )


def shadow_daily_status(result: OperationalAdmissionResult) -> ShadowDailyStatus:
    mapping = {
        OperationalAdmission.NOT_REQUIRED_GUARD_PASS: ShadowDailyStatus.GUARD_PASS_APPROVED,
        OperationalAdmission.OPERATIONAL_APPEND_ONLY_ACCEPTED: ShadowDailyStatus.OPERATIONAL_APPEND_ACCEPTED,
        OperationalAdmission.OPERATIONAL_REJECTED: ShadowDailyStatus.REJECTED,
        OperationalAdmission.BACKFILL_REQUIRES_STAGING: ShadowDailyStatus.BACKFILL_STAGED,
        OperationalAdmission.BOOTSTRAP_PENDING: ShadowDailyStatus.BOOTSTRAP_STAGED,
        OperationalAdmission.HISTORICAL_GAP_REQUIRES_REVIEW: ShadowDailyStatus.HISTORICAL_GAP_STAGED,
    }
    return mapping[result.admission]


__all__ = [
    "IngestionIntent",
    "OPERATIONAL_ONLY",
    "OperationalAdmission",
    "OperationalAdmissionResult",
    "OperationalReason",
    "POLICY",
    "SymbolIdentityState",
    "ShadowDailyStatus",
    "UNKNOWN",
    "evaluate_operational_admission",
    "shadow_daily_status",
]
