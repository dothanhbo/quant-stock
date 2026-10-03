from __future__ import annotations

"""Narrow, offline CafeF manual-file shadow adapter.

The adapter validates explicit ZIP/member identities, creates a SQLite backup-
API clone, and invokes the existing D4A/D4B shadow contract on that clone.  It
does not import a provider or production updater, infer completed sessions, or
mark adjustment provenance as verified.
"""

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from hashlib import sha256
import io
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
from typing import Callable, Mapping
from uuid import uuid4
import zipfile

from quantlab.operational_admission import IngestionIntent, SymbolIdentityState
from quantlab.preupdate_market_data_guard import AdjustmentBasis, CoverageMetadata, CoverageState
from quantlab.transactional_market_data import (
    ArchiveKind,
    AttributionState,
    ImmutableArchiveIdentity,
    PreparedPriceBatch,
    PreparedPriceRow,
    commit_price_batch_shadow,
    initialize_transactional_ingestion_schema,
    migrate_operational_admission_schema,
    migrate_shadow_runtime_foundation_schema,
)


CONTRACT_VERSION = "D4B2.8_CAFEF_MANUAL_EOD_V1"
VENUES = ("HSX", "HNX", "UPCOM")
CSV_FIELDS = (
    "<Ticker>", "<DTYYYYMMDD>", "<Open>", "<High>", "<Low>", "<Close>", "<Volume>"
)
MAX_ARCHIVE_BYTES = 50_000_000
MAX_MEMBER_BYTES = 10_000_000
MAX_TOTAL_MEMBER_BYTES = 30_000_000
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,15}$")


class ManualInputError(ValueError):
    """A manual archive is malformed, ambiguous, or inconsistent."""


class ShadowSafetyError(RuntimeError):
    """A disposable-shadow safety invariant was violated."""


class AdjustmentClaim(str, Enum):
    RAW = "RAW"
    ADJUSTED = "ADJUSTED"


@dataclass(frozen=True, slots=True)
class CafeFRow:
    venue: str
    symbol: str
    session: date
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class MemberEvidence:
    venue: str
    member_name: str
    content_sha256: str
    byte_length: int
    row_count: int


@dataclass(frozen=True, slots=True)
class ArchiveEvidence:
    source_name: str
    source_identity: str
    zip_sha256: str
    zip_byte_length: int
    session: date
    adjustment_claim: AdjustmentClaim
    adjustment_verified: bool
    members: tuple[MemberEvidence, ...]
    rows: tuple[CafeFRow, ...]


@dataclass(frozen=True, slots=True)
class DisposableClone:
    directory: Path
    database: Path
    source_sha256_before: str
    source_sha256_after_backup: str
    clone_integrity: str


@dataclass(frozen=True, slots=True)
class ManualShadowResult:
    outcome: str
    source_name: str
    source_zip_sha256: str
    source_rows: int
    accepted_rows: int
    staged_rows: int
    rejected_rows: int
    replayed_rows: int
    clone_database: str
    source_database_sha256_before: str
    source_database_sha256_after: str
    research_eligible: bool
    completed_session_inferred: bool
    adjustment_verified: bool
    admission_counts: tuple[tuple[str, int], ...]
    guard_counts: tuple[tuple[str, int], ...]

    def as_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["admission_counts"] = dict(self.admission_counts)
        value["guard_counts"] = dict(self.guard_counts)
        return value


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(payload.encode("utf-8")).hexdigest()


