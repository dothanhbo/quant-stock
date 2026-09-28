from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from manager.pages import dashboard as dashboard_page
from manager.pages import state as state_page
from manager.view_models import build_dashboard_model, build_state_model
from quantctl.cli import build_parser, main
from quantctl.registry import CommandSafety
from quantctl.state import inspect_forward_system, inspect_paper_system


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CANONICAL_HASHES = {
    "market.db": "38c4c423590824d66452cfc169c8083fd13ec9e272ae9db7a58d36cbe91c4c6b",
    "forward_validation.db": "4200785d80c8d25377dad265d33e586717f1fc898e7c6ae55d470e3a497140a5",
    "paper_trading.db": "b058888c6339afce7974e7e7730b542502646907a1599d425a89146b7c779d32",
    "paper_trading_v2.db": "d0261e033b903b8ce1209e588e3b99bb3eb17ddfa7e0a6bfb4e2773f6242ea05",
    "paper_trading_v3.db": "cd6fe45bb3871c1517053a895ecbd58367773bbe29d30ca1fef30d571a03a19e",
}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _paper_database(path: Path, positions: tuple[tuple[object, ...], ...] = ()) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE paper_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE paper_orders(client_order_id TEXT,status TEXT,created_at TEXT,updated_at TEXT);
            CREATE TABLE paper_fills(id INTEGER PRIMARY KEY,symbol TEXT,created_at TEXT);
            CREATE TABLE paper_positions(symbol TEXT PRIMARY KEY,quantity INTEGER,average_price REAL,market_price REAL,realized_pnl REAL);
            CREATE TABLE paper_portfolio_snapshots(id INTEGER PRIMARY KEY,created_at TEXT);
            CREATE TABLE paper_position_lifecycle(symbol TEXT PRIMARY KEY,entry_date TEXT,updated_at TEXT,strategy_version TEXT);
            CREATE TABLE paper_closed_trades(id INTEGER PRIMARY KEY,created_at TEXT);
            CREATE TABLE paper_pending_signals(id INTEGER PRIMARY KEY,status TEXT,created_at TEXT,processed_date TEXT);
            """
        )
        for symbol, quantity, price, entry_date, version in positions:
            connection.execute(
                "INSERT INTO paper_positions VALUES (?,?,?,?,?)",
                (symbol, quantity, price, price, 0.0),
            )
            connection.execute(
                "INSERT INTO paper_position_lifecycle VALUES (?,?,?,?)",
                (symbol, entry_date, "2026-09-27T10:00:00Z", version),
            )
        connection.execute(
            "INSERT INTO paper_portfolio_snapshots VALUES (?,?)",
            (1, "2026-09-27T11:00:00Z"),
        )
        connection.executemany(
            "INSERT INTO paper_pending_signals VALUES (?,?,?,?)",
            (
                (1, "PENDING", "2026-09-27T09:00:00Z", None),
                (2, "FILLED", "2026-09-26T09:00:00Z", "2026-09-27"),
            ),
        )


def _forward_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE forward_protocols(
                protocol_id TEXT PRIMARY KEY,protocol_version TEXT,activated_at_utc TEXT,
                operational_start_after_session TEXT,selection_policy TEXT,weighting_policy TEXT,
                budget INTEGER,tracked_horizons_json TEXT,benchmark TEXT
            );
            CREATE TABLE forward_formations(formation_identity TEXT PRIMARY KEY,recorded_at_utc TEXT);
            CREATE TABLE forward_positions(formation_identity TEXT,rank INTEGER,symbol TEXT);
            CREATE TABLE forward_maturities(
                event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,formation_identity TEXT,
                horizon_sessions INTEGER,status TEXT,recorded_at_utc TEXT
            );
            CREATE TABLE forward_outcomes(outcome_identity TEXT PRIMARY KEY,recorded_at_utc TEXT);
            CREATE TABLE forward_audit_events(event_identity TEXT PRIMARY KEY,recorded_at_utc TEXT);
            """
        )
        connection.execute(
            "INSERT INTO forward_protocols VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "QV-FWD-V1-test",
                "V1",
                "2026-09-25T00:00:00Z",
                "2026-09-25",
                "ADX_ONLY",
                "EQUAL_WEIGHT",
                5,
                "[5,10]",
                "VNINDEX",
            ),
        )
        connection.execute(
            "INSERT INTO forward_formations VALUES (?,?)",
            ("formation-1", "2026-09-26T00:00:00Z"),
        )
        connection.execute("INSERT INTO forward_positions VALUES (?,?,?)", ("formation-1", 1, "AAA"))
        connection.executemany(
            "INSERT INTO forward_maturities(formation_identity,horizon_sessions,status,recorded_at_utc) "
            "VALUES (?,?,?,?)",
            (
                ("formation-1", 5, "PENDING", "2026-09-26T00:00:00Z"),
                ("formation-1", 5, "MATURED", "2026-09-27T00:00:00Z"),
                ("formation-1", 10, "PENDING", "2026-09-26T00:00:00Z"),
            ),
        )


