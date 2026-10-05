from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import sqlite3

import pytest

import quantlab.transactional_market_data as market
from quantlab.operational_admission import IngestionIntent
from tests.test_disposable_shadow_target import source, _snapshot
from tests.test_operational_market_data_shadow import _batch


RAW_DML = (
    "INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES('ESCAPE','2030-01-01',1,1,1,1,1)",
    "UPDATE prices SET close=close+0.01 WHERE time='2026-09-01'",
    "DELETE FROM prices WHERE time='2026-09-01'",
    """INSERT INTO market_price_provenance SELECT 'AAA','2026-09-01',operation_id,batch_fingerprint
       FROM market_ingestion_manifests LIMIT 1""",
    "UPDATE market_price_provenance SET time='2026-09-02'",
    "DELETE FROM market_price_provenance",
)
PRICE_INSERT = "INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)"


@pytest.mark.parametrize("sql", RAW_DML)
@pytest.mark.parametrize("api", ["connection", "cursor", "executemany"])
def test_raw_actual_dml_denied_and_tables_unchanged(source, tmp_path, sql, api):
    clone = tmp_path / "raw.db"
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        market.commit_price_batch_shadow(handle, _batch("seed"), target_capability=capability)
        connection = market._CLONE_BINDINGS[handle].connection
        before, changes = _snapshot(clone), connection.total_changes
        with pytest.raises(sqlite3.DatabaseError, match="authorized"):
            if api == "connection": connection.execute(sql)
            elif api == "cursor": connection.cursor().execute(sql)
            else: connection.executemany(sql, [()])
        assert _snapshot(clone) == before and connection.total_changes == changes
        assert connection not in market._PRICE_DML_GRANTS


@pytest.mark.parametrize("authorizer", [None, lambda *args: sqlite3.SQLITE_OK])
def test_instance_authorizer_cannot_be_removed_or_replaced(source, tmp_path, authorizer):
    clone = tmp_path / "authorizer.db"
    with market.create_disposable_shadow_target(source, clone) as (handle, _):
        connection = market._CLONE_BINDINGS[handle].connection
        before = _snapshot(clone)
        with pytest.raises(market.ShadowTargetError, match="cannot be replaced"):
            connection.set_authorizer(authorizer)
        with pytest.raises(sqlite3.DatabaseError): connection.execute(RAW_DML[0])
        assert _snapshot(clone) == before


@pytest.mark.parametrize("authorizer", [None, lambda *args: sqlite3.SQLITE_OK])
def test_privileged_base_api_still_disables_protection(source, tmp_path, authorizer):
    clone = tmp_path / "privileged.db"
    with market.create_disposable_shadow_target(source, clone) as (handle, _):
        connection = market._CLONE_BINDINGS[handle].connection
        sqlite3.Connection.set_authorizer(connection, authorizer)
        connection.execute(RAW_DML[0])
        connection.commit()
        assert len(_snapshot(clone)[0]) == 5  # Explicit remaining privilege blocker.


@pytest.mark.parametrize("cache_size", [0, 128])
def test_authorized_statement_and_cursor_not_reusable_after_scope(source, tmp_path, monkeypatch, cache_size):
    connect = sqlite3.connect
    def cached(database, *args, **kwargs):
        if "mode=rw" in str(database): kwargs["cached_statements"] = cache_size
        return connect(database, *args, **kwargs)
    monkeypatch.setattr(market.sqlite3, "connect", cached)
    captured = []
    def append(connection, batch, sessions):
        rows = tuple(row for row in batch.rows if row.session.isoformat() in sessions)
        cursor = connection.cursor()
        row = rows[0]
        params = (row.symbol, row.session.isoformat(), row.open, row.high, row.low, row.close, row.volume)
        cursor.execute(PRICE_INSERT, params)
        captured.append((cursor, params))
        return rows
    monkeypatch.setattr(market, "_append_new_prices", append)
    clone = tmp_path / "cached.db"
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        result = market.commit_price_batch_shadow(handle, _batch("cache"), target_capability=capability)
        assert result.rows_written == 1
        connection = market._CLONE_BINDINGS[handle].connection
        before, changes = _snapshot(clone), connection.total_changes
        cursor, params = captured[0]
        for execute in (cursor.execute, connection.execute):
            with pytest.raises(sqlite3.DatabaseError):
                execute(PRICE_INSERT, (params[0], "2030-01-01", *params[2:]))
        assert _snapshot(clone) == before and connection.total_changes == changes


