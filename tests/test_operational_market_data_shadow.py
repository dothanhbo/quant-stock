from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import threading

import pandas as pd
import pytest

from quantlab.completed_session import (
    CalendarSessionEvidence,
    CalendarSessionStatus,
    ProviderCompletionWatermark,
    ProviderPublicationObservation,
    PublicationDelayPolicy,
    SymbolSessionEvidence,
    SymbolSessionStatus,
    evaluate_completed_session,
    normalized_price_row_fingerprint,
)
from quantlab.market_data_shadow_adapter import prepare_dataframe_price_batch
from quantlab.operational_admission import (
    IngestionIntent,
    OPERATIONAL_ONLY,
    OperationalAdmission,
    OperationalReason,
    ShadowDailyStatus,
    SymbolIdentityState,
    shadow_daily_status,
)
from quantlab.preupdate_market_data_guard import (
    AdjustmentBasis,
    CoverageMetadata,
    CoverageState,
    GuardDecision,
    GuardReason,
)
from quantlab.transactional_market_data import (
    AttributionState,
    D4B2_MIGRATION_ID,
    OperationIdentityConflict,
    ReceiptStatus,
    commit_price_batch_shadow,
    initialize_transactional_ingestion_schema,
    migrate_operational_admission_schema,
    migrate_shadow_runtime_foundation_schema,
    rehearse_operational_migration,
)


START = date(2026, 9, 1)


def _values(day: int, *, scale: float = 1.0, close_addition: float = 0.5):
    base = 100.0 + day
    return (
        "AAA",
        (START + timedelta(days=day)).isoformat(),
        base * scale,
        (base + 1.0) * scale,
        (base - 1.0) * scale,
        (base + close_addition) * scale,
        1_000.0 + day,
    )


def _frame(
    days: tuple[int, ...],
    *,
    scale: float = 1.0,
    changed_close_day: int | None = None,
) -> pd.DataFrame:
    rows = []
    for day in days:
        addition = 0.6 if day == changed_close_day else 0.5
        rows.append(_values(day, scale=scale, close_addition=addition))
    return pd.DataFrame(
        rows,
        columns=("symbol", "time", "open", "high", "low", "close", "volume"),
    )


def _database(
    tmp_path: Path,
    *,
    name: str = "market.db",
    days: tuple[int, ...] = (0, 1, 2, 3),
    migrate: bool = True,
    unique: bool = True,
) -> Path:
    path = tmp_path / name
    uniqueness = ", UNIQUE(symbol,time)" if unique else ""
    with sqlite3.connect(path) as connection:
        connection.execute(
            f"""
            CREATE TABLE prices(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                time TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL, volume REAL
                {uniqueness}
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO prices(symbol,time,open,high,low,close,volume)
            VALUES(?,?,?,?,?,?,?)
            """,
            tuple(_values(day) for day in days),
        )
        connection.commit()
        initialize_transactional_ingestion_schema(connection)
        if migrate:
            migrate_operational_admission_schema(connection)
            migrate_shadow_runtime_foundation_schema(connection)
    return path


