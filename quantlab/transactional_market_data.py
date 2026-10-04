from __future__ import annotations

"""Prospective transactional market-data foundation.

Nothing in production imports this module.  It operates only on an explicitly
supplied SQLite connection or path and never opens the canonical database by
default.
"""

from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import math
from numbers import Real
from pathlib import Path
import re
import shutil
import sqlite3
from typing import Any, Iterator

from quantlab.completed_session import (
    CompletedSessionDecision,
    CompletedSessionResult,
    SymbolSessionStatus,
)
from quantlab.market_data_operation_identity import (
    OperationIdentityAllocation,
    OperationIdentityConflict,
    OperationIdentityRequest,
    allocate_operation_identity_in_transaction,
    create_operation_identity_schema,
)
from quantlab.operational_admission import (
    IngestionIntent,
    OPERATIONAL_ONLY,
    POLICY,
    UNKNOWN,
    OperationalAdmission,
    OperationalAdmissionResult,
    OperationalReason,
    SymbolIdentityState,
    evaluate_operational_admission,
    historical_revision_review,
)
from quantlab.preupdate_market_data_guard import (
    AdjustmentBasis,
    BoundaryDiagnostic,
    BoundaryEvidence,
    CoverageMetadata,
    EvidenceKind,
    FieldChange,
    GuardDecision,
    GuardReason,
    GuardResult,
    PriceBasisMetadata,
    PriceBasisState,
    RevisionClaim,
    RevisionEvidence,
    evaluate_preupdate_market_data,
)


SCHEMA_VERSION = "d4b1-v1"
D4B2_MIGRATION_ID = "d4b2-operational-admission-v1"
D4B26_MIGRATION_ID = "d4b2.6-session-operation-identity-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AttributionState(str, Enum):
    VERIFIED = "VERIFIED"
    UNKNOWN = "UNKNOWN"


class ArchiveKind(str, Enum):
    ORIGINAL_PROVIDER_PAYLOAD = "ORIGINAL_PROVIDER_PAYLOAD"
    NORMALIZED_CANDIDATE = "NORMALIZED_CANDIDATE"
    OTHER = "OTHER"


