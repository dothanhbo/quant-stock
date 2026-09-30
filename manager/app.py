from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from manager.pages import dashboard, operations, research, run_history, state, system
from manager.theme import apply_manager_theme, sidebar_metadata_html
from manager.view_models import (
    build_dashboard_model,
    build_operations_model,
    build_research_model,
    build_run_history_model,
    build_state_model,
    build_system_model,
)
from quantctl.registry import inspect_git


_NAVIGATION_LABELS = {
    "Dashboard": "OVERVIEW  /  Dashboard",
    "Operations": "OPERATIONS  /  Operations",
    "Run History": "OPERATIONS  /  Run History",
    "State": "STATE  /  Paper & Forward",
    "Research": "RESEARCH  /  Research",
    "System / Doctor": "SYSTEM  /  Doctor",
}


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
    page = st.sidebar.radio(
        "Page",
        ("Dashboard", "Operations", "Run History", "State", "Research", "System / Doctor"),
        format_func=_NAVIGATION_LABELS.__getitem__,
        label_visibility="collapsed",
    )
    if st.sidebar.button("Refresh", type="secondary", width="stretch"):
        st.rerun()
    st.sidebar.caption("Refresh re-reads local state only.")

    if page == "Dashboard":
        dashboard.render(build_dashboard_model())
    elif page == "Operations":
        operations.render(build_operations_model())
    elif page == "Run History":
        run_history.render(build_run_history_model())
    elif page == "State":
        state.render(build_state_model())
    elif page == "Research":
        research.render(build_research_model())
    else:
        system.render(build_system_model())


if __name__ == "__main__":
    main()
