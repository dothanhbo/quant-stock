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


def test_research_model_uses_fixed_catalog_without_runner_discovery(tmp_path: Path) -> None:
    research = tmp_path / "research"
    archive = research / "archive"
    archive.mkdir(parents=True)
    (research / "run_quantlab_current.py").write_text("VALUE = 1\n", encoding="utf-8")
    (archive / "run_quantlab_old.py").write_text("VALUE = 1\n", encoding="utf-8")

    model = build_research_model(root=tmp_path, environ={})

    assert all(item.state.value == "UNAVAILABLE" for item in model.catalog.artifacts)
    assert not hasattr(model, "runners")


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


def test_manager_theme_preserves_readability_and_non_color_status_meaning() -> None:
    from manager.theme import MANAGER_CSS, status_badge_html

    assert '[data-testid="stMetricValue"]' in MANAGER_CSS
    assert '[data-testid="stMetricLabel"]' in MANAGER_CSS
    assert "text-overflow: clip" in MANAGER_CSS
    assert '[data-selected="true"]' in MANAGER_CSS
    assert "STALE RUNNING" in status_badge_html("STALE_RUNNING")
    assert "FAILED" in status_badge_html("FAILED")
    assert "WARNING" in status_badge_html("WARNING")
    assert "<script" not in MANAGER_CSS.lower()


def test_every_manager_page_exposes_the_operator_ux_contract() -> None:
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(PROJECT_ROOT / "manager" / "app.py"), default_timeout=15).run()
    control_pages = ("Dashboard", "Operations", "Run History", "Paper State", "System / Doctor")
    lab_pages = ("Research Home", "Explore", "Compare", "Portfolio & Risk", "Forward Evidence", "Decision Gate")
    pages = control_pages + lab_pages

    def navigate(page: str) -> None:
        radio = app.sidebar.radio[0] if page in control_pages else app.sidebar.radio[1]
        radio.set_value(page)
        app.run()
    expected_sections = {
        pages[0]: {"At a Glance", "Market Data", "Production", "Forward", "Last Run", "Research"},
        pages[1]: {"Read-only inspection", "State-changing operations"},
        pages[2]: {"Latest Operational Run", "Recent Runs"},
        pages[3]: {"Current State", "Active Paper", "Forward Validation"},
        pages[4]: {"Repository", "Python", "Environment", "Data", "Paper / Forward"},
        pages[5]: {
            "Research Progression",
            "Candidate Decisions",
            "Open Readiness Gaps",
        },
        pages[6]: {"Temporal Rank IC", "Spread, Incremental and Redundancy Evidence"},
        pages[7]: {"Paired Temporal Evidence", "Descriptive Differences"},
        pages[8]: {
            "Risk Profile by Budget",
            "Portfolio Structure",
            "Outcome and Hypothetical Cost Evidence",
            "Execution Evidence",
            "Evidence Availability",
        },
        pages[9]: set(),
        pages[10]: {
            "Canonical Disposition",
            "Evidence State Summary",
            "Evidence Dimension Matrix",
            "Blockers and Limitations",
            "Production Readiness — Separate Operational Gate",
            "Supporting Evidence Navigation",
        },
    }

    assert tuple(app.sidebar.radio[0].options) == control_pages
    assert tuple(app.sidebar.radio[1].options) == lab_pages
    for page in pages:
        navigate(page)
        assert not app.exception
        assert expected_sections[page].issubset({item.value for item in app.subheader})

    navigate(pages[0])
    dashboard_metrics = {item.label for item in app.metric}
    assert {
        "System health",
        "Latest market session",
        "Paper policy",
        "Latest operation",
        "Research readiness",
        "Open readiness gaps",
    }.issubset(dashboard_metrics)
    assert "System identity and runtime" in {item.label for item in app.expander}

    navigate(pages[1])
    operation_buttons = {item.label: item.disabled for item in app.button if item.label.startswith("Run ")}
    assert operation_buttons == {
        "Run Data Status": False,
        "Run Update": True,
        "Run Scan": True,
        "Run Daily": True,
    }
    assert len(app.checkbox) == 3
    assert any("state-changing" in item.value.lower() for item in app.warning)

    app.button[0].click()
    app.run()
    assert not app.exception
    assert "Latest operation result" in {item.value for item in app.subheader}
    assert any("completed successfully" in item.value for item in app.success)
    assert "Technical details" in {item.label for item in app.expander}

    navigate(pages[2])
    assert "Status meanings" in {item.label for item in app.expander}

    navigate(pages[3])
    state_metrics = {item.label for item in app.metric}
    assert {"Active paper store", "Evidence continuity", "Forward protocols"}.issubset(
        state_metrics
    )

    navigate(pages[5])
    research_expanders = {item.label for item in app.expander}
    assert "Provenance and limitations" in research_expanders
    assert any("Current research frontier" in item.value for item in app.markdown)
    assert any("Production readiness" in item.value for item in app.markdown)
    assert not app.text_input

    navigate(pages[4])
    assert {"Passed", "Warnings", "Failed", "Unknown"}.issubset(
        {item.label for item in app.metric}
    )

    navigate(pages[6])
    assert not app.exception
    assert {
        "Current gate / disposition",
        "Defined-date coverage",
        "Mean daily rank IC",
        "Positive spread-date rate",
        "Temporal consistency",
    }.issubset({item.label for item in app.metric})
    assert "Load persisted daily IC and spread detail" in {
        item.label for item in app.checkbox
    }

    navigate(pages[7])
    assert not app.exception
    assert "Compare" in {item.value for item in app.header}
    assert "Comparison family" in {item.label for item in app.selectbox}
    from quantctl.evidence_compare import ComparisonFamily

    app.selectbox[0].set_value(ComparisonFamily.FROZEN_POLICIES)
    app.run()
    assert not app.exception

    navigate(pages[8])
    assert not app.exception
    assert "Portfolio & Risk" in {item.value for item in app.header}
    assert any("no persisted Phase 6 portfolio" in item.value for item in app.info)
    assert {
        "Selection policy",
        "Budget",
        "Horizon",
        "Risk-policy variant",
    }.issubset({item.label for item in app.selectbox})

    navigate(pages[9])
    assert not app.exception
    assert "Forward Evidence" in {item.value for item in app.header}
    assert "Evidence source" in {item.label for item in app.radio}
    assert any("separate evidence populations" in item.value for item in app.warning)
    app.radio[0].set_value("Forward Protocol")
    app.run()
    assert not app.exception

    navigate(pages[10])
    assert not app.exception
    assert "Decision Gate" in {item.value for item in app.header}
    assert {"Candidate type", "Persisted candidate"}.issubset(
        {item.label for item in app.selectbox}
    )
    assert any("no score, ranking, approval, or promotion" in item.value.lower() for item in app.caption)