class ReceiptStatus(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class ImmutableArchiveIdentity:
    reference: str
    content_sha256: str
    kind: ArchiveKind

    def __post_init__(self) -> None:
        reference = _text(self.reference, "archive reference")
        digest = str(self.content_sha256).strip().lower()
        if not _SHA256.fullmatch(digest):
            raise ValueError("archive content hash must be a lowercase SHA-256 digest")
        object.__setattr__(self, "reference", reference)
        object.__setattr__(self, "content_sha256", digest)


@dataclass(frozen=True, slots=True)
class PreparedPriceRow:
    symbol: str
    session: date
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        symbol = _symbol(self.symbol)
        if not isinstance(self.session, date) or isinstance(self.session, datetime):
            raise ValueError("prepared session must be a date")
        values = tuple(_finite_number(getattr(self, name), name) for name in (
            "open", "high", "low", "close", "volume"
        ))
        open_value, high, low, close, volume = values
        if min(open_value, high, low, close) <= 0 or volume < 0:
            raise ValueError("OHLC must be positive and volume non-negative")
        if high < low or not low <= open_value <= high or not low <= close <= high:
            raise ValueError("OHLC envelope is invalid")
        object.__setattr__(self, "symbol", symbol)
        for name, value in zip(("open", "high", "low", "close", "volume"), values):
            object.__setattr__(self, name, value)

    def as_guard_row(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "time": self.session.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }

    def as_identity_dict(self) -> dict[str, object]:
        return self.as_guard_row()


@dataclass(frozen=True, slots=True)
class PreparedPriceBatch:
    operation_id: str
    symbol: str
    requested_start: date
    requested_end: date
    rows: tuple[PreparedPriceRow, ...]
    coverage: CoverageMetadata
    provider_identity: str
    endpoint_identity: str
    package_name: str
    package_version: str
    source_verification_state: AttributionState
    source_references: tuple[str, ...]
    price_unit: str
    price_unit_verification_state: AttributionState
    price_unit_references: tuple[str, ...]
    claimed_adjustment_basis: AdjustmentBasis
    adjustment_verification_state: AttributionState
    adjustment_evidence_references: tuple[str, ...]
    archive: ImmutableArchiveIdentity | None = None
    revision_evidence: tuple[RevisionEvidence, ...] = ()
    boundary_evidence: tuple[BoundaryEvidence, ...] = ()
    ingestion_intent: IngestionIntent = IngestionIntent.UNSPECIFIED
    symbol_identity_state: SymbolIdentityState = SymbolIdentityState.UNCERTAIN
    symbol_identity_references: tuple[str, ...] = ()
    completed_through: date | None = None
    completed_session_result: CompletedSessionResult | None = None
    corporate_action_verification_state: AttributionState = AttributionState.UNKNOWN
    normalized_content_sha256: str = field(init=False)
    batch_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        operation_id = _text(self.operation_id, "operation id")
        symbol = _symbol(self.symbol)
        if (
            not isinstance(self.requested_start, date)
            or isinstance(self.requested_start, datetime)
            or not isinstance(self.requested_end, date)
            or isinstance(self.requested_end, datetime)
            or self.requested_start > self.requested_end
        ):
            raise ValueError("prepared request range is invalid")
        rows = tuple(sorted(self.rows, key=lambda item: item.session))
        if not rows:
            raise ValueError("prepared batch must contain at least one row")
        if any(row.symbol != symbol for row in rows):
            raise ValueError("prepared rows must match the batch symbol")
        if len({row.session for row in rows}) != len(rows):
            raise ValueError("prepared rows must be unique by session")
        if any(not self.requested_start <= row.session <= self.requested_end for row in rows):
            raise ValueError("prepared rows fall outside the requested range")
        if (
            self.coverage.requested_start != self.requested_start
            or self.coverage.requested_end != self.requested_end
        ):
            raise ValueError("coverage metadata does not match the prepared request")

        source_references = _references(self.source_references)
        unit_references = _references(self.price_unit_references)
        adjustment_references = _references(self.adjustment_evidence_references)
        identity_references = _references(self.symbol_identity_references)
        if self.source_verification_state is AttributionState.VERIFIED and not source_references:
            raise ValueError("verified provider source requires attributable references")
        if self.price_unit_verification_state is AttributionState.VERIFIED and not unit_references:
            raise ValueError("verified price unit requires attributable references")
        if self.adjustment_verification_state is AttributionState.VERIFIED and (
            self.claimed_adjustment_basis is AdjustmentBasis.UNKNOWN
            or not adjustment_references
        ):
            raise ValueError("verified adjustment basis requires attributable evidence")
        if self.symbol_identity_state in {
            SymbolIdentityState.VERIFIED_STABLE,
            SymbolIdentityState.CONSISTENT_REQUEST_SYMBOL,
        } and not identity_references:
            raise ValueError("non-uncertain symbol identity requires attributable references")
        if self.completed_through is not None and (
            not isinstance(self.completed_through, date)
            or isinstance(self.completed_through, datetime)
        ):
            raise ValueError("completed_through must be a date")
        if self.completed_session_result is not None:
            completed = self.completed_session_result
            if (
                completed.symbol != symbol
                or completed.provider_identity != _text(self.provider_identity, "provider identity")
            ):
                raise ValueError("completed-session identities must match the prepared batch")
            if completed.target_session > self.requested_end:
                raise ValueError("completed-session target falls outside the prepared request")
            if completed.decision is CompletedSessionDecision.ADMITTED:
                if completed.symbol_session_status is not SymbolSessionStatus.TRADING_CONFIRMED:
                    raise ValueError(
                        "admitted completed session requires confirmed symbol-session evidence"
                    )
                target_rows = tuple(row for row in rows if row.session == completed.target_session)
                if len(target_rows) != 1:
                    raise ValueError("admitted completed session requires exactly one target row")
                if _hash(target_rows[0].as_identity_dict()) not in completed.normalized_row_fingerprints:
                    raise ValueError("completed-session fingerprint does not match the prepared target row")
                if self.completed_through is not None and self.completed_through != completed.target_session:
                    raise ValueError("completed-through conflicts with completed-session evidence")
                object.__setattr__(self, "completed_through", completed.target_session)

        revision_evidence = tuple(sorted(
            self.revision_evidence,
            key=lambda item: (item.symbol, item.kind.value, tuple(
                (claim.session, claim.field) for claim in item.claims
            )),
        ))
        boundary_evidence = tuple(sorted(
            self.boundary_evidence,
            key=lambda item: (item.symbol, item.previous_session, item.incoming_session),
        ))
        if any(item.symbol != symbol for item in (*revision_evidence, *boundary_evidence)):
            raise ValueError("evidence symbols must match the prepared batch")

        object.__setattr__(self, "operation_id", operation_id)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "rows", rows)
        object.__setattr__(self, "provider_identity", _text(self.provider_identity, "provider identity"))
        object.__setattr__(self, "endpoint_identity", _text(self.endpoint_identity, "endpoint identity"))
        object.__setattr__(self, "package_name", _text(self.package_name, "package name"))
        object.__setattr__(self, "package_version", _text(self.package_version, "package version"))
        object.__setattr__(self, "source_references", source_references)
        object.__setattr__(self, "price_unit", _text(self.price_unit, "price unit"))
        object.__setattr__(self, "price_unit_references", unit_references)
        object.__setattr__(self, "adjustment_evidence_references", adjustment_references)
        object.__setattr__(self, "symbol_identity_references", identity_references)
        object.__setattr__(self, "revision_evidence", revision_evidence)
        object.__setattr__(self, "boundary_evidence", boundary_evidence)

        normalized_hash = _hash([row.as_identity_dict() for row in rows])
        object.__setattr__(self, "normalized_content_sha256", normalized_hash)
        object.__setattr__(self, "batch_fingerprint", _hash(self.identity_payload()))

    @property
    def raw_payload_sha256(self) -> str | None:
        if self.archive is None or self.archive.kind is not ArchiveKind.ORIGINAL_PROVIDER_PAYLOAD:
            return None
        return self.archive.content_sha256

    @property
    def source_attributable(self) -> bool:
        return bool(
            self.operation_id
            and self.batch_fingerprint
            and self.provider_identity
            and self.endpoint_identity
            and self.package_name
            and self.package_version
            and self.source_verification_state is AttributionState.VERIFIED
            and self.source_references
        )

    def guard_price_basis(self) -> PriceBasisMetadata:
        verified = (
            self.source_verification_state is AttributionState.VERIFIED
            and self.price_unit_verification_state is AttributionState.VERIFIED
            and self.adjustment_verification_state is AttributionState.VERIFIED
            and self.claimed_adjustment_basis is not AdjustmentBasis.UNKNOWN
        )
        if not verified:
            return PriceBasisMetadata(
                self.price_unit,
                AdjustmentBasis.UNKNOWN,
                PriceBasisState.UNKNOWN,
            )
        references = tuple(sorted({
            *self.source_references,
            *self.price_unit_references,
            *self.adjustment_evidence_references,
        }))
        return PriceBasisMetadata(
            self.price_unit,
            self.claimed_adjustment_basis,
            PriceBasisState.VERIFIED,
            references,
        )

    def identity_payload(self) -> dict[str, object]:
        return {
            "contract": ["quantlab.prepared_price_batch", SCHEMA_VERSION],
            "symbol": self.symbol,
            "requested_start": self.requested_start.isoformat(),
            "requested_end": self.requested_end.isoformat(),
            "normalized_content_sha256": self.normalized_content_sha256,
            "coverage": {
                "requested_start": self.coverage.requested_start.isoformat(),
                "requested_end": self.coverage.requested_end.isoformat(),
                "returned_start": None if self.coverage.returned_start is None else self.coverage.returned_start.isoformat(),
                "returned_end": None if self.coverage.returned_end is None else self.coverage.returned_end.isoformat(),
                "state": self.coverage.state.value,
                "source_references": self.coverage.source_references,
            },
            "provider": {
                "identity": self.provider_identity,
                "endpoint": self.endpoint_identity,
                "package": self.package_name,
                "package_version": self.package_version,
                "verification_state": self.source_verification_state.value,
                "source_references": self.source_references,
            },
            "price_basis": {
                "unit": self.price_unit,
                "unit_verification_state": self.price_unit_verification_state.value,
                "unit_references": self.price_unit_references,
                "claimed_adjustment_basis": self.claimed_adjustment_basis.value,
                "adjustment_verification_state": self.adjustment_verification_state.value,
                "adjustment_evidence_references": self.adjustment_evidence_references,
            },
            "archive": None if self.archive is None else {
                "reference": self.archive.reference,
                "content_sha256": self.archive.content_sha256,
                "kind": self.archive.kind.value,
            },
            "revision_evidence": [_revision_evidence_dict(item) for item in self.revision_evidence],
            "boundary_evidence": [_boundary_evidence_dict(item) for item in self.boundary_evidence],
            "operational_context": {
                "ingestion_intent": self.ingestion_intent.value,
                "symbol_identity_state": self.symbol_identity_state.value,
                "symbol_identity_references": self.symbol_identity_references,
                "completed_through": None if self.completed_through is None else self.completed_through.isoformat(),
                "completed_session_result": (
                    None if self.completed_session_result is None
                    else self.completed_session_result.as_dict()
                ),
                "corporate_action_verification_state": self.corporate_action_verification_state.value,
            },
        }


@dataclass(frozen=True, slots=True)
class CommitPriceBatchResult:
    operation_id: str
    batch_fingerprint: str
    status: ReceiptStatus
    guard_result: GuardResult
    rows_written: int
    idempotent_replay: bool
    revision_review_required: bool = False


@dataclass(frozen=True, slots=True)
class ShadowCommitResult:
    operation_id: str
    batch_fingerprint: str
    status: ReceiptStatus
    guard_result: GuardResult
    operational_result: OperationalAdmissionResult
    rows_written: int
    idempotent_replay: bool
    completed_session_result: CompletedSessionResult | None = None
    operation_identity: OperationIdentityAllocation | None = None


