from __future__ import annotations

from hashlib import sha256
import json
import sqlite3
from pathlib import Path

import pytest

from execution.persistence import PaperTradingStore
from quantlab.operations.paper_reconciliation import (
    FindingState,
    PaperVersion,
    ReconciliationCapability,
    audit_paper_database,
)


def _store(path: Path) -> None:
    PaperTradingStore(path)
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "INSERT INTO paper_metadata(key,value) VALUES (?,?)",
            (("initial_cash", json.dumps(100_000.0)), ("cash", json.dumps(98_997.0))),
        )
        connection.execute(
            """INSERT INTO paper_orders VALUES
            ('order-1','AAA','BUY',10,'MARKET',NULL,100.0,'FILLED',10,100.15,NULL,
             '2026-01-02T00:00:00+00:00','2026-01-02T00:00:00+00:00','pending_signal:1')"""
        )
        connection.execute(
            """INSERT INTO paper_fills
            (order_id,symbol,side,quantity,price,gross_value,commission,slippage_cost,net_cash_flow,created_at)
            VALUES ('order-1','AAA','BUY',10,100.15,1001.5,1.5,0.15,-1003.0,'2026-01-02T00:00:00+00:00')"""
        )
        connection.execute(
            "INSERT INTO paper_positions VALUES ('AAA',10,100.3,100.15,0.0)"
        )
        connection.execute(
            """INSERT INTO paper_position_lifecycle
            (symbol,entry_date,entry_price,initial_quantity,stop_price,take_profit_price,
             highest_price,trailing_stop_price,trailing_atr_multiplier,maximum_holding_days,updated_at)
            VALUES ('AAA','2026-01-02',100.15,10,90.0,120.0,100.15,NULL,NULL,NULL,'2026-01-02T00:00:00+00:00')"""
        )
        connection.execute(
            """INSERT INTO paper_pending_signals
            (id,signal_date,symbol,payload,status,processed_date,reason,created_at)
            VALUES (1,'2026-01-01','AAA','{}','FILLED','2026-01-02','ok','2026-01-01T00:00:00+00:00')"""
        )


def _by_code(result):
    return {item.invariant: item for item in result.checks}


def test_clean_source_linked_order_fill_and_pending_invariants(tmp_path: Path) -> None:
    path = tmp_path / "paper.db"
    _store(path)
    result = audit_paper_database(path, paper_version=PaperVersion.V1)
    checks = _by_code(result)
    assert result.read_only
    assert result.source_intent_column_present and result.source_intent_unique_index_present
    assert checks["filled_order_exactly_one_fill"].state is FindingState.PASS
    assert checks["source_intent_unique"].state is FindingState.PASS
    assert checks["completed_pending_has_filled_source_execution"].state is FindingState.PASS
    assert checks["cash_reconstructable_from_durable_fills"].state is FindingState.PASS


