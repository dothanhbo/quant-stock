"""Offline synthetic D4 rehearsal; canonical source is opened read-only for backup.

Run from the repository root with the bundled Python. Output must be a new,
explicit directory. No provider or production module is imported.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quantlab.completed_session import (
    CalendarSessionEvidence, CalendarSessionStatus, ProviderCompletionWatermark,
    ProviderPublicationObservation, PublicationDelayPolicy, SymbolSessionEvidence,
    SymbolSessionStatus, evaluate_completed_session, normalized_price_row_fingerprint,
)
from quantlab.market_data_operation_identity import OperationIdentityRequest
from quantlab.operational_admission import IngestionIntent, SymbolIdentityState
from quantlab.preupdate_market_data_guard import (
    AdjustmentBasis, CoverageMetadata, CoverageState, EvidenceKind, RevisionClaim,
    RevisionEvidence,
)
from quantlab.transactional_market_data import (
    AttributionState, PreparedPriceBatch, PreparedPriceRow, SCHEMA_VERSION,
    commit_price_batch_shadow, initialize_transactional_ingestion_schema,
    migrate_operational_admission_schema, migrate_shadow_runtime_foundation_schema,
)

SYMBOL = "ZZD4TEST"
START = date(2026, 9, 1)
OBSERVED = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
TABLES = (
    "prices", "market_price_provenance", "market_ingestion_receipts",
    "market_ingestion_manifests", "market_ingestion_staging",
    "market_operation_requests", "market_operation_observations",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return sha256(stream.read()).hexdigest()


def row(day: int, addition: float = 0.5) -> PreparedPriceRow:
    base = 100.0 + day
    return PreparedPriceRow(SYMBOL, START + timedelta(days=day), base, base + 1,
                            base - 1, base + addition, 1000 + day)


def batch(days: tuple[int, ...], *, revision_addition: float | None = None,
          accepted_operation: str | None = None) -> PreparedPriceBatch:
    rows = tuple(row(day, revision_addition if day == 2 and revision_addition is not None else 0.5)
                 for day in days)
    target = rows[-1]
    fingerprint = normalized_price_row_fingerprint(
        symbol=SYMBOL, session=target.session, open=target.open, high=target.high,
        low=target.low, close=target.close, volume=target.volume,
    )
    completed = evaluate_completed_session(
        symbol=SYMBOL, provider_identity="SYNTHETIC_D4_REHEARSAL",
        target_session=target.session,
        calendar=CalendarSessionEvidence("HOSE", "synthetic-calendar", "fixture-v1",
            "fixture://d4-clone/calendar", target.session,
            CalendarSessionStatus.OPEN_COMPLETED, ("fixture://d4-clone/calendar-source",)),
        symbol_session=SymbolSessionEvidence(SYMBOL, "HOSE", target.session,
            SymbolSessionStatus.TRADING_CONFIRMED, "synthetic-lifecycle", "fixture-v1",
            ("fixture://d4-clone/lifecycle",)),
        observations=(ProviderPublicationObservation("SYNTHETIC_D4_REHEARSAL", SYMBOL,
            target.session, OBSERVED, fingerprint, True, ("fixture://d4-clone/observation",)),),
        publication_delay_policy=PublicationDelayPolicy("synthetic-delay", timedelta(minutes=30),
            ("fixture://d4-clone/delay",)),
        completion_watermark=ProviderCompletionWatermark("SYNTHETIC_D4_REHEARSAL",
            target.session, OBSERVED, "fixture-watermark", ("fixture://d4-clone/watermark",)),
    )
    evidence = ()
    if revision_addition is not None:
        require(accepted_operation is not None, "revision must identify accepted row source")
        evidence = (RevisionEvidence(SYMBOL, EvidenceKind.PROVIDER_CORRECTION,
            (RevisionClaim(row(2).session, "close", row(2).close, row(2, revision_addition).close),),
            ("fixture://d4-clone/exact-correction", f"market-ingestion-operation:{accepted_operation}")),)
    return PreparedPriceBatch(
        operation_id="placeholder-replaced-by-durable-allocator", symbol=SYMBOL,
        requested_start=rows[0].session, requested_end=target.session, rows=rows,
        coverage=CoverageMetadata(rows[0].session, target.session, rows[0].session,
            target.session, CoverageState.VERIFIED_COMPLETE, ("fixture://d4-clone/coverage",)),
        provider_identity="SYNTHETIC_D4_REHEARSAL", endpoint_identity="offline-fixture:1D",
        package_name="synthetic-no-provider-package", package_version="fixture-v1",
        source_verification_state=AttributionState.VERIFIED,
        source_references=("fixture://d4-clone/provider",), price_unit="synthetic-unit",
        price_unit_verification_state=AttributionState.VERIFIED,
        price_unit_references=("fixture://d4-clone/unit",),
        claimed_adjustment_basis=AdjustmentBasis.RAW,
        adjustment_verification_state=AttributionState.VERIFIED,
        adjustment_evidence_references=("fixture://d4-clone/basis",),
        revision_evidence=evidence, ingestion_intent=IngestionIntent.INCREMENTAL_UPDATE,
        symbol_identity_state=SymbolIdentityState.CONSISTENT_REQUEST_SYMBOL,
        symbol_identity_references=("fixture://d4-clone/security",),
        completed_session_result=completed,
    )


def request(candidate: PreparedPriceBatch) -> OperationIdentityRequest:
    return OperationIdentityRequest(
        "DISPOSABLE_SYNTHETIC_REHEARSAL", "canonical-backup-clone/synthetic/v1",
        "manual_update", SYMBOL, candidate.provider_identity, candidate.endpoint_identity,
        candidate.package_name, candidate.package_version, candidate.requested_start,
        candidate.requested_end, "HOSE", "synthetic-calendar", "fixture-v1",
        "fixture://d4-clone/calendar", candidate.rows[-1].session,
        "OPERATIONAL_APPEND_ONLY_V1",
    )


def snapshot(connection: sqlite3.Connection) -> dict[str, object]:
    result = {}
    for table in TABLES:
        digest = sha256()
        count = 0
        for value in connection.execute(f"SELECT * FROM {table} ORDER BY rowid"):
            digest.update((canonical(value) + "\n").encode())
            count += 1
        result[table] = {"count": count, "sha256": digest.hexdigest()}
    return result


def result_record(result) -> dict[str, object]:
    allocation = result.operation_identity
    return {
        "operation_id": result.operation_id, "request_identity": allocation.request_identity,
        "generation": allocation.observation_generation, "revision_of": allocation.revision_of,
        "guard": result.guard_result.decision.value, "status": result.status.value,
        "admission": result.operational_result.admission.value,
        "rows_written": result.rows_written, "replay": result.idempotent_replay,
        "research_eligible": result.operational_result.research_eligible,
    }


def seed(connection: sqlite3.Connection) -> None:
    """Attribute only the two explicitly synthetic seed rows on this clone."""
    candidate = replace(batch((0, 1)), operation_id="d4-synthetic-seed/v1")
    connection.execute("BEGIN IMMEDIATE")
    connection.executemany(
        "INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)",
        [(value.symbol, value.session.isoformat(), value.open, value.high, value.low,
          value.close, value.volume) for value in candidate.rows],
    )
    connection.execute(
        """INSERT INTO market_ingestion_receipts(
            operation_id,batch_fingerprint,symbol,requested_start,requested_end,decision,
            status,reason_codes_json,guard_result_json,rows_written,created_at_utc,
            data_class,research_eligible)
            VALUES(?,?,?,?,?,'PASS','APPROVED','[]','{}',2,?,'OPERATIONAL_ONLY',0)""",
        (candidate.operation_id, candidate.batch_fingerprint, SYMBOL,
         candidate.requested_start.isoformat(), candidate.requested_end.isoformat(), OBSERVED.isoformat()),
    )
    connection.execute(
        """INSERT INTO market_ingestion_manifests(
            operation_id,batch_fingerprint,schema_version,symbol,requested_start,requested_end,
            returned_start,returned_end,row_count,normalized_content_sha256,provider_identity,
            endpoint_identity,package_name,package_version,source_verification_state,source_references_json,
            price_unit,price_unit_verification_state,price_unit_references_json,claimed_adjustment_basis,
            adjustment_verification_state,adjustment_evidence_references_json,normalized_payload_is_raw,
            data_class,research_eligible)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,'OPERATIONAL_ONLY',0)""",
        (candidate.operation_id, candidate.batch_fingerprint, SCHEMA_VERSION, SYMBOL,
         candidate.requested_start.isoformat(), candidate.requested_end.isoformat(),
         candidate.requested_start.isoformat(), candidate.requested_end.isoformat(), 2,
         candidate.normalized_content_sha256, candidate.provider_identity, candidate.endpoint_identity,
         candidate.package_name, candidate.package_version, "VERIFIED", canonical(candidate.source_references),
         candidate.price_unit, "VERIFIED", canonical(candidate.price_unit_references), "RAW", "VERIFIED",
         canonical(candidate.adjustment_evidence_references)),
    )
    connection.executemany("INSERT INTO market_price_provenance VALUES(?,?,?,?)",
        [(SYMBOL, value.session.isoformat(), candidate.operation_id, candidate.batch_fingerprint)
         for value in candidate.rows])
    connection.commit()


def rehearse(source: Path, output: Path) -> dict[str, object]:
    source, output = source.resolve(strict=True), output.resolve()
    require(not output.exists(), "output directory must be new; never overwrite a rehearsal")
    require(output != source and source not in output.parents, "output must be separate from source")
    output.mkdir(parents=True)
    clone = output / "disposable_market.sqlite"
    before_hash = file_hash(source)
    connection = sqlite3.connect(clone)
    summary = {"evidence_kind": "SYNTHETIC_ONLY_NOT_REAL_ELIGIBILITY", "source": str(source),
               "clone": str(clone), "canonical_sha256_before": before_hash, "steps": {}}
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader:
            reader.execute("PRAGMA query_only=ON")
            source_count = reader.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
            reader.backup(connection)
        require(file_hash(source) == before_hash, "canonical changed during backup")
        require(connection.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == source_count,
                "backup row count mismatch")
        require(connection.execute("PRAGMA quick_check").fetchone()[0] == "ok", "clone quick_check")
        require(connection.execute("SELECT COUNT(*) FROM prices WHERE symbol=?", (SYMBOL,)).fetchone()[0] == 0,
                "synthetic fixture symbol already exists")
        initialize_transactional_ingestion_schema(connection)
        migrate_operational_admission_schema(connection)
        migrate_shadow_runtime_foundation_schema(connection)
        legacy_before = snapshot(connection)["prices"]
        seed(connection)
        a = batch((0, 1, 2))
        same_request = request(a)
        accepted = commit_price_batch_shadow(connection, a, operation_identity_request=same_request)
        require(accepted.rows_written == 1 and accepted.guard_result.decision.value == "PASS", "A append")
        require(not accepted.operational_result.research_eligible, "A research flag")
        summary["steps"]["A"] = result_record(accepted)

        # Same-request observation demonstrates actual revision_of linkage. With
        # the same target day already appended by A, it has no additional new day.
        linked_batch = batch((0, 1, 2), revision_addition=0.6, accepted_operation=accepted.operation_id)
        protected_before = snapshot(connection)
        linked = commit_price_batch_shadow(connection, linked_batch, operation_identity_request=same_request)
        require(linked.operation_identity.revision_of == accepted.operation_id, "same-request linkage")
        require(linked.operation_identity.observation_generation == 1, "linked generation")
        require(linked.rows_written == 0 and linked.guard_result.decision.value == "PASS", "linked revision writes")
        require(linked.operational_result.admission.value == "HISTORICAL_REVISION_REQUIRES_REVIEW", "linked revision staging")
        require(snapshot(connection)["prices"] == protected_before["prices"], "linked revision prices")
        summary["steps"]["B_same_request"] = result_record(linked)

        # B contains the exact correction to A's row AND one genuinely new date.
        # Target/range change means a new request; source claims link to A's row.
        b = batch((0, 1, 2, 3), revision_addition=0.6, accepted_operation=accepted.operation_id)
        b_request = request(b)
        require(b_request.request_identity != same_request.request_identity, "new target request identity")
        (output / "synthetic_inputs.json").write_text(canonical({
            "A": a.identity_payload(), "A_request": same_request.as_dict(),
            "B_same_request": linked_batch.identity_payload(),
            "B": b.identity_payload(), "B_request": b_request.as_dict(),
            "fault_revision_addition": 0.7,
        }), encoding="utf-8")
        price_attempts = []

        def prohibit_price_writes(action, table, column, database, trigger):
            if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE} and table in {
                "prices", "market_price_provenance",
            }:
                price_attempts.append((action, table))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        state_before_b = snapshot(connection)
        connection.set_authorizer(prohibit_price_writes)
        staged = commit_price_batch_shadow(connection, b, operation_identity_request=b_request)
        connection.set_authorizer(None)
        state_after_b = snapshot(connection)
        require(staged.guard_result.decision.value == "PASS", "B must test attributed Guard PASS")
        require(staged.operational_result.admission.value == "HISTORICAL_REVISION_REQUIRES_REVIEW", "B staging")
        require(staged.rows_written == 0 and not price_attempts, "B attempted price writes")
        require(not staged.operational_result.research_eligible, "B research flag")
        for table in ("prices", "market_price_provenance", "market_ingestion_manifests"):
            require(state_after_b[table] == state_before_b[table], f"B mutated {table}")
        summary["steps"]["B"] = result_record(staged)
        guard_json, = connection.execute("SELECT guard_result_json FROM market_ingestion_receipts WHERE operation_id=?",
                                        (staged.operation_id,)).fetchone()
        rows_json, metadata_json, eligible = connection.execute(
            "SELECT normalized_rows_json,metadata_json,research_eligible FROM market_ingestion_staging WHERE operation_id=?",
            (staged.operation_id,)).fetchone()
        changes = json.loads(guard_json)["revisions"]
        require(changes == [{"session": row(2).session.isoformat(), "field": "close",
                            "existing_value": row(2).close, "incoming_value": row(2, 0.6).close}], "exact old/new")
        claims = json.loads(metadata_json)["revision_evidence"][0]
        require(claims["claims"] == changes, "staged exact claims")
        require(f"market-ingestion-operation:{accepted.operation_id}" in claims["source_references"], "A row attribution")
        require(json.loads(rows_json) == [value.as_identity_dict() for value in b.rows], "entire B retained")
        require(eligible == 0, "staging research flag")
        require(connection.execute("SELECT COUNT(*) FROM prices WHERE symbol=? AND time=?",
            (SYMBOL, row(3).session.isoformat())).fetchone()[0] == 0, "B new day appended")
        summary["steps"]["C"] = {"price_write_attempts": price_attempts, "before": state_before_b,
            "after": state_after_b, "exact_changes": changes, "revision_evidence": claims,
            "new_day_absent": True, "research_eligible": False}

        connection.set_authorizer(prohibit_price_writes)
        replay = commit_price_batch_shadow(connection, b, operation_identity_request=b_request)
        connection.set_authorizer(None)
        require(replay.idempotent_replay and result_record(replay)["admission"] == result_record(staged)["admission"], "D replay")
        require(snapshot(connection) == state_after_b and not price_attempts, "D duplicate or mutation")
        summary["steps"]["D"] = result_record(replay)

        def fault_case(candidate: PreparedPriceBatch, fault_stage: str, deny_prices: bool):
            before = snapshot(connection)
            inside = {}

            def fail(stage: str):
                if stage == fault_stage:
                    inside.update(snapshot(connection))
                    require(inside != before, "fault must fire after durable-table mutations inside transaction")
                    raise RuntimeError("injected clone rehearsal failure")

            if deny_prices:
                connection.set_authorizer(prohibit_price_writes)
            try:
                commit_price_batch_shadow(connection, candidate, operation_identity_request=b_request,
                                          failure_injector=fail)
                raise AssertionError("fault was not triggered")
            except RuntimeError as error:
                require(str(error) == "injected clone rehearsal failure", "unexpected failure")
            finally:
                connection.set_authorizer(None)
            after = snapshot(connection)
            require(after == before and not connection.in_transaction, "E leaked transaction state")
            if not deny_prices:
                require(inside["prices"] != before["prices"] and inside["market_price_provenance"] != before["market_price_provenance"],
                        "append rollback must undo actual price and provenance insertion")
            return {"fault_stage": fault_stage, "before": before, "inside_transaction": inside,
                    "after_rollback": after, "all_seven_tables_identical": True}

        summary["steps"]["E_staging"] = fault_case(
            batch((0, 1, 2, 3), revision_addition=0.7, accepted_operation=accepted.operation_id),
            "after_rejection_or_staging", True,
        )
        summary["steps"]["E_append"] = fault_case(batch((0, 1, 2, 3)), "after_prices_and_links", False)
        require(not price_attempts, "rejected/replayed/staging-fault price attempt")
        require(connection.execute("PRAGMA foreign_key_check").fetchall() == [], "clone foreign keys")
        require(connection.execute("PRAGMA quick_check").fetchone()[0] == "ok", "final clone quick_check")
        # Stream all copied real rows including IDs, proving fixture operations
        # never changed the canonical-derived price rows on the clone either.
        digest, count = sha256(), 0
        for value in connection.execute("SELECT * FROM prices WHERE symbol != ? OR symbol IS NULL ORDER BY rowid", (SYMBOL,)):
            digest.update((canonical(value) + "\n").encode())
            count += 1
        require({"count": count, "sha256": digest.hexdigest()} == legacy_before, "copied real rows changed")
        summary.update({"copied_real_price_rows_unchanged": legacy_before, "final_state": snapshot(connection),
                        "quick_check": "ok", "foreign_key_check": [], "result": "PASS"})
    finally:
        connection.close()
        summary["canonical_sha256_after"] = file_hash(source)
        require(summary["canonical_sha256_after"] == before_hash, "canonical hash changed")
        (output / "rehearsal_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    arguments = parser.parse_args()
    completed = rehearse(arguments.source, arguments.output_directory)
    print(json.dumps({"result": completed["result"], "steps": list(completed["steps"]),
                      "canonical_sha256_before": completed["canonical_sha256_before"],
                      "canonical_sha256_after": completed["canonical_sha256_after"],
                      "evidence": str(arguments.output_directory / "rehearsal_summary.json")}, indent=2))