@dataclass(frozen=True, slots=True)
class MigrationRehearsalResult:
    source_sha256: str
    clone_before_sha256: str
    clone_migrated_sha256: str
    clone_restored_sha256: str
    migration_recorded: bool
    unique_price_index_valid: bool
    foreign_key_violations: tuple[tuple[object, ...], ...]

    @property
    def rollback_restored_bytes(self) -> bool:
        return self.clone_before_sha256 == self.clone_restored_sha256


def _text(value: object, name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be non-empty")
    return normalized


def _symbol(value: object) -> str:
    return _text(value, "symbol").upper()


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    return normalized


def _references(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({_text(value, "source reference") for value in values}))


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: object) -> str:
    return sha256(_json(value).encode("utf-8")).hexdigest()


def _revision_evidence_dict(value: RevisionEvidence) -> dict[str, object]:
    return {
        "symbol": value.symbol,
        "kind": value.kind.value,
        "claims": [
            {
                "session": claim.session.isoformat(),
                "field": claim.field,
                "existing_value": claim.existing_value,
                "incoming_value": claim.incoming_value,
            }
            for claim in value.claims
        ],
        "source_references": value.source_references,
    }


def _boundary_evidence_dict(value: BoundaryEvidence) -> dict[str, object]:
    return {
        "symbol": value.symbol,
        "kind": value.kind.value,
        "previous_session": value.previous_session.isoformat(),
        "incoming_session": value.incoming_session.isoformat(),
        "previous_close": value.previous_close,
        "incoming_open": value.incoming_open,
        "source_references": value.source_references,
    }


