from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import threading

import pytest

from quantlab.completed_session import (
    CalendarSessionEvidence,
    CalendarSessionStatus,
    CompletedSessionDecision,
    CompletedSessionReason,
    ProviderCompletionWatermark,
    ProviderPublicationObservation,
    PublicationDelayPolicy,
    evaluate_completed_session,
    normalized_price_row_fingerprint,
)
from quantlab.market_data_operation_identity import (
    OperationIdentityConflict,
    OperationIdentityRequest,
    allocate_operation_identity,
)
from quantlab.operational_admission import IngestionIntent, OperationalAdmission, SymbolIdentityState
from quantlab.preupdate_market_data_guard import AdjustmentBasis, CoverageMetadata, CoverageState
from quantlab.transactional_market_data import (
    AttributionState,
    PreparedPriceBatch,
    PreparedPriceRow,
    ReceiptStatus,
    commit_price_batch_shadow,
    initialize_transactional_ingestion_schema,
    migrate_operational_admission_schema,
    migrate_shadow_runtime_foundation_schema,
)


SESSION = date(2026, 9, 5)
OBSERVED = datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)
FINGERPRINT_A = "a" * 64
FINGERPRINT_B = "b" * 64


def _calendar(status: CalendarSessionStatus = CalendarSessionStatus.OPEN_COMPLETED):
    return CalendarSessionEvidence(
        "HOSE",
        "synthetic-vn-calendar",
        "fixture-v1",
        "fixture://calendar/snapshot",
        SESSION,
        status,
        ("fixture://calendar/source",),
    )


def _policy() -> PublicationDelayPolicy:
    return PublicationDelayPolicy(
        "approved-delay-fixture",
        timedelta(minutes=30),
        ("fixture://policy/publication-delay",),
    )


def _observation(
    minutes: int = 0,
    *,
    fingerprint: str = FINGERPRINT_A,
    complete: bool = True,
) -> ProviderPublicationObservation:
    return ProviderPublicationObservation(
        "KBS",
        "AAA",
        SESSION,
        OBSERVED + timedelta(minutes=minutes),
        fingerprint,
        complete,
        (f"fixture://provider/observation/{minutes}",),
    )


def _watermark() -> ProviderCompletionWatermark:
    return ProviderCompletionWatermark(
        "KBS",
        SESSION,
        OBSERVED,
        "fixture-watermark",
        ("fixture://provider/watermark",),
    )


def _evaluate(
    *,
    calendar: CalendarSessionEvidence | None = None,
    observations: tuple[ProviderPublicationObservation, ...] = (),
    watermark: ProviderCompletionWatermark | None = None,
):
    return evaluate_completed_session(
        symbol="AAA",
        provider_identity="KBS",
        target_session=SESSION,
        calendar=_calendar() if calendar is None else calendar,
        observations=observations,
        publication_delay_policy=_policy(),
        completion_watermark=watermark,
    )


def test_completed_ordinary_session_uses_attributable_watermark() -> None:
    result = _evaluate(observations=(_observation(),), watermark=_watermark())
    assert result.decision is CompletedSessionDecision.ADMITTED
    assert result.reasons == (
        CompletedSessionReason.COMPLETED_SESSION_ESTABLISHED_BY_WATERMARK,
    )


@pytest.mark.parametrize(
    ("status", "reason"),
    (
        (CalendarSessionStatus.NON_SESSION, CompletedSessionReason.NON_SESSION),
        (CalendarSessionStatus.EXCEPTIONAL_CLOSURE, CompletedSessionReason.EXCEPTIONAL_CLOSURE),
    ),
)
def test_calendar_non_sessions_are_rejected(
    status: CalendarSessionStatus,
    reason: CompletedSessionReason,
) -> None:
    result = _evaluate(calendar=_calendar(status), observations=(_observation(),), watermark=_watermark())
    assert result.decision is CompletedSessionDecision.REJECTED
    assert reason in result.reasons


def test_missing_calendar_evidence_is_unresolved() -> None:
    result = evaluate_completed_session(
        symbol="AAA",
        provider_identity="KBS",
        target_session=SESSION,
        calendar=None,
        observations=(_observation(),),
        publication_delay_policy=_policy(),
        completion_watermark=_watermark(),
    )
    assert result.decision is CompletedSessionDecision.UNRESOLVED
    assert CompletedSessionReason.CALENDAR_EVIDENCE_MISSING in result.reasons


def test_missing_watermark_and_single_observation_is_unresolved() -> None:
    result = _evaluate(observations=(_observation(),))
    assert result.decision is CompletedSessionDecision.UNRESOLVED
    assert CompletedSessionReason.PUBLICATION_NOT_ESTABLISHED in result.reasons


def test_two_matching_delayed_observations_establish_publication() -> None:
    result = _evaluate(observations=(_observation(), _observation(30)))
    assert result.decision is CompletedSessionDecision.ADMITTED
    assert CompletedSessionReason.COMPLETED_SESSION_ESTABLISHED_BY_STABLE_OBSERVATIONS in result.reasons