def test_quant_lab_cross_page_research_workflows_preserve_compatible_context() -> None:
    from streamlit.testing.v1 import AppTest
    from quantctl.evidence_compare import ComparisonFamily

    app = AppTest.from_file(str(PROJECT_ROOT / "manager" / "app.py"), default_timeout=15).run()

    def navigate(page: str) -> None:
        options = tuple(app.sidebar.radio[0].options)
        radio = app.sidebar.radio[0] if page in options else app.sidebar.radio[1]
        radio.set_value(page)
        app.run()

    # Flow A: Research Home opens Explore, which carries the exact factor
    # identity into Decision Gate.
    navigate("Research Home")
    next(item for item in app.button if item.label == "Explore factor evidence").click()
    app.run()
    assert "Explore" in {item.value for item in app.header}
    factor = app.selectbox[1].options[2]
    app.selectbox[1].set_value(factor)
    app.run()
    navigate("Decision Gate")
    assert app.selectbox[1].value == f"factor:{factor}"

    # Flow C: Decision Gate links back to the candidate's supporting page.
    next(item for item in app.button if item.label == "Open Explore").click()
    app.run()
    assert "Explore" in {item.value for item in app.header}
    assert app.selectbox[1].value == factor

    # Flow B: a comparison preserves its separately compatible ADX_ONLY context
    # for Portfolio & Risk, then carries exact budget/horizon through Forward.
    navigate("Research Home")
    next(item for item in app.button if item.label == "Compare candidates").click()
    app.run()
    assert "Compare" in {item.value for item in app.header}
    app.selectbox[0].set_value(ComparisonFamily.FROZEN_POLICIES)
    app.run()
    assert app.selectbox[1].value == "ADX_RSI_EQUAL_WEIGHT"
    assert app.selectbox[2].value == "ADX_ONLY"
    navigate("Portfolio & Risk")
    assert app.selectbox[0].value == "ADX_ONLY"
    assert any("separately carried compatible" in item.value for item in app.info)
    app.selectbox[1].set_value(5)
    app.selectbox[2].set_value(5)
    app.run()
    navigate("Forward Evidence")
    app.radio[0].set_value("Forward Protocol")
    app.run()
    assert not app.exception
    if any(item.label == "Persisted horizon" for item in app.selectbox):
        next(item for item in app.selectbox if item.label == "Persisted horizon").set_value(5)
        app.run()
        navigate("Decision Gate")
        assert app.selectbox[0].value.value == "Portfolio configuration"
        assert app.selectbox[1].value == "portfolio:5:5"