def initialize_transactional_ingestion_schema(connection: sqlite3.Connection) -> None:
    """Create only the D4B1 receipt/provenance tables on an explicit database."""
    if connection.in_transaction:
        raise ValueError("schema initialization requires an idle SQLite connection")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS market_ingestion_receipts (
            operation_id TEXT PRIMARY KEY,
            batch_fingerprint TEXT NOT NULL,
            symbol TEXT NOT NULL,
            requested_start TEXT NOT NULL,
            requested_end TEXT NOT NULL,
            decision TEXT NOT NULL CHECK (
                decision IN ('PASS', 'BLOCK', 'INSUFFICIENT_EVIDENCE')
            ),
            status TEXT NOT NULL CHECK (status IN ('APPROVED', 'REJECTED')),
            reason_codes_json TEXT NOT NULL,
            guard_result_json TEXT NOT NULL,
            rows_written INTEGER NOT NULL CHECK (rows_written >= 0),
            created_at_utc TEXT NOT NULL,
            UNIQUE(operation_id, batch_fingerprint)
        );

        CREATE TABLE IF NOT EXISTS market_ingestion_manifests (
            operation_id TEXT PRIMARY KEY
                REFERENCES market_ingestion_receipts(operation_id) ON DELETE RESTRICT,
            batch_fingerprint TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            symbol TEXT NOT NULL,
            requested_start TEXT NOT NULL,
            requested_end TEXT NOT NULL,
            returned_start TEXT,
            returned_end TEXT,
            row_count INTEGER NOT NULL CHECK (row_count > 0),
            normalized_content_sha256 TEXT NOT NULL,
            provider_identity TEXT NOT NULL,
            endpoint_identity TEXT NOT NULL,
            package_name TEXT NOT NULL,
            package_version TEXT NOT NULL,
            source_verification_state TEXT NOT NULL,
            source_references_json TEXT NOT NULL,
            price_unit TEXT NOT NULL,
            price_unit_verification_state TEXT NOT NULL,
            price_unit_references_json TEXT NOT NULL,
            claimed_adjustment_basis TEXT NOT NULL,
            adjustment_verification_state TEXT NOT NULL,
            adjustment_evidence_references_json TEXT NOT NULL,
            archive_reference TEXT,
            archive_content_sha256 TEXT,
            archive_kind TEXT,
            raw_payload_sha256 TEXT,
            normalized_payload_is_raw INTEGER NOT NULL CHECK (normalized_payload_is_raw = 0),
            FOREIGN KEY (operation_id, batch_fingerprint)
                REFERENCES market_ingestion_receipts(operation_id, batch_fingerprint)
                DEFERRABLE INITIALLY DEFERRED,
            UNIQUE(operation_id, batch_fingerprint)
        );

        CREATE TABLE IF NOT EXISTS market_price_provenance (
            symbol TEXT NOT NULL,
            time TEXT NOT NULL,
            operation_id TEXT NOT NULL,
            batch_fingerprint TEXT NOT NULL,
            PRIMARY KEY (symbol, time),
            FOREIGN KEY (operation_id)
                REFERENCES market_ingestion_manifests(operation_id) ON DELETE RESTRICT,
            FOREIGN KEY (symbol, time)
                REFERENCES prices(symbol, time) ON DELETE RESTRICT
        );
        """
    )
    connection.commit()


_RECEIPT_D4B2_COLUMNS = {
    "operational_policy": "TEXT",
    "operational_admission": "TEXT",
    "operational_reason_codes_json": "TEXT",
    "operational_result_json": "TEXT",
    "data_class": "TEXT",
    "research_eligible": "INTEGER",
    "adjustment_mode": "TEXT",
    "corporate_action_verification": "TEXT",
    "historical_anchor_dates_json": "TEXT",
}

_MANIFEST_D4B2_COLUMNS = {
    "data_class": "TEXT",
    "research_eligible": "INTEGER",
    "corporate_action_verification": "TEXT",
    "symbol_identity_state": "TEXT",
    "symbol_identity_references_json": "TEXT",
    "ingestion_intent": "TEXT",
    "completed_through": "TEXT",
}

_RECEIPT_D4B26_COLUMNS = {
    "request_identity": "TEXT",
    "observation_generation": "INTEGER",
    "revision_of": "TEXT",
    "completed_session_result_json": "TEXT",
}


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _has_unique_price_session_index(connection: sqlite3.Connection) -> bool:
    for row in connection.execute("PRAGMA index_list(prices)"):
        if not bool(row[2]):
            continue
        columns = tuple(
            value[2] for value in connection.execute(f"PRAGMA index_info({row[1]})")
        )
        if columns == ("symbol", "time"):
            return True
    return False


def migrate_operational_admission_schema(
    connection: sqlite3.Connection,
    *,
    failure_injector: Callable[[str], None] | None = None,
) -> bool:
    """Apply the additive D4B2 schema on an explicit, idle SQLite connection."""
    if connection.in_transaction:
        raise ValueError("operational migration requires an idle SQLite connection")
    inject = failure_injector or (lambda _stage: None)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        connection.execute("BEGIN IMMEDIATE")
        if not _has_unique_price_session_index(connection):
            raise RuntimeError("prices requires a unique (symbol, time) index")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS market_schema_migrations (
                migration_id TEXT PRIMARY KEY,
                applied_at_utc TEXT NOT NULL
            )
            """
        )
        already_applied = connection.execute(
            "SELECT 1 FROM market_schema_migrations WHERE migration_id=?",
            (D4B2_MIGRATION_ID,),
        ).fetchone() is not None

        receipt_columns = _table_columns(connection, "market_ingestion_receipts")
        for name, definition in _RECEIPT_D4B2_COLUMNS.items():
            if name not in receipt_columns:
                connection.execute(
                    f"ALTER TABLE market_ingestion_receipts ADD COLUMN {name} {definition}"
                )
        inject("after_receipt_columns")

        manifest_columns = _table_columns(connection, "market_ingestion_manifests")
        for name, definition in _MANIFEST_D4B2_COLUMNS.items():
            if name not in manifest_columns:
                connection.execute(
                    f"ALTER TABLE market_ingestion_manifests ADD COLUMN {name} {definition}"
                )
        inject("after_manifest_columns")

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS market_ingestion_staging (
                operation_id TEXT PRIMARY KEY
                    REFERENCES market_ingestion_receipts(operation_id) ON DELETE RESTRICT,
                batch_fingerprint TEXT NOT NULL,
                symbol TEXT NOT NULL,
                ingestion_intent TEXT NOT NULL,
                staging_outcome TEXT NOT NULL,
                normalized_content_sha256 TEXT NOT NULL,
                normalized_rows_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                data_class TEXT NOT NULL,
                research_eligible INTEGER NOT NULL CHECK (research_eligible = 0),
                created_at_utc TEXT NOT NULL
            )
            """
        )
        inject("after_staging_table")
        if not already_applied:
            connection.execute(
                "INSERT INTO market_schema_migrations VALUES (?, ?)",
                (
                    D4B2_MIGRATION_ID,
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                ),
            )
        violations = tuple(connection.execute("PRAGMA foreign_key_check"))
        if violations:
            raise RuntimeError(f"foreign-key violations after migration: {violations!r}")
        inject("before_migration_commit")
        connection.commit()
        return not already_applied
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise


def migrate_shadow_runtime_foundation_schema(
    connection: sqlite3.Connection,
    *,
    failure_injector: Callable[[str], None] | None = None,
) -> bool:
    """Apply the additive D4B2.6 runtime foundation on an idle shadow database."""
    if connection.in_transaction:
        raise ValueError("shadow runtime migration requires an idle SQLite connection")
    inject = failure_injector or (lambda _stage: None)
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        connection.execute("BEGIN IMMEDIATE")
        required = {"market_ingestion_receipts", "market_ingestion_manifests"}
        existing = {
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not required <= existing:
            raise RuntimeError("D4B2 schema must exist before D4B2.6 migration")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS market_schema_migrations (
                migration_id TEXT PRIMARY KEY,
                applied_at_utc TEXT NOT NULL
            )
            """
        )
        already_applied = connection.execute(
            "SELECT 1 FROM market_schema_migrations WHERE migration_id=?",
            (D4B26_MIGRATION_ID,),
        ).fetchone() is not None
        receipt_columns = _table_columns(connection, "market_ingestion_receipts")
        for name, definition in _RECEIPT_D4B26_COLUMNS.items():
            if name not in receipt_columns:
                connection.execute(
                    f"ALTER TABLE market_ingestion_receipts ADD COLUMN {name} {definition}"
                )
        inject("after_runtime_receipt_columns")
        create_operation_identity_schema(connection)
        inject("after_operation_identity_tables")
        if not already_applied:
            connection.execute(
                "INSERT INTO market_schema_migrations VALUES (?, ?)",
                (
                    D4B26_MIGRATION_ID,
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                ),
            )
        violations = tuple(connection.execute("PRAGMA foreign_key_check"))
        if violations:
            raise RuntimeError(f"foreign-key violations after runtime migration: {violations!r}")
        inject("before_runtime_migration_commit")
        connection.commit()
        return not already_applied
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rehearse_operational_migration(
    source_database: str | Path,
    rehearsal_directory: str | Path,
) -> MigrationRehearsalResult:
    """Migrate and byte-restore a disposable clone; never modifies the source."""
    source = Path(source_database).resolve()
    destination = Path(rehearsal_directory).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    clone = destination / "market.shadow-migration.db"
    backup = destination / "market.shadow-migration.pre-d4b2.db"
    if clone.exists() or backup.exists():
        raise FileExistsError("migration rehearsal outputs already exist")
    shutil.copy2(source, clone)
    shutil.copy2(source, backup)
    source_hash = _file_sha256(source)
    clone_before_hash = _file_sha256(clone)
    connection = sqlite3.connect(clone)
    try:
        foundation_exists = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='market_ingestion_receipts'
            """
        ).fetchone() is not None
        if not foundation_exists:
            initialize_transactional_ingestion_schema(connection)
        migrate_operational_admission_schema(connection)
        migrate_operational_admission_schema(connection)
        migration_recorded = connection.execute(
            "SELECT COUNT(*) FROM market_schema_migrations WHERE migration_id=?",
            (D4B2_MIGRATION_ID,),
        ).fetchone()[0] == 1
        unique_valid = _has_unique_price_session_index(connection)
        violations = tuple(tuple(row) for row in connection.execute("PRAGMA foreign_key_check"))
    finally:
        connection.close()
    migrated_hash = _file_sha256(clone)
    shutil.copy2(backup, clone)
    restored_hash = _file_sha256(clone)
    return MigrationRehearsalResult(
        source_hash,
        clone_before_hash,
        migrated_hash,
        restored_hash,
        migration_recorded,
        unique_valid,
        violations,
    )


@contextmanager
def _connection(
    target: sqlite3.Connection | str | Path,
    *,
    busy_timeout_seconds: float,
) -> Iterator[sqlite3.Connection]:
    owns_connection = not isinstance(target, sqlite3.Connection)
    if owns_connection:
        connection = sqlite3.connect(str(target), timeout=busy_timeout_seconds)
    else:
        connection = target
    try:
        if connection.in_transaction:
            raise ValueError("commit_price_batch requires an idle SQLite connection")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {max(0, int(busy_timeout_seconds * 1000))}")
        yield connection
    finally:
        if owns_connection:
            connection.close()


def _persisted_rows(connection: sqlite3.Connection, symbol: str) -> tuple[dict[str, object], ...]:
    rows = connection.execute(
        """
        SELECT symbol, time, open, high, low, close, volume
        FROM prices
        WHERE symbol = ?
        ORDER BY time
        """,
        (symbol,),
    ).fetchall()
    return tuple({
        "symbol": row[0],
        "time": row[1],
        "open": row[2],
        "high": row[3],
        "low": row[4],
        "close": row[5],
        "volume": row[6],
    } for row in rows)


def _relevant_existing_sessions(
    existing_rows: Sequence[Mapping[str, object]],
    incoming_rows: Sequence[Mapping[str, object]],
) -> tuple[str, ...]:
    existing_dates = tuple(str(row["time"]) for row in existing_rows)
    incoming_dates = tuple(str(row["time"]) for row in incoming_rows)
    relevant = set(existing_dates) & set(incoming_dates)
    if existing_dates and incoming_dates:
        first_new = next((value for value in incoming_dates if value > existing_dates[-1]), None)
        if first_new is not None:
            prior = tuple(value for value in existing_dates if value < first_new)
            if prior:
                relevant.add(prior[-1])
    return tuple(sorted(relevant))


def _persisted_price_basis(
    connection: sqlite3.Connection,
    symbol: str,
    sessions: Sequence[str],
) -> PriceBasisMetadata:
    if not sessions:
        return PriceBasisMetadata("UNKNOWN", AdjustmentBasis.UNKNOWN, PriceBasisState.UNKNOWN)
    placeholders = ",".join("?" for _ in sessions)
    rows = connection.execute(
        f"""
        SELECT p.time, m.operation_id, m.price_unit,
               m.source_verification_state,
               m.price_unit_verification_state,
               m.claimed_adjustment_basis,
               m.adjustment_verification_state
        FROM market_price_provenance AS p
        JOIN market_ingestion_manifests AS m ON m.operation_id = p.operation_id
        WHERE p.symbol = ? AND p.time IN ({placeholders})
        ORDER BY p.time
        """,
        (symbol, *sessions),
    ).fetchall()
    if len(rows) != len(sessions):
        return PriceBasisMetadata("UNKNOWN", AdjustmentBasis.UNKNOWN, PriceBasisState.UNKNOWN)
    if any(
        row[3] != AttributionState.VERIFIED.value
        or row[4] != AttributionState.VERIFIED.value
        or row[6] != AttributionState.VERIFIED.value
        for row in rows
    ):
        return PriceBasisMetadata("UNKNOWN", AdjustmentBasis.UNKNOWN, PriceBasisState.UNKNOWN)
    units = {row[2] for row in rows}
    bases = {row[5] for row in rows}
    if len(units) != 1 or len(bases) != 1 or AdjustmentBasis.UNKNOWN.value in bases:
        return PriceBasisMetadata("UNKNOWN", AdjustmentBasis.UNKNOWN, PriceBasisState.UNKNOWN)
    operation_references = tuple(sorted({f"market-ingestion-operation:{row[1]}" for row in rows}))
    return PriceBasisMetadata(
        units.pop(),
        AdjustmentBasis(bases.pop()),
        PriceBasisState.VERIFIED,
        operation_references,
    )


def _guard_result_dict(result: GuardResult) -> dict[str, object]:
    return {
        "decision": result.decision.value,
        "reasons": [reason.value for reason in result.reasons],
        "symbol": result.symbol,
        "requested_start": None if result.requested_start is None else result.requested_start.isoformat(),
        "requested_end": None if result.requested_end is None else result.requested_end.isoformat(),
        "overlap_dates": [value.isoformat() for value in result.overlap_dates],
        "revisions": [
            {
                "session": value.session.isoformat(),
                "field": value.field,
                "existing_value": value.existing_value,
                "incoming_value": value.incoming_value,
            }
            for value in result.revisions
        ],
        "genuinely_new_dates": [value.isoformat() for value in result.genuinely_new_dates],
        "historical_insert_dates": [value.isoformat() for value in result.historical_insert_dates],
        "boundary": None if result.boundary is None else {
            "previous_session": result.boundary.previous_session.isoformat(),
            "incoming_session": result.boundary.incoming_session.isoformat(),
            "previous_close": result.boundary.previous_close,
            "incoming_open": result.boundary.incoming_open,
            "absolute_return": result.boundary.absolute_return,
            "attributable": result.boundary.attributable,
        },
    }


def _guard_result_from_json(payload: str) -> GuardResult:
    value = json.loads(payload)
    boundary_value = value["boundary"]
    boundary = None if boundary_value is None else BoundaryDiagnostic(
        date.fromisoformat(boundary_value["previous_session"]),
        date.fromisoformat(boundary_value["incoming_session"]),
        boundary_value["previous_close"],
        boundary_value["incoming_open"],
        boundary_value["absolute_return"],
        boundary_value["attributable"],
    )
    return GuardResult(
        GuardDecision(value["decision"]),
        tuple(GuardReason(item) for item in value["reasons"]),
        value["symbol"],
        None if value["requested_start"] is None else date.fromisoformat(value["requested_start"]),
        None if value["requested_end"] is None else date.fromisoformat(value["requested_end"]),
        tuple(date.fromisoformat(item) for item in value["overlap_dates"]),
        tuple(FieldChange(
            date.fromisoformat(item["session"]),
            item["field"],
            item["existing_value"],
            item["incoming_value"],
        ) for item in value["revisions"]),
        tuple(date.fromisoformat(item) for item in value["genuinely_new_dates"]),
        tuple(date.fromisoformat(item) for item in value["historical_insert_dates"]),
        boundary,
    )


def _operational_result_dict(result: OperationalAdmissionResult) -> dict[str, object]:
    return {
        "policy": result.policy,
        "admission": result.admission.value,
        "reasons": [reason.value for reason in result.reasons],
        "guard_decision": result.guard_decision.value,
        "guard_reasons": [reason.value for reason in result.guard_reasons],
        "historical_anchor_dates": result.historical_anchor_dates,
        "new_session_dates": result.new_session_dates,
        "limitations": result.limitations,
        "data_class": result.data_class,
        "research_eligible": result.research_eligible,
        "adjustment_mode": result.adjustment_mode,
        "corporate_action_verification": result.corporate_action_verification,
    }


def _operational_result_from_json(payload: str) -> OperationalAdmissionResult:
    value = json.loads(payload)
    return OperationalAdmissionResult(
        value["policy"],
        OperationalAdmission(value["admission"]),
        tuple(OperationalReason(item) for item in value["reasons"]),
        GuardDecision(value["guard_decision"]),
        tuple(GuardReason(item) for item in value["guard_reasons"]),
        tuple(value["historical_anchor_dates"]),
        tuple(value["new_session_dates"]),
        tuple(value["limitations"]),
        value["data_class"],
        bool(value["research_eligible"]),
        value["adjustment_mode"],
        value["corporate_action_verification"],
    )


def _existing_receipt(
    connection: sqlite3.Connection,
    batch: PreparedPriceBatch,
) -> CommitPriceBatchResult | None:
    row = connection.execute(
        """
        SELECT batch_fingerprint, status, guard_result_json, rows_written
        FROM market_ingestion_receipts
        WHERE operation_id = ?
        """,
        (batch.operation_id,),
    ).fetchone()
    if row is None:
        return None
    if row[0] != batch.batch_fingerprint:
        raise OperationIdentityConflict(
            f"operation id {batch.operation_id!r} already names fingerprint {row[0]}"
        )
    return CommitPriceBatchResult(
        batch.operation_id,
        batch.batch_fingerprint,
        ReceiptStatus(row[1]),
        _guard_result_from_json(row[2]),
        row[3],
        True,
        "revision_review" in json.loads(row[2]),
    )


def _existing_shadow_receipt(
    connection: sqlite3.Connection,
    batch: PreparedPriceBatch,
) -> ShadowCommitResult | None:
    row = connection.execute(
        """
        SELECT batch_fingerprint, status, guard_result_json, rows_written,
               operational_result_json
        FROM market_ingestion_receipts
        WHERE operation_id = ?
        """,
        (batch.operation_id,),
    ).fetchone()
    if row is None:
        return None
    if row[0] != batch.batch_fingerprint:
        raise OperationIdentityConflict(
            f"operation id {batch.operation_id!r} already names fingerprint {row[0]}"
        )
    guard = _guard_result_from_json(row[2])
    review = json.loads(row[2]).get("revision_review")
    if row[4] is None and review is not None:
        # New D4B1 rejected PASS receipts must not become shadow guard successes.
        # Receipt-only review remains rejected; replay does not create staging.
        operational = _operational_result_from_json(_json(review["operational_result"]))
    elif row[4] is None:
        operational = OperationalAdmissionResult(
            POLICY,
            (
                OperationalAdmission.NOT_REQUIRED_GUARD_PASS
                if guard.decision is GuardDecision.PASS
                else OperationalAdmission.OPERATIONAL_REJECTED
            ),
            (
                (OperationalReason.GUARD_PASS_CONTROLS_WRITE,)
                if guard.decision is GuardDecision.PASS
                else (OperationalReason.UNEXPECTED_GUARD_REASON,)
            ),
            guard.decision,
            guard.reasons,
            tuple(value.isoformat() for value in guard.overlap_dates),
            tuple(value.isoformat() for value in guard.genuinely_new_dates),
            ("PRE_D4B2_RECEIPT_NO_OPERATIONAL_DECISION",),
        )
    else:
        operational = _operational_result_from_json(row[4])
    return ShadowCommitResult(
        batch.operation_id,
        batch.batch_fingerprint,
        ReceiptStatus(row[1]),
        guard,
        operational,
        row[3],
        True,
    )


def _insert_receipt(
    connection: sqlite3.Connection,
    batch: PreparedPriceBatch,
    result: GuardResult,
    status: ReceiptStatus,
    rows_written: int,
    *,
    revision_review: dict[str, object] | None = None,
) -> None:
    result_payload = _guard_result_dict(result)
    if revision_review is not None:
        result_payload["revision_review"] = revision_review
    connection.execute(
        """
        INSERT INTO market_ingestion_receipts (
            operation_id, batch_fingerprint, symbol, requested_start,
            requested_end, decision, status, reason_codes_json,
            guard_result_json, rows_written, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            batch.operation_id,
            batch.batch_fingerprint,
            batch.symbol,
            batch.requested_start.isoformat(),
            batch.requested_end.isoformat(),
            result.decision.value,
            status.value,
            _json([reason.value for reason in result.reasons]),
            _json(result_payload),
            rows_written,
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        ),
    )


