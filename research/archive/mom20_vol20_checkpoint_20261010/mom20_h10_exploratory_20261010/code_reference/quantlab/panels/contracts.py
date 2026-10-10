from __future__ import annotations

"""Immutable contracts for strategy-neutral point-in-time research panels."""

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Mapping

import pandas as pd

from quantlab.identity import canonical_identity_value, canonical_json


OBSERVATION_INDEX_CONTRACT = "quantlab.point_in_time_observation_index"
OBSERVATION_INDEX_VERSION = "v1"
OBSERVATION_INDEX_COLUMNS = (
    "session_date",
    "symbol",
    "benchmark_symbol",
    "membership_set_hash",
    "membership_count",
    "member_ordinal",
    "market_row_available",
    "availability",
    "source_market_time",
    "source_row_ordinal",
)
CALENDAR_SEMANTICS = "exact_stored_benchmark_sessions_inclusive_bounds_v1"
MEMBERSHIP_SEMANTICS = "point_in_time_members_as_of_benchmark_session_excluding_benchmark_v1"
ORDERING_SEMANTICS = "session_date_ascending_then_symbol_ascending_v1"
SOURCE_ORDINAL_SEMANTICS = "zero_based_normalized_symbol_frame_row_ordinal_v1"
MEMBERSHIP_HASH_SEMANTICS = "sha256_compact_sorted_utf8_json_members_v1"
AVAILABILITY_SEMANTICS = (
    ("AVAILABLE", "normalized exact-date market row exists"),
    (
        "MISSING_EXACT_MARKET_ROW",
        "no normalized exact-date market row; source absence and rejected malformed source rows are not distinguishable",
    ),
)


class ObservationAvailability(str, Enum):
    AVAILABLE = "AVAILABLE"
    MISSING_EXACT_MARKET_ROW = "MISSING_EXACT_MARKET_ROW"


def membership_set_hash(symbols: tuple[str, ...]) -> str:
    return sha256(canonical_json({"members": list(symbols)})).hexdigest()


@dataclass(frozen=True, slots=True)
class PointInTimeObservationIndexSpec:
    name: str
    version: str
    columns: tuple[str, ...] = OBSERVATION_INDEX_COLUMNS
    calendar_semantics: str = CALENDAR_SEMANTICS
    membership_semantics: str = MEMBERSHIP_SEMANTICS
    ordering_semantics: str = ORDERING_SEMANTICS
    source_ordinal_semantics: str = SOURCE_ORDINAL_SEMANTICS
    membership_hash_semantics: str = MEMBERSHIP_HASH_SEMANTICS
    availability_semantics: tuple[tuple[str, str], ...] = AVAILABILITY_SEMANTICS
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        name, version = str(self.name).strip(), str(self.version).strip()
        if not name or not version:
            raise ValueError("observation-index spec name and version are required")
        if tuple(self.columns) != OBSERVATION_INDEX_COLUMNS:
            raise ValueError("unsupported observation-index output schema")
        expected = (
            CALENDAR_SEMANTICS,
            MEMBERSHIP_SEMANTICS,
            ORDERING_SEMANTICS,
            SOURCE_ORDINAL_SEMANTICS,
            MEMBERSHIP_HASH_SEMANTICS,
            AVAILABILITY_SEMANTICS,
        )
        actual = (
            self.calendar_semantics,
            self.membership_semantics,
            self.ordering_semantics,
            self.source_ordinal_semantics,
            self.membership_hash_semantics,
            tuple(self.availability_semantics),
        )
        if actual != expected:
            raise ValueError("unsupported observation-index semantics")
        payload = {
            "name": name,
            "version": version,
            "columns": list(self.columns),
            "calendar_semantics": self.calendar_semantics,
            "membership_semantics": self.membership_semantics,
            "availability_semantics": [list(item) for item in self.availability_semantics],
            "ordering_semantics": self.ordering_semantics,
            "source_ordinal_semantics": self.source_ordinal_semantics,
            "membership_hash_semantics": self.membership_hash_semantics,
        }
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "columns", tuple(self.columns))
        object.__setattr__(self, "availability_semantics", tuple(self.availability_semantics))
        object.__setattr__(self, "fingerprint", sha256(canonical_json(payload)).hexdigest())


POINT_IN_TIME_OBSERVATION_INDEX_V1 = PointInTimeObservationIndexSpec(
    name=OBSERVATION_INDEX_CONTRACT,
    version=OBSERVATION_INDEX_VERSION,
)


