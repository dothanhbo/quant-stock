from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
import threading

import pytest

from quantlab.preupdate_market_data_guard import (
    AdjustmentBasis,
    CoverageMetadata,
    CoverageState,
    EvidenceKind,
    GuardDecision,
    GuardReason,
    RevisionClaim,
    RevisionEvidence,
)
from quantlab.operational_admission import OperationalAdmission, ShadowDailyStatus, shadow_daily_status
from quantlab.transactional_market_data import (
    ArchiveKind,
    AttributionState,
    ImmutableArchiveIdentity,
    OperationIdentityConflict,
    PreparedPriceBatch,
    PreparedPriceRow,
    ReceiptStatus,
    SCHEMA_VERSION,
    commit_price_batch,
    commit_price_batch_shadow,
    initialize_transactional_ingestion_schema,
    migrate_operational_admission_schema,
    migrate_shadow_runtime_foundation_schema,
)


START = date(2026, 9, 1)


def _row(day: int, *, scale: float = 1.0, close_addition: float = 0.5) -> PreparedPriceRow:
    base = 100.0 + day
    return PreparedPriceRow(
        "AAA",
        START + timedelta(days=day),
        base * scale,
        (base + 1.0) * scale,
        (base - 1.0) * scale,
        (base + close_addition) * scale,
        1_000.0 + day,
    )


def _batch(
    operation_id: str,
    rows: tuple[PreparedPriceRow, ...],
    *,
    adjustment_state: AttributionState = AttributionState.VERIFIED,
) -> PreparedPriceBatch:
    requested_start = rows[0].session
    requested_end = rows[-1].session
    verified = adjustment_state is AttributionState.VERIFIED
    return PreparedPriceBatch(
        operation_id=operation_id,
        symbol="AAA",
        requested_start=requested_start,
        requested_end=requested_end,
        rows=rows,
        coverage=CoverageMetadata(
            requested_start,
            requested_end,
            requested_start,
            requested_end,
            CoverageState.VERIFIED_COMPLETE,
            ("request-response:fixture",),
        ),
        provider_identity="KBS",
        endpoint_identity="Quote.history:1D",
        package_name="vnstock",
        package_version="fixture-version",
        source_verification_state=AttributionState.VERIFIED,
        source_references=("provider-contract:fixture",),
        price_unit="provider_native",
        price_unit_verification_state=AttributionState.VERIFIED,
        price_unit_references=("unit-contract:fixture",),
        claimed_adjustment_basis=AdjustmentBasis.RAW,
        adjustment_verification_state=adjustment_state,
        adjustment_evidence_references=("adjustment-contract:fixture",) if verified else (),
        archive=ImmutableArchiveIdentity(
            "immutable://normalized/fixture",
            "a" * 64,
            ArchiveKind.NORMALIZED_CANDIDATE,
        ),
    )


def _database(tmp_path: Path, *, verified_history: bool = True) -> Path:
    path = tmp_path / "market.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                time TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                UNIQUE(symbol, time)
            )
            """
        )
        initialize_transactional_ingestion_schema(connection)
        rows = tuple(_row(day) for day in range(4))
        connection.executemany(
            """
            INSERT INTO prices(symbol,time,open,high,low,close,volume)
            VALUES(?,?,?,?,?,?,?)
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
        if verified_history:
            _seed_verified_provenance(connection, rows)
        connection.commit()
    return path


