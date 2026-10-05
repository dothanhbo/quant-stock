from __future__ import annotations

from dataclasses import replace
import sqlite3

import pytest

import quantlab.transactional_market_data as market
from quantlab.completed_session import SymbolSessionStatus
from quantlab.operational_admission import IngestionIntent
from tests.test_disposable_shadow_target import source, _observe, _snapshot
from tests.test_operational_market_data_shadow import _batch, _frame


RAW_INSERT = """INSERT INTO prices(symbol,time,open,high,low,close,volume)
    VALUES('ESCAPE','2030-01-01',1,1,1,1,1)"""


def _delegate_spy(monkeypatch):
    delegate = market.commit_price_batch_shadow
    calls = []

    def record(target, batch, **kwargs):
        calls.append((target, kwargs.get("target_capability")))
        return delegate(target, batch, **kwargs)

    monkeypatch.setattr(market, "commit_price_batch_shadow", record)
    return calls


def _assert_revoked(handle, clone):
    connection = market._CLONE_BINDINGS[handle].connection
    assert not connection.in_transaction
    assert all(connection not in registry for registry in (
        market._PRICE_DML_GRANTS, market._ADMISSION_TRANSACTIONS, market._SCOPED_CURSORS,
    ))
    before = _snapshot(clone)
    with pytest.raises(sqlite3.DatabaseError):
        connection.cursor().execute(RAW_INSERT)
    assert _snapshot(clone) == before


@pytest.mark.parametrize("outcome", (
    "append", "replay", "backfill", "revision", "missing-completion", "not-trading", "unknown",
))
def test_strict_entrypoint_preserves_admission_outcomes(source, tmp_path, monkeypatch, outcome):
    clone = tmp_path / "outcomes.db"
    calls = _delegate_spy(monkeypatch)
    attempts = _observe(monkeypatch)
    batch = _batch("strict-outcome")
    if outcome == "backfill":
        batch = _batch("strict-outcome", intent=IngestionIntent.BACKFILL)
    if outcome == "revision":
        batch = _batch("strict-outcome", frame=_frame((2, 3, 4), changed_close_day=3))
    if outcome == "missing-completion":
        batch = replace(batch, completed_session_result=None)
    if outcome in {"not-trading", "unknown"}:
        batch = _batch("strict-outcome", symbol_session_status=(
            SymbolSessionStatus.NOT_TRADING if outcome == "not-trading" else SymbolSessionStatus.UNKNOWN
        ))
    source_before = _snapshot(source)
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        before = _snapshot(clone)
        result = market.commit_disposable_price_batch_shadow(handle, batch, target_capability=capability)
        assert calls == [(handle, capability)]
        assert result.operational_result.research_eligible is False
        stored = _snapshot(clone)
        if outcome in {"append", "replay"}:
            assert result.status is market.ReceiptStatus.APPROVED and result.rows_written == 1
            assert {attempt[1] for attempt in attempts} == {"prices", "market_price_provenance"}
            assert len(stored[0]) == 5 and len(stored[1]) == 1 and len(stored[3]) == 1
        else:
            assert result.status is market.ReceiptStatus.REJECTED and result.rows_written == 0
            assert not attempts and stored[:2] == before[:2] and not stored[3]
        if outcome in {"backfill", "revision"}:
            assert len(stored[-1]) == 1
            assert market._CLONE_BINDINGS[handle].connection.execute(
                "SELECT research_eligible FROM market_ingestion_staging"
            ).fetchall() == [(0,)]
        if outcome in {"replay", "backfill", "revision"}:
            attempts.clear()
            replay = market.commit_disposable_price_batch_shadow(handle, batch, target_capability=capability)
            assert replay.idempotent_replay and replay.operational_result == result.operational_result
            assert not attempts and _snapshot(clone) == stored
            assert calls == [(handle, capability)] * 2
        connection = market._CLONE_BINDINGS[handle].connection
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT research_eligible FROM market_ingestion_manifests").fetchall() in ([], [(0,)])
        _assert_revoked(handle, clone)
    assert _snapshot(source) == source_before