@dataclass(frozen=True, slots=True)
class ObservationSessionAudit:
    session_date: str
    membership_count: int
    membership_set_hash: str
    emitted_observation_row_count: int
    available_row_count: int
    missing_row_count: int

    def __post_init__(self) -> None:
        try:
            pd.Timestamp(self.session_date).date().isoformat()
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("session audit date must be valid") from exc
        counts = (
            self.membership_count,
            self.emitted_observation_row_count,
            self.available_row_count,
            self.missing_row_count,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts):
            raise ValueError("session audit counts must be non-negative integers")
        if self.membership_count != self.emitted_observation_row_count:
            raise ValueError("session membership and emitted-row counts must match")
        if self.available_row_count + self.missing_row_count != self.emitted_observation_row_count:
            raise ValueError("session available and missing counts must reconcile")
        if len(self.membership_set_hash) != 64:
            raise ValueError("session membership hash must be SHA-256")

    def canonical_content(self) -> dict[str, Any]:
        return {
            "session_date": self.session_date,
            "membership_count": self.membership_count,
            "membership_set_hash": self.membership_set_hash,
            "emitted_observation_row_count": self.emitted_observation_row_count,
            "available_row_count": self.available_row_count,
            "missing_row_count": self.missing_row_count,
        }


def _row_content(row: tuple[Any, ...]) -> dict[str, Any]:
    values = dict(zip(OBSERVATION_INDEX_COLUMNS, row))
    for name in ("source_market_time", "source_row_ordinal"):
        if pd.isna(values[name]):
            values[name] = None
    values["membership_count"] = int(values["membership_count"])
    values["member_ordinal"] = int(values["member_ordinal"])
    values["market_row_available"] = bool(values["market_row_available"])
    if values["source_row_ordinal"] is not None:
        values["source_row_ordinal"] = int(values["source_row_ordinal"])
    return values


def observation_content_identity(
    session_audit: tuple[ObservationSessionAudit, ...],
    frame: pd.DataFrame,
    *,
    batch_size: int = 2_048,
) -> str:
    """Hash bounded logical content without materializing one giant payload."""
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError("content hash batch_size must be a positive integer")
    digest = sha256()
    digest.update(b'{"effective_sessions":[')
    for index, audit in enumerate(session_audit):
        if index:
            digest.update(b",")
        digest.update(canonical_json(canonical_identity_value(audit.canonical_content())))
    digest.update(b'],"rows":[')
    first = True
    columns = list(OBSERVATION_INDEX_COLUMNS)
    for offset in range(0, len(frame), batch_size):
        batch = frame.iloc[offset:offset + batch_size]
        for row in batch.loc[:, columns].itertuples(index=False, name=None):
            if not first:
                digest.update(b",")
            digest.update(canonical_json(canonical_identity_value(_row_content(row))))
            first = False
    digest.update(b"]}")
    return digest.hexdigest()


def _immutable_metadata(value: Any) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, tuple):
        return all(_immutable_metadata(item) for item in value)
    if isinstance(value, MappingProxyType):
        return all(isinstance(key, str) and _immutable_metadata(item) for key, item in value.items())
    return False


