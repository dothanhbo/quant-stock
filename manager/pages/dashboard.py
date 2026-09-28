from __future__ import annotations

from manager.view_models import DashboardViewModel


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
    state_columns = st.columns(4)
    active = model.paper.active_store
    if active is None:
        state_columns[0].metric("Paper active store", "UNKNOWN")
        state_columns[1].metric("Paper strategy", "UNKNOWN")
        state_columns[2].metric("Paper state", "UNKNOWN")
    else:
        state_columns[0].metric("Paper active store", active.display_name)
        state_columns[1].metric("Paper strategy", active.strategy_identity)
        if not active.exists:
            paper_state = "MISSING"
        elif not active.readable or active.open_position_count is None:
            paper_state = "UNKNOWN"
        else:
            paper_state = (
                f"{active.open_position_count} open · "
                f"{active.pending_signal_count if active.pending_signal_count is not None else 'UNKNOWN'} pending"
            )
        state_columns[2].metric("Paper state", paper_state)
    forward = model.forward
    if not forward.exists:
        forward_value = "MISSING"
    elif not forward.readable or forward.active_protocol_count is None:
        forward_value = "UNKNOWN"
    else:
        forward_value = f"{forward.active_protocol_count} active"
    state_columns[3].metric("Forward", forward_value)
    inactive_state = any(
        (store.open_position_count or 0) > 0 or (store.pending_signal_count or 0) > 0
        for store in model.paper.other_stores
    )
    st.caption(f"Inactive paper state: {'PRESENT' if inactive_state else 'NONE'}")
    st.caption("Counts reflect persisted state, not strategy health or current market valuation.")

    st.subheader("Research")
    research_columns = st.columns(2)
    research_columns[0].metric("Active runners", len(snapshot.runners))
    research_columns[1].metric("Archive isolation", "OK" if snapshot.archive_isolated else "UNKNOWN")
    st.caption("Open Research from the sidebar to inspect the active runner catalog.")

    st.subheader("Telegram")
    st.metric("Module/config surface", "AVAILABLE" if snapshot.telegram_module_available else "MISSING")
    st.caption("No connection or Telegram API request is performed.")
