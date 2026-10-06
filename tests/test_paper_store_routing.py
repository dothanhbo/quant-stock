from __future__ import annotations

from pathlib import Path
import os
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from config.paper_store import (
    Q70_STRATEGY_IDENTITY,
    V3_STRATEGY_IDENTITY,
    resolve_active_paper_store,
)
from core.paths import PROJECT_ROOT
from execution.signal_executor import PaperExecutionConfig
from quantctl.commands.state import render_paper_status
from quantctl.state import PaperStoreRole, inspect_paper_system
from scripts import report_paper_performance, run_paper_v2_lifecycle, run_paper_v3_lifecycle
from strategy import scanner



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
                (symbol, entry_date, "2026-01-02T00:00:00Z", version),
            )


def _paper_root(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    _paper_database(
        data / "paper_trading.db",
        (("AAA", 10, 10.0, "2026-01-01", "OLD"),),
    )
    _paper_database(data / "paper_trading_v2.db")
    _paper_database(data / "paper_trading_v3.db")
    return tmp_path


def test_default_and_v3_strategy_resolve_canonical_store_not_generic_path(tmp_path: Path) -> None:
    generic = tmp_path / "generic.db"
    default = resolve_active_paper_store({"PAPER_DATABASE_PATH": str(generic)})
    v3 = resolve_active_paper_store(
        {
            "PAPER_STRATEGY_VERSION": V3_STRATEGY_IDENTITY,
            "PAPER_DATABASE_PATH": str(generic),
        }
    )

    assert default.strategy_identity == Q70_STRATEGY_IDENTITY
    # Defaults are repository-anchored (previously the bare relative path,
    # which resolved against the process CWD). Same file when run from root.
    assert default.database_path == (PROJECT_ROOT / "data/paper_trading_v2.db").resolve()
    assert v3.strategy_identity == V3_STRATEGY_IDENTITY
    assert v3.database_path == (PROJECT_ROOT / "data/paper_trading_v3.db").resolve()


def test_default_paper_store_is_anchored_to_repository_root_not_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    resolved = resolve_active_paper_store({})

    assert resolved.database_path == (PROJECT_ROOT / "data/paper_trading_v2.db").resolve()
    assert not (tmp_path / "data").exists()


def test_distinct_cwd_relative_default_store_fails_closed_instead_of_switching(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from config.paper_store import AmbiguousPaperStoreError

    (tmp_path / "data").mkdir()
    stray = tmp_path / "data" / "paper_trading_v2.db"
    stray.write_bytes(b"")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(AmbiguousPaperStoreError, match="PAPER_V2_DATABASE_PATH"):
        resolve_active_paper_store({})

    # An explicit override is never ambiguous.
    assert resolve_active_paper_store(
        {"PAPER_V2_DATABASE_PATH": str(stray)}
    ).database_path == stray.resolve()


def test_strategy_specific_path_overrides_are_respected(tmp_path: Path) -> None:
    v2 = tmp_path / "custom-v2.db"
    v3 = tmp_path / "custom-v3.db"

    assert resolve_active_paper_store({"PAPER_V2_DATABASE_PATH": str(v2)}).database_path == v2
    assert resolve_active_paper_store(
        {
            "PAPER_STRATEGY_VERSION": V3_STRATEGY_IDENTITY,
            "PAPER_V3_DATABASE_PATH": str(v3),
        }
    ).database_path == v3


def test_relative_paper_path_is_anchored_to_repository_root_not_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    resolved = resolve_active_paper_store(
        {"PAPER_V2_DATABASE_PATH": "data/custom-v2.db"},
    )

    assert resolved.database_path == (PROJECT_ROOT / "data/custom-v2.db").resolve()


def test_absolute_paper_path_remains_absolute_and_store_isolation_is_preserved(
    tmp_path: Path,
) -> None:
    v2 = tmp_path / "v2.db"
    v3 = tmp_path / "v3.db"

    resolved_v2 = resolve_active_paper_store(
        {"PAPER_V2_DATABASE_PATH": str(v2)},
    )
    resolved_v3 = resolve_active_paper_store(
        {
            "PAPER_STRATEGY_VERSION": V3_STRATEGY_IDENTITY,
            "PAPER_V3_DATABASE_PATH": str(v3),
        },
    )

    assert resolved_v2.database_path == v2.resolve()
    assert resolved_v3.database_path == v3.resolve()
    assert resolved_v2.database_path != resolved_v3.database_path


@pytest.mark.parametrize(
    ("strategy", "override", "expected"),
    (
        (Q70_STRATEGY_IDENTITY, "PAPER_V2_DATABASE_PATH", "v2.db"),
        (V3_STRATEGY_IDENTITY, "PAPER_V3_DATABASE_PATH", "v3.db"),
    ),
)
def test_daily_wrapper_and_standalone_executor_share_one_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    strategy: str,
    override: str,
    expected: str,
) -> None:
    path = tmp_path / expected
    monkeypatch.setenv(override, str(path))
    monkeypatch.setenv("PAPER_STRATEGY_VERSION", strategy)
    monkeypatch.setenv("PAPER_DATABASE_PATH", str(tmp_path / "wrong-generic.db"))
    monkeypatch.setattr("scripts.run_paper_lifecycle.main", lambda: None)

    if strategy == V3_STRATEGY_IDENTITY:
        run_paper_v3_lifecycle.main()
    else:
        run_paper_v2_lifecycle.main()
    daily_path = Path(os.environ["PAPER_DATABASE_PATH"])

    monkeypatch.setattr(scanner, "paper_signal_executor", None)
    monkeypatch.setattr(
        scanner.PaperSignalExecutor,
        "from_env",
        lambda: SimpleNamespace(config=PaperExecutionConfig.from_env()),
    )
    scanner_path = scanner.initialize_scanner_runtime().config.database_path
    assert daily_path == scanner_path == path


def test_active_role_comes_from_strategy_not_positions_or_version_number(tmp_path: Path) -> None:
    root = _paper_root(tmp_path)
    snapshot = inspect_paper_system(root=root, environ={})
    stores = {item.store_id: item for item in snapshot.stores}

    assert stores["q70-frozen"].role is PaperStoreRole.ACTIVE
    assert stores["q70-frozen"].open_position_count == 0
    assert stores["generic-paper"].role is PaperStoreRole.INACTIVE_WITH_STATE
    assert stores["generic-paper"].open_position_count == 1
    assert stores["v3-breadth-40-60"].role is PaperStoreRole.INACTIVE


def test_cli_places_one_active_store_before_other_stores(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for name in (
        "PAPER_STRATEGY_VERSION",
        "PAPER_DATABASE_PATH",
        "PAPER_V2_DATABASE_PATH",
        "PAPER_V3_DATABASE_PATH",
    ):
        monkeypatch.delenv(name, raising=False)
    root = _paper_root(tmp_path)
    output = render_paper_status(root=root)

    assert output.count("Active Store") == 1
    assert output.index("Active Store") < output.index("Other Stores")
    assert "Frozen Q70 Paper Store" in output.split("Other Stores", 1)[0]
    assert "Role: INACTIVE_WITH_STATE" in output
    assert "Inactive store contains persisted open positions." in output


def test_explicit_performance_report_database_remains_respected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "historical.db"
    captured: list[Path] = []
    monkeypatch.setattr(
        report_paper_performance,
        "calculate_paper_performance",
        lambda path, **kwargs: captured.append(path) or SimpleNamespace(),
    )
    monkeypatch.setattr(report_paper_performance, "print_report", lambda report: None)
    monkeypatch.setattr(sys, "argv", ["report", "--database", str(explicit)])

    report_paper_performance.main()

    assert captured == [explicit]
