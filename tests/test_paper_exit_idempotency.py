from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import json
import sqlite3
from pathlib import Path
from threading import Barrier

import pytest

from execution.exit_engine import ExitEngine
from execution.lifecycle_manager import PaperLifecycleManager
from execution.lifecycle_models import PositionLifecycleState
from execution.models import Order, OrderSide, OrderStatus, OrderType
from execution.order_manager import OrderManager
from execution.paper_broker import PaperBroker
from execution.persistence import PaperTradingStore
from execution.risk_guard import RiskGuard, RiskLimits


ENTRY_DATE = date(2026, 8, 27)
EXIT_DATE = date(2026, 8, 28)
RETRY_DATE = date(2026, 8, 31)
INITIAL_CASH = 10_000_000.0


def _market_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE prices(symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER)"
        )
        connection.executemany(
            "INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("VNINDEX", ENTRY_DATE.isoformat(), 1300, 1310, 1290, 1305, 1_000_000),
                ("VNINDEX", EXIT_DATE.isoformat(), 1305, 1315, 1295, 1310, 1_000_000),
                ("VNINDEX", RETRY_DATE.isoformat(), 1310, 1320, 1300, 1315, 1_000_000),
                ("AAA", ENTRY_DATE.isoformat(), 10, 10.2, 9.8, 10, 1_000_000),
                # With the lifecycle price scale of 1,000 this touches the 9,000 stop.
                ("AAA", EXIT_DATE.isoformat(), 10, 10.1, 8.9, 9.5, 1_000_000),
                ("AAA", RETRY_DATE.isoformat(), 9.5, 9.8, 9.2, 9.6, 1_000_000),
            ],
        )


def _runtime(paper_db: Path, market_db: Path):
    broker = PaperBroker(
        database_path=paper_db,
        initial_cash=INITIAL_CASH,
        commission_rate=0.0015,
        slippage_bps=0,
    )
    manager = OrderManager(
        broker=broker,
        risk_guard=RiskGuard(RiskLimits(
            maximum_position_pct=50,
            maximum_gross_exposure_pct=80,
            minimum_cash_buffer_pct=5,
        )),
    )
    lifecycle = PaperLifecycleManager(
        broker=broker,
        order_manager=manager,
        exit_engine=ExitEngine(),
        market_database_path=market_db,
        price_scale=1000,
    )
    return broker, manager, lifecycle


def _seed_open_position(paper_db: Path, market_db: Path):
    broker, manager, lifecycle = _runtime(paper_db, market_db)
    fill = manager.buy_market(symbol="AAA", quantity=100, price=10_000)
    assert fill is not None
    broker.save_position_lifecycle(PositionLifecycleState(
        symbol="AAA",
        entry_date=ENTRY_DATE,
        entry_price=fill.price,
        initial_quantity=fill.quantity,
        stop_price=9_000,
        highest_price=fill.price,
        updated_at=datetime(2026, 8, 27, 12, tzinfo=timezone.utc),
        entry_order_id=fill.order_id,
        strategy_version="paper-v2-test",
        policy_fingerprint="frozen-policy-test",
    ))
    return broker, manager, lifecycle


def _db_state(path: Path) -> dict[str, object]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        return {
            "cash": float(connection.execute(
                "SELECT value FROM paper_metadata WHERE key='cash'"
            ).fetchone()[0].strip('"')),
            "position": (lambda row: tuple(row) if row is not None else None)(connection.execute(
                "SELECT quantity FROM paper_positions WHERE symbol='AAA'"
            ).fetchone()),
            "orders": [tuple(row) for row in connection.execute(
                "SELECT client_order_id,side,status,source_intent_id FROM paper_orders ORDER BY created_at,client_order_id"
            )],
            "fills": [tuple(row) for row in connection.execute(
                "SELECT order_id,side,quantity,net_cash_flow FROM paper_fills ORDER BY id"
            )],
            "closed": int(connection.execute(
                "SELECT COUNT(*) FROM paper_closed_trades"
            ).fetchone()[0]),
            "lifecycle": int(connection.execute(
                "SELECT COUNT(*) FROM paper_position_lifecycle WHERE symbol='AAA'"
            ).fetchone()[0]),
        }


