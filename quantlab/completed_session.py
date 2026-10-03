from __future__ import annotations

"""Source-agnostic completed-session evidence for market-data shadowing."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
import json
import re
from numbers import Real
from typing import Sequence


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CalendarSessionStatus(str, Enum):
    OPEN_COMPLETED = "OPEN_COMPLETED"
    OPEN_NOT_COMPLETED = "OPEN_NOT_COMPLETED"
    NON_SESSION = "NON_SESSION"
    EXCEPTIONAL_CLOSURE = "EXCEPTIONAL_CLOSURE"
    UNKNOWN = "UNKNOWN"


class SymbolSessionStatus(str, Enum):
    TRADING_CONFIRMED = "TRADING_CONFIRMED"
    NOT_TRADING = "NOT_TRADING"
    UNKNOWN = "UNKNOWN"


class CompletedSessionDecision(str, Enum):
    ADMITTED = "ADMITTED"
    UNRESOLVED = "UNRESOLVED"
    REJECTED = "REJECTED"


class CompletedSessionReason(str, Enum):
    COMPLETED_SESSION_ESTABLISHED_BY_WATERMARK = "COMPLETED_SESSION_ESTABLISHED_BY_WATERMARK"
    COMPLETED_SESSION_ESTABLISHED_BY_STABLE_OBSERVATIONS = "COMPLETED_SESSION_ESTABLISHED_BY_STABLE_OBSERVATIONS"
    CALENDAR_EVIDENCE_MISSING = "CALENDAR_EVIDENCE_MISSING"
    CALENDAR_ATTRIBUTION_INCOMPLETE = "CALENDAR_ATTRIBUTION_INCOMPLETE"
    CALENDAR_SESSION_UNRESOLVED = "CALENDAR_SESSION_UNRESOLVED"
    SYMBOL_SESSION_EVIDENCE_MISSING = "SYMBOL_SESSION_EVIDENCE_MISSING"
    SYMBOL_SESSION_ATTRIBUTION_INCOMPLETE = "SYMBOL_SESSION_ATTRIBUTION_INCOMPLETE"
    SYMBOL_SESSION_IDENTITY_MISMATCH = "SYMBOL_SESSION_IDENTITY_MISMATCH"
    SYMBOL_NOT_TRADING = "SYMBOL_NOT_TRADING"
    SYMBOL_SESSION_UNKNOWN = "SYMBOL_SESSION_UNKNOWN"
    SESSION_NOT_COMPLETED = "SESSION_NOT_COMPLETED"
    NON_SESSION = "NON_SESSION"
    EXCEPTIONAL_CLOSURE = "EXCEPTIONAL_CLOSURE"
    PROVIDER_OBSERVATIONS_MISSING = "PROVIDER_OBSERVATIONS_MISSING"
    PROVIDER_ATTRIBUTION_INCOMPLETE = "PROVIDER_ATTRIBUTION_INCOMPLETE"
    OBSERVATION_IDENTITY_MISMATCH = "OBSERVATION_IDENTITY_MISMATCH"
    TARGET_ROW_INCOMPLETE = "TARGET_ROW_INCOMPLETE"
    PUBLICATION_WATERMARK_INSUFFICIENT = "PUBLICATION_WATERMARK_INSUFFICIENT"
    PUBLICATION_DELAY_NOT_MET = "PUBLICATION_DELAY_NOT_MET"
    PUBLICATION_OBSERVATIONS_DIFFER = "PUBLICATION_OBSERVATIONS_DIFFER"
    PUBLICATION_NOT_ESTABLISHED = "PUBLICATION_NOT_ESTABLISHED"


def _text(value: object, name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be non-empty")
    return normalized


def _references(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({_text(value, "evidence reference") for value in values}))


def _aware_utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _fingerprint(value: str) -> str:
    normalized = str(value).strip().lower()
    if not _SHA256.fullmatch(normalized):
        raise ValueError("normalized row fingerprint must be a lowercase SHA-256 digest")
    return normalized


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(payload.encode("utf-8")).hexdigest()


def normalized_price_row_fingerprint(
    *,
    symbol: str,
    session: date,
    open: Real,
    high: Real,
    low: Real,
    close: Real,
    volume: Real,
) -> str:
    """Hash the canonical normalized row representation used by D4B batches."""
    if not isinstance(session, date) or isinstance(session, datetime):
        raise ValueError("normalized row session must be a date")
    values = (open, high, low, close, volume)
    if any(isinstance(value, bool) or not isinstance(value, Real) for value in values):
        raise ValueError("normalized OHLCV values must be numeric")
    return _canonical_hash({
        "symbol": _text(symbol, "symbol").upper(),
        "time": session.isoformat(),
        "open": float(open),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": float(volume),
    })


@dataclass(frozen=True, slots=True)
class CalendarSessionEvidence:
    venue: str
    calendar_identity: str
    calendar_version: str
    snapshot_reference: str
    target_session: date
    status: CalendarSessionStatus
    source_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.target_session, date) or isinstance(self.target_session, datetime):
            raise ValueError("target session must be a date")
        object.__setattr__(self, "venue", _text(self.venue, "venue").upper())
        object.__setattr__(self, "calendar_identity", _text(self.calendar_identity, "calendar identity"))
        object.__setattr__(self, "calendar_version", _text(self.calendar_version, "calendar version"))
        object.__setattr__(self, "snapshot_reference", _text(self.snapshot_reference, "calendar snapshot reference"))
        object.__setattr__(self, "source_references", _references(self.source_references))

    @property
    def attributable(self) -> bool:
        return bool(self.source_references)


@dataclass(frozen=True, slots=True)
class SymbolSessionEvidence:
    symbol: str
    venue: str
    target_session: date
    status: SymbolSessionStatus
    source_identity: str
    snapshot_identity: str
    source_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.target_session, date) or isinstance(self.target_session, datetime):
            raise ValueError("symbol target session must be a date")
        if not isinstance(self.status, SymbolSessionStatus):
            raise ValueError("symbol-session status must be a SymbolSessionStatus")
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol").upper())
        object.__setattr__(self, "venue", _text(self.venue, "venue").upper())
        object.__setattr__(self, "source_identity", _text(self.source_identity, "symbol-session source identity"))
        object.__setattr__(self, "snapshot_identity", _text(self.snapshot_identity, "symbol-session snapshot identity"))
        object.__setattr__(self, "source_references", _references(self.source_references))

    @property
    def attributable(self) -> bool:
        return bool(self.source_references)


@dataclass(frozen=True, slots=True)
class ProviderPublicationObservation:
    provider_identity: str
    symbol: str
    target_session: date
    observed_at: datetime
    normalized_row_fingerprint: str
    row_complete: bool
    source_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.target_session, date) or isinstance(self.target_session, datetime):
            raise ValueError("observation target session must be a date")
        object.__setattr__(self, "provider_identity", _text(self.provider_identity, "provider identity"))
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol").upper())
        object.__setattr__(self, "observed_at", _aware_utc(self.observed_at, "observation timestamp"))
        object.__setattr__(self, "normalized_row_fingerprint", _fingerprint(self.normalized_row_fingerprint))
        object.__setattr__(self, "source_references", _references(self.source_references))

    @property
    def attributable(self) -> bool:
        return bool(self.source_references)


@dataclass(frozen=True, slots=True)
class ProviderCompletionWatermark:
    provider_identity: str
    completed_through: date
    observed_at: datetime
    reference: str
    source_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.completed_through, date) or isinstance(self.completed_through, datetime):
            raise ValueError("watermark completed-through value must be a date")
        object.__setattr__(self, "provider_identity", _text(self.provider_identity, "provider identity"))
        object.__setattr__(self, "observed_at", _aware_utc(self.observed_at, "watermark timestamp"))
        object.__setattr__(self, "reference", _text(self.reference, "watermark reference"))
        object.__setattr__(self, "source_references", _references(self.source_references))

    @property
    def attributable(self) -> bool:
        return bool(self.source_references)


@dataclass(frozen=True, slots=True)
class PublicationDelayPolicy:
    policy_identity: str
    minimum_delay: timedelta
    source_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.minimum_delay, timedelta) or self.minimum_delay.total_seconds() <= 0:
            raise ValueError("publication delay must be positive")
        object.__setattr__(self, "policy_identity", _text(self.policy_identity, "publication-delay policy identity"))
        object.__setattr__(self, "source_references", _references(self.source_references))

    @property
    def attributable(self) -> bool:
        return bool(self.source_references)


@dataclass(frozen=True, slots=True)
class CompletedSessionResult:
    decision: CompletedSessionDecision
    reasons: tuple[CompletedSessionReason, ...]
    symbol: str
    provider_identity: str
    target_session: date
    venue: str | None
    calendar_identity: str | None
    calendar_version: str | None
    calendar_snapshot_reference: str | None
    symbol_session_status: SymbolSessionStatus | None
    symbol_session_source_identity: str | None
    symbol_session_snapshot_identity: str | None
    symbol_session_source_references: tuple[str, ...]
    publication_policy_identity: str
    observation_timestamps: tuple[str, ...]
    normalized_row_fingerprints: tuple[str, ...]
    evidence_sha256: str

    @property
    def admitted(self) -> bool:
        return self.decision is CompletedSessionDecision.ADMITTED

    def as_dict(self) -> dict[str, object]:
        return {
            "decision": self.decision.value,
            "reasons": [reason.value for reason in self.reasons],
            "symbol": self.symbol,
            "provider_identity": self.provider_identity,
            "target_session": self.target_session.isoformat(),
            "venue": self.venue,
            "calendar_identity": self.calendar_identity,
            "calendar_version": self.calendar_version,
            "calendar_snapshot_reference": self.calendar_snapshot_reference,
            "symbol_session": None if self.symbol_session_status is None else {
                "status": self.symbol_session_status.value,
                "source_identity": self.symbol_session_source_identity,
                "snapshot_identity": self.symbol_session_snapshot_identity,
                "source_references": self.symbol_session_source_references,
            },
            "publication_policy_identity": self.publication_policy_identity,
            "observation_timestamps": self.observation_timestamps,
            "normalized_row_fingerprints": self.normalized_row_fingerprints,
            "evidence_sha256": self.evidence_sha256,
        }


def _result(
    decision: CompletedSessionDecision,
    reasons: Sequence[CompletedSessionReason],
    *,
    symbol: str,
    provider_identity: str,
    target_session: date,
    calendar: CalendarSessionEvidence | None,
    symbol_session: SymbolSessionEvidence | None,
    observations: Sequence[ProviderPublicationObservation],
    policy: PublicationDelayPolicy,
    evidence_payload: dict[str, object],
) -> CompletedSessionResult:
    return CompletedSessionResult(
        decision,
        tuple(sorted(set(reasons), key=lambda item: item.value)),
        symbol,
        provider_identity,
        target_session,
        None if calendar is None else calendar.venue,
        None if calendar is None else calendar.calendar_identity,
        None if calendar is None else calendar.calendar_version,
        None if calendar is None else calendar.snapshot_reference,
        None if symbol_session is None else symbol_session.status,
        None if symbol_session is None else symbol_session.source_identity,
        None if symbol_session is None else symbol_session.snapshot_identity,
        () if symbol_session is None else symbol_session.source_references,
        policy.policy_identity,
        tuple(item.observed_at.isoformat().replace("+00:00", "Z") for item in observations),
        tuple(item.normalized_row_fingerprint for item in observations),
        _canonical_hash(evidence_payload),
    )


def evaluate_completed_session(
    *,
    symbol: str,
    provider_identity: str,
    target_session: date,
    calendar: CalendarSessionEvidence | None,
    symbol_session: SymbolSessionEvidence | None,
    observations: Sequence[ProviderPublicationObservation],
    publication_delay_policy: PublicationDelayPolicy,
    completion_watermark: ProviderCompletionWatermark | None = None,
) -> CompletedSessionResult:
    """Decide session completion without deriving a calendar from price rows."""
    symbol = _text(symbol, "symbol").upper()
    provider_identity = _text(provider_identity, "provider identity")
    if not isinstance(target_session, date) or isinstance(target_session, datetime):
        raise ValueError("target session must be a date")
    observations = tuple(sorted(observations, key=lambda item: item.observed_at))
    evidence_payload = {
        "contract": "completed-session/v2",
        "symbol": symbol,
        "provider_identity": provider_identity,
        "target_session": target_session.isoformat(),
        "calendar": None if calendar is None else {
            "venue": calendar.venue,
            "identity": calendar.calendar_identity,
            "version": calendar.calendar_version,
            "snapshot": calendar.snapshot_reference,
            "target_session": calendar.target_session.isoformat(),
            "status": calendar.status.value,
            "references": calendar.source_references,
        },
        "symbol_session": None if symbol_session is None else {
            "symbol": symbol_session.symbol,
            "venue": symbol_session.venue,
            "target_session": symbol_session.target_session.isoformat(),
            "status": symbol_session.status.value,
            "source_identity": symbol_session.source_identity,
            "snapshot_identity": symbol_session.snapshot_identity,
            "references": symbol_session.source_references,
        },
        "observations": [
            {
                "provider": item.provider_identity,
                "symbol": item.symbol,
                "target_session": item.target_session.isoformat(),
                "observed_at": item.observed_at.isoformat(),
                "row_fingerprint": item.normalized_row_fingerprint,
                "row_complete": item.row_complete,
                "references": item.source_references,
            }
            for item in observations
        ],
        "watermark": None if completion_watermark is None else {
            "provider": completion_watermark.provider_identity,
            "completed_through": completion_watermark.completed_through.isoformat(),
            "observed_at": completion_watermark.observed_at.isoformat(),
            "reference": completion_watermark.reference,
            "references": completion_watermark.source_references,
        },
        "publication_delay_policy": {
            "identity": publication_delay_policy.policy_identity,
            "minimum_delay_seconds": publication_delay_policy.minimum_delay.total_seconds(),
            "references": publication_delay_policy.source_references,
        },
    }

    def finish(decision: CompletedSessionDecision, *reasons: CompletedSessionReason) -> CompletedSessionResult:
        return _result(
            decision,
            reasons,
            symbol=symbol,
            provider_identity=provider_identity,
            target_session=target_session,
            calendar=calendar,
            symbol_session=symbol_session,
            observations=observations,
            policy=publication_delay_policy,
            evidence_payload=evidence_payload,
        )

    if calendar is None:
        return finish(CompletedSessionDecision.UNRESOLVED, CompletedSessionReason.CALENDAR_EVIDENCE_MISSING)
    if calendar.target_session != target_session:
        return finish(CompletedSessionDecision.REJECTED, CompletedSessionReason.OBSERVATION_IDENTITY_MISMATCH)
    if not calendar.attributable or not publication_delay_policy.attributable:
        return finish(CompletedSessionDecision.UNRESOLVED, CompletedSessionReason.CALENDAR_ATTRIBUTION_INCOMPLETE)
    if calendar.status is CalendarSessionStatus.NON_SESSION:
        return finish(CompletedSessionDecision.REJECTED, CompletedSessionReason.NON_SESSION)
    if calendar.status is CalendarSessionStatus.EXCEPTIONAL_CLOSURE:
        return finish(CompletedSessionDecision.REJECTED, CompletedSessionReason.EXCEPTIONAL_CLOSURE)
    if calendar.status is CalendarSessionStatus.OPEN_NOT_COMPLETED:
        return finish(CompletedSessionDecision.UNRESOLVED, CompletedSessionReason.SESSION_NOT_COMPLETED)
    if calendar.status is CalendarSessionStatus.UNKNOWN:
        return finish(CompletedSessionDecision.UNRESOLVED, CompletedSessionReason.CALENDAR_SESSION_UNRESOLVED)

    if symbol_session is None:
        return finish(
            CompletedSessionDecision.UNRESOLVED,
            CompletedSessionReason.SYMBOL_SESSION_EVIDENCE_MISSING,
        )
    if (
        symbol_session.symbol != symbol
        or symbol_session.venue != calendar.venue
        or symbol_session.target_session != target_session
    ):
        return finish(
            CompletedSessionDecision.REJECTED,
            CompletedSessionReason.SYMBOL_SESSION_IDENTITY_MISMATCH,
        )
    if not symbol_session.attributable:
        return finish(
            CompletedSessionDecision.UNRESOLVED,
            CompletedSessionReason.SYMBOL_SESSION_ATTRIBUTION_INCOMPLETE,
        )
    if symbol_session.status is SymbolSessionStatus.NOT_TRADING:
        return finish(
            CompletedSessionDecision.REJECTED,
            CompletedSessionReason.SYMBOL_NOT_TRADING,
        )
    if symbol_session.status is SymbolSessionStatus.UNKNOWN:
        return finish(
            CompletedSessionDecision.UNRESOLVED,
            CompletedSessionReason.SYMBOL_SESSION_UNKNOWN,
        )

    if not observations:
        return finish(CompletedSessionDecision.UNRESOLVED, CompletedSessionReason.PROVIDER_OBSERVATIONS_MISSING)
    if any(
        item.provider_identity != provider_identity
        or item.symbol != symbol
        or item.target_session != target_session
        for item in observations
    ):
        return finish(CompletedSessionDecision.REJECTED, CompletedSessionReason.OBSERVATION_IDENTITY_MISMATCH)
    if any(not item.attributable for item in observations):
        return finish(CompletedSessionDecision.UNRESOLVED, CompletedSessionReason.PROVIDER_ATTRIBUTION_INCOMPLETE)
    complete = tuple(item for item in observations if item.row_complete)
    if not complete:
        return finish(CompletedSessionDecision.UNRESOLVED, CompletedSessionReason.TARGET_ROW_INCOMPLETE)

    if completion_watermark is not None:
        if completion_watermark.provider_identity != provider_identity:
            return finish(CompletedSessionDecision.REJECTED, CompletedSessionReason.OBSERVATION_IDENTITY_MISMATCH)
        if completion_watermark.attributable and completion_watermark.completed_through >= target_session:
            return finish(
                CompletedSessionDecision.ADMITTED,
                CompletedSessionReason.COMPLETED_SESSION_ESTABLISHED_BY_WATERMARK,
            )

    for left_index, left in enumerate(complete):
        for right in complete[left_index + 1:]:
            if (
                left.normalized_row_fingerprint == right.normalized_row_fingerprint
                and right.observed_at - left.observed_at >= publication_delay_policy.minimum_delay
            ):
                return finish(
                    CompletedSessionDecision.ADMITTED,
                    CompletedSessionReason.COMPLETED_SESSION_ESTABLISHED_BY_STABLE_OBSERVATIONS,
                )

    reasons: list[CompletedSessionReason] = []
    if completion_watermark is not None:
        reasons.append(CompletedSessionReason.PUBLICATION_WATERMARK_INSUFFICIENT)
    if len(complete) >= 2:
        if len({item.normalized_row_fingerprint for item in complete}) > 1:
            return finish(
                CompletedSessionDecision.REJECTED,
                CompletedSessionReason.PUBLICATION_OBSERVATIONS_DIFFER,
                *reasons,
            )
        reasons.append(CompletedSessionReason.PUBLICATION_DELAY_NOT_MET)
    reasons.append(CompletedSessionReason.PUBLICATION_NOT_ESTABLISHED)
    return finish(CompletedSessionDecision.UNRESOLVED, *reasons)


__all__ = [
    "CalendarSessionEvidence",
    "CalendarSessionStatus",
    "CompletedSessionDecision",
    "CompletedSessionReason",
    "CompletedSessionResult",
    "ProviderCompletionWatermark",
    "ProviderPublicationObservation",
    "PublicationDelayPolicy",
    "SymbolSessionEvidence",
    "SymbolSessionStatus",
    "evaluate_completed_session",
    "normalized_price_row_fingerprint",
]