def test_unfinished_returning_statement_is_closed_at_scope_exit(source, tmp_path, monkeypatch):
    captured = []
    def append(connection, batch, sessions):
        rows = tuple(row for row in batch.rows if row.session.isoformat() in sessions)
        row = rows[0]
        cursor = connection.cursor()
        cursor.execute(PRICE_INSERT + " RETURNING symbol,time", (
            row.symbol, row.session.isoformat(), row.open, row.high, row.low, row.close, row.volume,
        ))
        captured.append(cursor)  # Keep an active prepared DML statement at revocation.
        return rows
    monkeypatch.setattr(market, "_append_new_prices", append)
    clone = tmp_path / "returning.db"
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        result = market.commit_price_batch_shadow(handle, _batch("returning"), target_capability=capability)
        assert result.rows_written == 1
        connection = market._CLONE_BINDINGS[handle].connection
        before = _snapshot(clone)
        assert not connection.in_transaction and len(before[0]) == 5 and len(before[1]) == 1
        changes = connection.total_changes
        with pytest.raises(sqlite3.DatabaseError):
            captured[0].fetchall()  # An active DML VM cannot resume after revocation.
        assert connection.total_changes == changes and _snapshot(clone) == before
        with pytest.raises(sqlite3.DatabaseError): connection.execute(RAW_DML[0])


@pytest.mark.parametrize("sql", (
    "CREATE TRIGGER escape AFTER INSERT ON prices BEGIN DELETE FROM prices; END",
    "ALTER TABLE prices RENAME TO hidden_prices",
    "DROP TABLE prices",
    "CREATE VIEW escape AS SELECT * FROM prices",
    "PRAGMA writable_schema=ON",
))
def test_schema_operations_cannot_open_write_bypass(source, tmp_path, sql):
    clone = tmp_path / "schema.db"
    with market.create_disposable_shadow_target(source, clone) as (handle, _):
        connection = market._CLONE_BINDINGS[handle].connection
        before = _snapshot(clone)
        with pytest.raises(sqlite3.DatabaseError): connection.execute(sql)
        assert _snapshot(clone) == before


@pytest.mark.parametrize("kind", ["price-to-other", "other-to-price"])
def test_inherited_trigger_cannot_expand_dml(source, tmp_path, kind):
    # Source fixture definitions only, not a database migration.
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE audit_sink(x)")
        if kind == "price-to-other":
            connection.execute("""CREATE TRIGGER inherited AFTER INSERT ON prices
                BEGIN INSERT INTO audit_sink VALUES(1); END""")
        else:
            connection.execute("""CREATE TRIGGER inherited AFTER INSERT ON audit_sink
                BEGIN UPDATE prices SET close=1; END""")
    clone = tmp_path / "trigger.db"
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        before = _snapshot(clone)
        connection = market._CLONE_BINDINGS[handle].connection
        with pytest.raises((sqlite3.DatabaseError, market.ShadowTargetError)):
            if kind == "price-to-other":
                market.commit_price_batch_shadow(handle, _batch("trigger"), target_capability=capability)
            else:
                connection.execute("INSERT INTO audit_sink VALUES(1)")
        assert _snapshot(clone) == before
        assert connection.execute("SELECT * FROM audit_sink").fetchall() == []
        with pytest.raises(sqlite3.DatabaseError): connection.execute(RAW_DML[0])


def test_scope_cannot_authorize_other_table_or_connection(source, tmp_path, monkeypatch):
    original = market._append_new_prices
    with market.create_disposable_shadow_target(source, tmp_path / "other.db") as (other, _):
        other_connection = market._CLONE_BINDINGS[other].connection
        def append(connection, batch, sessions):
            for sql in (RAW_DML[1], RAW_DML[2], RAW_DML[4], RAW_DML[5],
                        "UPDATE market_ingestion_receipts SET rows_written=999",
                        "ATTACH DATABASE ':memory:' AS extra"):
                with pytest.raises(sqlite3.DatabaseError): connection.execute(sql)
            with pytest.raises(sqlite3.DatabaseError): other_connection.execute(RAW_DML[0])
            return original(connection, batch, sessions)
        monkeypatch.setattr(market, "_append_new_prices", append)
        clone = tmp_path / "scope.db"
        with market.create_disposable_shadow_target(source, clone) as (handle, capability):
            result = market.commit_price_batch_shadow(handle, _batch("scope"), target_capability=capability)
            assert result.rows_written == 1
            assert other_connection.execute("SELECT COUNT(*) FROM prices").fetchone() == (4,)


@pytest.mark.parametrize("stage", ["after_manifest", "during_price_insert", "after_prices_and_links", "after_rejection_or_staging"])
def test_exception_revokes_grant_and_following_raw_write_is_denied(source, tmp_path, monkeypatch, stage):
    if stage == "during_price_insert":
        original = market._append_new_prices
        def fail(connection, batch, sessions):
            original(connection, batch, sessions)
            raise RuntimeError("inside authorized DML")
        monkeypatch.setattr(market, "_append_new_prices", fail)
    def fault(at):
        if at == stage: raise RuntimeError("scope fault")
    clone = tmp_path / "exception.db"
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        before = _snapshot(clone)
        batch = _batch("exception")
        if stage == "after_rejection_or_staging":
            batch = _batch("exception", intent=IngestionIntent.BACKFILL)
        with pytest.raises(RuntimeError):
            market.commit_price_batch_shadow(handle, batch, target_capability=capability, failure_injector=fault)
        connection = market._CLONE_BINDINGS[handle].connection
        assert not connection.in_transaction and _snapshot(clone) == before
        assert connection not in market._PRICE_DML_GRANTS
        assert connection not in market._ADMISSION_TRANSACTIONS
        with pytest.raises(sqlite3.DatabaseError): connection.cursor().execute(RAW_DML[0])
        assert _snapshot(clone) == before


