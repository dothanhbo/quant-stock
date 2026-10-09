"""S0.1: production Paper column contracts and operation gating, in temp stores."""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3

import pytest

from execution.models import Order, OrderSide, OrderStatus
from execution.persistence import PaperTradingStore
from manager import view_models
from quantctl.state import PAPER_REQUIRED_COLUMNS, inspect_paper_store, inspect_paper_system


@pytest.fixture
def paper_store(tmp_path, monkeypatch):
    path = tmp_path / "data/paper_trading_v2.db"
    store = PaperTradingStore(path)
    store.save_order(Order(symbol="AAA", side=OrderSide.BUY, quantity=100))
    # Isolate the existing schema gate from installation/entrypoint checks.
    monkeypatch.setattr(view_models, "operation_available", lambda *args, **kwargs: True)
    return store


def _operations(path):
    model = view_models.build_operations_model(
        root=path.parent.parent, environ={"PAPER_STRATEGY_VERSION": "Q70_FROZEN"},
    )
    return {item.spec.name: item for item in model.operations}


def _assert_blocked(path):
    snapshot = inspect_paper_system(
        root=path.parent.parent, environ={"PAPER_STRATEGY_VERSION": "Q70_FROZEN"},
    ).active_store
    assert snapshot.readable and snapshot.schema_status == "SCHEMA_MISMATCH"
    assert snapshot.pending_signal_count is None and snapshot.order_count is None
    operations = _operations(path)
    for name in ("scan", "daily"):
        assert not operations[name].available
        assert operations[name].unavailable_reason == "Active Paper store has an incompatible schema."
    assert operations["data-status"].available and operations["update"].available
    return snapshot


@pytest.mark.parametrize("table,column", [
    ("paper_orders", "quantity"),
    ("paper_fills", "quantity"),
    ("paper_positions", "quantity"),
    ("paper_pending_signals", "payload"),
    ("paper_metadata", "value"),
])
def test_missing_runtime_column_is_schema_mismatch_and_blocks_paper(paper_store, table, column):
    path = paper_store.database_path
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    if table == "paper_orders":
        with pytest.raises(IndexError, match="No item with that key"):
            paper_store.load_orders()
    elif table == "paper_fills":
        with pytest.raises(sqlite3.OperationalError, match="no such column: quantity"):
            paper_store.load_fills()
    elif table == "paper_metadata":
        # A persisted evidence file makes inspection read the source metadata.
        # That earlier read must not mask the Paper schema mismatch as UNREADABLE.
        with closing(sqlite3.connect(path.parent / "prospective_portfolio_evidence.db")) as connection, connection:
            connection.execute("CREATE TABLE marker(value TEXT)")
    snapshot = _assert_blocked(path)
    assert f"missing column: {table}.{column}" in snapshot.warnings


def test_missing_required_table_blocks_paper(paper_store):
    path = paper_store.database_path
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("DROP TABLE paper_fills")
    assert "missing table: paper_fills" in _assert_blocked(path).warnings


def test_healthy_contract_wal_counts_identity_and_actions_are_preserved(paper_store, monkeypatch):
    path = paper_store.database_path
    # Compare the inspector's entire contract with the actual production DDL.
    with closing(sqlite3.connect(path)) as connection, connection:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'paper_%'"
            )
        }
        assert tables == PAPER_REQUIRED_COLUMNS.keys()
        for table, required in PAPER_REQUIRED_COLUMNS.items():
            assert required == {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        # Compatible extensions must remain accepted.
        connection.execute("ALTER TABLE paper_orders ADD COLUMN extension_note TEXT")
    for status in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED):
        paper_store.save_order(Order(symbol="AAA", side=OrderSide.BUY, quantity=100, status=status))
    writer = sqlite3.connect(path)
    try:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            "INSERT INTO paper_pending_signals(signal_date,symbol,payload,created_at) "
            "VALUES('2026-10-08','AAA','{}','2026-10-08T10:00:00Z')"
        )
        writer.commit()
        wal = Path(str(path) + "-wal")
        assert wal.stat().st_size > 0
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (path, wal)}
        traced = []
        original_connect = sqlite3.connect

        def observe(database, *args, **kwargs):
            connection = original_connect(database, *args, **kwargs)
            connection.set_trace_callback(traced.append)
            return connection

        monkeypatch.setattr(sqlite3, "connect", observe)
        snapshot = inspect_paper_system(
            root=path.parent.parent, environ={"PAPER_STRATEGY_VERSION": "Q70_FROZEN"},
        ).active_store
        assert snapshot.database_path == path.resolve()
        assert snapshot.store_id == "q70-frozen" and snapshot.strategy_identity == "Q70_FROZEN"
        assert snapshot.readable and snapshot.schema_status == "OK" and not snapshot.warnings
        assert snapshot.order_count == 4
        assert snapshot.pending_signal_count == 1 and snapshot.open_position_count == 0
        assert all(item.available for item in _operations(path).values())
        assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before} == before
        assert all(sql.lstrip().upper().startswith(
            ("SELECT", "PRAGMA QUERY_ONLY", "PRAGMA TABLE_INFO", "BEGIN", "COMMIT")
        ) for sql in traced)
    finally:
        writer.close()


@pytest.mark.parametrize("unreadable", [False, True])
def test_absent_and_unreadable_stores_remain_unavailable(tmp_path, monkeypatch, unreadable):
    path = tmp_path / "data/paper_trading_v2.db"
    if unreadable:
        path.parent.mkdir()
        path.write_bytes(b"not a SQLite database")
    monkeypatch.setattr(view_models, "operation_available", lambda *args, **kwargs: True)
    snapshot = inspect_paper_store(path, name="S01_TEMP")
    assert snapshot.schema_status == ("UNREADABLE" if unreadable else "MISSING")
    assert not snapshot.readable
    assert all(not _operations(path)[name].available for name in ("scan", "daily"))
    assert path.exists() == unreadable