def _batch(
    operation_id: str,
    days: tuple[int, ...] = (2, 3, 4),
    *,
    frame: pd.DataFrame | None = None,
    intent: IngestionIntent = IngestionIntent.INCREMENTAL_UPDATE,
    coverage_state: CoverageState = CoverageState.VERIFIED_COMPLETE,
    identity_state: SymbolIdentityState = SymbolIdentityState.CONSISTENT_REQUEST_SYMBOL,
    symbol_session_status: SymbolSessionStatus = SymbolSessionStatus.TRADING_CONFIRMED,
):
    candidate = _frame(days) if frame is None else frame
    start = START + timedelta(days=min(days))
    end = START + timedelta(days=max(days))
    coverage = CoverageMetadata(
        start,
        end,
        start,
        end,
        coverage_state,
        ("normalized-response:fixture",) if coverage_state is CoverageState.VERIFIED_COMPLETE else (),
    )
    observed_at = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    target_row = candidate.sort_values("time").iloc[-1]
    target_fingerprint = normalized_price_row_fingerprint(
        symbol="AAA",
        session=end,
        open=target_row["open"],
        high=target_row["high"],
        low=target_row["low"],
        close=target_row["close"],
        volume=target_row["volume"],
    )
    completed_session = evaluate_completed_session(
        symbol="AAA",
        provider_identity="KBS",
        target_session=end,
        calendar=CalendarSessionEvidence(
            "HOSE",
            "synthetic-vn-calendar",
            "fixture-v1",
            "fixture://calendar/snapshot",
            end,
            CalendarSessionStatus.OPEN_COMPLETED,
            ("fixture://calendar/source",),
        ),
        symbol_session=SymbolSessionEvidence(
            "AAA",
            "HOSE",
            end,
            symbol_session_status,
            "synthetic-symbol-session-register",
            "fixture-symbol-session-v1",
            ("fixture://symbol-session/source",),
        ),
        observations=(ProviderPublicationObservation(
            "KBS",
            "AAA",
            end,
            observed_at,
            target_fingerprint,
            True,
            ("fixture://provider/observation",),
        ),),
        publication_delay_policy=PublicationDelayPolicy(
            "fixture-delay-policy",
            timedelta(minutes=30),
            ("fixture://policy/publication-delay",),
        ),
        completion_watermark=ProviderCompletionWatermark(
            "KBS",
            end,
            observed_at,
            "fixture-watermark",
            ("fixture://provider/watermark",),
        ),
    )
    return prepare_dataframe_price_batch(
        candidate,
        operation_id=operation_id,
        symbol="AAA",
        requested_start=start,
        requested_end=end,
        coverage=coverage,
        provider_identity="KBS",
        endpoint_identity="Quote.history:1D",
        package_name="vnstock",
        package_version="fixture-version",
        source_verification_state=AttributionState.VERIFIED,
        source_references=("installed-package:fixture",),
        price_unit="provider_native",
        price_unit_verification_state=AttributionState.UNKNOWN,
        claimed_adjustment_basis=AdjustmentBasis.UNKNOWN,
        adjustment_verification_state=AttributionState.UNKNOWN,
        ingestion_intent=intent,
        symbol_identity_state=identity_state,
        symbol_identity_references=("request-symbol:AAA",) if identity_state in {
            SymbolIdentityState.CONSISTENT_REQUEST_SYMBOL,
            SymbolIdentityState.VERIFIED_STABLE,
        } else (),
        completed_through=end,
        completed_session_result=completed_session,
        corporate_action_verification_state=AttributionState.UNKNOWN,
        archive=None,
    )


def _prices(path: Path):
    with sqlite3.connect(path) as connection:
        return tuple(connection.execute(
            "SELECT id,symbol,time,open,high,low,close,volume FROM prices ORDER BY time"
        ))


def _count(path: Path, table: str, operation_id: str | None = None) -> int:
    with sqlite3.connect(path) as connection:
        if operation_id is None:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        return int(connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE operation_id=?",
            (operation_id,),
        ).fetchone()[0])


def test_ordinary_unknown_adjustment_kbs_update_is_operationally_admitted(tmp_path: Path) -> None:
    path = _database(tmp_path)
    batch = _batch("shadow-daily")

    result = commit_price_batch_shadow(path, batch)

    assert result.guard_result.decision is GuardDecision.INSUFFICIENT_EVIDENCE
    assert result.operational_result.admission is OperationalAdmission.OPERATIONAL_APPEND_ONLY_ACCEPTED
    assert result.status is ReceiptStatus.APPROVED
    assert result.rows_written == 1
    assert shadow_daily_status(result.operational_result) is ShadowDailyStatus.OPERATIONAL_APPEND_ACCEPTED
    assert len(_prices(path)) == 5


@pytest.mark.parametrize(
    ("symbol_status", "operational_reason"),
    (
        (
            SymbolSessionStatus.NOT_TRADING,
            OperationalReason.SYMBOL_SESSION_NOT_TRADING,
        ),
        (
            SymbolSessionStatus.UNKNOWN,
            OperationalReason.SYMBOL_SESSION_UNKNOWN,
        ),
    ),
)
def test_symbol_session_failure_is_durable_and_precedes_price_mutation(
    tmp_path: Path,
    symbol_status: SymbolSessionStatus,
    operational_reason: OperationalReason,
) -> None:
    path = _database(tmp_path)
    before = _prices(path)

    result = commit_price_batch_shadow(
        path,
        _batch("symbol-session-blocked", symbol_session_status=symbol_status),
    )

    assert result.status is ReceiptStatus.REJECTED
    assert result.rows_written == 0
    assert operational_reason in result.operational_result.reasons
    assert _prices(path) == before
    with sqlite3.connect(path) as connection:
        persisted = connection.execute(
            """
            SELECT completed_session_result_json, operational_result_json
            FROM market_ingestion_receipts
            WHERE operation_id=?
            """,
            (result.operation_id,),
        ).fetchone()
    assert persisted is not None
    assert symbol_status.value in persisted[0]
    assert "synthetic-symbol-session-register" in persisted[0]
    assert "fixture-symbol-session-v1" in persisted[0]
    assert "fixture://symbol-session/source" in persisted[0]
    assert operational_reason.value in persisted[1]


