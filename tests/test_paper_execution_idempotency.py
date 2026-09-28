from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import sqlite3
from pathlib import Path
from threading import Barrier

import pytest

from execution.models import Order, OrderSide, OrderType
from execution.paper_broker import PaperBroker
from execution.persistence import PaperTradingStore
from execution.signal_executor import PaperExecutionConfig, PaperSignalExecutor


SIGNAL_DATE = date(2026, 8, 5)
EXECUTION_DATE = date(2026, 8, 6)


def _market_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE prices(symbol TEXT, time TEXT, open REAL, close REAL, volume REAL)"
        )
        for offset in range(19, -1, -1):
            day = SIGNAL_DATE - timedelta(days=offset)
            connection.execute(
                "INSERT INTO prices VALUES ('AAA', ?, 10, 10, 1000000)",
                (day.isoformat(),),
            )
        for symbol, open_price in (("VNINDEX", 1300.0), ("AAA", 51.0)):
            connection.execute(
                "INSERT INTO prices VALUES (?, ?, ?, ?, 1000000)",
                (symbol, EXECUTION_DATE.isoformat(), open_price, open_price),
            )


def _executor(paper_db: Path) -> PaperSignalExecutor:
    return PaperSignalExecutor(PaperExecutionConfig(
        enabled=True,
        database_path=paper_db,
        initial_cash=100_000_000,
        position_sizer="fixed_fraction",
        fixed_fraction_pct=10.0,
        atr_stop_multiplier=2.0,
        maximum_orders_per_scan=3,
        lot_size=100,
        commission_rate=0.0015,
        slippage_bps=5.0,
        maximum_position_pct=20.0,
        maximum_gross_exposure_pct=80.0,
        maximum_open_positions=10,
        minimum_cash_buffer_pct=5.0,
        maximum_order_adtv20_pct=1.0,
    ))


def _queue(executor: PaperSignalExecutor) -> int:
    result = executor.queue_signals([{
        "symbol": "AAA", "date": SIGNAL_DATE.isoformat(), "entry": 10.0,
        "atr": 1.0, "score": 90, "regime": "BULL",
    }], report_date=SIGNAL_DATE)
    assert result.queued_count == 1
    with sqlite3.connect(executor.config.database_path) as connection:
        return int(connection.execute("SELECT id FROM paper_pending_signals").fetchone()[0])


def _counts(path: Path) -> tuple[int, int, int, int, str]:
    with sqlite3.connect(path) as connection:
        orders, fills, quantity = connection.execute(
            "SELECT (SELECT COUNT(*) FROM paper_orders), "
            "(SELECT COUNT(*) FROM paper_fills), "
            "COALESCE((SELECT quantity FROM paper_positions WHERE symbol='AAA'), 0)"
        ).fetchone()
        pending = connection.execute(
            "SELECT COUNT(*) FROM paper_pending_signals WHERE status='PENDING'"
        ).fetchone()[0]
        status = connection.execute(
            "SELECT status FROM paper_pending_signals"
        ).fetchone()[0]
    return int(orders), int(fills), int(quantity), int(pending), str(status)


def test_first_pending_execution_is_durable_and_identical_retry_is_reported(tmp_path: Path) -> None:
    paper_db, market_db = tmp_path / "paper.db", tmp_path / "market.db"
    _market_db(market_db)
    executor = _executor(paper_db)
    pending_id = _queue(executor)

    first = executor.execute_pending_signals(
        valuation_date=EXECUTION_DATE, market_database_path=market_db
    )
    restarted = _executor(paper_db)
    retry = restarted.execute_pending_signals(
        valuation_date=EXECUTION_DATE, market_database_path=market_db
    )

    assert first.executions[0].status == "FILLED"
    assert retry.executions == []
    assert _counts(paper_db)[:4] == (1, 1, first.executions[0].quantity, 0)
    with sqlite3.connect(paper_db) as connection:
        source = connection.execute(
            "SELECT source_intent_id FROM paper_orders"
        ).fetchone()[0]
    assert source == f"pending_signal:{pending_id}"


def test_retry_before_any_execution_write_eventually_executes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_db, market_db = tmp_path / "paper.db", tmp_path / "market.db"
    _market_db(market_db)
    executor = _executor(paper_db)
    _queue(executor)
    original = executor.order_manager.buy_market

    def fail_before_submit(**kwargs):
        raise RuntimeError("injected crash before broker submission")

    monkeypatch.setattr(executor.order_manager, "buy_market", fail_before_submit)
    with pytest.raises(RuntimeError, match="injected crash"):
        executor.execute_pending_signals(
            valuation_date=EXECUTION_DATE, market_database_path=market_db
        )
    assert _counts(paper_db)[:4] == (0, 0, 0, 1)

    monkeypatch.setattr(executor.order_manager, "buy_market", original)
    retry = _executor(paper_db).execute_pending_signals(
        valuation_date=EXECUTION_DATE, market_database_path=market_db
    )
    assert retry.executions[0].status == "FILLED"
    assert _counts(paper_db)[:4] == (1, 1, retry.executions[0].quantity, 0)