def _attach_operational_receipt(
    connection: sqlite3.Connection,
    batch: PreparedPriceBatch,
    result: OperationalAdmissionResult,
) -> None:
    payload = _operational_result_dict(result)
    connection.execute(
        """
        UPDATE market_ingestion_receipts
        SET operational_policy=?, operational_admission=?,
            operational_reason_codes_json=?, operational_result_json=?,
            data_class=?, research_eligible=?, adjustment_mode=?,
            corporate_action_verification=?, historical_anchor_dates_json=?
        WHERE operation_id=? AND batch_fingerprint=?
        """,
        (
            result.policy,
            result.admission.value,
            _json([reason.value for reason in result.reasons]),
            _json(payload),
            result.data_class,
            int(result.research_eligible),
            result.adjustment_mode,
            result.corporate_action_verification,
            _json(result.historical_anchor_dates),
            batch.operation_id,
            batch.batch_fingerprint,
        ),
    )


def _validate_operation_identity_request(
    batch: PreparedPriceBatch,
    request: OperationIdentityRequest,
) -> None:
    expected = (
        batch.symbol,
        batch.provider_identity,
        batch.endpoint_identity,
        batch.package_name,
        batch.package_version,
        batch.requested_start,
        batch.requested_end,
    )
    actual = (
        request.symbol,
        request.provider_identity,
        request.endpoint_identity,
        request.package_name,
        request.package_version,
        request.requested_start,
        request.requested_end,
    )
    if actual != expected:
        raise OperationIdentityConflict("operation request does not match prepared batch identity")
    completed = batch.completed_session_result
    if completed is None:
        raise OperationIdentityConflict("operation request requires completed-session evidence")
    if (
        request.target_session != completed.target_session
        or request.venue != completed.venue
        or request.calendar_identity != completed.calendar_identity
        or request.calendar_version != completed.calendar_version
        or request.calendar_snapshot_reference != completed.calendar_snapshot_reference
    ):
        raise OperationIdentityConflict("operation request does not match completed-session evidence")


