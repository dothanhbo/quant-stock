from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from manager.pages import dashboard, research, system
from manager.view_models import (
    build_dashboard_model,
    build_research_model,
    build_system_model,
)


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="Quant Manager", layout="wide")
    st.title("Quant Manager")
    st.caption("System Control & Research Console")
    st.info("READ-ONLY MODE")

    st.sidebar.header("Navigation")
    page = st.sidebar.radio("Page", ("Dashboard", "Research", "System / Doctor"))
    if st.sidebar.button("Refresh", type="secondary", use_container_width=True):
        st.rerun()
    st.sidebar.caption("Refresh re-reads local state only.")

    if page == "Dashboard":
        dashboard.render(build_dashboard_model())
    elif page == "Research":
        research.render(build_research_model())
    else:
        system.render(build_system_model())


if __name__ == "__main__":
    main()