def _text(value: object, field: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ManualInputError(f"{field} must be non-empty")
    return normalized


def _expected_member(venue: str, session: date, claim: AdjustmentClaim) -> str:
    stamp = session.strftime("%d.%m.%Y")
    prefix = "CafeF.RAW_" if claim is AdjustmentClaim.RAW else "CafeF."
    return f"{prefix}{venue}.{stamp}.csv"


def expected_venue_members(session: date, claim: AdjustmentClaim) -> dict[str, str]:
    """Return names for callers to copy into an explicit, reviewable mapping."""
    return {venue: _expected_member(venue, session, claim) for venue in VENUES}


def _numeric(value: str, field: str, *, integer: bool = False) -> float:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ManualInputError(f"{field} is not numeric") from exc
    if not parsed.is_finite():
        raise ManualInputError(f"{field} must be finite")
    if integer and parsed != parsed.to_integral_value():
        raise ManualInputError(f"{field} must be an integer")
    return float(parsed)


def validate_cafef_archive(
    archive_path: str | Path,
    *,
    session: date,
    adjustment_claim: AdjustmentClaim,
    source_identity: str,
    venue_members: Mapping[str, str],
) -> ArchiveEvidence:
    """Validate one archive without extracting or changing it."""
    path = Path(archive_path).resolve()
    if not path.is_file():
        raise ManualInputError("archive must be an existing file")
    source_identity = _text(source_identity, "source identity")
    if not isinstance(session, date) or isinstance(session, datetime):
        raise ManualInputError("session must be a date")
    if set(venue_members) != set(VENUES):
        raise ManualInputError("venue mapping must explicitly contain HSX, HNX, and UPCOM")
    normalized_mapping = {
        str(venue).strip().upper(): _text(member, f"{venue} member")
        for venue, member in venue_members.items()
    }
    for venue in VENUES:
        if normalized_mapping[venue] != _expected_member(venue, session, adjustment_claim):
            raise ManualInputError(
                f"{venue} member is inconsistent with session or adjustment claim"
            )
    archive_size = path.stat().st_size
    if archive_size <= 0 or archive_size > MAX_ARCHIVE_BYTES:
        raise ManualInputError("archive size is outside the supported bound")
    zip_digest = _file_sha256(path)

    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            if archive.testzip() is not None:
                raise ManualInputError("archive contains a member with a bad CRC")
            if len(infos) != len(VENUES):
                raise ManualInputError("archive must contain exactly three CSV members")
            names = {info.filename for info in infos}
            if names != set(normalized_mapping.values()):
                raise ManualInputError("archive members do not match the explicit venue mapping")
            total_size = 0
            for info in infos:
                member_path = PurePosixPath(info.filename)
                unix_type = (info.external_attr >> 16) & 0o170000
                if (
                    info.is_dir()
                    or info.flag_bits & 0x1
                    or member_path.is_absolute()
                    or len(member_path.parts) != 1
                    or ".." in member_path.parts
                    or "\\" in info.filename
                    or unix_type == 0o120000
                ):
                    raise ManualInputError("archive members must be flat, unencrypted regular files")
                if info.file_size <= 0 or info.file_size > MAX_MEMBER_BYTES:
                    raise ManualInputError("archive member size is outside the supported bound")
                total_size += info.file_size
            if total_size > MAX_TOTAL_MEMBER_BYTES:
                raise ManualInputError("expanded archive exceeds the supported bound")

            rows: list[CafeFRow] = []
            evidence: list[MemberEvidence] = []
            seen: set[tuple[str, date]] = set()
            for venue in VENUES:
                member = normalized_mapping[venue]
                raw = archive.read(member)
                try:
                    text = raw.decode("utf-8-sig", errors="strict")
                except UnicodeDecodeError as exc:
                    raise ManualInputError(f"{venue} CSV is not UTF-8") from exc
                reader = csv.DictReader(io.StringIO(text, newline=""))
                if tuple(reader.fieldnames or ()) != CSV_FIELDS:
                    raise ManualInputError(f"{venue} CSV has an unsupported schema")
                member_count = 0
                for line_number, source_row in enumerate(reader, start=2):
                    if None in source_row or set(source_row) != set(CSV_FIELDS):
                        raise ManualInputError(f"{venue} line {line_number} has extra columns")
                    if any(value is None or value.strip() == "" for value in source_row.values()):
                        raise ManualInputError(f"{venue} line {line_number} has a missing field")
                    symbol = source_row["<Ticker>"].strip().upper()
                    if source_row["<Ticker>"] != symbol or not _SYMBOL.fullmatch(symbol):
                        raise ManualInputError(f"{venue} line {line_number} has an invalid ticker")
                    if source_row["<DTYYYYMMDD>"] != session.strftime("%Y%m%d"):
                        raise ManualInputError(f"{venue} line {line_number} has an unsupported date")
                    key = (symbol, session)
                    if key in seen:
                        raise ManualInputError(f"duplicate ticker/session combination: {symbol}")
                    seen.add(key)
                    open_value = _numeric(source_row["<Open>"], "open")
                    high = _numeric(source_row["<High>"], "high")
                    low = _numeric(source_row["<Low>"], "low")
                    close = _numeric(source_row["<Close>"], "close")
                    volume = _numeric(source_row["<Volume>"], "volume", integer=True)
                    try:
                        prepared = PreparedPriceRow(
                            symbol, session, open_value, high, low, close, volume
                        )
                    except ValueError as exc:
                        raise ManualInputError(
                            f"{venue}/{symbol} line {line_number} has invalid OHLCV"
                        ) from exc
                    rows.append(CafeFRow(
                        venue,
                        prepared.symbol,
                        prepared.session,
                        prepared.open,
                        prepared.high,
                        prepared.low,
                        prepared.close,
                        prepared.volume,
                    ))
                    member_count += 1
                if member_count == 0:
                    raise ManualInputError(f"{venue} CSV contains no rows")
                evidence.append(MemberEvidence(
                    venue, member, sha256(raw).hexdigest(), len(raw), member_count
                ))
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        if isinstance(exc, ManualInputError):
            raise
        raise ManualInputError("archive is malformed or unreadable") from exc

    return ArchiveEvidence(
        path.name,
        source_identity,
        zip_digest,
        archive_size,
        session,
        adjustment_claim,
        False,
        tuple(evidence),
        tuple(sorted(rows, key=lambda row: (row.venue, row.symbol))),
    )


def compare_adjustment_claims(raw: ArchiveEvidence, adjusted: ArchiveEvidence) -> dict[str, object]:
    if raw.adjustment_claim is not AdjustmentClaim.RAW:
        raise ManualInputError("raw evidence is not labeled RAW")
    if adjusted.adjustment_claim is not AdjustmentClaim.ADJUSTED:
        raise ManualInputError("adjusted evidence is not labeled ADJUSTED")
    if raw.session != adjusted.session:
        raise ManualInputError("adjustment archives describe different sessions")
    raw_members = {item.venue: item.content_sha256 for item in raw.members}
    adjusted_members = {item.venue: item.content_sha256 for item in adjusted.members}
    return {
        "session": raw.session.isoformat(),
        "byte_identical_by_venue": {
            venue: raw_members[venue] == adjusted_members[venue] for venue in VENUES
        },
        "row_identity_equal": raw.rows == adjusted.rows,
        "adjustment_verified": False,
        "limitation": "RAW and ADJUSTED are source claims; equality does not verify adjustment semantics",
    }


def _member_for(evidence: ArchiveEvidence, venue: str) -> MemberEvidence:
    return next(item for item in evidence.members if item.venue == venue)


def prepare_manual_batches(evidence: ArchiveEvidence) -> tuple[PreparedPriceBatch, ...]:
    """Map validated rows into D4B batches without strengthening evidence."""
    basis = (
        AdjustmentBasis.RAW
        if evidence.adjustment_claim is AdjustmentClaim.RAW
        else AdjustmentBasis.ADJUSTED
    )
    batches: list[PreparedPriceBatch] = []
    for row in evidence.rows:
        member = _member_for(evidence, row.venue)
        references = (
            f"sha256:{evidence.zip_sha256}",
            f"zip-member:{member.member_name}#sha256={member.content_sha256}",
            f"source-claim:{evidence.source_identity}:{evidence.adjustment_claim.value}",
        )
        identity_payload = {
            "contract": CONTRACT_VERSION,
            "zip_sha256": evidence.zip_sha256,
            "member_sha256": member.content_sha256,
            "adjustment_claim": evidence.adjustment_claim.value,
            "venue": row.venue,
            "symbol": row.symbol,
            "session": row.session.isoformat(),
            "ohlcv": [row.open, row.high, row.low, row.close, row.volume],
        }
        operation_id = _canonical_hash(identity_payload)
        prepared_row = PreparedPriceRow(
            row.symbol, row.session, row.open, row.high, row.low, row.close, row.volume
        )
        batches.append(PreparedPriceBatch(
            operation_id=operation_id,
            symbol=row.symbol,
            requested_start=row.session,
            requested_end=row.session,
            rows=(prepared_row,),
            coverage=CoverageMetadata(
                row.session, row.session, row.session, row.session,
                CoverageState.UNKNOWN, references,
            ),
            provider_identity=evidence.source_identity,
            endpoint_identity=f"manual-file:{evidence.source_name}/{member.member_name}",
            package_name="quantlab.cafef_manual_eod",
            package_version=CONTRACT_VERSION,
            source_verification_state=AttributionState.UNKNOWN,
            source_references=references,
            price_unit="SOURCE_UNSPECIFIED",
            price_unit_verification_state=AttributionState.UNKNOWN,
            price_unit_references=(),
            claimed_adjustment_basis=basis,
            adjustment_verification_state=AttributionState.UNKNOWN,
            adjustment_evidence_references=(references[-1],),
            archive=ImmutableArchiveIdentity(
                f"zip-member:{member.member_name}",
                member.content_sha256,
                ArchiveKind.NORMALIZED_CANDIDATE,
            ),
            ingestion_intent=IngestionIntent.INCREMENTAL_UPDATE,
            symbol_identity_state=SymbolIdentityState.CONSISTENT_REQUEST_SYMBOL,
            symbol_identity_references=(references[1],),
            completed_through=None,
            completed_session_result=None,
            corporate_action_verification_state=AttributionState.UNKNOWN,
        ))
    return tuple(batches)


def create_disposable_clone(source_database: str | Path, output_root: str | Path) -> DisposableClone:
    """Clone SQLite through its backup API into a new unique directory."""
    source = Path(source_database).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    before = _file_sha256(source)
    root = Path(output_root).resolve()
    run_directory = root / f"cafef-shadow-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid4().hex}"
    run_directory.mkdir(parents=True, exist_ok=False)
    clone = run_directory / "market.shadow.db"
    source_uri = source.as_uri() + "?mode=ro"
    source_connection = sqlite3.connect(source_uri, uri=True)
    try:
        if source_connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ShadowSafetyError("source database integrity_check failed")
        clone_connection = sqlite3.connect(clone)
        try:
            source_connection.backup(clone_connection)
            clone_connection.commit()
            integrity = str(clone_connection.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            clone_connection.close()
    finally:
        source_connection.close()
    after = _file_sha256(source)
    if before != after:
        raise ShadowSafetyError("source database changed during backup")
    if integrity != "ok":
        raise ShadowSafetyError("clone database integrity_check failed")
    return DisposableClone(run_directory, clone, before, after, integrity)


def _ensure_shadow_schema(database: Path) -> None:
    connection = sqlite3.connect(database)
    try:
        tables = {
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "market_ingestion_receipts" not in tables:
            initialize_transactional_ingestion_schema(connection)
        migrate_operational_admission_schema(connection)
        migrate_shadow_runtime_foundation_schema(connection)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS cafef_manual_shadow_series(
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                source_zip_sha256 TEXT NOT NULL,
                adjustment_claim TEXT NOT NULL,
                source_identity TEXT NOT NULL,
                session TEXT NOT NULL
            )
            """
        )
        connection.commit()
    finally:
        connection.close()


def apply_archive_to_shadow(
    clone_database: str | Path,
    evidence: ArchiveEvidence,
    *,
    failure_injector: Callable[[str, str], None] | None = None,
) -> ManualShadowResult:
    """Apply manual rows only to an explicit disposable clone."""
    database = Path(clone_database).resolve()
    _ensure_shadow_schema(database)
    accepted = staged = rejected = replayed = 0
    admission_counts: dict[str, int] = {}
    guard_counts: dict[str, int] = {}
    connection = sqlite3.connect(database)
    try:
        series = connection.execute(
            """
            SELECT source_zip_sha256,adjustment_claim,source_identity,session
            FROM cafef_manual_shadow_series WHERE singleton=1
            """
        ).fetchone()
        expected_series = (
            evidence.zip_sha256,
            evidence.adjustment_claim.value,
            evidence.source_identity,
            evidence.session.isoformat(),
        )
        if series is None:
            connection.execute(
                "INSERT INTO cafef_manual_shadow_series VALUES(1,?,?,?,?)",
                expected_series,
            )
            connection.commit()
        elif tuple(series) != expected_series:
            raise ShadowSafetyError(
                "disposable clone is already bound to a different source or adjustment series"
            )
        price_count_before = int(connection.execute("SELECT COUNT(*) FROM prices").fetchone()[0])
        for batch in prepare_manual_batches(evidence):
            injector = None
            if failure_injector is not None:
                injector = lambda stage, symbol=batch.symbol: failure_injector(symbol, stage)
            result = commit_price_batch_shadow(connection, batch, failure_injector=injector)
            if (
                result.completed_session_result is not None
                or result.operational_result.research_eligible
                or result.operational_result.adjustment_mode != "UNKNOWN"
            ):
                raise ShadowSafetyError(
                    "manual evidence unexpectedly acquired completion, research, or adjustment status"
                )
            admission = result.operational_result.admission.value
            guard = result.guard_result.decision.value
            admission_counts[admission] = admission_counts.get(admission, 0) + 1
            guard_counts[guard] = guard_counts.get(guard, 0) + 1
            if result.idempotent_replay:
                replayed += 1
            if result.rows_written:
                accepted += result.rows_written
            elif result.operational_result.requires_staging:
                staged += len(batch.rows)
            else:
                rejected += len(batch.rows)
        price_count_after = int(connection.execute("SELECT COUNT(*) FROM prices").fetchone()[0])
        eligible = int(connection.execute(
            "SELECT COUNT(*) FROM market_ingestion_staging WHERE research_eligible != 0"
        ).fetchone()[0])
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        connection.close()
    if accepted != 0 or price_count_after != price_count_before:
        raise ShadowSafetyError("manual evidence unexpectedly mutated clone price rows")
    if eligible != 0:
        raise ShadowSafetyError("manual staging unexpectedly became research eligible")
    if integrity != "ok":
        raise ShadowSafetyError("shadow integrity_check failed")
    outcome = "STAGED" if staged else "BLOCKED"
    return ManualShadowResult(
        outcome,
        evidence.source_name,
        evidence.zip_sha256,
        len(evidence.rows),
        accepted,
        staged,
        rejected,
        replayed,
        str(database),
        "",
        "",
        False,
        False,
        False,
        tuple(sorted(admission_counts.items())),
        tuple(sorted(guard_counts.items())),
    )


def run_manual_shadow(
    archive_path: str | Path,
    *,
    canonical_database: str | Path,
    output_root: str | Path,
    session: date,
    adjustment_claim: AdjustmentClaim,
    source_identity: str,
    venue_members: Mapping[str, str],
) -> ManualShadowResult:
    source_archive = Path(archive_path).resolve()
    source_database = Path(canonical_database).resolve()
    archive_hash_before = _file_sha256(source_archive)
    database_hash_before = _file_sha256(source_database)
    evidence = validate_cafef_archive(
        source_archive,
        session=session,
        adjustment_claim=adjustment_claim,
        source_identity=source_identity,
        venue_members=venue_members,
    )
    clone = create_disposable_clone(source_database, output_root)
    result = apply_archive_to_shadow(clone.database, evidence)
    archive_hash_after = _file_sha256(source_archive)
    database_hash_after = _file_sha256(source_database)
    if archive_hash_before != archive_hash_after:
        raise ShadowSafetyError("source archive changed during shadow run")
    if database_hash_before != database_hash_after:
        raise ShadowSafetyError("canonical database changed during shadow run")
    return ManualShadowResult(
        result.outcome,
        result.source_name,
        result.source_zip_sha256,
        result.source_rows,
        result.accepted_rows,
        result.staged_rows,
        result.rejected_rows,
        result.replayed_rows,
        result.clone_database,
        database_hash_before,
        database_hash_after,
        False,
        False,
        False,
        result.admission_counts,
        result.guard_counts,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a CafeF manual EOD disposable shadow")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--canonical-database", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--session", type=date.fromisoformat, required=True)
    parser.add_argument("--adjustment-claim", choices=[item.value for item in AdjustmentClaim], required=True)
    parser.add_argument("--source-identity", required=True)
    parser.add_argument("--venue-member", action="append", required=True)
    arguments = parser.parse_args(argv)
    mapping: dict[str, str] = {}
    for item in arguments.venue_member:
        if "=" not in item:
            parser.error("--venue-member must use VENUE=MEMBER")
        venue, member = item.split("=", 1)
        if venue.strip().upper() in mapping:
            parser.error("duplicate --venue-member venue")
        mapping[venue.strip().upper()] = member
    result = run_manual_shadow(
        arguments.archive,
        canonical_database=arguments.canonical_database,
        output_root=arguments.output_root,
        session=arguments.session,
        adjustment_claim=AdjustmentClaim(arguments.adjustment_claim),
        source_identity=arguments.source_identity,
        venue_members=mapping,
    )
    print(json.dumps(result.as_dict(), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