def _attach_runtime_receipt(
    connection: sqlite3.Connection,
    batch: PreparedPriceBatch,
    allocation: OperationIdentityAllocation | None,
) -> None:
    completed_json = (
        None
        if batch.completed_session_result is None
        else _json(batch.completed_session_result.as_dict())
    )
    connection.execute(
        """
        UPDATE market_ingestion_receipts
        SET request_identity=?, observation_generation=?, revision_of=?,
            completed_session_result_json=?
        WHERE operation_id=? AND batch_fingerprint=?
        """,
        (
            None if allocation is None else allocation.request_identity,
            None if allocation is None else allocation.observation_generation,
            None if allocation is None else allocation.revision_of,
            completed_json,
            batch.operation_id,
            batch.batch_fingerprint,
        ),
    )


def _insert_manifest(connection: sqlite3.Connection, batch: PreparedPriceBatch) -> None:
    archive = batch.archive
    connection.execute(
        """
        INSERT INTO market_ingestion_manifests (
            operation_id, batch_fingerprint, schema_version, symbol,
            requested_start, requested_end, returned_start, returned_end,
            row_count, normalized_content_sha256, provider_identity,
            endpoint_identity, package_name, package_version,
            source_verification_state, source_references_json, price_unit,
            price_unit_verification_state, price_unit_references_json,
            claimed_adjustment_basis, adjustment_verification_state,
            adjustment_evidence_references_json, archive_reference,
            archive_content_sha256, archive_kind, raw_payload_sha256,
            normalized_payload_is_raw
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, 0
        )
        """,
        (
            batch.operation_id,
            batch.batch_fingerprint,
            SCHEMA_VERSION,
            batch.symbol,
            batch.requested_start.isoformat(),
            batch.requested_end.isoformat(),
            None if batch.coverage.returned_start is None else batch.coverage.returned_start.isoformat(),
            None if batch.coverage.returned_end is None else batch.coverage.returned_end.isoformat(),
            len(batch.rows),
            batch.normalized_content_sha256,
            batch.provider_identity,
            batch.endpoint_identity,
            batch.package_name,
            batch.package_version,
            batch.source_verification_state.value,
            _json(batch.source_references),
            batch.price_unit,
            batch.price_unit_verification_state.value,
            _json(batch.price_unit_references),
            batch.claimed_adjustment_basis.value,
            batch.adjustment_verification_state.value,
            _json(batch.adjustment_evidence_references),
            None if archive is None else archive.reference,
            None if archive is None else archive.content_sha256,
            None if archive is None else archive.kind.value,
            batch.raw_payload_sha256,
        ),
    )


