from __future__ import annotations

"""Pure, fail-closed guard for a normalized OHLCV candidate batch.

This module deliberately has no database or provider dependency.  A PASS applies
only to the supplied candidate and persisted snapshot; it never certifies the
rest of a symbol's history and it never transforms either input.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
import math
from numbers import Real
from typing import Any


OHLC_FIELDS = ("open", "high", "low", "close")
VALUE_FIELDS = (*OHLC_FIELDS, "volume")
REQUIRED_FIELDS = ("symbol", "time", *VALUE_FIELDS)


class GuardDecision(str, Enum):
    PASS = "PASS"
    BLOCK = "BLOCK"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class GuardReason(str, Enum):
    BATCH_SAFE = "BATCH_SAFE"
    WHOLE_HISTORY_NOT_CERTIFIED = "WHOLE_HISTORY_NOT_CERTIFIED"
    IDENTICAL_OVERLAP = "IDENTICAL_OVERLAP"
    ATTRIBUTED_HISTORICAL_REVISION = "ATTRIBUTED_HISTORICAL_REVISION"
    ATTRIBUTED_BOUNDARY_EVENT = "ATTRIBUTED_BOUNDARY_EVENT"
    INVALID_REQUEST_RANGE = "INVALID_REQUEST_RANGE"
    INVALID_INCOMING_OHLCV = "INVALID_INCOMING_OHLCV"
    INVALID_PERSISTED_SNAPSHOT = "INVALID_PERSISTED_SNAPSHOT"
    COVERAGE_METADATA_MISMATCH = "COVERAGE_METADATA_MISMATCH"
    COVERAGE_UNVERIFIED = "COVERAGE_UNVERIFIED"
    PRICE_BASIS_UNVERIFIED = "PRICE_BASIS_UNVERIFIED"
    PRICE_BASIS_MISMATCH = "PRICE_BASIS_MISMATCH"
    MISSING_OVERLAP = "MISSING_OVERLAP"
    INSUFFICIENT_HISTORICAL_ANCHORS = "INSUFFICIENT_HISTORICAL_ANCHORS"
    UNANCHORED_HISTORICAL_INSERT = "UNANCHORED_HISTORICAL_INSERT"
    UNATTRIBUTED_HISTORICAL_REVISION = "UNATTRIBUTED_HISTORICAL_REVISION"
    BOUNDARY_DISCONTINUITY_UNEXPLAINED = "BOUNDARY_DISCONTINUITY_UNEXPLAINED"


class CoverageState(str, Enum):
    VERIFIED_COMPLETE = "VERIFIED_COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class PriceBasisState(str, Enum):
    VERIFIED = "VERIFIED"
    UNKNOWN = "UNKNOWN"


class AdjustmentBasis(str, Enum):
    RAW = "RAW"
    ADJUSTED = "ADJUSTED"
    UNKNOWN = "UNKNOWN"


class EvidenceKind(str, Enum):
    PROVIDER_CORRECTION = "PROVIDER_CORRECTION"
    CORPORATE_ACTION_RESTATEMENT = "CORPORATE_ACTION_RESTATEMENT"
    CORPORATE_ACTION_EVENT = "CORPORATE_ACTION_EVENT"


@dataclass(frozen=True, slots=True)
class CoverageMetadata:
    requested_start: date
    requested_end: date
    returned_start: date | None
    returned_end: date | None
    state: CoverageState
    source_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.requested_start > self.requested_end:
            raise ValueError("coverage request range is inverted")
        if (self.returned_start is None) != (self.returned_end is None):
            raise ValueError("coverage returned range must be supplied together")
        if self.returned_start is not None and self.returned_start > self.returned_end:
            raise ValueError("coverage returned range is inverted")
        references = _references(self.source_references)
        if self.state is CoverageState.VERIFIED_COMPLETE and not references:
            raise ValueError("verified coverage requires source references")
        object.__setattr__(self, "source_references", references)


@dataclass(frozen=True, slots=True)
class PriceBasisMetadata:
    unit: str
    adjustment_basis: AdjustmentBasis
    state: PriceBasisState
    source_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        unit = str(self.unit).strip()
        if not unit:
            raise ValueError("price unit must be non-empty")
        references = _references(self.source_references)
        if self.state is PriceBasisState.VERIFIED and (
            self.adjustment_basis is AdjustmentBasis.UNKNOWN or not references
        ):
            raise ValueError("verified price basis requires a known basis and source references")
        object.__setattr__(self, "unit", unit)
        object.__setattr__(self, "source_references", references)


@dataclass(frozen=True, slots=True)
class OHLCVRow:
    symbol: str
    session: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class FieldChange:
    session: date
    field: str
    existing_value: float
    incoming_value: float


@dataclass(frozen=True, slots=True)
class RevisionClaim:
    session: date
    field: str
    existing_value: float
    incoming_value: float

    def __post_init__(self) -> None:
        if self.field not in VALUE_FIELDS:
            raise ValueError(f"unsupported revision field: {self.field}")
        for value in (self.existing_value, self.incoming_value):
            if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
                raise ValueError("revision claim values must be finite numbers")


@dataclass(frozen=True, slots=True)
class RevisionEvidence:
    symbol: str
    kind: EvidenceKind
    claims: tuple[RevisionClaim, ...]
    source_references: tuple[str, ...]

    def __post_init__(self) -> None:
        symbol = _symbol(self.symbol)
        references = _references(self.source_references)
        if self.kind is EvidenceKind.CORPORATE_ACTION_EVENT:
            raise ValueError("boundary events cannot authorize historical revisions")
        if not self.claims or not references:
            raise ValueError("revision evidence requires claims and source references")
        claims = tuple(sorted(self.claims, key=lambda item: (item.session, item.field)))
        if len({(item.session, item.field) for item in claims}) != len(claims):
            raise ValueError("revision evidence claims must be unique by session and field")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "claims", claims)
        object.__setattr__(self, "source_references", references)


@dataclass(frozen=True, slots=True)
class BoundaryEvidence:
    symbol: str
    kind: EvidenceKind
    previous_session: date
    incoming_session: date
    previous_close: float
    incoming_open: float
    source_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.kind is not EvidenceKind.CORPORATE_ACTION_EVENT:
            raise ValueError("boundary evidence must describe a corporate-action event")
        if self.previous_session >= self.incoming_session:
            raise ValueError("boundary evidence sessions must be ordered")
        for value in (self.previous_close, self.incoming_open):
            if isinstance(value, bool) or not isinstance(value, Real) or float(value) <= 0:
                raise ValueError("boundary evidence prices must be positive numbers")
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        object.__setattr__(self, "source_references", _references(self.source_references, required=True))


@dataclass(frozen=True, slots=True)
class BoundaryDiagnostic:
    previous_session: date
    incoming_session: date
    previous_close: float
    incoming_open: float
    absolute_return: float
    attributable: bool


@dataclass(frozen=True, slots=True)
class GuardResult:
    decision: GuardDecision
    reasons: tuple[GuardReason, ...]
    symbol: str
    requested_start: date | None
    requested_end: date | None
    overlap_dates: tuple[date, ...] = ()
    revisions: tuple[FieldChange, ...] = ()
    genuinely_new_dates: tuple[date, ...] = ()
    historical_insert_dates: tuple[date, ...] = ()
    boundary: BoundaryDiagnostic | None = None

    @property
    def overlap_size(self) -> int:
        return len(self.overlap_dates)


class _RowsInvalid(ValueError):
    pass


def _references(values: Sequence[str], *, required: bool = False) -> tuple[str, ...]:
    normalized = tuple(sorted({str(value).strip() for value in values if str(value).strip()}))
    if required and not normalized:
        raise ValueError("source references are required")
    return normalized


def _symbol(value: object) -> str:
    normalized = str(value).strip().upper()
    if not normalized:
        raise ValueError("symbol must be non-empty")
    return normalized


def _session(value: object) -> date:
    if isinstance(value, datetime):
        if value.time() != datetime.min.time():
            raise _RowsInvalid("session datetime must be normalized to midnight")
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as error:
            raise _RowsInvalid("session must be an ISO date") from error
        if parsed.isoformat() != value:
            raise _RowsInvalid("session must be a normalized ISO date")
        return parsed
    raise _RowsInvalid("session must be a date or normalized ISO date")


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise _RowsInvalid(f"{field} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise _RowsInvalid(f"{field} must be finite")
    return normalized


def _rows(values: Sequence[Mapping[str, Any]], expected_symbol: str) -> tuple[OHLCVRow, ...]:
    rows: list[OHLCVRow] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise _RowsInvalid("each OHLCV row must be a mapping")
        missing = tuple(field for field in REQUIRED_FIELDS if field not in value)
        if missing:
            raise _RowsInvalid(f"missing fields: {', '.join(missing)}")
        try:
            row_symbol = _symbol(value["symbol"])
        except ValueError as error:
            raise _RowsInvalid(str(error)) from error
        if row_symbol != expected_symbol:
            raise _RowsInvalid("row symbol differs from requested symbol")
        session = _session(value["time"])
        open_value, high, low, close, volume = (
            _number(value[field], field) for field in VALUE_FIELDS
        )
        if min(open_value, high, low, close) <= 0 or volume < 0:
            raise _RowsInvalid("OHLC must be positive and volume non-negative")
        if high < low or not low <= open_value <= high or not low <= close <= high:
            raise _RowsInvalid("OHLC envelope is invalid")
        rows.append(OHLCVRow(row_symbol, session, open_value, high, low, close, volume))
    rows.sort(key=lambda item: item.session)
    if len({item.session for item in rows}) != len(rows):
        raise _RowsInvalid("duplicate symbol/session rows are not allowed")
    return tuple(rows)


def _changed(left: float, right: float, *, rel_tol: float, abs_tol: float) -> bool:
    return not math.isclose(left, right, rel_tol=rel_tol, abs_tol=abs_tol)


def _claim_matches(
    change: FieldChange,
    evidence: Sequence[RevisionEvidence],
    symbol: str,
    *,
    rel_tol: float,
    abs_tol: float,
) -> bool:
    return any(
        item.symbol == symbol
        and any(
            claim.session == change.session
            and claim.field == change.field
            and not _changed(claim.existing_value, change.existing_value, rel_tol=rel_tol, abs_tol=abs_tol)
            and not _changed(claim.incoming_value, change.incoming_value, rel_tol=rel_tol, abs_tol=abs_tol)
            for claim in item.claims
        )
        for item in evidence
    )


def _boundary_matches(
    diagnostic: BoundaryDiagnostic,
    evidence: Sequence[BoundaryEvidence],
    symbol: str,
    *,
    rel_tol: float,
    abs_tol: float,
) -> bool:
    return any(
        item.symbol == symbol
        and item.previous_session == diagnostic.previous_session
        and item.incoming_session == diagnostic.incoming_session
        and not _changed(item.previous_close, diagnostic.previous_close, rel_tol=rel_tol, abs_tol=abs_tol)
        and not _changed(item.incoming_open, diagnostic.incoming_open, rel_tol=rel_tol, abs_tol=abs_tol)
        for item in evidence
    )


def evaluate_preupdate_market_data(
    existing_rows: Sequence[Mapping[str, Any]],
    incoming_rows: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    requested_start: date,
    requested_end: date,
    coverage: CoverageMetadata,
    existing_price_basis: PriceBasisMetadata,
    incoming_price_basis: PriceBasisMetadata,
    revision_evidence: Sequence[RevisionEvidence] = (),
    boundary_evidence: Sequence[BoundaryEvidence] = (),
    minimum_overlap_sessions: int = 2,
    boundary_return_threshold: float = 0.35,
    relative_tolerance: float = 1e-9,
    absolute_tolerance: float = 1e-9,
) -> GuardResult:
    """Evaluate one symbol/batch without mutating either input.

    Large boundary movement is diagnostic evidence only.  Without exact,
    attributable event evidence it yields INSUFFICIENT_EVIDENCE, not an assumed
    corporate action.  Any unattributed material revision to an existing row is
    a BLOCK.
    """
    normalized_symbol = _symbol(symbol)
    base_reasons = {GuardReason.WHOLE_HISTORY_NOT_CERTIFIED}
    valid_request_dates = (
        isinstance(requested_start, date)
        and not isinstance(requested_start, datetime)
        and isinstance(requested_end, date)
        and not isinstance(requested_end, datetime)
    )
    if (
        not valid_request_dates
        or (valid_request_dates and requested_start > requested_end)
        or minimum_overlap_sessions < 1
        or not 0 < boundary_return_threshold
        or min(relative_tolerance, absolute_tolerance) < 0
    ):
        return GuardResult(
            GuardDecision.BLOCK,
            tuple(sorted(base_reasons | {GuardReason.INVALID_REQUEST_RANGE}, key=lambda item: item.value)),
            normalized_symbol,
            requested_start if isinstance(requested_start, date) and not isinstance(requested_start, datetime) else None,
            requested_end if isinstance(requested_end, date) and not isinstance(requested_end, datetime) else None,
        )

    try:
        incoming = _rows(incoming_rows, normalized_symbol)
    except _RowsInvalid:
        return GuardResult(
            GuardDecision.BLOCK,
            tuple(sorted(base_reasons | {GuardReason.INVALID_INCOMING_OHLCV}, key=lambda item: item.value)),
            normalized_symbol,
            requested_start,
            requested_end,
        )
    try:
        existing = _rows(existing_rows, normalized_symbol)
    except _RowsInvalid:
        return GuardResult(
            GuardDecision.INSUFFICIENT_EVIDENCE,
            tuple(sorted(base_reasons | {GuardReason.INVALID_PERSISTED_SNAPSHOT}, key=lambda item: item.value)),
            normalized_symbol,
            requested_start,
            requested_end,
        )

    blocking: set[GuardReason] = set()
    insufficient: set[GuardReason] = set()
    informative: set[GuardReason] = set(base_reasons)

    if any(not requested_start <= row.session <= requested_end for row in incoming):
        blocking.add(GuardReason.INVALID_REQUEST_RANGE)
    if coverage.requested_start != requested_start or coverage.requested_end != requested_end:
        insufficient.add(GuardReason.COVERAGE_METADATA_MISMATCH)
    if coverage.state is not CoverageState.VERIFIED_COMPLETE:
        insufficient.add(GuardReason.COVERAGE_UNVERIFIED)
    if incoming and (
        coverage.returned_start is None
        or coverage.returned_end is None
        or coverage.returned_start > incoming[0].session
        or coverage.returned_end < incoming[-1].session
    ):
        insufficient.add(GuardReason.COVERAGE_METADATA_MISMATCH)

    if (
        existing_price_basis.state is not PriceBasisState.VERIFIED
        or incoming_price_basis.state is not PriceBasisState.VERIFIED
    ):
        insufficient.add(GuardReason.PRICE_BASIS_UNVERIFIED)
    elif (
        existing_price_basis.unit != incoming_price_basis.unit
        or existing_price_basis.adjustment_basis is not incoming_price_basis.adjustment_basis
    ):
        blocking.add(GuardReason.PRICE_BASIS_MISMATCH)

    existing_by_date = {row.session: row for row in existing}
    incoming_by_date = {row.session: row for row in incoming}
    overlap_dates = tuple(sorted(existing_by_date.keys() & incoming_by_date.keys()))
    if not overlap_dates:
        insufficient.add(GuardReason.MISSING_OVERLAP)
    elif len(overlap_dates) < minimum_overlap_sessions:
        insufficient.add(GuardReason.INSUFFICIENT_HISTORICAL_ANCHORS)

    revisions: list[FieldChange] = []
    for session in overlap_dates:
        old = existing_by_date[session]
        new = incoming_by_date[session]
        for field in VALUE_FIELDS:
            old_value = getattr(old, field)
            new_value = getattr(new, field)
            if _changed(old_value, new_value, rel_tol=relative_tolerance, abs_tol=absolute_tolerance):
                revisions.append(FieldChange(session, field, old_value, new_value))
    revisions_tuple = tuple(revisions)
    if revisions_tuple:
        if all(
            _claim_matches(
                change,
                revision_evidence,
                normalized_symbol,
                rel_tol=relative_tolerance,
                abs_tol=absolute_tolerance,
            )
            for change in revisions_tuple
        ):
            informative.add(GuardReason.ATTRIBUTED_HISTORICAL_REVISION)
        else:
            blocking.add(GuardReason.UNATTRIBUTED_HISTORICAL_REVISION)
    elif overlap_dates:
        informative.add(GuardReason.IDENTICAL_OVERLAP)

    latest_existing = existing[-1].session if existing else None
    genuinely_new_dates = tuple(
        row.session for row in incoming if latest_existing is None or row.session > latest_existing
    )
    historical_insert_dates = tuple(
        row.session
        for row in incoming
        if latest_existing is not None
        and row.session <= latest_existing
        and row.session not in existing_by_date
    )
    if historical_insert_dates:
        insufficient.add(GuardReason.UNANCHORED_HISTORICAL_INSERT)

    boundary: BoundaryDiagnostic | None = None
    if existing and genuinely_new_dates:
        first_new = incoming_by_date[genuinely_new_dates[0]]
        prior_candidates = tuple(row for row in existing if row.session < first_new.session)
        if not prior_candidates:
            insufficient.add(GuardReason.INSUFFICIENT_HISTORICAL_ANCHORS)
        else:
            prior = prior_candidates[-1]
            absolute_return = abs(first_new.open / prior.close - 1.0)
            if absolute_return >= boundary_return_threshold:
                provisional = BoundaryDiagnostic(
                    prior.session,
                    first_new.session,
                    prior.close,
                    first_new.open,
                    absolute_return,
                    False,
                )
                attributable = _boundary_matches(
                    provisional,
                    boundary_evidence,
                    normalized_symbol,
                    rel_tol=relative_tolerance,
                    abs_tol=absolute_tolerance,
                )
                boundary = BoundaryDiagnostic(
                    provisional.previous_session,
                    provisional.incoming_session,
                    provisional.previous_close,
                    provisional.incoming_open,
                    provisional.absolute_return,
                    attributable,
                )
                if attributable:
                    informative.add(GuardReason.ATTRIBUTED_BOUNDARY_EVENT)
                else:
                    insufficient.add(GuardReason.BOUNDARY_DISCONTINUITY_UNEXPLAINED)

    if blocking:
        decision = GuardDecision.BLOCK
    elif insufficient:
        decision = GuardDecision.INSUFFICIENT_EVIDENCE
    else:
        decision = GuardDecision.PASS
        informative.add(GuardReason.BATCH_SAFE)
    reasons = tuple(sorted(informative | insufficient | blocking, key=lambda item: item.value))
    return GuardResult(
        decision,
        reasons,
        normalized_symbol,
        requested_start,
        requested_end,
        overlap_dates,
        revisions_tuple,
        genuinely_new_dates,
        historical_insert_dates,
        boundary,
    )


__all__ = [
    "AdjustmentBasis",
    "BoundaryDiagnostic",
    "BoundaryEvidence",
    "CoverageMetadata",
    "CoverageState",
    "EvidenceKind",
    "FieldChange",
    "GuardDecision",
    "GuardReason",
    "GuardResult",
    "OHLCVRow",
    "PriceBasisMetadata",
    "PriceBasisState",
    "RevisionClaim",
    "RevisionEvidence",
    "evaluate_preupdate_market_data",
]
