from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from manager.pages import compare, dashboard, decision_gate, explore, forward_evidence, operations, portfolio_risk, research, run_history, state, system
from manager.theme import apply_manager_theme, sidebar_metadata_html
from manager.view_models import (
    build_dashboard_model,
    build_compare_model,
    build_decision_gate_model,
    build_explore_model,
    build_forward_evidence_model,
    build_operations_model,
    build_portfolio_risk_model,
    build_research_model,
    build_run_history_model,
    build_state_model,
    build_system_model,
)
from quantctl.registry import inspect_git


_CONTROL_PAGES = ("Dashboard", "Operations", "Run History", "Paper State", "System / Doctor")
_QUANT_LAB_PAGES = ("Research Home", "Explore", "Compare", "Portfolio & Risk", "Forward Evidence", "Decision Gate")
_PAGES = _CONTROL_PAGES + _QUANT_LAB_PAGES


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="Quant Manager", layout="wide")
    apply_manager_theme(st)
    st.title("Quant Manager")
    st.caption("Canonical Management & Research Console")
    st.markdown(
        '<div class="qm-console-mode">READ-ONLY BY DEFAULT &nbsp;·&nbsp; '
        "State-changing operations require explicit confirmation.</div>",
        unsafe_allow_html=True,
    )

    git = inspect_git()
    st.sidebar.markdown(
        sidebar_metadata_html(
            environment="LOCAL",
            repository=git.tag or git.head or "UNKNOWN",
        ),
        unsafe_allow_html=True,
    )
    requested_page = st.session_state.pop("_quant_manager_nav_request", None)
    if requested_page in _PAGES:
        st.session_state["quant_manager_page"] = requested_page
        st.session_state.pop("quant_manager_control_page", None)
        st.session_state.pop("quant_manager_lab_page", None)
    page = st.session_state.get("quant_manager_page", "Dashboard")
    st.session_state["quant_manager_page"] = page

    def select_control_page() -> None:
        selected = st.session_state.get("quant_manager_control_page")
        if selected in _CONTROL_PAGES:
            st.session_state["quant_manager_page"] = selected
            st.session_state["quant_manager_lab_page"] = None

    def select_lab_page() -> None:
        selected = st.session_state.get("quant_manager_lab_page")
        if selected in _QUANT_LAB_PAGES:
            st.session_state["quant_manager_page"] = selected
            st.session_state["quant_manager_control_page"] = None

    st.sidebar.markdown('<div class="qm-nav-label">CONTROL</div>', unsafe_allow_html=True)
    st.sidebar.radio(
        "Control navigation",
        _CONTROL_PAGES,
        index=_CONTROL_PAGES.index(page) if page in _CONTROL_PAGES else None,
        label_visibility="collapsed",
        key="quant_manager_control_page",
        on_change=select_control_page,
    )
    st.sidebar.markdown('<div class="qm-nav-label">QUANT LAB</div>', unsafe_allow_html=True)
    st.sidebar.radio(
        "Quant Lab navigation",
        _QUANT_LAB_PAGES,
        index=_QUANT_LAB_PAGES.index(page) if page in _QUANT_LAB_PAGES else None,
        label_visibility="collapsed",
        key="quant_manager_lab_page",
        on_change=select_lab_page,
    )
    page = st.session_state["quant_manager_page"]
    if st.sidebar.button("Refresh", type="secondary", width="stretch"):
        st.rerun()
    st.sidebar.caption("Refresh re-reads local state only.")

    if page == "Dashboard":
        dashboard.render(build_dashboard_model())
    elif page == "Operations":
        operations.render(build_operations_model())
    elif page == "Run History":
        run_history.render(build_run_history_model())
    elif page == "Paper State":
        state.render(build_state_model())
    elif page == "Research Home":
        research.render(build_research_model())
    elif page == "Explore":
        explore.render(build_explore_model())
    elif page == "Compare":
        compare.render(build_compare_model())
    elif page == "Portfolio & Risk":
        portfolio_risk.render(build_portfolio_risk_model())
    elif page == "Forward Evidence":
        forward_evidence.render(build_forward_evidence_model())
    elif page == "Decision Gate":
        decision_gate.render(build_decision_gate_model())
    elif page == "System / Doctor":
        system.render(build_system_model())


if __name__ == "__main__":
    main()