def _attach_operational_manifest(
    connection: sqlite3.Connection,
    batch: PreparedPriceBatch,
    result: OperationalAdmissionResult,
) -> None:
    connection.execute(
        """
        UPDATE market_ingestion_manifests
        SET data_class=?, research_eligible=?, corporate_action_verification=?,
            symbol_identity_state=?, symbol_identity_references_json=?,
            ingestion_intent=?, completed_through=?
        WHERE operation_id=?
        """,
        (
            result.data_class,
            int(result.research_eligible),
            result.corporate_action_verification,
            batch.symbol_identity_state.value,
            _json(batch.symbol_identity_references),
            batch.ingestion_intent.value,
            None if batch.completed_through is None else batch.completed_through.isoformat(),
            batch.operation_id,
        ),
    )


def _upsert_prices(connection: sqlite3.Connection, batch: PreparedPriceBatch) -> None:
    connection.executemany(
        """
        INSERT INTO prices (symbol, time, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, time) DO UPDATE SET
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            volume = excluded.volume
        """,
        tuple(
            (
                row.symbol,
                row.session.isoformat(),
                row.open,
                row.high,
                row.low,
                row.close,
                row.volume,
            )
            for row in batch.rows
        ),
    )


def _link_prices(connection: sqlite3.Connection, batch: PreparedPriceBatch) -> None:
    connection.executemany(
        """
        INSERT INTO market_price_provenance (
            symbol, time, operation_id, batch_fingerprint
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(symbol, time) DO UPDATE SET
            operation_id = excluded.operation_id,
            batch_fingerprint = excluded.batch_fingerprint
        """,
        tuple(
            (row.symbol, row.session.isoformat(), batch.operation_id, batch.batch_fingerprint)
            for row in batch.rows
        ),
    )