def test_exit_commits_once_and_identical_retry_is_already_executed(tmp_path: Path) -> None:
    paper_db, market_db = tmp_path / "paper.db", tmp_path / "market.db"
    _market_db(market_db)
    broker, _manager, lifecycle = _seed_open_position(paper_db, market_db)
    initial_cash = broker.get_cash()

    result = lifecycle.run(valuation_date=EXIT_DATE)

    assert len(result.exited) == 1
    assert result.exited[0].reason == "STOP_LOSS"
    assert broker.get_position("AAA") is None
    assert broker.get_position_lifecycle("AAA") is None
    state = _db_state(paper_db)
    assert len(state["fills"]) == 2
    assert sum(row[1] == "SELL" for row in state["fills"]) == 1
    assert state["closed"] == 1 and state["lifecycle"] == 0
    assert float(state["cash"]) > initial_cash
    assert float(state["cash"]) == pytest.approx(
        INITIAL_CASH + sum(float(row[3]) for row in state["fills"]), abs=0.01
    )
    with sqlite3.connect(paper_db) as connection:
        trade = connection.execute(
            "SELECT realized_pnl,entry_price,quantity FROM paper_closed_trades"
        ).fetchone()
        sell_net = float(connection.execute(
            "SELECT net_cash_flow FROM paper_fills WHERE side='SELL'"
        ).fetchone()[0])
        exit_context = json.loads(connection.execute(
            "SELECT execution_context FROM paper_orders WHERE side='SELL'"
        ).fetchone()[0])["exit"]
    # Closed economics preserve the pre-exit average-cost basis; the position
    # is removed atomically, so the close record is the authoritative basis.
    assert float(trade[0]) == pytest.approx(sell_net - float(trade[1]) * int(trade[2]))
    sell_order = next(row for row in state["orders"] if row[1] == "SELL")
    source = sell_order[3]
    assert source.startswith("paper_exit:")
    assert exit_context["entry_order_id"]
    assert exit_context["strategy_version"] == "paper-v2-test"
    assert exit_context["policy_fingerprint"] == "frozen-policy-test"
    assert broker.consume_execution_disposition(source) == "EXECUTED"

    restarted = PaperBroker(database_path=paper_db, initial_cash=INITIAL_CASH, slippage_bps=0)
    duplicate = restarted.submit_order(Order(
        symbol="AAA", side=OrderSide.SELL, quantity=100,
        order_type=OrderType.MARKET, reference_price=9_000,
        source_intent_id=source,
    ))
    assert duplicate is not None and duplicate.order_id == sell_order[0]
    assert restarted.consume_execution_disposition(source) == "ALREADY_EXECUTED"
    assert restarted.get_position("AAA") is None
    after = _db_state(paper_db)
    assert after["cash"] == state["cash"]
    assert after["fills"] == state["fills"]
    assert after["closed"] == 1


def test_failure_before_exit_submission_retries_to_one_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paper_db, market_db = tmp_path / "paper.db", tmp_path / "market.db"
    _market_db(market_db)
    broker, manager, lifecycle = _seed_open_position(paper_db, market_db)
    original = manager.sell_market

    def fail_before_submit(**_kwargs):
        raise RuntimeError("injected before exit submit")

    monkeypatch.setattr(manager, "sell_market", fail_before_submit)
    with pytest.raises(RuntimeError, match="before exit submit"):
        lifecycle.run(valuation_date=EXIT_DATE)
    assert broker.get_position("AAA").quantity == 100
    assert len(broker.get_fills()) == 1
    assert broker.get_position_lifecycle("AAA") is not None

    monkeypatch.setattr(manager, "sell_market", original)
    retry = lifecycle.run(valuation_date=EXIT_DATE)
    assert len(retry.exited) == 1
    assert sum(fill.side is OrderSide.SELL for fill in broker.get_fills()) == 1
    assert len(broker.get_closed_trades()) == 1


