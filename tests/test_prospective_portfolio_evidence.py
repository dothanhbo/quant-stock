from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from execution.models import Position
from execution.paper_broker import PaperBroker
from execution.persistence import PaperTradingStore
from execution.portfolio_state import PortfolioState
from quantlab.evidence import (
    PaperEventCursor,
    ProspectivePortfolioEvidenceLedger,
    capture_prospective_portfolio_evidence,
    inspect_prospective_portfolio_evidence,
    paper_store_identity,
    read_paper_account_epoch,
)
from quantlab.evidence import prospective_portfolio as evidence_module


STRATEGY = "Q70_FROZEN"
STORE_ID = "q70-frozen"
CONFIG_A = "config-a-fingerprint"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _paper_counts(path: Path) -> tuple[int, int, int]:
    with sqlite3.connect(path) as connection:
        return tuple(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("paper_orders", "paper_fills", "paper_positions")
        )


def _market_database(path: Path, sessions: tuple[str, ...] = ("2026-01-02", "2026-01-05", "2026-01-06")) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE prices(symbol TEXT,time TEXT,open REAL,high REAL,low REAL,close REAL,volume INTEGER)"
        )
        for index, session in enumerate(sessions):
            connection.execute(
                "INSERT INTO prices VALUES (?,?,?,?,?,?,?)",
                ("VNINDEX", session, 1000 + index, 1001 + index, 999 + index, 1000 + index, 1_000_000),
            )