def test_multi_session_append_requires_symbol_evidence_for_each_new_date(tmp_path: Path) -> None:
    path = _database(tmp_path)
    before = _prices(path)

    result = commit_price_batch_shadow(path, _batch("multi-session", days=(2, 3, 4, 5)))

    assert result.status is ReceiptStatus.REJECTED
    assert result.rows_written == 0
    assert OperationalReason.SYMBOL_SESSION_COVERAGE_MISSING in result.operational_result.reasons
    assert _prices(path) == before


def test_operational_append_never_rewrites_overlap_rows_or_links(tmp_path: Path) -> None:
    path = _database(tmp_path)
    before = _prices(path)
    result = commit_price_batch_shadow(path, _batch("shadow-no-rewrite"))
    after = _prices(path)

    assert result.operational_result.admitted
    assert after[:4] == before
    with sqlite3.connect(path) as connection:
        links = tuple(connection.execute(
            "SELECT time FROM market_price_provenance ORDER BY time"
        ))
    assert links == (((START + timedelta(days=4)).isoformat(),),)


def test_unknown_semantics_and_research_exclusion_are_durable(tmp_path: Path) -> None:
    path = _database(tmp_path)
    batch = _batch("shadow-semantics")
    result = commit_price_batch_shadow(path, batch)

    assert result.operational_result.data_class == OPERATIONAL_ONLY
    assert result.operational_result.research_eligible is False
    assert result.operational_result.adjustment_mode == "UNKNOWN"
    assert result.operational_result.corporate_action_verification == "UNKNOWN"
    with sqlite3.connect(path) as connection:
        receipt = connection.execute(
            """
            SELECT decision,operational_admission,data_class,research_eligible,
                   adjustment_mode,corporate_action_verification
            FROM market_ingestion_receipts WHERE operation_id=?
            """,
            (batch.operation_id,),
        ).fetchone()
        manifest = connection.execute(
            """
            SELECT raw_payload_sha256,data_class,research_eligible,
                   claimed_adjustment_basis,adjustment_verification_state
            FROM market_ingestion_manifests WHERE operation_id=?
            """,
            (batch.operation_id,),
        ).fetchone()
    assert receipt == (
        "INSUFFICIENT_EVIDENCE",
        "OPERATIONAL_APPEND_ONLY_ACCEPTED",
        "OPERATIONAL_ONLY",
        0,
        "UNKNOWN",
        "UNKNOWN",
    )
    assert manifest == (None, "OPERATIONAL_ONLY", 0, "UNKNOWN", "UNKNOWN")


def test_vhm_style_mixed_adjustment_is_blocked_atomically(tmp_path: Path) -> None:
    path = _database(tmp_path)
    before = _prices(path)
    batch = _batch("shadow-mixed", frame=_frame((2, 3, 4), scale=0.5))

    result = commit_price_batch_shadow(path, batch)

    assert result.guard_result.decision is GuardDecision.BLOCK
    assert GuardReason.UNATTRIBUTED_HISTORICAL_REVISION in result.guard_result.reasons
    assert result.operational_result.admission is OperationalAdmission.OPERATIONAL_REJECTED
    assert _prices(path) == before


def test_historical_revision_is_rejected(tmp_path: Path) -> None:
    path = _database(tmp_path)
    batch = _batch(
        "shadow-revision",
        frame=_frame((2, 3, 4), changed_close_day=2),
    )

    result = commit_price_batch_shadow(path, batch)

    assert result.guard_result.decision is GuardDecision.BLOCK
    assert result.rows_written == 0
    assert _count(path, "market_ingestion_manifests", batch.operation_id) == 0