def test_failure_inside_exit_transaction_rolls_back_then_resumes_same_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_db, market_db = tmp_path / "paper.db", tmp_path / "market.db"
    _market_db(market_db)
    broker, _manager, lifecycle = _seed_open_position(paper_db, market_db)
    before = _db_state(paper_db)
    original = broker._store._save_closed_trade

    def fail_close_write(_connection, _trade):
        raise RuntimeError("injected during atomic close bundle")

    monkeypatch.setattr(broker._store, "_save_closed_trade", fail_close_write)
    with pytest.raises(RuntimeError, match="atomic close bundle"):
        lifecycle.run(valuation_date=EXIT_DATE)

    failed = _db_state(paper_db)
    assert failed["cash"] == before["cash"]
    assert failed["position"] == (100,)
    assert failed["fills"] == before["fills"]
    assert failed["closed"] == 0 and failed["lifecycle"] == 1
    sell_orders = [row for row in failed["orders"] if row[1] == "SELL"]
    assert len(sell_orders) == 1 and sell_orders[0][2] == "ACCEPTED"
    exit_order_id = sell_orders[0][0]

    monkeypatch.setattr(broker._store, "_save_closed_trade", original)
    restarted_broker, _manager, restarted_lifecycle = _runtime(paper_db, market_db)
    result = restarted_lifecycle.run(valuation_date=EXIT_DATE)
    assert len(result.exited) == 1
    state = _db_state(paper_db)
    sell_orders = [row for row in state["orders"] if row[1] == "SELL"]
    assert len(sell_orders) == 1
    assert sell_orders[0][0] == exit_order_id and sell_orders[0][2] == "FILLED"
    assert sum(row[1] == "SELL" for row in state["fills"]) == 1
    assert state["closed"] == 1 and state["lifecycle"] == 0
    assert restarted_broker.get_position("AAA") is None


def test_interruption_after_atomic_commit_needs_no_second_exit_or_metadata_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_db, market_db = tmp_path / "paper.db", tmp_path / "market.db"
    _market_db(market_db)
    broker, _manager, lifecycle = _seed_open_position(paper_db, market_db)
    original = broker._store.save_intent_execution

    def commit_then_interrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        raise RuntimeError("injected interruption after atomic commit")

    monkeypatch.setattr(broker._store, "save_intent_execution", commit_then_interrupt)
    with pytest.raises(RuntimeError, match="after atomic commit"):
        lifecycle.run(valuation_date=EXIT_DATE)

    committed = _db_state(paper_db)
    assert sum(row[1] == "SELL" for row in committed["fills"]) == 1
    assert committed["closed"] == 1 and committed["lifecycle"] == 0
    assert committed["position"] is None
    assert committed["cash"] == pytest.approx(
        INITIAL_CASH + sum(float(row[3]) for row in committed["fills"]), abs=0.01
    )

    restarted_broker, _manager, restarted_lifecycle = _runtime(paper_db, market_db)
    result = restarted_lifecycle.run(valuation_date=RETRY_DATE)
    assert result.exited == []
    assert restarted_broker.get_position("AAA") is None
    after = _db_state(paper_db)
    assert after["fills"] == committed["fills"]
    assert after["closed"] == 1 and after["lifecycle"] == 0


def test_accepted_unfilled_exit_reuses_same_durable_order(tmp_path: Path) -> None:
    paper_db, market_db = tmp_path / "paper.db", tmp_path / "market.db"
    _market_db(market_db)
    broker, _manager, lifecycle = _seed_open_position(paper_db, market_db)
    frozen = broker.get_position_lifecycle("AAA")
    assert frozen is not None
    source = lifecycle._exit_intent_id(
        lifecycle=frozen, valuation_date=EXIT_DATE, quantity=100, reason="STOP_LOSS"
    )
    pending = Order(
        symbol="AAA", side=OrderSide.SELL, quantity=100,
        order_type=OrderType.MARKET, reference_price=9_000,
        status=OrderStatus.ACCEPTED, source_intent_id=source,
        execution_context={"exit": {
            "entry_date": ENTRY_DATE.isoformat(), "entry_price": 10_015,
            "exit_date": EXIT_DATE.isoformat(), "holding_days": 1,
            "exit_reason": "STOP_LOSS",
        }},
    )
    broker.record_order(pending)

    result = lifecycle.run(valuation_date=RETRY_DATE)

    assert len(result.exited) == 1
    sell_orders = [order for order in broker.get_orders() if order.side is OrderSide.SELL]
    sell_fills = [fill for fill in broker.get_fills() if fill.side is OrderSide.SELL]
    assert len(sell_orders) == len(sell_fills) == 1
    assert sell_orders[0].client_order_id == pending.client_order_id
    assert sell_orders[0].status is OrderStatus.FILLED
    assert len(broker.get_closed_trades()) == 1
    assert broker.consume_execution_disposition(source) == "RESUMED"


