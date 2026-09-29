from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys

from manager.view_models import (
    build_dashboard_model,
    build_research_model,
    build_system_model,
)
from quantctl.commands import status


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CANONICAL_DATABASES = (
    "market.db",
    "forward_validation.db",
    "paper_trading.db",
    "paper_trading_v2.db",
    "paper_trading_v3.db",
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _market_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE prices (symbol TEXT, time TEXT, close REAL)")
        connection.executemany(
            "INSERT INTO prices VALUES (?, ?, ?)",
            (("AAA", "2026-01-02", 10.0), ("VNINDEX", "2026-01-02", 1000.0)),
        )


def test_dashboard_model_is_read_only(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    market = data / "market.db"
    _market_database(market)
    before = _digest(market)

    model = build_dashboard_model(root=tmp_path, environ={})

    assert model.snapshot.market.latest_session == "2026-01-02"
    assert _digest(market) == before
    assert not tuple(data.glob("*.db-wal"))
    assert not tuple(data.glob("*.db-shm"))


def test_research_model_lists_only_active_root_runners(tmp_path: Path) -> None:
    research = tmp_path / "research"
    archive = research / "archive"
    archive.mkdir(parents=True)
    (research / "run_quantlab_current.py").write_text("VALUE = 1\n", encoding="utf-8")
    (archive / "run_quantlab_old.py").write_text("VALUE = 1\n", encoding="utf-8")

    model = build_research_model(root=tmp_path)

    assert tuple(item.short_name for item in model.runners) == ("current",)
    assert all("archive" not in item.path.parts for item in model.runners)


def test_missing_databases_are_presented_gracefully(tmp_path: Path) -> None:
    (tmp_path / "research").mkdir()
    model = build_dashboard_model(root=tmp_path, environ={})

    assert not model.snapshot.market.exists
    assert not model.snapshot.market.readable
    assert all(not state.exists for state in model.snapshot.persistent_databases)


def test_system_model_never_contains_secret_values(tmp_path: Path) -> None:
    token = "secret-token-never-render"
    chat_id = "123456789"
    model = build_system_model(
        root=tmp_path,
        environ={"TELEGRAM_TOKEN": token, "CHAT_ID": chat_id},
    )
    rendered_facts = repr(model)

    assert token not in rendered_facts
    assert chat_id not in rendered_facts
    assert any(item.name == "TELEGRAM_TOKEN" and item.detail == "CONFIGURED" for item in model.checks)
    assert any(item.name == "CHAT_ID" and item.detail == "CONFIGURED" for item in model.checks)


def test_ui_has_no_operational_command_binding() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((PROJECT_ROOT / "manager").rglob("*.py"))
    )
    forbidden = (
        "scripts.run_daily",
        "scripts.update_data",
        "strategy.scanner",
        "TelegramClient(",
        "run_backtest(",
        "run_walk_forward(",
        "subprocess.run(",
        "requests.get(",
        "requests.post(",
    )
    assert all(item not in sources for item in forbidden)
    assert sources.count("st.sidebar.button(") == 1
    assert '"Refresh"' in sources


def test_existing_quantctl_status_contract_remains_available(tmp_path: Path) -> None:
    (tmp_path / "research").mkdir()
    output = status.render(root=tmp_path)
    assert "QUANT SYSTEM STATUS" in output
    assert "Database: MISSING" in output
    assert "Forward: MISSING" in output


def test_importing_manager_does_not_import_streamlit_or_touch_state() -> None:
    code = (
        "import sys; "
        "import manager, manager.app, manager.view_models, "
        "manager.pages.dashboard, manager.pages.research, manager.pages.system; "
        "print('streamlit' in sys.modules)"
    )
    completed = subprocess.run(
        (sys.executable, "-B", "-c", code),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "False"


def test_manager_reads_canonical_databases_without_mutation() -> None:
    data = PROJECT_ROOT / "data"
    before = {name: _digest(data / name) for name in CANONICAL_DATABASES}

    build_dashboard_model(root=PROJECT_ROOT, environ={})
    build_system_model(root=PROJECT_ROOT, environ={})

    after = {name: _digest(data / name) for name in CANONICAL_DATABASES}
    assert after == before


def test_view_models_are_immutable(tmp_path: Path) -> None:
    (tmp_path / "research").mkdir()
    dashboard = build_dashboard_model(root=tmp_path, environ={})
    try:
        dashboard.doctor_status = "PASS"
    except (AttributeError, TypeError):
        pass
    else:
        raise AssertionError("dashboard model must be immutable")


def test_manager_uses_current_streamlit_width_api() -> None:
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((PROJECT_ROOT / "manager").rglob("*.py"))
    )

    assert "use_container_width" not in sources
    configuration = (PROJECT_ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert "showSidebarNavigation = false" in configuration


def test_every_manager_page_exposes_the_operator_ux_contract() -> None:
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(PROJECT_ROOT / "manager" / "app.py"), default_timeout=15).run()
    pages = (
        "OVERVIEW  /  Dashboard",
        "OPERATIONS  /  Operations",
        "OPERATIONS  /  Run History",
        "STATE  /  Paper & Forward",
        "RESEARCH  /  Research",
        "SYSTEM  /  Doctor",
    )
    expected_sections = {
        pages[0]: {"System", "Market Data", "Production", "Forward", "Last Run", "Research"},
        pages[1]: {"Data Status", "Update Market Data", "Run Scanner", "Run Daily Pipeline"},
        pages[2]: {"Latest Operational Run", "Recent Runs"},
        pages[3]: {"Active Paper", "Forward Validation"},
        pages[4]: {"Research Frontier", "Production Replacement Status", "Factor Decisions"},
        pages[5]: {"Repository", "Python", "Environment", "Data", "Paper / Forward"},
    }

    assert tuple(app.sidebar.radio[0].options) == pages
    for page in pages:
        app.sidebar.radio[0].set_value(page)
        app.run()
        assert not app.exception
        assert expected_sections[page].issubset({item.value for item in app.subheader})

    app.sidebar.radio[0].set_value(pages[1])
    app.run()
    operation_buttons = {item.label: item.disabled for item in app.button if item.label.startswith("Run ")}
    assert operation_buttons == {
        "Run Data Status": False,
        "Run Update": True,
        "Run Scan": True,
        "Run Daily": True,
    }
    assert len(app.checkbox) == 3

    app.button[0].click()
    app.run()
    assert not app.exception
    assert "Latest operation result" in {item.value for item in app.subheader}
    assert any("completed successfully" in item.value for item in app.success)
    assert "Technical details" in {item.label for item in app.expander}
