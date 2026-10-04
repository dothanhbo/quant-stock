from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from quantlab.completed_session import (
    CalendarSessionEvidence, CalendarSessionStatus, CompletedSessionDecision,
    CompletedSessionReason, ProviderCompletionWatermark, ProviderPublicationObservation,
    PublicationDelayPolicy, SymbolSessionEvidence, SymbolSessionStatus,
    evaluate_completed_session, normalized_price_row_fingerprint,
)
from quantlab.operational_admission import (
    IngestionIntent, OperationalAdmission, OperationalReason, SymbolIdentityState,
    evaluate_operational_admission,
)
from quantlab.preupdate_market_data_guard import AdjustmentBasis, CoverageMetadata, CoverageState
from quantlab.symbol_session_packet_adapter import load_symbol_session_packet
from quantlab.transactional_market_data import (
    AttributionState, PreparedPriceBatch, PreparedPriceRow, ReceiptStatus,
    commit_price_batch_shadow, initialize_transactional_ingestion_schema,
    migrate_operational_admission_schema, migrate_shadow_runtime_foundation_schema,
)

ROOT = Path(__file__).resolve().parents[1]
PACKET_REL = Path("research/symbol_session_evidence/CTR_2022-02-17/evidence_packet.json")
# Pin the reviewed version at a41fd4c; do not learn the trusted hash from input.
PACKET_SHA256 = "87825cae36bea85cdf42b6daa8e477c98d6aec6b344e42ab8893f057a7e44a7e"
TARGET = date(2022, 2, 17)
REFS = ("fixture://synthetic-calendar-completion-and-price-only",)


def _completion(evidence: SymbolSessionEvidence):
    target = evidence.target_session
    observed = datetime.combine(target, datetime.min.time(), timezone.utc) + timedelta(hours=12)
    fingerprint = normalized_price_row_fingerprint(
        symbol=evidence.symbol, session=target, open=10, high=11, low=9, close=10.5, volume=100,
    )
    return evaluate_completed_session(
        symbol=evidence.symbol, provider_identity="FIXTURE_ONLY", target_session=target,
        calendar=CalendarSessionEvidence(evidence.venue, "fixture-only", "v1", REFS[0],
            target, CalendarSessionStatus.OPEN_COMPLETED, REFS),
        symbol_session=evidence,
        observations=tuple(ProviderPublicationObservation("FIXTURE_ONLY", evidence.symbol,
            target, observed + delta, fingerprint, True, REFS)
            for delta in (timedelta(), timedelta(hours=1))),
        publication_delay_policy=PublicationDelayPolicy("fixture-delay", timedelta(minutes=30), REFS),
        completion_watermark=ProviderCompletionWatermark("FIXTURE_ONLY", target, observed, REFS[0], REFS),
    )