def test_missing_fill_and_duplicate_source_identity_are_detected(tmp_path: Path) -> None:
    path = tmp_path / "paper.db"
    _store(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX uq_paper_orders_source_intent")
        connection.execute(
            """INSERT INTO paper_orders VALUES
            ('order-2','BBB','BUY',1,'MARKET',NULL,10.0,'FILLED',1,10.0,NULL,
             '2026-01-03','2026-01-03','pending_signal:1')"""
        )
        connection.execute(
            """INSERT INTO paper_orders VALUES
            ('order-3','CCC','BUY',1,'MARKET',NULL,10.0,'FILLED',1,10.0,NULL,
             '2026-01-03','2026-01-03','pending_signal:3')"""
        )
    checks = _by_code(audit_paper_database(path, paper_version="V2"))
    assert checks["filled_order_exactly_one_fill"].affected_count == 2
    assert checks["source_intent_unique"].affected_count == 1
    assert checks["source_intent_uniqueness_schema"].state is FindingState.ISSUE


def test_unresolved_sell_order_is_not_assumed_retry_safe(tmp_path: Path) -> None:
    path = tmp_path / "paper.db"
    _store(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO paper_orders VALUES
            ('sell-open','AAA','SELL',10,'MARKET',NULL,110.0,'ACCEPTED',0,NULL,NULL,
             '2026-01-03','2026-01-03',NULL)"""
        )
    check = _by_code(audit_paper_database(path, paper_version="V1"))["unresolved_sell_order"]
    assert check.affected_count == 1
    assert check.capability is ReconciliationCapability.MANUAL_REVIEW_REQUIRED


def test_sell_fill_without_closed_trade_is_detectable_but_not_repaired(tmp_path: Path) -> None:
    path = tmp_path / "paper.db"
    _store(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO paper_orders VALUES
            ('sell-filled','AAA','SELL',10,'MARKET',NULL,110.0,'FILLED',10,109.9,NULL,
             '2026-01-04','2026-01-04',NULL)"""
        )
        connection.execute(
            """INSERT INTO paper_fills
            (order_id,symbol,side,quantity,price,gross_value,commission,slippage_cost,net_cash_flow,created_at)
            VALUES ('sell-filled','AAA','SELL',10,109.9,1099.0,2.6,0.1,1096.4,'2026-01-04')"""
        )
    check = _by_code(audit_paper_database(path, paper_version="V3"))["sell_fill_has_closed_trade_record"]
    assert check.state is FindingState.ISSUE
    assert check.affected_count == 1
    assert check.capability is ReconciliationCapability.MANUAL_REVIEW_REQUIRED


def test_dangling_fill_closed_trade_and_lifecycle_references_are_detected(tmp_path: Path) -> None:
    path = tmp_path / "paper.db"
    _store(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM paper_orders WHERE client_order_id='order-1'")
        connection.execute(
            """INSERT INTO paper_closed_trades
            (symbol,entry_date,exit_date,quantity,entry_price,exit_price,gross_proceeds,
             commission,realized_pnl,return_pct,holding_days,exit_reason,order_id,created_at)
            VALUES ('AAA','2026-01-01','2026-01-03',10,90,100,1000,1,99,11,2,'STOP','missing-order','2026-01-03')"""
        )
    checks = _by_code(audit_paper_database(path, paper_version="V3"))
    assert checks["fill_references_existing_order"].affected_count == 1
    assert checks["lifecycle_has_open_position"].affected_count == 0
    assert checks["closed_trade_has_sell_order_and_fill"].affected_count == 1


def test_position_fill_mismatch_is_ambiguous_not_auto_repaired(tmp_path: Path) -> None:
    path = tmp_path / "paper.db"
    _store(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE paper_positions SET quantity=8 WHERE symbol='AAA'")
    check = _by_code(audit_paper_database(path, paper_version="V1"))["position_quantity_vs_fill_net"]
    assert check.state is FindingState.AMBIGUOUS
    assert check.capability is ReconciliationCapability.MANUAL_REVIEW_REQUIRED


def test_missing_lifecycle_is_not_reconstructed_from_current_policy(tmp_path: Path) -> None:
    path = tmp_path / "paper.db"
    _store(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM paper_position_lifecycle WHERE symbol='AAA'")
    check = _by_code(audit_paper_database(path, paper_version="V2"))["open_position_has_lifecycle"]
    assert check.state is FindingState.ISSUE
    assert check.capability is ReconciliationCapability.MANUAL_REVIEW_REQUIRED
    assert "strategy/config context is not inferred" in check.evidence
    context = _by_code(audit_paper_database(path, paper_version="V2"))["lifecycle_reconstruction_context_persisted"]
    assert context.state is FindingState.AMBIGUOUS
    assert context.capability is ReconciliationCapability.REPAIR_WITH_FROZEN_CONTEXT


def test_read_only_audit_does_not_modify_db_or_call_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "paper.db"
    _store(path)
    before = sha256(path.read_bytes()).hexdigest()

    def forbidden(*args, **kwargs):
        raise AssertionError("network must not be called")

    monkeypatch.setattr("socket.create_connection", forbidden)
    result = audit_paper_database(path, paper_version="V3")
    after = sha256(path.read_bytes()).hexdigest()
    assert before == after
    assert result.read_only


def test_v1_v2_v3_are_explicit_and_audit_identity_is_repeatable(tmp_path: Path) -> None:
    path = tmp_path / "paper.db"
    _store(path)
    results = [audit_paper_database(path, paper_version=version) for version in PaperVersion]
    assert [item.paper_version.value for item in results] == ["V1", "V2", "V3"]
    assert len({item.identity for item in results}) == 3
    same = audit_paper_database(path, paper_version="V1")
    assert same.identity == results[0].identity
    assert same.checks == results[0].checks
    exit_link = _by_code(same)["exit_intent_durable_link"]
    assert exit_link.state is FindingState.AMBIGUOUS
    assert exit_link.severity.value == "MATERIAL"
    assert "no source intent" in exit_link.evidence


def test_nonexistent_db_fails_closed_without_creating_it(tmp_path: Path) -> None:
    path = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        audit_paper_database(path, paper_version="V1")
    assert not path.exists()