def test_missing_overlap_is_not_allowlisted(tmp_path: Path) -> None:
    path = _database(tmp_path)
    batch = _batch("shadow-missing-overlap", (4,))

    result = commit_price_batch_shadow(path, batch)

    assert result.guard_result.decision is GuardDecision.INSUFFICIENT_EVIDENCE
    assert GuardReason.MISSING_OVERLAP in result.guard_result.reasons
    assert result.operational_result.admission is OperationalAdmission.OPERATIONAL_REJECTED
    assert result.rows_written == 0


def test_unexpected_guard_reason_fails_closed(tmp_path: Path) -> None:
    path = _database(tmp_path)
    batch = _batch("shadow-unexpected", coverage_state=CoverageState.UNKNOWN)

    result = commit_price_batch_shadow(path, batch)

    assert GuardReason.COVERAGE_UNVERIFIED in result.guard_result.reasons
    assert result.operational_result.admission is OperationalAdmission.OPERATIONAL_REJECTED
    assert len(_prices(path)) == 4


def test_bootstrap_is_staged_without_canonical_promotion(tmp_path: Path) -> None:
    path = _database(tmp_path, days=())
    batch = _batch(
        "shadow-bootstrap",
        (0, 1, 2),
        intent=IngestionIntent.BOOTSTRAP,
    )

    result = commit_price_batch_shadow(path, batch)

    assert result.operational_result.admission is OperationalAdmission.BOOTSTRAP_PENDING
    assert result.status is ReceiptStatus.REJECTED
    assert _count(path, "market_ingestion_staging", batch.operation_id) == 1
    assert _prices(path) == ()


def test_backfill_and_historical_gap_have_distinct_staging_outcomes(tmp_path: Path) -> None:
    backfill_path = _database(tmp_path, name="backfill.db")
    backfill = commit_price_batch_shadow(
        backfill_path,
        _batch("shadow-backfill", intent=IngestionIntent.BACKFILL),
    )
    gap_path = _database(tmp_path, name="gap.db", days=(0, 1, 3))
    gap = commit_price_batch_shadow(
        gap_path,
        _batch("shadow-gap", (1, 2, 3)),
    )

    assert backfill.operational_result.admission is OperationalAdmission.BACKFILL_REQUIRES_STAGING
    assert gap.operational_result.admission is OperationalAdmission.HISTORICAL_GAP_REQUIRES_REVIEW
    assert _count(backfill_path, "market_ingestion_staging", "shadow-backfill") == 1
    assert _count(gap_path, "market_ingestion_staging", "shadow-gap") == 1


def test_retry_and_operation_conflict_are_deterministic(tmp_path: Path) -> None:
    path = _database(tmp_path)
    batch = _batch("shadow-retry")
    first = commit_price_batch_shadow(path, batch)
    retry = commit_price_batch_shadow(path, batch)
    conflict = _batch(
        "shadow-retry",
        frame=_frame((2, 3, 4), changed_close_day=4),
    )

    assert first.idempotent_replay is False
    assert retry.idempotent_replay is True
    assert retry.operational_result == first.operational_result
    with pytest.raises(OperationIdentityConflict):
        commit_price_batch_shadow(path, conflict)
    assert _count(path, "market_ingestion_receipts", batch.operation_id) == 1


def test_concurrent_competing_writers_serialize_under_begin_immediate(tmp_path: Path) -> None:
    path = _database(tmp_path)
    first = _batch("shadow-concurrent")
    second = _batch(
        "shadow-concurrent",
        frame=_frame((2, 3, 4), changed_close_day=4),
    )
    lock_acquired = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()

    def hold(stage: str) -> None:
        if stage == "after_begin_immediate":
            lock_acquired.set()
            assert release_first.wait(timeout=5)

    def run_first():
        return commit_price_batch_shadow(path, first, failure_injector=hold)

    def run_second():
        second_started.set()
        try:
            return commit_price_batch_shadow(path, second, busy_timeout_seconds=5)
        except OperationIdentityConflict as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_first = executor.submit(run_first)
        assert lock_acquired.wait(timeout=5)
        future_second = executor.submit(run_second)
        assert second_started.wait(timeout=5)
        release_first.set()
        outcomes = (future_first.result(timeout=5), future_second.result(timeout=5))

    assert outcomes[0].operational_result.admitted
    assert isinstance(outcomes[1], OperationIdentityConflict)
    assert len(_prices(path)) == 5