def test_transaction_restart_cannot_reuse_admission_token(source, tmp_path):
    clone = tmp_path / "restart.db"
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        connection = market._CLONE_BINDINGS[handle].connection
        before = _snapshot(clone)
        def restart(stage):
            if stage == "after_manifest":
                connection.rollback()
                connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(market.ShadowTargetError, match="transaction changed"):
            market.commit_price_batch_shadow(handle, _batch("restart"), target_capability=capability, failure_injector=restart)
        assert not connection.in_transaction and _snapshot(clone) == before
        with pytest.raises(sqlite3.DatabaseError): connection.execute(RAW_DML[0])


def test_factory_raw_overload_cannot_open_scope_without_handle(source, tmp_path):
    clone = tmp_path / "raw-overload.db"
    with market.create_disposable_shadow_target(source, clone) as (handle, _):
        connection = market._CLONE_BINDINGS[handle].connection
        before = _snapshot(clone)
        with pytest.raises(sqlite3.DatabaseError): market.commit_price_batch_shadow(connection, _batch("raw"))
        assert _snapshot(clone) == before and connection not in market._PRICE_DML_GRANTS


def test_new_unmanaged_connection_is_outside_scope(source, tmp_path):
    clone = tmp_path / "unmanaged.db"
    with market.create_disposable_shadow_target(source, clone):
        with sqlite3.connect(clone) as connection:
            connection.execute(RAW_DML[0])
    assert len(_snapshot(clone)[0]) == 5  # Demonstrated connection boundary.


@pytest.mark.parametrize("outcome", ["success", "replay", "staging", "rejection"])
def test_every_normal_outcome_revokes_transaction_and_dml_scope(source, tmp_path, outcome):
    clone = tmp_path / "outcome.db"
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        batch = _batch("outcome")
        if outcome == "staging": batch = _batch("outcome", intent=IngestionIntent.BACKFILL)
        if outcome == "rejection": batch = replace(batch, completed_session_result=None)
        result = market.commit_price_batch_shadow(handle, batch, target_capability=capability)
        if outcome == "replay":
            result = market.commit_price_batch_shadow(handle, batch, target_capability=capability)
            assert result.idempotent_replay
        if outcome in {"staging", "rejection"}: assert result.rows_written == 0
        assert result.operational_result.research_eligible is False
        connection = market._CLONE_BINDINGS[handle].connection
        assert not connection.in_transaction
        assert all(connection not in mapping for mapping in (
            market._PRICE_DML_GRANTS, market._ADMISSION_TRANSACTIONS, market._SCOPED_CURSORS,
        ))
        before = _snapshot(clone)
        for sql in (RAW_DML[0], RAW_DML[-1]):
            with pytest.raises(sqlite3.DatabaseError): connection.execute(sql)
        assert _snapshot(clone) == before


def test_cursor_factory_cannot_disable_scope_tracking(source, tmp_path):
    with market.create_disposable_shadow_target(source, tmp_path / "cursor.db") as (handle, _):
        connection = market._CLONE_BINDINGS[handle].connection
        with pytest.raises(market.ShadowTargetError, match="cursor cannot be replaced"):
            connection.cursor(factory=sqlite3.Cursor)


def test_admission_still_supports_native_autoincrement(source, tmp_path):
    auto_source = tmp_path / "autoincrement-fixture.db"
    # Build a fresh variant fixture, not ALTER/migration of an existing target.
    with sqlite3.connect(source) as original, sqlite3.connect(auto_source) as variant:
        for name, ddl in original.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"):
            if name == "prices":
                ddl = ddl.replace("id INTEGER PRIMARY KEY,", "id INTEGER PRIMARY KEY AUTOINCREMENT,")
            variant.execute(ddl)
            rows = original.execute(f"SELECT * FROM {name}").fetchall()
            if rows:
                variant.executemany(f"INSERT INTO {name} VALUES({','.join('?' for _ in rows[0])})", rows)
    clone = tmp_path / "autoincrement-clone.db"
    with market.create_disposable_shadow_target(auto_source, clone) as (handle, capability):
        result = market.commit_price_batch_shadow(handle, _batch("autoincrement"), target_capability=capability)
        assert result.rows_written == 1
        connection = market._CLONE_BINDINGS[handle].connection
        assert connection.execute("SELECT seq FROM sqlite_sequence WHERE name='prices'").fetchone() == (5,)
        with pytest.raises(sqlite3.DatabaseError): connection.execute(RAW_DML[0])
