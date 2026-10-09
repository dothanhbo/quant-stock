"""S0.2 regressions against isolated production-schema Forward ledgers."""
from contextlib import closing
from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
import sys
from unittest.mock import MagicMock

import pytest

from manager.pages import state
from manager.view_models import build_dashboard_model
from quantctl.forward_evidence import ForwardEvidenceState, inspect_forward_evidence_catalog
from quantctl.state import FORWARD_REQUIRED_COLUMNS, inspect_forward_system
from quantlab.forward.ledger import ForwardValidationLedger
from tests.test_forward_evidence_view import _activation
from tests.ui.v1_p1_fixtures import prepare_p1_fixture


@pytest.fixture
def forward_fixture(tmp_path):
    fixture = prepare_p1_fixture(tmp_path / "forward")
    yield fixture
    fixture.close()


def _catalog(fixture):
    return inspect_forward_evidence_catalog(
        root=fixture.root, environ=fixture.environ, market_database_path=fixture.market,
    )


def _render_unavailable(monkeypatch, snapshot, catalog):
    assert catalog.protocols == ()
    assert sum(s.qualified_outcome_count for p in catalog.protocols for s in p.summaries) == 0
    fake = MagicMock()
    monkeypatch.setitem(sys.modules, "streamlit", fake)
    state._forward(snapshot)
    return fake


@pytest.mark.parametrize("table,column", [
    ("forward_formations", "protocol_id"),
    ("forward_outcomes", "availability"),
])
def test_missing_forward_column_blocks_evidence_without_dashboard_exception(
    forward_fixture, monkeypatch, table, column,
):
    fixture = forward_fixture
    path = fixture.root / "data/forward_validation.db"
    # Renaming also works for columns referenced by immutable ledger constraints.
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"ALTER TABLE {table} RENAME COLUMN {column} TO removed_{column}")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    model = build_dashboard_model(root=fixture.root, environ=fixture.environ)
    assert model.forward.readable and model.forward.schema_status == "SCHEMA_MISMATCH"
    assert model.forward.active_protocol_count is None and model.forward.outcome_count is None
    assert f"missing column: {table}.{column}" in model.forward.warnings
    catalog = _catalog(fixture)
    assert catalog.forward_state is ForwardEvidenceState.SCHEMA_INCOMPATIBLE
    fake = _render_unavailable(monkeypatch, model.forward, catalog)
    fake.write.assert_any_call("Schema: SCHEMA_MISMATCH")
    fake.warning.assert_any_call(f"missing column: {table}.{column}")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("failure", [
    sqlite3.OperationalError("no such column: protocol_id"),
    IndexError("No item with that key"),
])
def test_forward_read_failure_discards_partial_qualified_evidence(
    forward_fixture, monkeypatch, failure,
):
    fixture = forward_fixture
    path = fixture.root / "data/forward_validation.db"
    ledger = ForwardValidationLedger(path)
    ledger.activate(replace(_activation(), protocol_id="protocol-b",
                            protocol_fingerprint="fingerprint-b", activation_identity="activation-b"))
    original = ForwardValidationLedger.formations
    reads = []

    def fail_second(self, protocol_id):
        reads.append(protocol_id)
        if protocol_id == "protocol-b":
            raise failure
        return original(self, protocol_id)

    monkeypatch.setattr(ForwardValidationLedger, "formations", fail_second)
    model = build_dashboard_model(root=fixture.root, environ=fixture.environ)
    catalog = _catalog(fixture)
    assert reads == ["protocol-a", "protocol-b"]
    assert model.forward.schema_status == "OK"
    assert catalog.forward_state is ForwardEvidenceState.UNAVAILABLE
    assert catalog.forward_detail == f"Forward evidence read failed: {type(failure).__name__}"
    _render_unavailable(monkeypatch, model.forward, catalog)


def test_healthy_forward_contract_qualification_and_wal_are_preserved(forward_fixture, monkeypatch):
    fixture = forward_fixture
    path = fixture.root / "data/forward_validation.db"
    with closing(sqlite3.connect(path)) as connection:
        for table, required in FORWARD_REQUIRED_COLUMNS.items():
            assert required == {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    writer = sqlite3.connect(path)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        ForwardValidationLedger(path).activate(replace(
            _activation(), protocol_id="protocol-b", protocol_fingerprint="fingerprint-b",
            activation_identity="activation-b",
        ))
        wal = Path(str(path) + "-wal")
        assert wal.stat().st_size > 0
        # A new activation is committed in WAL while the main DB still has one.
        with closing(sqlite3.connect(path.as_uri() + "?immutable=1", uri=True)) as main_only:
            assert main_only.execute("SELECT COUNT(*) FROM forward_protocols").fetchone()[0] == 1
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (path, wal)}
        traced = []
        connect = sqlite3.connect

        def observe(database, *args, **kwargs):
            connection = connect(database, *args, **kwargs)
            if str(database) == str(path) or str(database).startswith(path.as_uri()):
                connection.set_trace_callback(traced.append)
            return connection

        monkeypatch.setattr(sqlite3, "connect", observe)
        snapshot = inspect_forward_system(root=fixture.root)
        assert snapshot.schema_status == "OK" and snapshot.readable and not snapshot.warnings
        assert snapshot.database_path == path.resolve() and snapshot.active_protocol_count == 2
        assert (snapshot.formation_count, snapshot.pending_maturity_count,
                snapshot.matured_maturity_count, snapshot.outcome_count) == (1, 1, 1, 1)
        model = build_dashboard_model(root=fixture.root, environ=fixture.environ)
        assert model.forward.schema_status == "OK"
        catalog = _catalog(fixture)
        assert catalog.forward_state is ForwardEvidenceState.AVAILABLE
        summary = next(s for s in catalog.protocols[0].summaries if s.horizon_sessions == 5)
        assert summary.available_outcome_count == summary.qualified_outcome_count == 1
        assert summary.legacy_unbound_outcome_count == 0
        assert summary.mean_excess_forward_return_pct_points == 2.0
        assert summary.qualified_mean_excess_forward_return_pct_points == 2.0
        assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before} == before
        assert all(sql.lstrip().upper().startswith(
            ("SELECT", "PRAGMA QUERY_ONLY", "PRAGMA TABLE_INFO", "PRAGMA FOREIGN_KEYS", "BEGIN", "COMMIT")
        ) for sql in traced)
    finally:
        writer.close()