def _state_root(tmp_path: Path) -> Path:
    (tmp_path / ".env").write_text("PAPER_STRATEGY_VERSION=Q70_FROZEN\n", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    _paper_database(
        data / "paper_trading.db",
        (("aaa", 10, 101.5, "2026-09-20", "Q70_FROZEN"), ("ZERO", 0, 5.0, "2026-09-20", None)),
    )
    _paper_database(data / "paper_trading_v2.db")
    _paper_database(
        data / "paper_trading_v3.db",
        (("BBB", 25, 88.0, "2026-09-21", "V3_BREADTH_40_60"),),
    )
    _forward_database(data / "forward_validation.db")
    return tmp_path


def test_state_status_commands_are_read_only_and_route_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser = build_parser()
    assert parser.parse_args(["paper", "status"]).safety is CommandSafety.READ_ONLY
    assert parser.parse_args(["forward", "status"]).safety is CommandSafety.READ_ONLY
    calls: list[str] = []
    monkeypatch.setattr("quantctl.commands.state.run_paper_status", lambda: calls.append("paper") or 0)
    monkeypatch.setattr("quantctl.commands.state.run_forward_status", lambda: calls.append("forward") or 0)

    assert main(["paper", "status"]) == 0
    assert main(["forward", "status"]) == 0
    assert calls == ["paper", "forward"]


def test_missing_databases_are_graceful_and_not_created(tmp_path: Path) -> None:
    paper = inspect_paper_system(root=tmp_path)
    forward = inspect_forward_system(root=tmp_path)

    assert all(store.schema_status == "MISSING" for store in paper.stores)
    assert forward.schema_status == "MISSING"
    assert not (tmp_path / "data").exists()


def test_paper_stores_are_distinct_and_open_semantics_match_portfolio(tmp_path: Path) -> None:
    snapshot = inspect_paper_system(root=_state_root(tmp_path))
    stores = {store.store_id: store for store in snapshot.stores}
    generic = stores["generic-paper"]
    q70 = stores["q70-frozen"]
    v3 = stores["v3-breadth-40-60"]

    assert snapshot.active_store is q70
    assert q70.role.value == "ACTIVE"
    assert generic.role.value == "INACTIVE_WITH_STATE"
    assert (generic.open_position_count, q70.open_position_count, v3.open_position_count) == (1, 0, 1)
    assert tuple(item.symbol for item in generic.positions) == ("AAA",)
    assert generic.positions[0].entry_date == "2026-09-20"
    assert generic.positions[0].status == "OPEN"
    assert generic.strategy_versions == ("Q70_FROZEN",)
    assert v3.strategy_versions == ("V3_BREADTH_40_60",)
    assert generic.pending_signal_count == 1
    assert generic.latest_activity == "2026-09-27T11:00:00Z"


def test_forward_active_and_latest_maturity_semantics_match_canonical_code(tmp_path: Path) -> None:
    snapshot = inspect_forward_system(root=_state_root(tmp_path))

    assert snapshot.active_protocol_count == 1
    assert snapshot.protocol_versions == ("V1",)
    assert snapshot.formation_count == 1
    assert snapshot.position_count == 1
    assert snapshot.pending_maturity_count == 1
    assert snapshot.matured_maturity_count == 1
    assert snapshot.latest_activity == "2026-09-27T00:00:00Z"
    assert snapshot.protocols[0].tracked_horizons == (5, 10)


def test_fixture_databases_are_inspected_without_mutation(tmp_path: Path) -> None:
    root = _state_root(tmp_path)
    paths = tuple((root / "data").glob("*.db"))
    before = {path.name: _digest(path) for path in paths}

    inspect_paper_system(root=root)
    inspect_forward_system(root=root)

    assert {path.name: _digest(path) for path in paths} == before
    assert not tuple((root / "data").glob("*.db-wal"))
    assert not tuple((root / "data").glob("*.db-shm"))


def test_schema_mismatch_is_reported_without_crashing(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    for filename in ("paper_trading.db", "paper_trading_v2.db", "paper_trading_v3.db", "forward_validation.db"):
        with sqlite3.connect(data / filename) as connection:
            connection.execute("CREATE TABLE marker(value INTEGER)")

    assert all(store.schema_status == "SCHEMA_MISMATCH" for store in inspect_paper_system(root=tmp_path).stores)
    assert inspect_forward_system(root=tmp_path).schema_status == "SCHEMA_MISMATCH"


def test_state_and_dashboard_models_share_state_snapshots(tmp_path: Path) -> None:
    root = _state_root(tmp_path)
    (root / "research").mkdir()
    state = build_state_model(root=root)
    dashboard = build_dashboard_model(root=root, environ={})

    assert state.paper.active_store is not None
    assert state.paper.active_store.store_id == "q70-frozen"
    assert dashboard.paper.active_store is not None
    assert dashboard.paper.active_store.store_id == "q70-frozen"
    assert state.forward.active_protocol_count == dashboard.forward.active_protocol_count == 1


class _Tab:
    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None


class _FakeStreamlit:
    def __init__(self) -> None:
        self.metrics: list[tuple[str, object]] = []

    def __getattr__(self, name: str):
        if name in {
            "header",
            "info",
            "subheader",
            "caption",
            "warning",
            "error",
            "dataframe",
            "markdown",
        }:
            return lambda *args, **kwargs: None
        raise AttributeError(name)

    def columns(self, count: int):
        return [self for _ in range(count)]

    def metric(self, label: str, value: object) -> None:
        self.metrics.append((label, value))

    def tabs(self, labels: tuple[str, ...]):
        return [_Tab() for _ in labels]

    def expander(self, *args: object, **kwargs: object):
        return _Tab()


def test_state_page_renders_without_actions_or_sqlite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model = build_state_model(root=_state_root(tmp_path))
    fake = _FakeStreamlit()
    monkeypatch.setitem(sys.modules, "streamlit", fake)

    state_page.render(model)

    source = (PROJECT_ROOT / "manager" / "pages" / "state.py").read_text(encoding="utf-8")
    assert "sqlite3" not in source
    assert "st.button" not in source
    for forbidden in (
        "run_paper_lifecycle",
        "run_forward_validation",
        "execute_operation",
        "st.button",
        "reset_state",
    ):
        assert forbidden not in source.lower()


def test_dashboard_renders_compact_state_counts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = _state_root(tmp_path)
    (root / "research").mkdir()
    model = build_dashboard_model(root=root, environ={})
    fake = _FakeStreamlit()
    monkeypatch.setitem(sys.modules, "streamlit", fake)

    dashboard_page.render(model)

    assert ("Paper active store", "Frozen Q70 Paper Store") in fake.metrics
    assert ("Deployed paper policy", "Q70_FROZEN") in fake.metrics
    assert ("Paper state", "0 open · 1 pending") in fake.metrics
    assert ("Forward", "1 active") in fake.metrics


def test_import_boundary_has_no_streamlit_or_runtime_action_imports() -> None:
    code = (
        "import sys; import quantctl.state, quantctl.commands.state, manager.pages.state; "
        "blocked={'streamlit','execution.persistence','quantlab.forward.ledger'}; "
        "print(','.join(sorted(blocked.intersection(sys.modules))))"
    )
    completed = subprocess.run(
        (sys.executable, "-B", "-c", code),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == ""


def test_canonical_database_hashes_remain_unchanged() -> None:
    data = PROJECT_ROOT / "data"
    before = {name: _digest(data / name) for name in CANONICAL_HASHES}
    assert before == CANONICAL_HASHES

    inspect_paper_system(root=PROJECT_ROOT)
    inspect_forward_system(root=PROJECT_ROOT)

    assert {name: _digest(data / name) for name in CANONICAL_HASHES} == before