def _seed_verified_provenance(
    connection: sqlite3.Connection,
    rows: tuple[PreparedPriceRow, ...],
) -> None:
    fingerprint = "b" * 64
    connection.execute(
        """
        INSERT INTO market_ingestion_receipts(
            operation_id,batch_fingerprint,symbol,requested_start,requested_end,
            decision,status,reason_codes_json,guard_result_json,rows_written,created_at_utc
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "verified-seed",
            fingerprint,
            "AAA",
            rows[0].session.isoformat(),
            rows[-1].session.isoformat(),
            "PASS",
            "APPROVED",
            json.dumps(["BATCH_SAFE"]),
            "{}",
            len(rows),
            "2026-09-01T00:00:00Z",
        ),
    )
    connection.execute(
        """
        INSERT INTO market_ingestion_manifests(
            operation_id,batch_fingerprint,schema_version,symbol,
            requested_start,requested_end,returned_start,returned_end,row_count,
            normalized_content_sha256,provider_identity,endpoint_identity,
            package_name,package_version,source_verification_state,
            source_references_json,price_unit,price_unit_verification_state,
            price_unit_references_json,claimed_adjustment_basis,
            adjustment_verification_state,adjustment_evidence_references_json,
            normalized_payload_is_raw
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
        """,
        (
            "verified-seed",
            fingerprint,
            SCHEMA_VERSION,
            "AAA",
            rows[0].session.isoformat(),
            rows[-1].session.isoformat(),
            rows[0].session.isoformat(),
            rows[-1].session.isoformat(),
            len(rows),
            "c" * 64,
            "KBS",
            "Quote.history:1D",
            "vnstock",
            "fixture-version",
            "VERIFIED",
            '["provider-contract:fixture"]',
            "provider_native",
            "VERIFIED",
            '["unit-contract:fixture"]',
            "RAW",
            "VERIFIED",
            '["adjustment-contract:fixture"]',
        ),
    )
    connection.executemany(
        """
        INSERT INTO market_price_provenance(symbol,time,operation_id,batch_fingerprint)
        VALUES(?,?,?,?)
        """,
        tuple(
            (row.symbol, row.session.isoformat(), "verified-seed", fingerprint)
            for row in rows
        ),
    )


def _prices(path: Path) -> tuple[tuple[object, ...], ...]:
    with sqlite3.connect(path) as connection:
        return tuple(connection.execute(
            "SELECT symbol,time,open,high,low,close,volume FROM prices ORDER BY symbol,time"
        ))


def _count(path: Path, table: str, operation_id: str | None = None) -> int:
    with sqlite3.connect(path) as connection:
        if operation_id is None:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        return int(connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE operation_id=?",
            (operation_id,),
        ).fetchone()[0])


def test_valid_batch_atomically_writes_receipt_manifest_prices_and_links(tmp_path: Path) -> None:
    path = _database(tmp_path)
    batch = _batch("operation-approved", tuple(_row(day) for day in range(2, 5)))

    result = commit_price_batch(path, batch)

    assert result.status is ReceiptStatus.APPROVED
    assert result.guard_result.decision is GuardDecision.PASS
    assert result.rows_written == 3
    assert len(_prices(path)) == 5
    assert _count(path, "market_ingestion_receipts", batch.operation_id) == 1
    assert _count(path, "market_ingestion_manifests", batch.operation_id) == 1
    assert _count(path, "market_price_provenance", batch.operation_id) == 3
    with sqlite3.connect(path) as connection:
        archive_kind, raw_hash, normalized_is_raw = connection.execute(
            """
            SELECT archive_kind,raw_payload_sha256,normalized_payload_is_raw
            FROM market_ingestion_manifests WHERE operation_id=?
            """,
            (batch.operation_id,),
        ).fetchone()
    assert archive_kind == ArchiveKind.NORMALIZED_CANDIDATE.value
    assert raw_hash is None
    assert normalized_is_raw == 0


def test_block_is_durable_and_preserves_all_existing_prices(tmp_path: Path) -> None:
    path = _database(tmp_path)
    incoming = [_row(day) for day in range(2, 5)]
    incoming[0] = _row(2, close_addition=0.6)
    batch = _batch("operation-block", tuple(incoming))
    before = _prices(path)

    result = commit_price_batch(path, batch)

    assert result.status is ReceiptStatus.REJECTED
    assert result.guard_result.decision is GuardDecision.BLOCK
    assert GuardReason.UNATTRIBUTED_HISTORICAL_REVISION in result.guard_result.reasons
    assert _prices(path) == before
    assert _count(path, "market_ingestion_receipts", batch.operation_id) == 1
    assert _count(path, "market_ingestion_manifests", batch.operation_id) == 0


def test_insufficient_evidence_is_durable_and_preserves_prices(tmp_path: Path) -> None:
    path = _database(tmp_path, verified_history=False)
    batch = _batch("operation-insufficient", tuple(_row(day) for day in range(2, 5)))
    before = _prices(path)

    result = commit_price_batch(path, batch)

    assert result.status is ReceiptStatus.REJECTED
    assert result.guard_result.decision is GuardDecision.INSUFFICIENT_EVIDENCE
    assert GuardReason.PRICE_BASIS_UNVERIFIED in result.guard_result.reasons
    assert _prices(path) == before
    assert _count(path, "market_ingestion_receipts", batch.operation_id) == 1


def test_injected_failure_after_prices_rolls_back_everything(tmp_path: Path) -> None:
    path = _database(tmp_path)
    batch = _batch("operation-failure", tuple(_row(day) for day in range(2, 5)))
    before = _prices(path)

    def fail(stage: str) -> None:
        if stage == "after_prices":
            raise RuntimeError("injected before commit")

    with pytest.raises(RuntimeError, match="injected before commit"):
        commit_price_batch(path, batch, failure_injector=fail)

    assert _prices(path) == before
    assert _count(path, "market_ingestion_receipts", batch.operation_id) == 0
    assert _count(path, "market_ingestion_manifests", batch.operation_id) == 0
    assert _count(path, "market_price_provenance", batch.operation_id) == 0


def test_receipt_without_prices_cannot_survive_failed_approved_transaction(tmp_path: Path) -> None:
    path = _database(tmp_path)
    batch = _batch("operation-receipt-failure", tuple(_row(day) for day in range(2, 5)))

    def fail(stage: str) -> None:
        if stage == "after_approval_receipt":
            raise RuntimeError("stop after receipt")

    with pytest.raises(RuntimeError, match="stop after receipt"):
        commit_price_batch(path, batch, failure_injector=fail)

    assert _count(path, "market_ingestion_receipts", batch.operation_id) == 0
    assert _count(path, "market_ingestion_manifests", batch.operation_id) == 0
    assert len(_prices(path)) == 4


def test_same_operation_and_fingerprint_is_an_idempotent_retry(tmp_path: Path) -> None:
    path = _database(tmp_path)
    batch = _batch("operation-retry", tuple(_row(day) for day in range(2, 5)))

    first = commit_price_batch(path, batch)
    second = commit_price_batch(path, batch)

    assert first.idempotent_replay is False
    assert second.idempotent_replay is True
    assert second.guard_result == first.guard_result
    assert _count(path, "market_ingestion_receipts", batch.operation_id) == 1
    assert len(_prices(path)) == 5


def test_same_operation_with_different_fingerprint_is_an_explicit_conflict(tmp_path: Path) -> None:
    path = _database(tmp_path)
    first = _batch("operation-conflict", tuple(_row(day) for day in range(2, 5)))
    changed_rows = tuple(_row(day) for day in range(2, 4)) + (_row(4, close_addition=0.6),)
    conflicting = _batch("operation-conflict", changed_rows)
    commit_price_batch(path, first)
    before = _prices(path)

    with pytest.raises(OperationIdentityConflict, match="already names fingerprint"):
        commit_price_batch(path, conflicting)

    assert first.batch_fingerprint != conflicting.batch_fingerprint
    assert _prices(path) == before
    assert _count(path, "market_ingestion_receipts", first.operation_id) == 1


def test_unattributed_revision_is_rejected_with_reason_code(tmp_path: Path) -> None:
    path = _database(tmp_path)
    batch = _batch(
        "operation-revision",
        (_row(2), _row(3, close_addition=0.55), _row(4)),
    )

    result = commit_price_batch(path, batch)

    assert result.guard_result.decision is GuardDecision.BLOCK
    assert result.rows_written == 0
    with sqlite3.connect(path) as connection:
        stored = json.loads(connection.execute(
            "SELECT reason_codes_json FROM market_ingestion_receipts WHERE operation_id=?",
            (batch.operation_id,),
        ).fetchone()[0])
    assert GuardReason.UNATTRIBUTED_HISTORICAL_REVISION.value in stored


def test_synthetic_mixed_adjustment_incident_is_rejected(tmp_path: Path) -> None:
    path = _database(tmp_path)
    batch = _batch(
        "operation-mixed-adjustment",
        tuple(_row(day, scale=0.5) for day in range(2, 5)),
    )
    before = _prices(path)

    result = commit_price_batch(path, batch)

    assert result.guard_result.decision is GuardDecision.BLOCK
    assert GuardReason.UNATTRIBUTED_HISTORICAL_REVISION in result.guard_result.reasons
    assert len(result.guard_result.revisions) == 8
    assert _prices(path) == before


def test_concurrent_competing_writers_serialize_and_conflict(tmp_path: Path) -> None:
    path = _database(tmp_path)
    operation_id = "operation-concurrent"
    first = _batch(operation_id, tuple(_row(day) for day in range(2, 5)))
    second = _batch(
        operation_id,
        tuple(_row(day) for day in range(2, 4)) + (_row(4, close_addition=0.6),),
    )
    barrier = threading.Barrier(2)

    def run(batch: PreparedPriceBatch):
        barrier.wait(timeout=5)
        try:
            return commit_price_batch(path, batch, busy_timeout_seconds=5)
        except OperationIdentityConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(run, (first, second)))

    assert sum(isinstance(item, OperationIdentityConflict) for item in outcomes) == 1
    approved = next(item for item in outcomes if not isinstance(item, Exception))
    assert approved.status is ReceiptStatus.APPROVED
    assert _count(path, "market_ingestion_receipts", operation_id) == 1
    assert _count(path, "market_ingestion_manifests", operation_id) == 1
    assert len(_prices(path)) == 5


def test_unknown_adjustment_claim_cannot_self_certify(tmp_path: Path) -> None:
    path = _database(tmp_path)
    batch = _batch(
        "operation-unknown-adjustment",
        tuple(_row(day) for day in range(2, 5)),
        adjustment_state=AttributionState.UNKNOWN,
    )

    result = commit_price_batch(path, batch)

    assert batch.claimed_adjustment_basis is AdjustmentBasis.RAW
    assert batch.guard_price_basis().adjustment_basis is AdjustmentBasis.UNKNOWN
    assert result.guard_result.decision is GuardDecision.INSUFFICIENT_EVIDENCE
    assert GuardReason.PRICE_BASIS_UNVERIFIED in result.guard_result.reasons
    assert _count(path, "market_ingestion_manifests", batch.operation_id) == 0


def test_legacy_rows_remain_unverified_after_rejected_attempt(tmp_path: Path) -> None:
    path = _database(tmp_path, verified_history=False)
    batch = _batch("operation-legacy", tuple(_row(day) for day in range(2, 5)))

    result = commit_price_batch(path, batch)

    assert result.guard_result.decision is GuardDecision.INSUFFICIENT_EVIDENCE
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM market_price_provenance"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT status FROM market_ingestion_receipts WHERE operation_id=?",
            (batch.operation_id,),
        ).fetchone()[0] == ReceiptStatus.REJECTED.value


def test_operation_identity_is_separate_from_deterministic_batch_fingerprint() -> None:
    rows = tuple(_row(day) for day in range(2, 5))
    first = _batch("retry-id-one", rows)
    second = _batch("retry-id-two", rows)

    assert first.operation_id != second.operation_id
    assert first.batch_fingerprint == second.batch_fingerprint
    assert first.normalized_content_sha256 == second.normalized_content_sha256


def _revision_batch(kind: EvidenceKind | None = EvidenceKind.PROVIDER_CORRECTION):
    original, changed = _row(3), _row(3, close_addition=0.6)
    evidence = () if kind is None else (RevisionEvidence("AAA", kind,
        (RevisionClaim(original.session, "close", original.close, changed.close),),
        ("fixture://reviewed-exact-revision",)),)
    return replace(_batch("detected-revision", (_row(2), changed, _row(4))), revision_evidence=evidence)


def _migrate_review_schema(connection):
    migrate_operational_admission_schema(connection)
    migrate_shadow_runtime_foundation_schema(connection)


def _all_tables(connection):
    tables = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {table: connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall() for table in tables}


def _deny_price_writes(attempts):
    def authorize(action, table, column, database, trigger):
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE) and table in (
            "prices", "market_price_provenance",
        ):
            attempts.append((action, table))
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    return authorize


@pytest.mark.parametrize("migrated", (False, True), ids=("base-receipt-review", "existing-staging"))
@pytest.mark.parametrize("kind", (EvidenceKind.PROVIDER_CORRECTION, EvidenceKind.CORPORATE_ACTION_RESTATEMENT, None))
def test_d4b1_detected_revision_blocks_entire_batch_and_replays_without_writes(tmp_path, migrated, kind):
    path = _database(tmp_path)
    batch = _revision_batch(kind)
    with sqlite3.connect(path) as connection:
        if migrated: _migrate_review_schema(connection)
        before = _all_tables(connection)
        attempts = []
        connection.set_authorizer(_deny_price_writes(attempts))
        result = commit_price_batch(connection, batch)
        after = _all_tables(connection)
        replay = commit_price_batch(connection, batch)
        assert _all_tables(connection) == after
        connection.set_authorizer(None)
        assert attempts == []
        assert result.status is ReceiptStatus.REJECTED
        assert result.guard_result.decision is (GuardDecision.PASS if kind is not None else GuardDecision.BLOCK)
        assert result.rows_written == 0
        assert result.revision_review_required
        assert replay.idempotent_replay and replay.revision_review_required
        assert replay.status is ReceiptStatus.REJECTED and replay.rows_written == 0
        assert replay.guard_result == result.guard_result
        assert set(before) == set(after)  # No automatic migration, even on base schema.
        for table in ("prices", "market_price_provenance", "market_ingestion_manifests"):
            assert after[table] == before[table]
        assert len(after["market_ingestion_receipts"]) == len(before["market_ingestion_receipts"]) + 1
        assert all(row in after["market_ingestion_receipts"] for row in before["market_ingestion_receipts"])
        payload = json.loads(connection.execute("SELECT guard_result_json FROM market_ingestion_receipts"
            " WHERE operation_id=?", (batch.operation_id,)).fetchone()[0])
        changes = [{"session": _row(3).session.isoformat(), "field": "close",
            "existing_value": _row(3).close, "incoming_value": _row(3, close_addition=0.6).close}]
        assert payload["revisions"] == changes
        review = payload["revision_review"]
        expected_evidence = [] if kind is None else [{"symbol": "AAA", "kind": kind.value,
            "claims": changes, "source_references": ["fixture://reviewed-exact-revision"]}]
        assert review["revision_evidence"] == expected_evidence
        assert review["source_references"] == list(batch.source_references)
        assert review["normalized_rows"] == [row.as_identity_dict() for row in batch.rows]
        assert review["research_eligible"] is False
        assert review["operational_result"]["research_eligible"] is False
        assert review["staging_persisted"] is migrated
        if migrated:
            rows_json, metadata_json, eligible = connection.execute("SELECT normalized_rows_json,metadata_json,"
                "research_eligible FROM market_ingestion_staging WHERE operation_id=?", (batch.operation_id,)).fetchone()
            assert json.loads(rows_json) == review["normalized_rows"]
            assert json.loads(metadata_json)["revision_evidence"] == expected_evidence
            assert eligible == 0
            assert len(after["market_ingestion_staging"]) == 1
            assert connection.execute("SELECT research_eligible FROM market_ingestion_receipts WHERE operation_id=?",
                (batch.operation_id,)).fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM prices WHERE time=?", (_row(4).session.isoformat(),)).fetchone() == (0,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("migrated", (False, True))
def test_d4b1_unchanged_guard_pass_preserves_existing_upsert_contract(tmp_path, migrated):
    path = _database(tmp_path)
    batch = _batch("no-revision-control", tuple(_row(day) for day in range(2, 5)))
    with sqlite3.connect(path) as connection:
        if migrated: _migrate_review_schema(connection)
        result = commit_price_batch(connection, batch)
        assert result.status is ReceiptStatus.APPROVED and result.rows_written == 3
        assert result.guard_result.decision is GuardDecision.PASS
        assert not result.revision_review_required
        assert connection.execute("SELECT COUNT(*) FROM prices").fetchone() == (5,)
        assert connection.execute("SELECT COUNT(*) FROM market_price_provenance WHERE operation_id=?",
            (batch.operation_id,)).fetchone() == (3,)
        before_replay = _all_tables(connection)
        replay = commit_price_batch(connection, batch)
        assert replay.status is ReceiptStatus.APPROVED and not replay.revision_review_required
        assert replay.idempotent_replay
        assert _all_tables(connection) == before_replay


@pytest.mark.parametrize("migrated", (False, True))
def test_d4b1_revision_shadow_replay_never_reports_guard_success_or_promotes(tmp_path, migrated):
    path = _database(tmp_path)
    batch = _revision_batch()
    with sqlite3.connect(path) as connection:
        if migrated: _migrate_review_schema(connection)
        first = commit_price_batch(connection, batch)
        assert first.guard_result.decision is GuardDecision.PASS
        if not migrated: _migrate_review_schema(connection)  # Explicit fixture migration after receipt.
        before = _all_tables(connection)
        attempts = []
        connection.set_authorizer(_deny_price_writes(attempts))
        replay = commit_price_batch_shadow(connection, batch)
        connection.set_authorizer(None)
        assert replay.idempotent_replay and replay.status is ReceiptStatus.REJECTED
        assert replay.rows_written == 0 and not replay.operational_result.research_eligible
        assert replay.operational_result.admission is (
            OperationalAdmission.HISTORICAL_REVISION_REQUIRES_REVIEW if migrated else OperationalAdmission.OPERATIONAL_REJECTED)
        assert shadow_daily_status(replay.operational_result) is (
            ShadowDailyStatus.HISTORICAL_REVISION_STAGED if migrated else ShadowDailyStatus.REJECTED)
        assert attempts == [] and _all_tables(connection) == before


@pytest.mark.parametrize("migrated", (False, True))
@pytest.mark.parametrize("fault_stage", ("after_rejection_receipt", "after_rejection_or_staging"))
def test_d4b1_revision_review_rollback_is_atomic(tmp_path, migrated, fault_stage):
    path = _database(tmp_path)
    batch = _revision_batch()
    with sqlite3.connect(path) as connection:
        if migrated: _migrate_review_schema(connection)
        before = _all_tables(connection)
        attempts = []

        def fail(stage):
            if stage == fault_stage:
                inside = _all_tables(connection)
                assert len(inside["market_ingestion_receipts"]) == len(before["market_ingestion_receipts"]) + 1
                if migrated and stage == "after_rejection_or_staging":
                    assert len(inside["market_ingestion_staging"]) == 1
                raise RuntimeError("injected review failure")

        connection.set_authorizer(_deny_price_writes(attempts))
        with pytest.raises(RuntimeError, match="injected review failure"):
            commit_price_batch(connection, batch, failure_injector=fail)
        assert not connection.in_transaction
        assert _all_tables(connection) == before and attempts == []
        retry = commit_price_batch(connection, batch)
        connection.set_authorizer(None)
        assert retry.status is ReceiptStatus.REJECTED and retry.rows_written == 0
        assert attempts == []


def test_legacy_attributed_pass_receipt_replay_retains_original_decision_without_writes(tmp_path):
    path = _database(tmp_path)
    batch = _revision_batch()
    with sqlite3.connect(path) as connection:
        rejected = commit_price_batch(connection, batch)
        assert rejected.guard_result.decision is GuardDecision.PASS
        payload = json.loads(connection.execute("SELECT guard_result_json FROM market_ingestion_receipts"
            " WHERE operation_id=?", (batch.operation_id,)).fetchone()[0])
        del payload["revision_review"]
        # Manufacture the old approved-revision receipt shape on this fixture
        # only. It is historical decision evidence, not a qualified price state.
        connection.execute("UPDATE market_ingestion_receipts SET status='APPROVED',rows_written=?,"
            "guard_result_json=? WHERE operation_id=?", (len(batch.rows), json.dumps(payload), batch.operation_id))
        connection.commit()
        before = _all_tables(connection)
        attempts = []
        connection.set_authorizer(_deny_price_writes(attempts))
        replay = commit_price_batch(connection, batch)
        connection.set_authorizer(None)
        assert replay.idempotent_replay and replay.status is ReceiptStatus.APPROVED
        assert replay.rows_written == len(batch.rows)  # Original receipt count, not fresh writes.
        assert not replay.revision_review_required
        assert _all_tables(connection) == before and attempts == []