def _assert_shadow_zero_writes(tmp_path: Path, evidence: SymbolSessionEvidence):
    """Exercise actual D4B2 admission and SQL on a new synthetic SQLite target."""
    completed = _completion(evidence)
    target = evidence.target_session
    rows = tuple(PreparedPriceRow(evidence.symbol, target - timedelta(days=offset),
        10, 11, 9, 10.5, 100) for offset in (2, 1, 0))
    batch = PreparedPriceBatch(
        operation_id="fixture-ctr-packet", symbol=evidence.symbol,
        requested_start=rows[0].session, requested_end=target, rows=rows,
        coverage=CoverageMetadata(rows[0].session, target, rows[0].session, target,
            CoverageState.VERIFIED_COMPLETE, REFS),
        provider_identity="FIXTURE_ONLY", endpoint_identity="offline:1D",
        package_name="synthetic", package_version="fixture-v1",
        source_verification_state=AttributionState.VERIFIED, source_references=REFS,
        price_unit="synthetic", price_unit_verification_state=AttributionState.UNKNOWN,
        price_unit_references=(), claimed_adjustment_basis=AdjustmentBasis.UNKNOWN,
        adjustment_verification_state=AttributionState.UNKNOWN, adjustment_evidence_references=(),
        ingestion_intent=IngestionIntent.INCREMENTAL_UPDATE,
        symbol_identity_state=SymbolIdentityState.CONSISTENT_REQUEST_SYMBOL,
        symbol_identity_references=REFS, completed_session_result=completed,
    )
    with sqlite3.connect(tmp_path / "synthetic-shadow.sqlite") as connection:
        connection.execute("CREATE TABLE prices(id INTEGER PRIMARY KEY, symbol TEXT NOT NULL,"
            " time TEXT NOT NULL, open REAL, high REAL, low REAL, close REAL, volume REAL, UNIQUE(symbol,time))")
        connection.executemany("INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)",
            [(r.symbol, r.session.isoformat(), r.open, r.high, r.low, r.close, r.volume) for r in rows[:2]])
        connection.commit()
        initialize_transactional_ingestion_schema(connection)
        migrate_operational_admission_schema(connection)
        migrate_shadow_runtime_foundation_schema(connection)
        prices_before = connection.execute("SELECT * FROM prices ORDER BY id").fetchall()
        links_before = connection.execute("SELECT * FROM market_price_provenance").fetchall()
        attempts = []

        def deny_price_mutation(action, table, column, database, trigger):
            if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE) and table in (
                "prices", "market_price_provenance",
            ):
                attempts.append((action, table))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(deny_price_mutation)
        result = commit_price_batch_shadow(connection, batch)
        connection.set_authorizer(None)
        assert attempts == []
        assert result.status is ReceiptStatus.REJECTED
        assert result.rows_written == 0
        assert result.operational_result.admission is OperationalAdmission.OPERATIONAL_REJECTED
        assert result.operational_result.research_eligible is False
        assert connection.execute("SELECT * FROM prices ORDER BY id").fetchall() == prices_before
        assert connection.execute("SELECT * FROM market_price_provenance").fetchall() == links_before
        assert connection.execute("SELECT COUNT(*) FROM market_ingestion_manifests").fetchone() == (0,)
        receipt = connection.execute("SELECT completed_session_result_json,operational_result_json,research_eligible"
            " FROM market_ingestion_receipts WHERE operation_id=?", (batch.operation_id,)).fetchone()
        assert receipt[2] == 0
        assert json.loads(receipt[1])["research_eligible"] is False
        persisted = json.loads(receipt[0])
        assert persisted["symbol_session"]["status"] == evidence.status.value
        assert persisted["symbol_session"]["source_identity"] == evidence.source_identity
        assert persisted["symbol_session"]["snapshot_identity"] == evidence.snapshot_identity
        assert set(persisted["symbol_session"]["source_references"]) == set(evidence.source_references)
        assert persisted["evidence_sha256"] == completed.evidence_sha256
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

        # Prove the same fixture calendar/publication and price inputs would pass
        # both gates with a synthetic lifecycle control, without executing a write.
        control_evidence = replace(evidence, status=SymbolSessionStatus.TRADING_CONFIRMED,
            source_identity="fixture-only-control", snapshot_identity="fixture-only-control", source_references=REFS)
        control_completed = _completion(control_evidence)
        assert control_completed.admitted
        existing = [dict(zip(("id", "symbol", "time", "open", "high", "low", "close", "volume"), r))
            for r in prices_before]
        control = evaluate_operational_admission(replace(batch, completed_session_result=control_completed),
            result.guard_result, existing)
        assert control.admission is OperationalAdmission.OPERATIONAL_APPEND_ONLY_ACCEPTED
        assert control.research_eligible is False
    return completed, result


def test_reviewed_ctr_packet_reaches_shadow_rejection_with_retained_attribution(tmp_path):
    evidence = load_symbol_session_packet(ROOT / PACKET_REL, repository_root=ROOT,
        expected_packet_sha256=PACKET_SHA256, symbol="CTR", venue="HOSE", target_session=TARGET)
    assert evidence.status is SymbolSessionStatus.NOT_TRADING
    packet = json.loads((ROOT / PACKET_REL).read_text(encoding="utf-8"))
    mapping = packet["symbol_session_schema_mapping"]
    assert set(mapping["source_references"]) <= set(evidence.source_references)
    assert "evidence-packet:sha256:" + PACKET_SHA256 in evidence.source_references
    assert evidence.snapshot_identity == mapping["snapshot_identity"]
    assert packet["point_in_time"]["historical_availability_established"] is False
    completed, result = _assert_shadow_zero_writes(tmp_path, evidence)
    assert completed.decision is CompletedSessionDecision.REJECTED
    assert completed.reasons == (CompletedSessionReason.SYMBOL_NOT_TRADING,)
    assert OperationalReason.SYMBOL_SESSION_NOT_TRADING in result.operational_result.reasons