def test_injected_shadow_failure_rolls_back_receipt_manifest_price_and_link(tmp_path: Path) -> None:
    path = _database(tmp_path)
    batch = _batch("shadow-failure")
    before = _prices(path)

    def fail(stage: str) -> None:
        if stage == "after_prices_and_links":
            raise RuntimeError("injected shadow failure")

    with pytest.raises(RuntimeError, match="injected shadow failure"):
        commit_price_batch_shadow(path, batch, failure_injector=fail)

    assert _prices(path) == before
    assert _count(path, "market_ingestion_receipts", batch.operation_id) == 0
    assert _count(path, "market_ingestion_manifests", batch.operation_id) == 0
    assert _count(path, "market_price_provenance", batch.operation_id) == 0


def test_receipt_price_and_current_provenance_are_consistent(tmp_path: Path) -> None:
    path = _database(tmp_path)
    batch = _batch("shadow-consistency")
    result = commit_price_batch_shadow(path, batch)

    with sqlite3.connect(path) as connection:
        receipt_rows = connection.execute(
            "SELECT rows_written FROM market_ingestion_receipts WHERE operation_id=?",
            (batch.operation_id,),
        ).fetchone()[0]
        linked_rows = connection.execute(
            "SELECT COUNT(*) FROM market_price_provenance WHERE operation_id=?",
            (batch.operation_id,),
        ).fetchone()[0]
        manifest_rows = connection.execute(
            "SELECT COUNT(*) FROM market_ingestion_manifests WHERE operation_id=?",
            (batch.operation_id,),
        ).fetchone()[0]
        foreign_key_violations = tuple(connection.execute("PRAGMA foreign_key_check"))
    assert receipt_rows == linked_rows == result.rows_written == 1
    assert manifest_rows == 1
    assert foreign_key_violations == ()


def test_migration_is_additive_idempotent_and_legacy_compatible(tmp_path: Path) -> None:
    path = _database(tmp_path, migrate=False)
    before = _prices(path)
    with sqlite3.connect(path) as connection:
        assert migrate_operational_admission_schema(connection) is True
        assert migrate_operational_admission_schema(connection) is False
        columns = {row[1] for row in connection.execute(
            "PRAGMA table_info(market_ingestion_receipts)"
        )}
        migration_count = connection.execute(
            "SELECT COUNT(*) FROM market_schema_migrations WHERE migration_id=?",
            (D4B2_MIGRATION_ID,),
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO market_ingestion_staging(
                    operation_id,batch_fingerprint,symbol,ingestion_intent,
                    staging_outcome,normalized_content_sha256,normalized_rows_json,
                    metadata_json,data_class,research_eligible,created_at_utc
                ) VALUES('missing','x','AAA','BACKFILL','BACKFILL_REQUIRES_STAGING',
                         'x','[]','{}','OPERATIONAL_ONLY',0,'now')
                """
            )
    assert "operational_admission" in columns
    assert migration_count == 1
    assert _prices(path) == before


def test_migration_failure_rolls_back_and_clone_rehearsal_restores_bytes(tmp_path: Path) -> None:
    failure_path = _database(tmp_path, name="failure.db", migrate=False)

    def fail(stage: str) -> None:
        if stage == "after_receipt_columns":
            raise RuntimeError("migration failure")

    with sqlite3.connect(failure_path) as connection:
        with pytest.raises(RuntimeError, match="migration failure"):
            migrate_operational_admission_schema(connection, failure_injector=fail)
        columns = {row[1] for row in connection.execute(
            "PRAGMA table_info(market_ingestion_receipts)"
        )}
    assert "operational_admission" not in columns

    source = _database(tmp_path, name="rehearsal-source.db", migrate=False)
    result = rehearse_operational_migration(source, tmp_path / "rehearsal")
    assert result.migration_recorded is True
    assert result.unique_price_index_valid is True
    assert result.foreign_key_violations == ()
    assert result.source_sha256 == result.clone_before_sha256
    assert result.rollback_restored_bytes is True


def test_migration_refuses_prices_without_required_unique_index(tmp_path: Path) -> None:
    path = _database(tmp_path, name="no-unique.db", migrate=False, unique=False)
    with sqlite3.connect(path) as connection:
        with pytest.raises(RuntimeError, match=r"unique \(symbol, time\) index"):
            migrate_operational_admission_schema(connection)
