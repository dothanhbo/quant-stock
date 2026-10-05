from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import os
import sqlite3

import pytest

import quantlab.transactional_market_data as market
from core.paths import DEFAULT_MARKET_DATABASE_PATH
from tests.test_operational_market_data_shadow import _batch, _frame, _values


# Fresh synthetic already-shaped schema, not a migration or canonical import.
# Reuse the writer's column contracts; no migration helper/ALTER is executed.
@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic-source.db"
    receipt_base = """
        operation_id TEXT PRIMARY KEY, batch_fingerprint TEXT NOT NULL,
        symbol TEXT, requested_start TEXT, requested_end TEXT, decision TEXT,
        status TEXT, reason_codes_json TEXT, guard_result_json TEXT,
        rows_written INTEGER, created_at_utc TEXT,
    """
    manifest_base = """
        operation_id TEXT PRIMARY KEY REFERENCES market_ingestion_receipts(operation_id),
        batch_fingerprint TEXT, schema_version TEXT, symbol TEXT,
        requested_start TEXT, requested_end TEXT, returned_start TEXT, returned_end TEXT,
        row_count INTEGER, normalized_content_sha256 TEXT,
        provider_identity TEXT, endpoint_identity TEXT, package_name TEXT, package_version TEXT,
        source_verification_state TEXT, source_references_json TEXT, price_unit TEXT,
        price_unit_verification_state TEXT, price_unit_references_json TEXT,
        claimed_adjustment_basis TEXT, adjustment_verification_state TEXT,
        adjustment_evidence_references_json TEXT, archive_reference TEXT,
        archive_content_sha256 TEXT, archive_kind TEXT, raw_payload_sha256 TEXT,
        normalized_payload_is_raw INTEGER CHECK(normalized_payload_is_raw=0),
    """
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE prices(
            id INTEGER PRIMARY KEY, symbol TEXT, time TEXT, open REAL, high REAL,
            low REAL, close REAL, volume REAL, UNIQUE(symbol,time))""")
        for name, base, columns in (
            ("market_ingestion_receipts", receipt_base,
             market._RECEIPT_D4B2_COLUMNS | market._RECEIPT_D4B26_COLUMNS),
            ("market_ingestion_manifests", manifest_base, market._MANIFEST_D4B2_COLUMNS),
        ):
            additions = ",".join(f"{column} {kind}" for column, kind in columns.items())
            connection.execute(f"CREATE TABLE {name}({base}{additions})")
        connection.execute("""CREATE TABLE market_price_provenance(
            symbol TEXT, time TEXT, operation_id TEXT, batch_fingerprint TEXT,
            PRIMARY KEY(symbol,time),
            FOREIGN KEY(symbol,time) REFERENCES prices(symbol,time),
            FOREIGN KEY(operation_id) REFERENCES market_ingestion_manifests(operation_id))""")
        connection.execute("""CREATE TABLE market_ingestion_staging(
            operation_id TEXT PRIMARY KEY REFERENCES market_ingestion_receipts(operation_id),
            batch_fingerprint TEXT, symbol TEXT, ingestion_intent TEXT, staging_outcome TEXT,
            normalized_content_sha256 TEXT, normalized_rows_json TEXT, metadata_json TEXT,
            data_class TEXT, research_eligible INTEGER CHECK(research_eligible=0),
            created_at_utc TEXT)""")
        connection.executemany(
            "INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)",
            [_values(day) for day in (0, 1, 2, 3)],
        )
    return path


def _snapshot(path: Path):
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        return tuple(tuple(connection.execute(f"SELECT * FROM {table} ORDER BY 1")) for table in (
            "prices", "market_price_provenance", "market_ingestion_receipts",
            "market_ingestion_manifests", "market_ingestion_staging",
        ))


def _observe(monkeypatch):
    attempts = []
    authorize = market._clone_authorizer

    def recorder(action, table, column, database, trigger):
        if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}:
            if table in {"prices", "market_price_provenance"}:
                attempts.append((action, table, database))
        return authorize(action, table, column, database, trigger)

    monkeypatch.setattr(market, "_clone_authorizer", recorder)
    return attempts


def test_clone_append_replay_and_source_preservation(source, tmp_path, monkeypatch):
    before = _snapshot(source)
    digest = sha256(source.read_bytes()).hexdigest()
    attempts = _observe(monkeypatch)
    clone = tmp_path / "append.db"
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        result = market.commit_price_batch_shadow(handle, _batch("clone-append"), target_capability=capability)
        assert result.rows_written == 1 and result.status is market.ReceiptStatus.APPROVED
        assert result.operational_result.research_eligible is False
        assert {attempt[1] for attempt in attempts} == {"prices", "market_price_provenance"}
        stored = _snapshot(clone)
        attempts.clear()
        replay = market.commit_price_batch_shadow(handle, _batch("clone-append"), target_capability=capability)
        assert replay.idempotent_replay and replay.operational_result == result.operational_result
        assert not attempts and _snapshot(clone) == stored
        with sqlite3.connect(clone) as connection:
            assert connection.execute("SELECT research_eligible FROM market_ingestion_manifests").fetchall() == [(0,)]
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert _snapshot(source) == before and sha256(source.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("kind", ["missing", "fabricated", "foreign", "unregistered", "raw-path", "raw-connection"])
def test_wrong_capability_rejects_before_price_attempts(source, tmp_path, monkeypatch, kind):
    attempts = _observe(monkeypatch)
    clone = tmp_path / "one.db"
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        with market.create_disposable_shadow_target(source, tmp_path / "two.db") as (_, foreign):
            target, token = handle, capability
            if kind == "missing": token = None
            if kind == "fabricated": token = market.ShadowTargetCapability()
            if kind == "foreign": token = foreign
            if kind == "unregistered": target = market.DisposableShadowTarget()
            if kind == "raw-path": target = clone
            if kind == "raw-connection": target = market._CLONE_BINDINGS[handle].connection
            before = _snapshot(clone)
            with pytest.raises(market.ShadowTargetError):
                market.commit_price_batch_shadow(target, _batch("bad-cap"), target_capability=token)
            assert not attempts and _snapshot(clone) == before


@pytest.mark.parametrize("when", ["before", "after_approval_receipt"])
def test_closed_connection_fails_and_rolls_back(source, tmp_path, monkeypatch, when):
    attempts = _observe(monkeypatch)
    clone = tmp_path / "closed.db"
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        connection = market._CLONE_BINDINGS[handle].connection
        before = _snapshot(clone)
        if when == "before": connection.close()

        def close(stage):
            if stage == when: connection.close()

        with pytest.raises(market.ShadowTargetError):
            market.commit_price_batch_shadow(handle, _batch("closed"),
                target_capability=capability, failure_injector=close)
        assert not attempts and _snapshot(clone) == before


def test_substituted_connection_is_detected_through_actual_writer(source, tmp_path, monkeypatch):
    clone = tmp_path / "binding.db"
    attempts = _observe(monkeypatch)
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        replacement = sqlite3.connect(source)
        replacement.set_authorizer(market._clone_authorizer)
        before = _snapshot(source), _snapshot(clone)

        @contextmanager
        def substituted(*args, **kwargs):
            yield replacement

        monkeypatch.setattr(market, "_connection", substituted)
        try:
            with pytest.raises(market.ShadowTargetError, match="substituted"):
                market.commit_price_batch_shadow(handle, _batch("substitution"), target_capability=capability)
            assert not replacement.in_transaction
            assert not attempts and (_snapshot(source), _snapshot(clone)) == before
        finally:
            replacement.close()


def test_context_exit_revokes_handle(source, tmp_path, monkeypatch):
    attempts = _observe(monkeypatch)
    with market.create_disposable_shadow_target(source, tmp_path / "revoked.db") as (handle, capability):
        pass
    with pytest.raises(market.ShadowTargetError):
        market.commit_price_batch_shadow(handle, _batch("expired"), target_capability=capability)
    assert not attempts


def test_subclass_cannot_impersonate_registered_handle_by_hash_equality(source, tmp_path, monkeypatch):
    attempts = _observe(monkeypatch)
    clone = tmp_path / "spoof.db"
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        class Impersonator(market.DisposableShadowTarget):
            def __hash__(self): return hash(handle)
            def __eq__(self, other): return True
        before = _snapshot(clone)
        with pytest.raises(market.ShadowTargetError, match="exact factory-issued"):
            market.commit_price_batch_shadow(Impersonator(), _batch("spoof"), target_capability=capability)
        assert not attempts and _snapshot(clone) == before


@pytest.mark.parametrize("kind", ["canonical", "normalized-alias", "hardlink", "symlink", "configured"])
def test_canonical_and_aliases_rejected_without_sql_open(source, tmp_path, monkeypatch, kind):
    protected_paths = market._protected_market_paths
    monkeypatch.setattr(market, "_protected_market_paths", lambda: protected_paths() + (source.resolve(),))
    target = source
    if kind == "canonical": target = DEFAULT_MARKET_DATABASE_PATH
    if kind == "normalized-alias": target = source.parent / "." / source.name
    if kind in {"hardlink", "symlink"}:
        target = tmp_path / (kind + ".db")
        try:
            if kind == "hardlink": os.link(source, target)
            else: target.symlink_to(source)
        except OSError as error:
            pytest.skip(f"platform cannot create {kind}: {error}")
    if kind == "configured":
        target = tmp_path / "configured-canonical.db"
        monkeypatch.setenv("MARKET_DATABASE_PATH", str(target))

    def no_sql(*args, **kwargs):
        pytest.fail("canonical rejection must happen before opening SQLite")

    monkeypatch.setattr(market.sqlite3, "connect", no_sql)
    with pytest.raises(market.ShadowTargetError, match="canonical"):
        with market.create_disposable_shadow_target(source, target):
            pytest.fail("canonical handle minted")


def test_existing_path_and_env_claim_cannot_mint_handle(source, tmp_path, monkeypatch):
    monkeypatch.setenv("D4_DISPOSABLE", "1")
    monkeypatch.setenv("D4_ACTIVE", "1")
    before = _snapshot(source)
    with pytest.raises(market.ShadowTargetError, match="new disposable"):
        with market.create_disposable_shadow_target(source, source):
            pytest.fail("adopted caller-claimed target")
    assert _snapshot(source) == before


def test_non_sqlite_source_is_unverifiable(tmp_path):
    source = tmp_path / "not-sqlite.db"
    source.write_bytes(b"not a SQLite database")
    with pytest.raises(market.ShadowTargetError):
        with market.create_disposable_shadow_target(source, tmp_path / "invalid.db"):
            pytest.fail("invalid database accepted")


@pytest.mark.parametrize("actual_target", ["anonymous", "another-file"])
def test_wrong_actual_connection_cannot_be_backed_up_or_minted(source, tmp_path, monkeypatch, actual_target):
    connect = sqlite3.connect
    before = _snapshot(source)
    calls = []

    def anonymous(database, *args, **kwargs):
        calls.append(str(database))
        if "mode=rw" in str(database):
            if actual_target == "anonymous": return connect(":memory:")
            return connect(source.as_uri() + "?mode=ro", uri=True)
        return connect(database, *args, **kwargs)

    monkeypatch.setattr(market.sqlite3, "connect", anonymous)
    with pytest.raises(market.ShadowTargetError):
        with market.create_disposable_shadow_target(source, tmp_path / "anonymous.db"):
            pytest.fail("wrong actual database mistaken for disk clone")
    assert len(calls) == 1  # No source connection/backup before target verification.
    assert _snapshot(source) == before


def test_hardlink_added_after_mint_invalidates_binding(source, tmp_path, monkeypatch):
    attempts = _observe(monkeypatch)
    clone = tmp_path / "linked.db"
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        try: os.link(clone, tmp_path / "extra-link.db")
        except OSError as error: pytest.skip(f"hardlink unavailable: {error}")
        before = _snapshot(clone)
        with pytest.raises(market.ShadowTargetError, match="aliased"):
            market.commit_price_batch_shadow(handle, _batch("aliased"), target_capability=capability)
        assert not attempts and _snapshot(clone) == before


@pytest.mark.parametrize("sql", ["ATTACH DATABASE ':memory:' AS extra", "CREATE TEMP TABLE prices(x)", "PRAGMA foreign_keys=OFF"])
def test_target_redirection_is_denied_by_connection(source, tmp_path, sql):
    with market.create_disposable_shadow_target(source, tmp_path / "fenced.db") as (handle, _):
        with pytest.raises(sqlite3.DatabaseError):
            market._CLONE_BINDINGS[handle].connection.execute(sql)


@pytest.mark.parametrize("when", ["before", "after_manifest"])
def test_private_authorizer_escape_attach_still_rejects_before_price_attempts(source, tmp_path, monkeypatch, when):
    attempts = _observe(monkeypatch)
    clone = tmp_path / "attach.db"
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        connection = market._CLONE_BINDINGS[handle].connection
        before = _snapshot(clone)

        def escape():
            sqlite3.Connection.set_authorizer(connection, None)
            connection.execute("ATTACH DATABASE ? AS extra", (source.as_uri() + "?mode=ro",))
            market._install_clone_authorizer(connection)

        if when == "before": escape()
        def inject(stage):
            if stage == when: escape()

        with pytest.raises(market.ShadowTargetError, match="attached"):
            market.commit_price_batch_shadow(handle, _batch("attach"),
                target_capability=capability, failure_injector=inject)
        assert not attempts and _snapshot(clone) == before
        assert not connection.in_transaction


@pytest.mark.parametrize("when", ["before", "after_manifest"])
def test_private_temp_table_shadowing_rejects_before_price_attempts(source, tmp_path, monkeypatch, when):
    attempts = _observe(monkeypatch)
    clone = tmp_path / "temp.db"
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        connection = market._CLONE_BINDINGS[handle].connection
        before = _snapshot(clone)
        def escape():
            sqlite3.Connection.set_authorizer(connection, None)
            connection.execute("CREATE TEMP TABLE prices(x)")
            market._install_clone_authorizer(connection)
        if when == "before": escape()
        def inject(stage):
            if stage == when: escape()
        with pytest.raises(market.ShadowTargetError, match="temporary objects"):
            market.commit_price_batch_shadow(handle, _batch("temp"),
                target_capability=capability, failure_injector=inject)
        assert not attempts and _snapshot(clone) == before
        assert not connection.in_transaction


def test_atomic_rollback_then_retry_and_conflict(source, tmp_path, monkeypatch):
    clone = tmp_path / "rollback.db"
    attempts = _observe(monkeypatch)
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        before = _snapshot(clone)
        def fault(stage):
            if stage == "after_prices_and_links": raise RuntimeError("clone fault")
        with pytest.raises(RuntimeError, match="clone fault"):
            market.commit_price_batch_shadow(handle, _batch("rollback"),
                target_capability=capability, failure_injector=fault)
        assert {attempt[1] for attempt in attempts} == {"prices", "market_price_provenance"}
        assert _snapshot(clone) == before
        assert not market._CLONE_BINDINGS[handle].connection.in_transaction
        result = market.commit_price_batch_shadow(handle, _batch("rollback"), target_capability=capability)
        assert result.rows_written == 1
        stored = _snapshot(clone)
        attempts.clear()
        changed = replace(_batch("rollback"), source_references=("fixture://changed-source",))
        with pytest.raises(market.OperationIdentityConflict):
            market.commit_price_batch_shadow(handle, changed, target_capability=capability)
        assert not attempts and _snapshot(clone) == stored


def test_revision_staging_rollback_and_replay_keep_zero_price_attempts(source, tmp_path, monkeypatch):
    attempts = _observe(monkeypatch)
    clone = tmp_path / "revision.db"
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        batch = _batch("clone-revision", frame=_frame((2, 3, 4), changed_close_day=3))
        before = _snapshot(clone)
        def fault(stage):
            if stage == "after_rejection_or_staging": raise RuntimeError("staging fault")
        with pytest.raises(RuntimeError, match="staging fault"):
            market.commit_price_batch_shadow(handle, batch, target_capability=capability, failure_injector=fault)
        assert not attempts and _snapshot(clone) == before
        result = market.commit_price_batch_shadow(handle, batch, target_capability=capability)
        stored = _snapshot(clone)
        assert result.status is market.ReceiptStatus.REJECTED and result.rows_written == 0
        assert result.operational_result.research_eligible is False
        assert stored[0:2] == before[0:2] and len(stored[-1]) == 1
        replay = market.commit_price_batch_shadow(handle, batch, target_capability=capability)
        assert replay.idempotent_replay and not attempts and _snapshot(clone) == stored


@pytest.mark.parametrize("connection_target", [False, True])
def test_legacy_raw_shadow_contract_remains_an_explicit_bypass(source, connection_target):
    connection = sqlite3.connect(source)
    try:
        target = connection if connection_target else source
        result = market.commit_price_batch_shadow(target, _batch("legacy"))
        assert result.rows_written == 1
    finally:
        connection.close()


def test_privileged_base_authorizer_removal_remains_a_demonstrated_escape(source, tmp_path):
    clone = tmp_path / "raw-escape.db"
    with market.create_disposable_shadow_target(source, clone) as (handle, _):
        connection = market._CLONE_BINDINGS[handle].connection
        sqlite3.Connection.set_authorizer(connection, None)
        connection.execute("INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES('ESCAPE','2000-01-01',1,1,1,1,1)")
        connection.commit()
        assert connection.execute("SELECT count(*) FROM market_ingestion_receipts").fetchone() == (0,)
    assert len(_snapshot(clone)[0]) == 5  # Demonstrated blocker, not claimed closure.