@pytest.mark.parametrize(("symbol", "venue", "session"), (
    ("AAA", "HOSE", TARGET), ("CTR", "HNX", TARGET),
    ("CTR", "HOSE", date(2022, 2, 18)),
    ("CTR", "HOSE", date(2022, 2, 23)), ("CTR", "HOSE", date(2022, 2, 24)),
))
def test_packet_cannot_qualify_another_identity_or_future_session(tmp_path, symbol, venue, session):
    evidence = load_symbol_session_packet(ROOT / PACKET_REL, repository_root=ROOT,
        expected_packet_sha256=PACKET_SHA256, symbol=symbol, venue=venue, target_session=session)
    assert evidence.status is SymbolSessionStatus.UNKNOWN
    assert evidence.source_identity == "packet-unresolved:PACKET_IDENTITY_MISMATCH"
    assert not evidence.attributable
    completed, _ = _assert_shadow_zero_writes(tmp_path, evidence)
    assert completed.decision is CompletedSessionDecision.UNRESOLVED


@pytest.mark.parametrize("damage", (
    "packet_missing", "packet_hash", "snapshot_missing", "snapshot_hash", "mapping_symbol",
    "mapping_venue", "mapping_session", "trading_mapping", "malformed_relocations",
))
def test_invalid_packet_or_snapshot_is_unknown_through_shadow(tmp_path, damage):
    # Copy evidence bytes only into a disposable fixture. Original packet and all
    # sources remain untouched; changed packet pins below are negative test inputs.
    root = tmp_path / "repository"
    shutil.copytree((ROOT / PACKET_REL).parent, (root / PACKET_REL).parent)
    path = root / PACKET_REL
    packet = json.loads(path.read_text(encoding="utf-8"))
    expected_hash = PACKET_SHA256
    if damage == "packet_missing":
        path.unlink()
    elif damage == "packet_hash":
        path.write_bytes(path.read_bytes() + b" ")
    elif damage.startswith("snapshot_"):
        source = next(s for s in packet["sources"] if s["source_id"] == "hose-first-trading-191-20220215")
        snapshot = root / packet["checkpoint"]["snapshot_paths"][source["snapshot_path"]]
        if damage == "snapshot_missing":
            snapshot.unlink()
        else:
            content = snapshot.read_bytes()
            # Preserve length, so rejection specifically exercises SHA-256.
            snapshot.write_bytes(bytes([content[0] ^ 1]) + content[1:])
    else:
        mapping = packet["symbol_session_schema_mapping"]
        if damage == "malformed_relocations": packet["checkpoint"]["snapshot_paths"] = []
        elif damage == "mapping_symbol": mapping["symbol"] = "AAA"
        elif damage == "mapping_venue": mapping["venue"] = "HNX"
        elif damage == "mapping_session": mapping["target_session"] = "2022-02-18"
        else: mapping["status"] = packet["trading_status"] = "TRADING_CONFIRMED"
        path.write_text(json.dumps(packet), encoding="utf-8")
        expected_hash = sha256(path.read_bytes()).hexdigest()
    evidence = load_symbol_session_packet(path, repository_root=root,
        expected_packet_sha256=expected_hash, symbol="CTR", venue="HOSE", target_session=TARGET)
    assert evidence.status is SymbolSessionStatus.UNKNOWN
    assert evidence.source_references == ()
    completed, result = _assert_shadow_zero_writes(tmp_path, evidence)
    assert completed.decision is CompletedSessionDecision.UNRESOLVED
    assert OperationalReason.COMPLETED_SESSION_UNRESOLVED in result.operational_result.reasons


def test_missing_reviewed_pin_is_unknown_through_shadow(tmp_path):
    evidence = load_symbol_session_packet(ROOT / PACKET_REL, repository_root=ROOT,
        expected_packet_sha256=None, symbol="CTR", venue="HOSE", target_session=TARGET)
    assert evidence.status is SymbolSessionStatus.UNKNOWN
    assert evidence.source_identity == "packet-unresolved:PACKET_PIN_INVALID"
    assert not evidence.attributable
    completed, _ = _assert_shadow_zero_writes(tmp_path, evidence)
    assert completed.decision is CompletedSessionDecision.UNRESOLVED