def _append_new_prices(
    connection: sqlite3.Connection,
    batch: PreparedPriceBatch,
    new_dates: Sequence[str],
) -> tuple[PreparedPriceRow, ...]:
    allowed = set(new_dates)
    rows = tuple(row for row in batch.rows if row.session.isoformat() in allowed)
    if len(rows) != len(allowed):
        raise RuntimeError("operational new-session set does not match prepared rows")
    connection.executemany(
        """
        INSERT INTO prices (symbol, time, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(
            (
                row.symbol,
                row.session.isoformat(),
                row.open,
                row.high,
                row.low,
                row.close,
                row.volume,
            )
            for row in rows
        ),
    )
    return rows


def _link_selected_prices(
    connection: sqlite3.Connection,
    batch: PreparedPriceBatch,
    rows: Sequence[PreparedPriceRow],
) -> None:
    connection.executemany(
        """
        INSERT INTO market_price_provenance (
            symbol, time, operation_id, batch_fingerprint
        ) VALUES (?, ?, ?, ?)
        """,
        tuple(
            (row.symbol, row.session.isoformat(), batch.operation_id, batch.batch_fingerprint)
            for row in rows
        ),
    )


def _insert_staging_candidate(
    connection: sqlite3.Connection,
    batch: PreparedPriceBatch,
    result: OperationalAdmissionResult,
) -> None:
    metadata = {
        "provider_identity": batch.provider_identity,
        "endpoint_identity": batch.endpoint_identity,
        "package_name": batch.package_name,
        "package_version": batch.package_version,
        "source_verification_state": batch.source_verification_state.value,
        "claimed_adjustment_basis": batch.claimed_adjustment_basis.value,
        "adjustment_verification_state": batch.adjustment_verification_state.value,
        "archive": None if batch.archive is None else {
            "reference": batch.archive.reference,
            "content_sha256": batch.archive.content_sha256,
            "kind": batch.archive.kind.value,
        },
        "raw_payload_sha256": batch.raw_payload_sha256,
        "revision_evidence": [_revision_evidence_dict(item) for item in batch.revision_evidence],
    }
    connection.execute(
        """
        INSERT INTO market_ingestion_staging (
            operation_id, batch_fingerprint, symbol, ingestion_intent,
            staging_outcome, normalized_content_sha256, normalized_rows_json,
            metadata_json, data_class, research_eligible, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (
            batch.operation_id,
            batch.batch_fingerprint,
            batch.symbol,
            batch.ingestion_intent.value,
            result.admission.value,
            batch.normalized_content_sha256,
            _json([row.as_identity_dict() for row in batch.rows]),
            _json(metadata),
            result.data_class,
            datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        ),
    )


def commit_price_batch(
    target: sqlite3.Connection | str | Path,
    batch: PreparedPriceBatch,
    *,
    busy_timeout_seconds: float = 5.0,
    failure_injector: Callable[[str], None] | None = None,
) -> CommitPriceBatchResult:
    """Atomically decide and record one prospective symbol batch.

    The schema must already exist.  ``failure_injector`` is a test seam; any
    exception it raises is handled like any other pre-commit failure and rolls
    the entire transaction back. Detected revisions always reject the whole
    batch, even on Guard PASS. This is not full D4B2 admission. Consumers must
    use receipt status, not Guard PASS alone, to determine write success.
    """
    inject = failure_injector or (lambda _stage: None)
    with _connection(target, busy_timeout_seconds=busy_timeout_seconds) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            inject("after_begin_immediate")
            replay = _existing_receipt(connection, batch)
            if replay is not None:
                connection.commit()
                return replay

            existing_rows = _persisted_rows(connection, batch.symbol)
            incoming_rows = tuple(row.as_guard_row() for row in batch.rows)
            relevant_sessions = _relevant_existing_sessions(existing_rows, incoming_rows)
            persisted_basis = _persisted_price_basis(
                connection,
                batch.symbol,
                relevant_sessions,
            )
            guard_result = evaluate_preupdate_market_data(
                existing_rows,
                incoming_rows,
                symbol=batch.symbol,
                requested_start=batch.requested_start,
                requested_end=batch.requested_end,
                coverage=batch.coverage,
                existing_price_basis=persisted_basis,
                incoming_price_basis=batch.guard_price_basis(),
                revision_evidence=batch.revision_evidence,
                boundary_evidence=batch.boundary_evidence,
            )
            inject("after_guard")

            if guard_result.revisions:
                staging_columns = {
                    "operation_id", "batch_fingerprint", "symbol", "ingestion_intent",
                    "staging_outcome", "normalized_content_sha256", "normalized_rows_json",
                    "metadata_json", "data_class", "research_eligible", "created_at_utc",
                }
                can_stage = (
                    _RECEIPT_D4B2_COLUMNS.keys() <= _table_columns(connection, "market_ingestion_receipts")
                    and staging_columns <= _table_columns(connection, "market_ingestion_staging")
                )
                review_result = historical_revision_review(guard_result)
                review_result = replace(review_result, limitations=review_result.limitations + (
                    "D4B1_REVISION_ONLY_NOT_FULL_OPERATIONAL_ADMISSION",
                ))
                if not can_stage:
                    review_result = replace(
                        review_result, admission=OperationalAdmission.OPERATIONAL_REJECTED,
                        limitations=review_result.limitations + ("D4B1_RECEIPT_ONLY_REVISION_REVIEW",),
                    )
                _insert_receipt(
                    connection, batch, guard_result, ReceiptStatus.REJECTED, 0,
                    revision_review={
                        "operational_result": _operational_result_dict(review_result),
                        "staging_persisted": can_stage,
                        "research_eligible": False,
                        "normalized_rows": [row.as_identity_dict() for row in batch.rows],
                        "revision_evidence": [_revision_evidence_dict(item) for item in batch.revision_evidence],
                        "source_references": list(batch.source_references),
                    },
                )
                inject("after_rejection_receipt")
                if can_stage:
                    _attach_operational_receipt(connection, batch, review_result)
                    _insert_staging_candidate(connection, batch, review_result)
                inject("after_rejection_or_staging")
                connection.commit()
                return CommitPriceBatchResult(
                    batch.operation_id, batch.batch_fingerprint, ReceiptStatus.REJECTED,
                    guard_result, 0, False, True,
                )

            if guard_result.decision is not GuardDecision.PASS:
                _insert_receipt(
                    connection,
                    batch,
                    guard_result,
                    ReceiptStatus.REJECTED,
                    0,
                )
                inject("after_rejection_receipt")
                connection.commit()
                return CommitPriceBatchResult(
                    batch.operation_id,
                    batch.batch_fingerprint,
                    ReceiptStatus.REJECTED,
                    guard_result,
                    0,
                    False,
                )

            _insert_receipt(
                connection,
                batch,
                guard_result,
                ReceiptStatus.APPROVED,
                len(batch.rows),
            )
            inject("after_approval_receipt")
            _insert_manifest(connection, batch)
            inject("after_manifest")
            _upsert_prices(connection, batch)
            inject("after_prices")
            _link_prices(connection, batch)
            inject("after_provenance_links")
            connection.commit()
            return CommitPriceBatchResult(
                batch.operation_id,
                batch.batch_fingerprint,
                ReceiptStatus.APPROVED,
                guard_result,
                len(batch.rows),
                False,
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise


def commit_price_batch_shadow(
    target: sqlite3.Connection | str | Path,
    batch: PreparedPriceBatch,
    *,
    busy_timeout_seconds: float = 5.0,
    failure_injector: Callable[[str], None] | None = None,
    operation_identity_request: OperationIdentityRequest | None = None,
) -> ShadowCommitResult:
    """Evaluate D4A and operational admission inside one reserved transaction.

    D4B2.6 callers pass an operation request; generation allocation then shares
    the write transaction so a rollback cannot leave a phantom observation.
    """
    inject = failure_injector or (lambda _stage: None)
    with _connection(target, busy_timeout_seconds=busy_timeout_seconds) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            inject("after_begin_immediate")
            allocation = None
            if operation_identity_request is not None:
                _validate_operation_identity_request(batch, operation_identity_request)
                allocation = allocate_operation_identity_in_transaction(
                    connection,
                    operation_identity_request,
                    batch.normalized_content_sha256,
                )
                batch = replace(batch, operation_id=allocation.operation_id)
                inject("after_operation_identity")
            replay = _existing_shadow_receipt(connection, batch)
            if replay is not None:
                connection.commit()
                return replace(
                    replay,
                    completed_session_result=batch.completed_session_result,
                    operation_identity=allocation,
                )

            existing_rows = _persisted_rows(connection, batch.symbol)
            incoming_rows = tuple(row.as_guard_row() for row in batch.rows)
            relevant_sessions = _relevant_existing_sessions(existing_rows, incoming_rows)
            persisted_basis = _persisted_price_basis(
                connection,
                batch.symbol,
                relevant_sessions,
            )
            guard_result = evaluate_preupdate_market_data(
                existing_rows,
                incoming_rows,
                symbol=batch.symbol,
                requested_start=batch.requested_start,
                requested_end=batch.requested_end,
                coverage=batch.coverage,
                existing_price_basis=persisted_basis,
                incoming_price_basis=batch.guard_price_basis(),
                revision_evidence=batch.revision_evidence,
                boundary_evidence=batch.boundary_evidence,
            )
            operational_result = evaluate_operational_admission(
                batch,
                guard_result,
                existing_rows,
            )
            inject("after_decisions")

            write_approved = operational_result.admission in {
                OperationalAdmission.NOT_REQUIRED_GUARD_PASS,
                OperationalAdmission.OPERATIONAL_APPEND_ONLY_ACCEPTED,
            }
            if write_approved:
                new_session_dates = (
                    tuple(value.isoformat() for value in guard_result.genuinely_new_dates)
                    if guard_result.decision is GuardDecision.PASS
                    else operational_result.new_session_dates
                )
                rows_written = len(new_session_dates)
                _insert_receipt(
                    connection,
                    batch,
                    guard_result,
                    ReceiptStatus.APPROVED,
                    rows_written,
                )
                _attach_operational_receipt(connection, batch, operational_result)
                _attach_runtime_receipt(connection, batch, allocation)
                inject("after_approval_receipt")
                _insert_manifest(connection, batch)
                _attach_operational_manifest(connection, batch, operational_result)
                inject("after_manifest")
                appended = _append_new_prices(
                    connection,
                    batch,
                    new_session_dates,
                )
                _link_selected_prices(connection, batch, appended)
                inject("after_prices_and_links")
                connection.commit()
                return ShadowCommitResult(
                    batch.operation_id,
                    batch.batch_fingerprint,
                    ReceiptStatus.APPROVED,
                    guard_result,
                    operational_result,
                    rows_written,
                    False,
                    batch.completed_session_result,
                    allocation,
                )

            _insert_receipt(
                connection,
                batch,
                guard_result,
                ReceiptStatus.REJECTED,
                0,
            )
            _attach_operational_receipt(connection, batch, operational_result)
            _attach_runtime_receipt(connection, batch, allocation)
            if operational_result.requires_staging:
                _insert_staging_candidate(connection, batch, operational_result)
            inject("after_rejection_or_staging")
            connection.commit()
            return ShadowCommitResult(
                batch.operation_id,
                batch.batch_fingerprint,
                ReceiptStatus.REJECTED,
                guard_result,
                operational_result,
                0,
                False,
                batch.completed_session_result,
                allocation,
            )
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise


__all__ = [
    "ArchiveKind",
    "AttributionState",
    "CommitPriceBatchResult",
    "D4B2_MIGRATION_ID",
    "D4B26_MIGRATION_ID",
    "ImmutableArchiveIdentity",
    "MigrationRehearsalResult",
    "OperationIdentityConflict",
    "PreparedPriceBatch",
    "PreparedPriceRow",
    "ReceiptStatus",
    "SCHEMA_VERSION",
    "ShadowCommitResult",
    "commit_price_batch",
    "commit_price_batch_shadow",
    "initialize_transactional_ingestion_schema",
    "migrate_operational_admission_schema",
    "migrate_shadow_runtime_foundation_schema",
    "rehearse_operational_migration",
]