def test_legacy_open_sell_without_recovery_context_fails_closed(tmp_path: Path) -> None:
    paper_db, market_db = tmp_path / "paper.db", tmp_path / "market.db"
    _market_db(market_db)
    broker, _manager, lifecycle = _seed_open_position(paper_db, market_db)
    legacy = Order(
        symbol="AAA", side=OrderSide.SELL, quantity=100,
        order_type=OrderType.MARKET, reference_price=9_000,
        status=OrderStatus.ACCEPTED,
    )
    broker.record_order(legacy)

    result = lifecycle.run(valuation_date=EXIT_DATE)

    assert result.rejected_exits == ["AAA"]
    assert broker.get_position("AAA").quantity == 100
    assert sum(fill.side is OrderSide.SELL for fill in broker.get_fills()) == 0
    assert broker.get_position_lifecycle("AAA") is not None


def test_two_concurrent_attempts_for_same_exit_create_one_economic_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_db, market_db = tmp_path / "paper.db", tmp_path / "market.db"
    _market_db(market_db)
    _seed_open_position(paper_db, market_db)
    barrier = Barrier(2)
    original = PaperTradingStore.save_exit_intent

    def synchronized(store, order):
        barrier.wait(timeout=10)
        return original(store, order)

    monkeypatch.setattr(PaperTradingStore, "save_exit_intent", synchronized)
    runtimes = [_runtime(paper_db, market_db) for _ in range(2)]

    def attempt(runtime):
        return runtime[2].run(valuation_date=EXIT_DATE)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(attempt, runtimes))

    assert all(not result.rejected_exits for result in results)
    with sqlite3.connect(paper_db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_orders WHERE side='SELL'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM paper_fills WHERE side='SELL'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM paper_closed_trades").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM paper_position_lifecycle WHERE symbol='AAA'").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM paper_positions WHERE symbol='AAA'").fetchone()[0] == 0


def test_late_racing_attempt_observes_filled_reservation_without_reopening_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_db, market_db = tmp_path / "paper.db", tmp_path / "market.db"
    _market_db(market_db)
    first_broker, first_manager, first_lifecycle = _seed_open_position(paper_db, market_db)
    frozen = first_broker.get_position_lifecycle("AAA")
    assert frozen is not None
    source = first_lifecycle._exit_intent_id(
        lifecycle=frozen, valuation_date=EXIT_DATE, quantity=100, reason="STOP_LOSS"
    )
    context = {"exit": {
        "entry_date": ENTRY_DATE.isoformat(), "entry_price": 10_015,
        "exit_date": EXIT_DATE.isoformat(), "holding_days": 1,
        "exit_reason": "STOP_LOSS",
    }}
    first_broker.record_order(Order(
        symbol="AAA", side=OrderSide.SELL, quantity=100,
        order_type=OrderType.MARKET, reference_price=9_000,
        status=OrderStatus.ACCEPTED, source_intent_id=source,
        execution_context=context,
    ))
    retry_broker, _retry_manager, retry_lifecycle = _runtime(paper_db, market_db)
    original_reservation = retry_broker._store.save_exit_intent
    executed = False

    def fill_between_lookup_and_reservation(order):
        nonlocal executed
        if not executed:
            executed = True
            fill = first_manager.sell_market(
                symbol="AAA", quantity=100, price=9_000,
                source_intent_id=source, execution_context=context,
            )
            assert fill is not None
        return original_reservation(order)

    monkeypatch.setattr(
        retry_broker._store, "save_exit_intent", fill_between_lookup_and_reservation
    )
    result = retry_lifecycle.run(valuation_date=EXIT_DATE)

    assert len(result.exited) == 1
    assert retry_broker.consume_execution_disposition(source) == "ALREADY_EXECUTED"
    state = _db_state(paper_db)
    assert sum(row[1] == "SELL" for row in state["orders"]) == 1
    assert sum(row[1] == "SELL" for row in state["fills"]) == 1
    assert state["closed"] == 1 and state["lifecycle"] == 0


def test_distinct_exit_transitions_have_distinct_durable_intents(tmp_path: Path) -> None:
    paper_db, market_db = tmp_path / "paper.db", tmp_path / "market.db"
    _market_db(market_db)
    broker, _manager, lifecycle = _seed_open_position(paper_db, market_db)
    frozen = broker.get_position_lifecycle("AAA")
    assert frozen is not None
    first = lifecycle._exit_intent_id(
        lifecycle=frozen, valuation_date=EXIT_DATE, quantity=100, reason="STOP_LOSS"
    )
    second = lifecycle._exit_intent_id(
        lifecycle=frozen, valuation_date=date(2026, 8, 31), quantity=100, reason="EXIT_SIGNAL"
    )
    assert first != second