@pytest.mark.parametrize("kind", (
    "path", "string", "raw-factory", "raw-unmanaged", "path-with-capability", "raw-with-capability",
    "no-handle", "unregistered", "missing-capability", "fabricated-capability", "foreign-capability",
))
def test_bad_target_never_reaches_legacy_delegate(source, tmp_path, monkeypatch, kind):
    clone = tmp_path / "bad-target.db"
    calls = _delegate_spy(monkeypatch)
    attempts = _observe(monkeypatch)
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        with market.create_disposable_shadow_target(source, tmp_path / "other.db") as (_, foreign):
            connection = market._CLONE_BINDINGS[handle].connection
            unmanaged = sqlite3.connect(clone)
            try:
                target, token = handle, capability
                if kind in {"path", "path-with-capability"}: target = clone
                if kind == "string": target = str(clone)
                if kind in {"raw-factory", "raw-with-capability"}: target = connection
                if kind == "raw-unmanaged": target = unmanaged
                if kind == "no-handle": target = None
                if kind == "unregistered": target = market.DisposableShadowTarget()
                if kind in {"path", "string", "raw-factory", "raw-unmanaged", "missing-capability"}: token = None
                if kind == "fabricated-capability": token = market.ShadowTargetCapability()
                if kind == "foreign-capability": token = foreign
                before = _snapshot(clone)
                with pytest.raises(market.ShadowTargetError):
                    if kind == "missing-capability":
                        market.commit_disposable_price_batch_shadow(target, _batch("bad-target"))
                    else:
                        market.commit_disposable_price_batch_shadow(target, _batch("bad-target"), target_capability=token)
                assert not calls and not attempts and _snapshot(clone) == before
            finally:
                unmanaged.close()


@pytest.mark.parametrize("kind", ["closed", "expired"])
def test_dead_handle_rejects_before_delegation(source, tmp_path, monkeypatch, kind):
    clone = tmp_path / "dead.db"
    calls = _delegate_spy(monkeypatch)
    attempts = _observe(monkeypatch)
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        if kind == "closed":
            market._CLONE_BINDINGS[handle].connection.close()
            with pytest.raises(market.ShadowTargetError):
                market.commit_disposable_price_batch_shadow(handle, _batch("dead"), target_capability=capability)
    if kind == "expired":
        with pytest.raises(market.ShadowTargetError):
            market.commit_disposable_price_batch_shadow(handle, _batch("dead"), target_capability=capability)
    assert not calls and not attempts and _snapshot(clone) == _snapshot(source)


@pytest.mark.parametrize("stage", ["after_manifest", "after_prices_and_links", "after_rejection_or_staging"])
def test_entrypoint_fault_rolls_back_without_fallback(source, tmp_path, monkeypatch, stage):
    clone = tmp_path / "fault.db"
    calls = _delegate_spy(monkeypatch)
    attempts = _observe(monkeypatch)
    batch = _batch("strict-fault")
    if stage == "after_rejection_or_staging":
        batch = _batch("strict-fault", intent=IngestionIntent.BACKFILL)
    def fault(at):
        if at == stage: raise RuntimeError("strict injected fault")
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        before = _snapshot(clone)
        with pytest.raises(RuntimeError, match="strict injected fault"):
            market.commit_disposable_price_batch_shadow(handle, batch,
                target_capability=capability, failure_injector=fault)
        assert calls == [(handle, capability)] and _snapshot(clone) == before
        if stage == "after_prices_and_links":
            assert {attempt[1] for attempt in attempts} == {"prices", "market_price_provenance"}
        else:
            assert not attempts
        _assert_revoked(handle, clone)


def test_identity_conflict_propagates_without_fallback(source, tmp_path, monkeypatch):
    clone = tmp_path / "conflict.db"
    calls = _delegate_spy(monkeypatch)
    attempts = _observe(monkeypatch)
    batch = _batch("strict-conflict")
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        market.commit_disposable_price_batch_shadow(handle, batch, target_capability=capability)
        before = _snapshot(clone)
        attempts.clear()
        with pytest.raises(market.OperationIdentityConflict):
            market.commit_disposable_price_batch_shadow(handle,
                replace(batch, source_references=("fixture://changed-source",)), target_capability=capability)
        assert calls == [(handle, capability)] * 2
        assert not attempts and _snapshot(clone) == before
        _assert_revoked(handle, clone)


def test_delegate_validation_error_is_not_retried(source, tmp_path, monkeypatch):
    clone = tmp_path / "delegate-error.db"
    calls = []
    def fail(target, batch, **kwargs):
        calls.append((target, kwargs["target_capability"]))
        raise ValueError("delegate validation failed")
    monkeypatch.setattr(market, "commit_price_batch_shadow", fail)
    attempts = _observe(monkeypatch)
    with market.create_disposable_shadow_target(source, clone) as (handle, capability):
        before = _snapshot(clone)
        with pytest.raises(ValueError, match="delegate validation failed"):
            market.commit_disposable_price_batch_shadow(handle, _batch("delegate-error"), target_capability=capability)
        assert calls == [(handle, capability)] and not attempts and _snapshot(clone) == before
        _assert_revoked(handle, clone)
