"""Offline P1 software fixtures: real gates/stores, no research or live actions."""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3

from config.paper_store import resolve_active_paper_store
from core.evidence_market_binding import MarketBindingSession
from core.market_admission import AdmissionContext, admit_price_batch
from core.market_observation_log import open_observation_log
from execution.models import Order, OrderSide, OrderStatus
from execution.persistence import PaperTradingStore
from quantlab.evidence import ProspectivePortfolioEvidenceLedger
from quantlab.forward.contracts import ForwardMaturity, ForwardOutcome, MaturityStatus, OutcomeAvailability
from quantlab.forward.ledger import ForwardValidationLedger
from tests.test_forward_evidence_view import _activation, _formation, _paper_record

NOW = datetime(2026, 10, 8, 10, tzinfo=timezone.utc)
SESSIONS = ("2026-10-06", "2026-10-07", "2026-10-08")


def _market(path):
    import pandas as pd
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE prices(id INTEGER PRIMARY KEY,symbol TEXT,time TEXT,open REAL,high REAL,low REAL,close REAL,volume INTEGER)")
        connection.executemany("INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)",
                               [(symbol, SESSIONS[0], 10, 12, 9, 11, 1000) for symbol in ("AAA", "VNINDEX")])
    open_observation_log(path).ensure_initial_baseline(path, now=NOW)
    context = AdmissionContext(source="TEST_SYNTHETIC", endpoint="offline fixture", source_mode="UPDATE",
                               package_name="fixture", package_version="1", request_start=SESSIONS[0], request_end=SESSIONS[-1])
    for symbol in ("AAA", "VNINDEX"):
        frame = pd.DataFrame([dict(symbol=symbol, time=day, open=10, high=12, low=9, close=11, volume=1000) for day in SESSIONS])
        outcome = admit_price_batch(frame, symbol=symbol, context=context, market_db_path=path, now=NOW)
        assert outcome.applied, outcome


def _block(path):
    import pandas as pd
    context = AdmissionContext(source="TEST_SYNTHETIC", endpoint="offline revision", source_mode="UPDATE",
                               package_name="fixture", package_version="1", request_start=SESSIONS[-1], request_end=SESSIONS[-1])
    frame = pd.DataFrame([dict(symbol="AAA", time=SESSIONS[-1], open=8, high=10, low=7, close=9, volume=1000)])
    assert admit_price_batch(frame, symbol="AAA", context=context, market_db_path=path, now=NOW).blocked


@dataclass
class P1Fixture:
    root: Path
    market: Path
    paper: Path
    environ: dict[str, str]
    writer: sqlite3.Connection
    binding: dict

    def close(self):
        self.writer.close()


def prepare_p1_fixture(root: Path, *, scenario="healthy", pending=True, orders=True) -> P1Fixture:
    repo = Path(__file__).resolve().parents[2]
    assert root.resolve() != repo and not root.resolve().is_relative_to(repo / "data")
    assert not root.exists(), "Do not overwrite an existing fixture"
    _market(root / "data/market.db")
    market = root / "configured/active-market.db"
    _market(market)
    environ = {"MARKET_DATABASE_PATH": str(market), "PAPER_STRATEGY_VERSION": "Q70_FROZEN"}
    paper = resolve_active_paper_store(environ, root=root).database_path
    store = PaperTradingStore(paper)
    writer = sqlite3.connect(paper)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("INSERT OR REPLACE INTO paper_metadata VALUES('account_epoch_id', ?)", (json.dumps("epoch-a"),))
    writer.commit()
    if orders:
        for status in (OrderStatus.PENDING, OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED):
            store.save_order(Order(symbol="AAA", side=OrderSide.BUY, quantity=10, status=status))
    if pending:
        writer.execute("INSERT INTO paper_pending_signals(signal_date,symbol,payload,status,created_at) VALUES(?,?,?,?,?)",
                       (SESSIONS[-1], "AAA", "{}", "PENDING", NOW.isoformat()))
        writer.commit()

    session = MarketBindingSession(market)
    ledger = ForwardValidationLedger(root / "data/forward_validation.db")
    ledger.activate(_activation())
    formation = _formation(SESSIONS[1])
    ledger.record_formation(formation, (5, 10), market_binding=session.bind("FORMATION", formation.formation_identity,
                           (("FORMATION_CLOSE", "AAA", SESSIONS[1]), ("BENCHMARK_FORMATION_CLOSE", "VNINDEX", SESSIONS[1]))))
    ledger.record_maturities((ForwardMaturity("protocol-a", formation.formation_identity, SESSIONS[1], 5,
                             SESSIONS[-1], MaturityStatus.MATURED, "TEST_SYNTHETIC", NOW.isoformat(), "fixture-maturity"),))
    outcome = ForwardOutcome("protocol-a", formation.formation_identity, 5, SESSIONS[-1], "AAA", 1.0,
                             OutcomeAvailability.AVAILABLE, 2.0, 0.0, 2.0, NOW.isoformat(), "fixture-outcome")
    binding = session.bind("OUTCOME", outcome.outcome_identity,
                           (("STOCK_TARGET_CLOSE", "AAA", SESSIONS[-1], SESSIONS[1]),
                            ("BENCHMARK_TARGET_CLOSE", "VNINDEX", SESSIONS[-1], SESSIONS[1])))
    ledger.record_outcomes((outcome,), market_bindings={outcome.outcome_identity: binding})
    evidence = ProspectivePortfolioEvidenceLedger(root / "data/prospective_portfolio_evidence.db")
    record = replace(_paper_record(paper_path=paper, session=SESSIONS[-1], equity=100.0), market_database_path=str(market))
    evidence.append(record, market_binding=session.bind("PAPER_OBSERVATION", evidence.evidence_key(record),
                    (("PAPER_POSITION_CLOSE", "AAA", SESSIONS[-1]), ("BENCHMARK_CLOSE", "VNINDEX", SESSIONS[-1]))))
    if scenario == "blocked":
        _block(market)
    elif scenario == "schema":
        writer.execute("DROP TABLE paper_pending_signals")
        writer.commit()
    elif scenario == "missing":
        environ["MARKET_DATABASE_PATH"] = str(root / "missing/market.db")
    elif scenario != "healthy":
        raise ValueError(scenario)
    (root / ".quant-ui-test.json").write_text(json.dumps({"classification": "TEST_SYNTHETIC"}), encoding="utf-8")
    return P1Fixture(root, market, paper, environ, writer, binding)