@dataclass(frozen=True, slots=True)
class PointInTimeObservationIndex:
    spec: PointInTimeObservationIndexSpec
    canonical_database_path: str
    snapshot_id: str
    universe_membership_identity: str
    benchmark_symbol: str
    requested_start_date: str
    requested_through_date: str
    session_audit: tuple[ObservationSessionAudit, ...]
    symbols: tuple[str, ...]
    metadata: Mapping[str, Any]
    _frame: pd.DataFrame = field(repr=False, compare=False)
    effective_first_session_date: str = field(init=False)
    effective_last_session_date: str = field(init=False)
    benchmark_session_count: int = field(init=False)
    total_membership_row_count: int = field(init=False)
    available_row_count: int = field(init=False)
    missing_row_count: int = field(init=False)
    distinct_member_symbol_count: int = field(init=False)
    content_identity: str = field(init=False)
    identity: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.spec, PointInTimeObservationIndexSpec):
            raise TypeError("spec must be PointInTimeObservationIndexSpec")
        for name in ("canonical_database_path", "snapshot_id", "universe_membership_identity", "benchmark_symbol"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.metadata, MappingProxyType) or not _immutable_metadata(self.metadata):
            raise ValueError("observation-index metadata must be immutable")
        audits = tuple(self.session_audit)
        if not audits:
            raise ValueError("observation index requires benchmark-session audit records")
        audit_dates = tuple(item.session_date for item in audits)
        if audit_dates != tuple(sorted(audit_dates)) or len(set(audit_dates)) != len(audit_dates):
            raise ValueError("session audit must be uniquely ordered by date")
        frame = self._frame.copy(deep=True)
        if tuple(frame.columns) != OBSERVATION_INDEX_COLUMNS:
            raise ValueError("unsupported observation-index output schema")
        row_keys = list(zip(frame["session_date"].astype(str), frame["symbol"].astype(str)))
        if row_keys != sorted(row_keys) or len(set(row_keys)) != len(row_keys):
            raise ValueError("observation rows must be unique and canonically ordered")
        benchmark = self.benchmark_symbol.strip().upper()
        if any(symbol == benchmark for symbol in frame["symbol"].astype(str)):
            raise ValueError("benchmark cannot be emitted as a research observation")
        audit_map = {item.session_date: item for item in audits}
        if any(session not in audit_map for session in frame["session_date"].astype(str)):
            raise ValueError("observation row has no benchmark-session audit")
        available_total = missing_total = 0
        for audit in audits:
            rows = frame.loc[frame["session_date"].astype(str) == audit.session_date]
            members = tuple(rows["symbol"].astype(str))
            if members != tuple(sorted(members)):
                raise ValueError("session members must be sorted")
            if len(members) != audit.membership_count or membership_set_hash(members) != audit.membership_set_hash:
                raise ValueError("session membership count/hash is inconsistent")
            if tuple(rows["member_ordinal"].astype(int)) != tuple(range(len(rows))):
                raise ValueError("member ordinals must be zero-based and contiguous")
            if not rows.empty:
                if not bool((rows["membership_count"].astype(int) == audit.membership_count).all()):
                    raise ValueError("row membership count is inconsistent")
                if not bool((rows["membership_set_hash"].astype(str) == audit.membership_set_hash).all()):
                    raise ValueError("row membership hash is inconsistent")
            available = rows["market_row_available"].astype(bool)
            statuses = rows["availability"].astype(str)
            expected_status = available.map({
                True: ObservationAvailability.AVAILABLE.value,
                False: ObservationAvailability.MISSING_EXACT_MARKET_ROW.value,
            })
            if not statuses.reset_index(drop=True).equals(expected_status.reset_index(drop=True)):
                raise ValueError("market availability flag/status is inconsistent")
            available_rows = rows.loc[available]
            missing_rows = rows.loc[~available]
            if not available_rows.empty:
                if not bool((available_rows["source_market_time"].astype(str) == audit.session_date).all()):
                    raise ValueError("available source market time must equal its session date")
                if available_rows["source_row_ordinal"].isna().any() or bool((available_rows["source_row_ordinal"].astype(int) < 0).any()):
                    raise ValueError("available rows require non-negative source ordinals")
            if not missing_rows.empty and (
                missing_rows["source_market_time"].notna().any()
                or missing_rows["source_row_ordinal"].notna().any()
            ):
                raise ValueError("missing rows must have nullable source evidence")
            if (len(available_rows), len(missing_rows)) != (audit.available_row_count, audit.missing_row_count):
                raise ValueError("session availability counts are inconsistent")
            available_total += len(available_rows)
            missing_total += len(missing_rows)
        total = len(frame)
        if total != sum(item.emitted_observation_row_count for item in audits):
            raise ValueError("global observation row count does not reconcile")
        if available_total + missing_total != total:
            raise ValueError("global availability counts do not reconcile")
        symbols = tuple(sorted(set(frame["symbol"].astype(str))))
        if tuple(self.symbols) != symbols:
            raise ValueError("ordered symbol tuple is inconsistent with observation rows")
        content_identity = observation_content_identity(audits, frame)
        effective_first, effective_last = audit_dates[0], audit_dates[-1]
        payload = {
            "contract": {"name": self.spec.name, "version": self.spec.version},
            "snapshot_id": self.snapshot_id,
            "universe_membership_identity": self.universe_membership_identity,
            "specification_fingerprint": self.spec.fingerprint,
            "requested_start_date": self.requested_start_date,
            "requested_through_date": self.requested_through_date,
            "benchmark_symbol": benchmark,
            "effective_first_session_date": effective_first,
            "effective_last_session_date": effective_last,
            "benchmark_session_count": len(audits),
            "total_membership_row_count": total,
            "available_row_count": available_total,
            "missing_row_count": missing_total,
            "distinct_member_symbol_count": len(symbols),
            "content_identity": content_identity,
        }
        object.__setattr__(self, "benchmark_symbol", benchmark)
        object.__setattr__(self, "session_audit", audits)
        object.__setattr__(self, "symbols", symbols)
        object.__setattr__(self, "_frame", frame)
        object.__setattr__(self, "effective_first_session_date", effective_first)
        object.__setattr__(self, "effective_last_session_date", effective_last)
        object.__setattr__(self, "benchmark_session_count", len(audits))
        object.__setattr__(self, "total_membership_row_count", total)
        object.__setattr__(self, "available_row_count", available_total)
        object.__setattr__(self, "missing_row_count", missing_total)
        object.__setattr__(self, "distinct_member_symbol_count", len(symbols))
        object.__setattr__(self, "content_identity", content_identity)
        object.__setattr__(self, "identity", sha256(canonical_json(canonical_identity_value(payload))).hexdigest())

    @property
    def frame(self) -> pd.DataFrame:
        return self._frame.copy(deep=True)

    @property
    def specification_fingerprint(self) -> str:
        return self.spec.fingerprint