def test_two_differing_observations_are_rejected() -> None:
    result = _evaluate(observations=(_observation(), _observation(30, fingerprint=FINGERPRINT_B)))
    assert result.decision is CompletedSessionDecision.REJECTED
    assert CompletedSessionReason.PUBLICATION_OBSERVATIONS_DIFFER in result.reasons


def test_incomplete_current_session_is_unresolved_even_with_watermark() -> None:
    result = _evaluate(observations=(_observation(complete=False),), watermark=_watermark())
    assert result.decision is CompletedSessionDecision.UNRESOLVED
    assert CompletedSessionReason.TARGET_ROW_INCOMPLETE in result.reasons


def test_valid_session_does_not_require_a_vnindex_row() -> None:
    result = _evaluate(observations=(_observation(), _observation(30)))
    assert result.decision is CompletedSessionDecision.ADMITTED
    assert "VNINDEX" not in str(result.as_dict())


def _identity_database(tmp_path: Path, name: str = "identity.db") -> Path:
    path = tmp_path / name
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE prices(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                time TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL, volume REAL,
                UNIQUE(symbol,time)
            )
            """
        )
        connection.commit()
        initialize_transactional_ingestion_schema(connection)
        migrate_operational_admission_schema(connection)
        migrate_shadow_runtime_foundation_schema(connection)
    return path


def _request(namespace: str = "daily_update") -> OperationIdentityRequest:
    return OperationIdentityRequest(
        "shadow",
        "temporary-fixture-db",
        namespace,
        "AAA",
        "KBS",
        "Quote.history:1D",
        "vnstock",
        "fixture-version",
        date(2026, 9, 3),
        SESSION,
        "HOSE",
        "synthetic-vn-calendar",
        "fixture-v1",
        "fixture://calendar/snapshot",
        SESSION,
        "OPERATIONAL_APPEND_ONLY_V1",
    )


def test_identical_operation_retry_reuses_generation_and_operation_id(tmp_path: Path) -> None:
    path = _identity_database(tmp_path)
    with sqlite3.connect(path) as connection:
        first = allocate_operation_identity(connection, _request(), FINGERPRINT_A)
        retry = allocate_operation_identity(connection, _request(), FINGERPRINT_A)
    assert first.observation_generation == 0
    assert retry.operation_id == first.operation_id
    assert retry.idempotent_replay is True


def test_later_provider_revision_is_linked_without_overwrite(tmp_path: Path) -> None:
    path = _identity_database(tmp_path)
    with sqlite3.connect(path) as connection:
        first = allocate_operation_identity(connection, _request(), FINGERPRINT_A)
        revision = allocate_operation_identity(connection, _request(), FINGERPRINT_B)
        rows = tuple(connection.execute(
            "SELECT operation_id,observation_generation,revision_of FROM market_operation_observations ORDER BY observation_generation"
        ))
    assert revision.observation_generation == 1
    assert revision.revision_of == first.operation_id
    assert rows == ((first.operation_id, 0, None), (revision.operation_id, 1, first.operation_id))


def test_entrypoint_namespaces_cannot_collide(tmp_path: Path) -> None:
    path = _identity_database(tmp_path)
    with sqlite3.connect(path) as connection:
        update = allocate_operation_identity(connection, _request("daily_update"), FINGERPRINT_A)
        backfill = allocate_operation_identity(connection, _request("backfill_stage"), FINGERPRINT_A)
    assert update.request_identity != backfill.request_identity
    assert update.operation_id != backfill.operation_id


def test_incompatible_stored_operation_content_raises_conflict(tmp_path: Path) -> None:
    path = _identity_database(tmp_path)
    with sqlite3.connect(path) as connection:
        allocated = allocate_operation_identity(connection, _request(), FINGERPRINT_A)
        connection.execute(
            "UPDATE market_operation_observations SET operation_payload_json='{}' WHERE operation_id=?",
            (allocated.operation_id,),
        )
        connection.commit()
        with pytest.raises(OperationIdentityConflict, match="incompatible"):
            allocate_operation_identity(connection, _request(), FINGERPRINT_A)


def test_concurrent_identical_allocations_are_deterministic(tmp_path: Path) -> None:
    path = _identity_database(tmp_path)
    barrier = threading.Barrier(2)

    def run():
        with sqlite3.connect(path, timeout=5) as connection:
            barrier.wait(timeout=5)
            return allocate_operation_identity(connection, _request(), FINGERPRINT_A)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = tuple(executor.map(lambda _value: run(), range(2)))
    assert first.operation_id == second.operation_id
    assert {first.idempotent_replay, second.idempotent_replay} == {False, True}
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM market_operation_observations").fetchone()[0] == 1


def test_injected_allocator_failure_rolls_back_without_phantom_generation(tmp_path: Path) -> None:
    path = _identity_database(tmp_path)

    def fail(stage: str) -> None:
        if stage == "after_generation_insert":
            raise RuntimeError("injected allocation failure")

    with sqlite3.connect(path) as connection:
        with pytest.raises(RuntimeError, match="injected allocation failure"):
            allocate_operation_identity(connection, _request(), FINGERPRINT_A, failure_injector=fail)
        assert connection.execute("SELECT COUNT(*) FROM market_operation_requests").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM market_operation_observations").fetchone()[0] == 0


def _price(day: int, *, close_addition: float = 0.5) -> PreparedPriceRow:
    value = 100.0 + day
    return PreparedPriceRow(
        "AAA", date(2026, 9, 1) + timedelta(days=day),
        value, value + 1, value - 1, value + close_addition, 1000 + day,
    )


def _shadow_database(tmp_path: Path) -> Path:
    path = _identity_database(tmp_path, "shadow.db")
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)",
            tuple(
                (row.symbol, row.session.isoformat(), row.open, row.high, row.low, row.close, row.volume)
                for row in (_price(0), _price(1), _price(2), _price(3))
            ),
        )
        connection.commit()
    return path


def _batch(completed_session, *, target_close_addition: float = 0.5) -> PreparedPriceBatch:
    rows = (_price(2), _price(3), _price(4, close_addition=target_close_addition))
    return PreparedPriceBatch(
        operation_id="placeholder-not-production-identity",
        symbol="AAA",
        requested_start=rows[0].session,
        requested_end=rows[-1].session,
        rows=rows,
        coverage=CoverageMetadata(
            rows[0].session,
            rows[-1].session,
            rows[0].session,
            rows[-1].session,
            CoverageState.VERIFIED_COMPLETE,
            ("fixture://coverage",),
        ),
        provider_identity="KBS",
        endpoint_identity="Quote.history:1D",
        package_name="vnstock",
        package_version="fixture-version",
        source_verification_state=AttributionState.VERIFIED,
        source_references=("fixture://provider",),
        price_unit="provider_native",
        price_unit_verification_state=AttributionState.UNKNOWN,
        price_unit_references=(),
        claimed_adjustment_basis=AdjustmentBasis.UNKNOWN,
        adjustment_verification_state=AttributionState.UNKNOWN,
        adjustment_evidence_references=(),
        ingestion_intent=IngestionIntent.INCREMENTAL_UPDATE,
        symbol_identity_state=SymbolIdentityState.CONSISTENT_REQUEST_SYMBOL,
        symbol_identity_references=("fixture://symbol/AAA",),
        completed_session_result=completed_session,
    )


def _completed_for_row(row: PreparedPriceRow):
    fingerprint = normalized_price_row_fingerprint(
        symbol=row.symbol,
        session=row.session,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
    )
    return _evaluate(
        observations=(_observation(fingerprint=fingerprint),),
        watermark=_watermark(),
    )


def test_unresolved_completed_session_cannot_mutate_shadow_prices(tmp_path: Path) -> None:
    path = _shadow_database(tmp_path)
    unresolved = _evaluate(observations=(_observation(),))
    batch = _batch(unresolved)
    with sqlite3.connect(path) as connection:
        before = tuple(connection.execute("SELECT * FROM prices ORDER BY time"))
    result = commit_price_batch_shadow(path, batch)
    with sqlite3.connect(path) as connection:
        after = tuple(connection.execute("SELECT * FROM prices ORDER BY time"))
    assert result.status is ReceiptStatus.REJECTED
    assert result.operational_result.admission is OperationalAdmission.OPERATIONAL_REJECTED
    assert before == after


def test_shadow_failure_after_identity_allocation_rolls_back_generation(tmp_path: Path) -> None:
    path = _shadow_database(tmp_path)
    completed = _completed_for_row(_price(4))
    batch = _batch(completed)

    def fail(stage: str) -> None:
        if stage == "after_operation_identity":
            raise RuntimeError("injected shadow failure")

    with pytest.raises(RuntimeError, match="injected shadow failure"):
        commit_price_batch_shadow(
            path,
            batch,
            operation_identity_request=_request(),
            failure_injector=fail,
        )
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM market_operation_observations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM market_ingestion_receipts").fetchone()[0] == 0


def test_linked_revision_observation_does_not_authorize_price_rewrite(tmp_path: Path) -> None:
    path = _shadow_database(tmp_path)
    first = commit_price_batch_shadow(
        path,
        _batch(_completed_for_row(_price(4))),
        operation_identity_request=_request(),
    )
    changed_row = _price(4, close_addition=0.6)
    revision = commit_price_batch_shadow(
        path,
        _batch(_completed_for_row(changed_row), target_close_addition=0.6),
        operation_identity_request=_request(),
    )
    assert first.status is ReceiptStatus.APPROVED
    assert revision.status is ReceiptStatus.REJECTED
    assert revision.operation_identity is not None
    assert revision.operation_identity.observation_generation == 1
    assert revision.operation_identity.revision_of == first.operation_id
    with sqlite3.connect(path) as connection:
        stored_close = connection.execute(
            "SELECT close FROM prices WHERE symbol='AAA' AND time=?",
            (SESSION.isoformat(),),
        ).fetchone()[0]
    assert stored_close == _price(4).close