def _paper_database(path: Path) -> None:
    store = PaperTradingStore(path)
    store.save_initial_cash(1_000.0)
    store.save_portfolio_state(
        PortfolioState(
            initial_cash=1_000.0,
            cash=800.0,
            realized_pnl=5.0,
            positions={"AAA": Position("AAA", quantity=10, average_price=11.0, market_price=12.0)},
        )
    )
    with sqlite3.connect(path) as connection:
        now = "2026-01-02T16:00:00+00:00"
        connection.execute(
            "INSERT INTO paper_pending_signals(signal_date,symbol,payload,status,created_at) VALUES (?,?,?,?,?)",
            ("2026-01-01", "BBB", json.dumps({"symbol": "BBB", "date": "2026-01-01", "entry": 10.0}), "FILLED", now),
        )
        connection.executemany(
            """
            INSERT INTO paper_orders(
                client_order_id,symbol,side,quantity,order_type,limit_price,reference_price,
                status,filled_quantity,average_fill_price,rejection_reason,created_at,updated_at,
                source_intent_id,execution_context
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                (
                    "entry-bbb", "BBB", "BUY", 10, "MARKET", None, 10.1, "FILLED", 10, 10.2,
                    None, now, now, "pending_signal:1", None,
                ),
                (
                    "exit-bbb", "BBB", "SELL", 10, "MARKET", None, 12.0, "FILLED", 10, 11.9,
                    None, now, now, "paper_exit:test", json.dumps({"exit": {
                        "entry_date": "2026-01-01", "entry_price": 10.2,
                        "exit_date": "2026-01-02", "holding_days": 1,
                        "exit_reason": "TAKE_PROFIT", "entry_order_id": "entry-bbb",
                        "strategy_version": STRATEGY, "policy_fingerprint": "policy-a",
                    }}),
                ),
            ),
        )
        connection.executemany(
            """
            INSERT INTO paper_fills(
                order_id,symbol,side,quantity,price,gross_value,commission,slippage_cost,net_cash_flow,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                ("entry-bbb", "BBB", "BUY", 10, 10.2, 102.0, 1.0, 0.5, -103.0, now),
                ("exit-bbb", "BBB", "SELL", 10, 11.9, 119.0, 1.5, 0.7, 117.5, now),
            ),
        )
        connection.execute(
            """
            INSERT INTO paper_closed_trades(
                symbol,entry_date,exit_date,quantity,entry_price,exit_price,gross_proceeds,
                commission,realized_pnl,return_pct,holding_days,exit_reason,order_id,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("BBB", "2026-01-01", "2026-01-02", 10, 10.2, 11.9, 119.0, 1.5, 15.5, 15.196078, 1, "TAKE_PROFIT", "exit-bbb", now),
        )


@pytest.fixture()
def stores(tmp_path: Path) -> tuple[Path, Path, Path]:
    paper, market, evidence = tmp_path / "paper.db", tmp_path / "market.db", tmp_path / "evidence.db"
    _paper_database(paper)
    _market_database(market)
    return paper, market, evidence


def _capture(
    stores: tuple[Path, Path, Path],
    *,
    session: str = "2026-01-02",
    configuration: str = CONFIG_A,
    baseline: PaperEventCursor | None = PaperEventCursor(),
) -> object:
    paper, market, evidence = stores
    return capture_prospective_portfolio_evidence(
        observation_date=session,
        paper_database_path=paper,
        source_store_id=STORE_ID,
        strategy_identity=STRATEGY,
        runtime_configuration_fingerprint=configuration,
        market_database_path=market,
        evidence_database_path=evidence,
        baseline_event_cursor=baseline,
        captured_at_utc="2026-01-02T17:00:00+00:00",
    )


def test_capture_is_immutable_deterministic_and_reconciles_canonical_equity(stores: tuple[Path, Path, Path]) -> None:
    result = _capture(stores)
    record = result.record

    assert result.created is True
    assert record.evidence_version == "v1"
    assert record.strategy_identity == STRATEGY
    assert record.runtime_configuration_fingerprint == CONFIG_A
    assert record.cash + record.positions_value == pytest.approx(record.equity)
    assert record.unrealized_pnl == pytest.approx(sum(item.unrealized_pnl for item in record.positions))
    assert (record.realized_pnl, record.unrealized_pnl) == (5.0, 10.0)
    assert record.daily_pnl is None and record.daily_return_pct is None
    assert record.drawdown_pct == 0.0
    with pytest.raises(FrozenInstanceError):
        record.cash = 0.0  # type: ignore[misc]


def test_replay_is_idempotent_and_capture_does_not_mutate_paper_source(stores: tuple[Path, Path, Path]) -> None:
    paper, _market, evidence = stores
    before = _digest(paper)
    counts_before = _paper_counts(paper)
    first = _capture(stores)
    second = _capture(stores)

    assert first.created is True
    assert second.created is False
    assert second.record.record_identity == first.record.record_identity
    assert _digest(paper) == before
    assert _paper_counts(paper) == counts_before
    assert len(ProspectivePortfolioEvidenceLedger(evidence).records()) == 1


def test_same_session_lifecycle_retry_reuses_existing_snapshot_with_event_delta(stores: tuple[Path, Path, Path]) -> None:
    first = _capture(stores, baseline=PaperEventCursor())
    # A real retry reads this high-water mark after the first completed
    # lifecycle, so it must not re-derive an empty delta and conflict.
    second = _capture(stores, baseline=PaperEventCursor(2, 1))

    assert first.created is True
    assert second.created is False
    assert second.record.record_identity == first.record.record_identity
    assert tuple(item.fill_id for item in second.record.fills_since_previous) == (1, 2)


def test_concurrent_identical_ledger_appends_converge_on_one_record(stores: tuple[Path, Path, Path]) -> None:
    record = _capture(stores).record
    concurrent_ledger = ProspectivePortfolioEvidenceLedger(stores[2].with_name("concurrent-evidence.db"))
    concurrent_ledger.initialize()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(concurrent_ledger.append, (record, record)))

    assert sum(item.created for item in results) == 1
    assert {item.record.record_identity for item in results} == {record.record_identity}
    assert len(concurrent_ledger.records()) == 1


def test_evidence_write_failure_never_partially_mutates_paper_source(stores: tuple[Path, Path, Path]) -> None:
    paper, market, _evidence = stores
    before = _digest(paper)
    counts_before = _paper_counts(paper)

    with pytest.raises(sqlite3.OperationalError):
        capture_prospective_portfolio_evidence(
            observation_date="2026-01-02",
            paper_database_path=paper,
            source_store_id=STORE_ID,
            strategy_identity=STRATEGY,
            runtime_configuration_fingerprint=CONFIG_A,
            market_database_path=market,
            evidence_database_path=paper.parent,
            baseline_event_cursor=PaperEventCursor(),
        )

    assert _digest(paper) == before
    assert _paper_counts(paper) == counts_before


def test_durable_lifecycle_baseline_recovers_events_after_evidence_write_failure(
    stores: tuple[Path, Path, Path],
) -> None:
    paper, market, evidence = stores
    store = PaperTradingStore(paper)
    baseline = store.get_or_create_prospective_evidence_baseline("2026-01-02")
    assert baseline == (2, 1)
    with sqlite3.connect(paper) as connection:
        connection.execute(
            """
            INSERT INTO paper_fills(
                order_id,symbol,side,quantity,price,gross_value,commission,
                slippage_cost,net_cash_flow,created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            ("entry-bbb", "BBB", "BUY", 1, 10.3, 10.3, 0.1, 0.0, -10.4, "2026-01-02T17:01:00+00:00"),
        )
    with pytest.raises(sqlite3.OperationalError):
        capture_prospective_portfolio_evidence(
            observation_date="2026-01-02",
            paper_database_path=paper,
            source_store_id=STORE_ID,
            strategy_identity=STRATEGY,
            runtime_configuration_fingerprint=CONFIG_A,
            market_database_path=market,
            evidence_database_path=paper.parent,
            baseline_event_cursor=PaperEventCursor(*baseline),
        )

    assert store.get_or_create_prospective_evidence_baseline("2026-01-02") == baseline
    recovered = capture_prospective_portfolio_evidence(
        observation_date="2026-01-02",
        paper_database_path=paper,
        source_store_id=STORE_ID,
        strategy_identity=STRATEGY,
        runtime_configuration_fingerprint=CONFIG_A,
        market_database_path=market,
        evidence_database_path=evidence,
        baseline_event_cursor=PaperEventCursor(*baseline),
    )
    assert tuple(item.fill_id for item in recovered.record.fills_since_previous) == (3,)


def test_fills_exits_and_friction_use_authoritative_source_links(stores: tuple[Path, Path, Path]) -> None:
    record = _capture(stores).record

    assert tuple(item.fill_id for item in record.fills_since_previous) == (1, 2)
    entry = record.fills_since_previous[0]
    assert (entry.commission, entry.slippage_cost, entry.spread_or_market_impact) == (1.0, 0.5, None)
    close = record.exits_since_previous[0]
    assert close.provenance_status == "COMPLETE_SOURCE_LINK"
    assert close.entry_order_id == "entry-bbb"
    assert close.signal_date == "2026-01-01"
    assert close.signal_reference_price == 10.0
    assert close.entry_fill_price == 10.2
    assert close.exit_fill_price == 11.9
    assert close.entry_commission == 1.0
    assert close.exit_commission == 1.5
    assert close.entry_slippage_cost == 0.5
    assert close.exit_slippage_cost == 0.7
    assert close.gross_pnl == pytest.approx(17.0)
    assert close.realized_pnl == pytest.approx(15.5)
    assert close.spread_or_market_impact is None


def test_configuration_change_has_a_distinct_immutable_evidence_identity(stores: tuple[Path, Path, Path]) -> None:
    first = _capture(stores)
    second = _capture(stores, configuration="config-b-fingerprint", baseline=PaperEventCursor(2, 1))

    assert second.created is True
    assert second.record.record_identity != first.record.record_identity
    ledger = ProspectivePortfolioEvidenceLedger(stores[2])
    assert len(ledger.records(runtime_configuration_fingerprint=CONFIG_A)) == 1
    assert len(ledger.records(runtime_configuration_fingerprint="config-b-fingerprint")) == 1


def test_daily_return_and_drawdown_require_contiguous_prospective_sessions(stores: tuple[Path, Path, Path]) -> None:
    first = _capture(stores)
    paper, _market, _evidence = stores
    with sqlite3.connect(paper) as connection:
        connection.execute("UPDATE paper_metadata SET value=? WHERE key='cash'", (json.dumps(750.0),))
        connection.execute("UPDATE paper_positions SET market_price=10.0 WHERE symbol='AAA'")
    second = _capture(stores, session="2026-01-05", baseline=None)

    assert first.record.equity == pytest.approx(920.0)
    assert second.record.equity == pytest.approx(850.0)
    assert second.record.daily_pnl == pytest.approx(-70.0)
    assert second.record.daily_return_pct == pytest.approx(-70.0 / 920.0 * 100.0)
    assert second.record.running_equity_peak == pytest.approx(920.0)
    assert second.record.drawdown_pct == pytest.approx(850.0 / 920.0 * 100.0 - 100.0)


def test_reset_starts_a_new_source_account_epoch_without_cross_account_drawdown(
    stores: tuple[Path, Path, Path],
) -> None:
    first = _capture(stores).record
    paper, market, evidence = stores
    broker = PaperBroker(
        database_path=paper,
        initial_cash=1_000.0,
        commission_rate=0.0015,
        slippage_bps=5.0,
        restore_state=True,
    )
    broker.reset_paper_account(initial_cash=1_000.0)

    second = capture_prospective_portfolio_evidence(
        observation_date="2026-01-05",
        paper_database_path=paper,
        source_store_id=STORE_ID,
        strategy_identity=STRATEGY,
        runtime_configuration_fingerprint=CONFIG_A,
        market_database_path=market,
        evidence_database_path=evidence,
        baseline_event_cursor=PaperEventCursor(),
        captured_at_utc="2026-01-05T17:00:00+00:00",
    ).record

    assert read_paper_account_epoch(paper) == second.source_account_epoch_id
    assert second.source_account_epoch_id != first.source_account_epoch_id
    assert second.source_store_identity != first.source_store_identity
    assert second.daily_pnl is None and second.daily_return_pct is None
    assert second.running_equity_peak == pytest.approx(1_000.0)
    assert second.drawdown_pct == 0.0
    assert second.fills_since_previous == () and second.exits_since_previous == ()


def test_legacy_source_without_account_epoch_never_derives_daily_drawdown(stores: tuple[Path, Path, Path]) -> None:
    paper, _market, _evidence = stores
    with sqlite3.connect(paper) as connection:
        connection.execute("DELETE FROM paper_metadata WHERE key='account_epoch_id'")

    record = _capture(stores).record

    assert record.source_account_epoch_id is None
    assert record.daily_pnl is None and record.daily_return_pct is None
    assert record.running_equity_peak is None and record.drawdown_pct is None
    assert "SOURCE_ACCOUNT_EPOCH_UNAVAILABLE_DAILY_RETURN_UNAVAILABLE" in record.provenance_warnings


def test_normal_broker_restore_adds_only_legacy_epoch_provenance(stores: tuple[Path, Path, Path]) -> None:
    paper, _market, _evidence = stores
    with sqlite3.connect(paper) as connection:
        connection.execute("DELETE FROM paper_metadata WHERE key='account_epoch_id'")
    before = _paper_counts(paper)

    restored = PaperBroker(database_path=paper, initial_cash=1_000.0, restore_state=True)

    assert restored.get_cash() == pytest.approx(800.0)
    assert _paper_counts(paper) == before
    assert read_paper_account_epoch(paper)


def test_gap_is_visible_and_never_backfilled(stores: tuple[Path, Path, Path]) -> None:
    _capture(stores)
    paper, _market, evidence = stores
    with sqlite3.connect(paper) as connection:
        connection.execute("UPDATE paper_metadata SET value=? WHERE key='cash'", (json.dumps(790.0),))
    _capture(stores, session="2026-01-06", baseline=None)

    status = inspect_prospective_portfolio_evidence(
        evidence_database_path=evidence,
        market_database_path=stores[1],
        source_store_id=STORE_ID,
        strategy_identity=STRATEGY,
        paper_database_path=paper,
    )
    assert status.continuity_state == "GAPS_DETECTED"
    assert status.missing_sessions == ("2026-01-05",)
    records = ProspectivePortfolioEvidenceLedger(evidence).records()
    assert tuple(item.observation_date for item in records) == ("2026-01-02", "2026-01-06")


def test_first_capture_without_baseline_does_not_backfill_historical_events(stores: tuple[Path, Path, Path]) -> None:
    result = _capture(stores, baseline=None)

    assert result.record.fills_since_previous == ()
    assert result.record.exits_since_previous == ()
    assert "PRE_ACTIVATION_FILL_AND_EXIT_EVENTS_NOT_BACKFILLED" in result.record.provenance_warnings


def test_regime_is_causal_and_unknown_when_history_is_insufficient(stores: tuple[Path, Path, Path]) -> None:
    _paper, market, _evidence = stores
    initial, _warnings = evidence_module._market_context(market, "2026-01-02")
    with sqlite3.connect(market) as connection:
        connection.execute(
            "INSERT INTO prices VALUES (?,?,?,?,?,?,?)",
            ("VNINDEX", "2026-12-31", 999, 1000, 998, 1.0, 1_000_000),
        )
    after, _warnings = evidence_module._market_context(market, "2026-01-02")

    assert initial["benchmark_close"] == after["benchmark_close"] == 1000.0
    assert initial["market_regime_label"] == after["market_regime_label"] == "UNKNOWN"
    assert initial["regime_computation_identity"] == after["regime_computation_identity"]


def test_query_is_ordered_and_append_only_storage_rejects_mutation(stores: tuple[Path, Path, Path]) -> None:
    _capture(stores)
    paper, _market, evidence = stores
    with sqlite3.connect(paper) as connection:
        connection.execute("UPDATE paper_metadata SET value=? WHERE key='cash'", (json.dumps(790.0),))
    _capture(stores, session="2026-01-05", baseline=None)
    ledger = ProspectivePortfolioEvidenceLedger(evidence)

    records = ledger.records(strategy_identity=STRATEGY, start_date="2026-01-01", end_date="2026-01-05")
    assert tuple(item.observation_date for item in records) == ("2026-01-02", "2026-01-05")
    with sqlite3.connect(evidence) as connection:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute("UPDATE prospective_portfolio_evidence SET observation_date='2026-01-04'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            connection.execute("DELETE FROM prospective_portfolio_evidence")


def test_record_boundary_rejects_inconsistent_accounting(stores: tuple[Path, Path, Path]) -> None:
    record = _capture(stores).record

    with pytest.raises(ValueError, match="positions value"):
        replace(record, cash=799.0, positions_value=121.0, equity=920.0)
    with pytest.raises(ValueError, match="gross exposure"):
        replace(record, gross_exposure_pct=0.0)
    with pytest.raises(ValueError, match="position market value"):
        replace(record.positions[0], market_value=1.0)


def test_reader_rejects_append_only_schema_and_denormalized_column_tampering(
    stores: tuple[Path, Path, Path],
) -> None:
    _capture(stores)
    evidence = stores[2]
    with sqlite3.connect(evidence) as connection:
        connection.execute("DROP TRIGGER prospective_portfolio_evidence_no_update")
        connection.execute(
            """
            CREATE TRIGGER prospective_portfolio_evidence_no_update
            BEFORE UPDATE ON prospective_portfolio_evidence
            BEGIN SELECT 1; END
            """
        )

    with pytest.raises(ValueError, match="append-only triggers are invalid"):
        ProspectivePortfolioEvidenceLedger(evidence).records()

    with sqlite3.connect(evidence) as connection:
        connection.execute("DROP TRIGGER prospective_portfolio_evidence_no_update")
        connection.execute(
            "UPDATE prospective_portfolio_evidence SET strategy_identity='TAMPERED'"
        )
        connection.execute(
            """
            CREATE TRIGGER prospective_portfolio_evidence_no_update
            BEFORE UPDATE ON prospective_portfolio_evidence
            BEGIN SELECT RAISE(ABORT, 'append-only table: prospective_portfolio_evidence'); END
            """
        )

    with pytest.raises(ValueError, match="stored query column does not match payload"):
        ProspectivePortfolioEvidenceLedger(evidence).records(
            strategy_identity=STRATEGY
        )


def test_same_session_replay_rejects_changed_event_content(stores: tuple[Path, Path, Path]) -> None:
    _capture(stores, baseline=PaperEventCursor())
    paper, _market, _evidence = stores
    with sqlite3.connect(paper) as connection:
        connection.execute("UPDATE paper_fills SET price=99.0 WHERE id=1")

    with pytest.raises(ValueError, match="conflicting prospective evidence source state"):
        _capture(stores, baseline=PaperEventCursor(2, 1))


def test_store_identity_binds_canonical_paper_path(stores: tuple[Path, Path, Path]) -> None:
    paper, _market, _evidence = stores
    identity = paper_store_identity(store_id=STORE_ID, strategy_identity=STRATEGY, database_path=paper)

    assert identity == paper_store_identity(store_id=STORE_ID, strategy_identity=STRATEGY, database_path=paper.resolve())
    assert identity != paper_store_identity(store_id=STORE_ID, strategy_identity="V3_BREADTH_40_60", database_path=paper)