def test_order_identity_and_fill_bundle_roll_back_together_then_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_db, market_db = tmp_path / "paper.db", tmp_path / "market.db"
    _market_db(market_db)
    executor = _executor(paper_db)
    _queue(executor)
    original = executor.broker._store._save_fill

    def fail_after_order_insert(connection, fill):
        raise RuntimeError("injected crash after order insert")

    monkeypatch.setattr(executor.broker._store, "_save_fill", fail_after_order_insert)
    with pytest.raises(RuntimeError, match="after order insert"):
        executor.execute_pending_signals(
            valuation_date=EXECUTION_DATE, market_database_path=market_db
        )
    assert _counts(paper_db)[:4] == (0, 0, 0, 1)

    monkeypatch.setattr(executor.broker._store, "_save_fill", original)
    result = _executor(paper_db).execute_pending_signals(
        valuation_date=EXECUTION_DATE, market_database_path=market_db
    )
    assert result.executions[0].status == "FILLED"
    assert _counts(paper_db)[:4] == (1, 1, result.executions[0].quantity, 0)


def test_fill_and_portfolio_commit_before_pending_completion_is_deduplicated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_db, market_db = tmp_path / "paper.db", tmp_path / "market.db"
    _market_db(market_db)
    executor = _executor(paper_db)
    _queue(executor)

    def fail_pending_completion(*args, **kwargs):
        raise RuntimeError("injected crash before pending completion")

    monkeypatch.setattr(executor.broker, "complete_pending_signal", fail_pending_completion)
    with pytest.raises(RuntimeError, match="before pending completion"):
        executor.execute_pending_signals(
            valuation_date=EXECUTION_DATE, market_database_path=market_db
        )
    persisted_quantity = _counts(paper_db)[2]
    assert persisted_quantity > 0
    assert _counts(paper_db)[:4] == (1, 1, persisted_quantity, 1)

    retry = _executor(paper_db).execute_pending_signals(
        valuation_date=EXECUTION_DATE, market_database_path=market_db
    )
    assert retry.executions[0].status == "ALREADY_EXECUTED"
    assert _counts(paper_db)[:4] == (1, 1, persisted_quantity, 0)
    assert _counts(paper_db)[4] == "ALREADY_EXECUTED"


def test_retry_repairs_missing_lifecycle_after_execution_bundle_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_db, market_db = tmp_path / "paper.db", tmp_path / "market.db"
    _market_db(market_db)
    executor = _executor(paper_db)
    _queue(executor)

    def fail_after_execution_commit(**kwargs):
        raise RuntimeError("injected interruption before lifecycle initialization")

    monkeypatch.setattr(executor, "_initialize_filled_position", fail_after_execution_commit)
    with pytest.raises(RuntimeError, match="before lifecycle initialization"):
        executor.execute_pending_signals(
            valuation_date=EXECUTION_DATE, market_database_path=market_db
        )
    persisted_quantity = _counts(paper_db)[2]
    assert persisted_quantity > 0
    assert _counts(paper_db)[:4] == (1, 1, persisted_quantity, 1)
    first_lifecycle = executor.broker.get_position_lifecycle("AAA")
    assert first_lifecycle is not None
    assert first_lifecycle.entry_order_id is not None
    assert first_lifecycle.policy_fingerprint is not None

    restarted = _executor(paper_db)
    result = restarted.execute_pending_signals(
        valuation_date=EXECUTION_DATE, market_database_path=market_db
    )
    assert result.executions[0].status == "ALREADY_EXECUTED"
    assert restarted.broker.get_position_lifecycle("AAA") == first_lifecycle
    assert _counts(paper_db)[:4] == (1, 1, persisted_quantity, 0)


