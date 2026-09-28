from __future__ import annotations

from manager.view_models import DashboardViewModel


def _state_label(*, exists: bool, readable: bool) -> str:
    if not exists:
        return "MISSING"
    return "AVAILABLE" if readable else "UNKNOWN"


def render(model: DashboardViewModel) -> None:
    import streamlit as st

    snapshot = model.snapshot
    st.header("Dashboard")
    st.caption("Current local facts from QuantCtl inspection services.")

    st.subheader("System")
    columns = st.columns(4)
    columns[0].metric("Doctor", model.doctor_status)
    columns[1].metric("QuantCtl", model.quantctl_version)
    columns[2].metric("Git HEAD", snapshot.git.head or "UNKNOWN")
    columns[3].metric("Working tree", snapshot.git.working_tree)
    st.caption(f"Repository tag: {snapshot.git.tag or 'UNKNOWN'}")

    st.subheader("Market Data")
    market_columns = st.columns(4)
    market_columns[0].metric(
        "Database",
        "AVAILABLE" if snapshot.market.readable else ("MISSING" if not snapshot.market.exists else "UNKNOWN"),
    )
    market_columns[1].metric("Latest session", snapshot.market.latest_session or "UNKNOWN")
    market_columns[2].metric("Sessions", snapshot.market.session_count if snapshot.market.session_count is not None else "UNKNOWN")
    market_columns[3].metric("Symbols", snapshot.market.symbol_count if snapshot.market.symbol_count is not None else "UNKNOWN")

    st.subheader("Persistent State")
    state_columns = st.columns(len(snapshot.persistent_databases))
    for column, state in zip(state_columns, snapshot.persistent_databases, strict=True):
        column.metric(state.label, _state_label(exists=state.exists, readable=state.readable))
    st.caption("Availability is a storage fact, not a strategy-health assessment.")

    st.subheader("Research")
    research_columns = st.columns(2)
    research_columns[0].metric("Active runners", len(snapshot.runners))
    research_columns[1].metric("Archive isolation", "OK" if snapshot.archive_isolated else "UNKNOWN")
    st.caption("Open Research from the sidebar to inspect the active runner catalog.")

    st.subheader("Telegram")
    st.metric("Module/config surface", "AVAILABLE" if snapshot.telegram_module_available else "MISSING")
    st.caption("No connection or Telegram API request is performed.")