def test_order_identity_uses_unique_durable_key_and_completed_intent_is_idempotent(
    tmp_path: Path,
) -> None:
    paper_db = tmp_path / "paper.db"
    broker = PaperBroker(database_path=paper_db, initial_cash=1_000_000, slippage_bps=0)
    broker.update_market_price("AAA", 100.0, persist_snapshot=False)
    source = "pending_signal:51"
    first = broker.submit_order(Order(
        symbol="AAA", side=OrderSide.BUY, quantity=100,
        order_type=OrderType.MARKET, reference_price=100.0, source_intent_id=source,
    ))
    restarted = PaperBroker(database_path=paper_db, initial_cash=1_000_000, slippage_bps=0)
    retry = restarted.submit_order(Order(
        symbol="AAA", side=OrderSide.BUY, quantity=100,
        order_type=OrderType.MARKET, reference_price=100.0, source_intent_id=source,
    ))

    assert first is not None and retry is not None
    assert retry.order_id == first.order_id
    assert restarted.consume_duplicate_execution(source) is True
    assert restarted.get_position("AAA").quantity == 100
    with sqlite3.connect(paper_db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1


def test_retry_resumes_a_durably_recorded_incomplete_order_identity(tmp_path: Path) -> None:
    paper_db = tmp_path / "paper.db"
    source = "pending_signal:52"
    original_order = Order(
        symbol="AAA", side=OrderSide.BUY, quantity=100,
        order_type=OrderType.MARKET, reference_price=100.0,
        source_intent_id=source,
    )
    PaperTradingStore(paper_db).save_order(original_order)

    broker = PaperBroker(database_path=paper_db, initial_cash=1_000_000, slippage_bps=0)
    broker.update_market_price("AAA", 100.0, persist_snapshot=False)
    fill = broker.submit_order(Order(
        symbol="AAA", side=OrderSide.BUY, quantity=100,
        order_type=OrderType.MARKET, reference_price=100.0,
        source_intent_id=source,
    ))

    assert fill is not None
    assert fill.order_id == original_order.client_order_id
    assert broker.consume_execution_disposition(source) == "RESUMED"
    with sqlite3.connect(paper_db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1
        assert connection.execute("SELECT quantity FROM paper_positions WHERE symbol='AAA'").fetchone()[0] == 100


def test_risk_guard_duplicate_check_allows_same_intent_resume_only(tmp_path: Path) -> None:
    executor = _executor(tmp_path / "paper.db")
    executor.broker.record_order(Order(
        symbol="AAA", side=OrderSide.BUY, quantity=100,
        order_type=OrderType.MARKET, reference_price=100.0,
        source_intent_id="pending_signal:53",
    ))
    same_intent = Order(
        symbol="AAA", side=OrderSide.BUY, quantity=100,
        order_type=OrderType.MARKET, reference_price=100.0,
        source_intent_id="pending_signal:53",
    )
    different_intent = Order(
        symbol="AAA", side=OrderSide.BUY, quantity=100,
        order_type=OrderType.MARKET, reference_price=100.0,
        source_intent_id="pending_signal:54",
    )

    assert executor.order_manager._duplicate_order_exists(same_intent) is False
    assert executor.order_manager._duplicate_order_exists(different_intent) is True


def test_distinct_intent_ids_for_same_symbol_are_distinct_executions(tmp_path: Path) -> None:
    paper_db = tmp_path / "paper.db"
    broker = PaperBroker(database_path=paper_db, initial_cash=1_000_000, slippage_bps=0)
    broker.update_market_price("AAA", 100.0, persist_snapshot=False)
    for source in ("pending_signal:61", "pending_signal:62"):
        fill = broker.submit_order(Order(
            symbol="AAA", side=OrderSide.BUY, quantity=100,
            order_type=OrderType.MARKET, reference_price=100.0,
            source_intent_id=source,
        ))
        assert fill is not None
    assert broker.get_position("AAA").quantity == 200
    with sqlite3.connect(paper_db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 2


def test_two_brokers_racing_same_intent_create_one_economic_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_db = tmp_path / "paper.db"
    brokers = [
        PaperBroker(database_path=paper_db, initial_cash=1_000_000, slippage_bps=0),
        PaperBroker(database_path=paper_db, initial_cash=1_000_000, slippage_bps=0),
    ]
    for broker in brokers:
        broker.update_market_price("AAA", 100.0, persist_snapshot=False)
    barrier = Barrier(2)
    for broker in brokers:
        original = broker._store.save_intent_execution

        def synchronized(*args, _original=original, **kwargs):
            barrier.wait(timeout=5)
            return _original(*args, **kwargs)

        monkeypatch.setattr(broker._store, "save_intent_execution", synchronized)

    def attempt(broker: PaperBroker):
        return broker.submit_order(Order(
            symbol="AAA", side=OrderSide.BUY, quantity=100,
            order_type=OrderType.MARKET, reference_price=100.0,
            source_intent_id="pending_signal:race",
        ))

    with ThreadPoolExecutor(max_workers=2) as pool:
        fills = tuple(pool.map(attempt, brokers))
    assert all(fill is not None for fill in fills)
    assert fills[0].order_id == fills[1].order_id
    with sqlite3.connect(paper_db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1
        assert connection.execute("SELECT quantity FROM paper_positions WHERE symbol='AAA'").fetchone()[0] == 100
    assert sum(broker.consume_duplicate_execution("pending_signal:race") for broker in brokers) == 1


def test_additive_migration_preserves_legacy_order_rows_and_repeats_safely(
    tmp_path: Path,
) -> None:
    paper_db = tmp_path / "legacy.db"
    with sqlite3.connect(paper_db) as connection:
        connection.execute(
            """CREATE TABLE paper_orders (
                client_order_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, side TEXT NOT NULL,
                quantity INTEGER NOT NULL, order_type TEXT NOT NULL, limit_price REAL,
                reference_price REAL, status TEXT NOT NULL, filled_quantity INTEGER NOT NULL,
                average_fill_price REAL, rejection_reason TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL)"""
        )
        connection.execute(
            """CREATE TABLE paper_position_lifecycle (
                symbol TEXT PRIMARY KEY, entry_date TEXT NOT NULL, entry_price REAL NOT NULL,
                initial_quantity INTEGER NOT NULL, stop_price REAL NOT NULL,
                take_profit_price REAL, highest_price REAL, trailing_stop_price REAL,
                trailing_atr_multiplier REAL, maximum_holding_days INTEGER, updated_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO paper_orders VALUES ('legacy-1','AAA','BUY',100,'MARKET',NULL,10,'FILLED',100,10,NULL,'2026-01-01','2026-01-01')"
        )
        connection.execute(
            "INSERT INTO paper_position_lifecycle VALUES "
            "('AAA','2026-01-01',10,100,9,NULL,10,NULL,NULL,NULL,'2026-01-01')"
        )

    store = PaperTradingStore(paper_db)
    store.initialize()
    with sqlite3.connect(paper_db) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_orders)")}
        lifecycle_columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_position_lifecycle)")}
        client_id, source = connection.execute(
            "SELECT client_order_id, source_intent_id FROM paper_orders"
        ).fetchone()
        context = connection.execute(
            "SELECT entry_order_id,strategy_version,policy_fingerprint FROM paper_position_lifecycle"
        ).fetchone()
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(paper_orders)")}
    assert "source_intent_id" in columns
    assert "execution_context" in columns
    assert {"entry_order_id", "strategy_version", "policy_fingerprint"} <= lifecycle_columns
    assert (client_id, source) == ("legacy-1", None)
    assert context == (None, None, None)
    assert "uq_paper_orders_source_intent" in indexes


def test_legacy_executed_intent_without_frozen_context_fails_closed(tmp_path: Path) -> None:
    executor = _executor(tmp_path / "paper.db")
    from execution.models import Fill, OrderSide

    fill = Fill(
        order_id="legacy-order", symbol="AAA", side=OrderSide.BUY,
        quantity=100, price=10.0, gross_value=1_000.0,
        commission=1.0, slippage_cost=0.0, net_cash_flow=-1_001.0,
    )
    with pytest.raises(RuntimeError, match="no frozen lifecycle provenance"):
        executor._restore_missing_intent_lifecycle(
            signal={"symbol": "AAA"}, fill=fill, reference_price=10.0,
            execution_date="2026-01-02", execution_context=None,
        )
    assert executor.broker.get_position_lifecycle("AAA") is None


def test_no_crash_economics_match_and_unprotected_orders_keep_legacy_null_source(
    tmp_path: Path,
) -> None:
    legacy = PaperBroker(database_path=tmp_path / "legacy.db", initial_cash=1_000_000, slippage_bps=5)
    protected = PaperBroker(database_path=tmp_path / "protected.db", initial_cash=1_000_000, slippage_bps=5)
    for broker in (legacy, protected):
        broker.update_market_price("AAA", 100.0, persist_snapshot=False)
    old_fill = legacy.submit_order(Order(
        symbol="AAA", side=OrderSide.BUY, quantity=100, reference_price=100.0,
    ))
    new_fill = protected.submit_order(Order(
        symbol="AAA", side=OrderSide.BUY, quantity=100, reference_price=100.0,
        source_intent_id="pending_signal:71",
    ))

    assert old_fill is not None and new_fill is not None
    assert (old_fill.price, old_fill.gross_value, old_fill.commission, old_fill.net_cash_flow) == (
        new_fill.price, new_fill.gross_value, new_fill.commission, new_fill.net_cash_flow
    )
    assert legacy.get_cash() == protected.get_cash()
    assert legacy.get_position("AAA").quantity == protected.get_position("AAA").quantity == 100
    with sqlite3.connect(tmp_path / "legacy.db") as connection:
        assert connection.execute("SELECT source_intent_id FROM paper_orders").fetchone()[0] is None
