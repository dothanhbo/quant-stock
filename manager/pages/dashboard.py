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

    st.subheader("Production")
    state_columns = st.columns(4)
    active = model.paper.active_store
    if active is None:
        state_columns[0].metric("Paper active store", "UNKNOWN")
        state_columns[1].metric("Deployed paper policy", model.production.deployed_strategy_identity)
        state_columns[2].metric("Paper state", "UNKNOWN")
    else:
        state_columns[0].metric("Paper active store", active.display_name)
        state_columns[1].metric("Deployed paper policy", model.production.deployed_strategy_identity)
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
    st.caption(f"Production role: {model.production.role} · Research status is reported separately below.")
    inactive_state = any(
        (store.open_position_count or 0) > 0 or (store.pending_signal_count or 0) > 0
        for store in model.paper.other_stores
    )
    st.caption(f"Inactive paper state: {'PRESENT' if inactive_state else 'NONE'}")
    st.caption("Counts reflect persisted state, not strategy health or current market valuation.")

    st.subheader("Last Run")
    if model.history_warning:
        st.warning(model.history_warning)
    latest = model.latest_run
    if latest is None:
        st.caption("No operational runs recorded.")
    else:
        run = latest.summary
        run_columns = st.columns(4)
        run_columns[0].metric("Operation", run.operation.upper())
        run_columns[1].metric("Status", run.status.value)
        run_columns[2].metric("Started", run.started_at_utc)
        run_columns[3].metric(
            "Duration",
            "UNKNOWN" if run.duration_ms is None else f"{run.duration_ms / 1000:.1f}s",
        )
        st.caption(
            f"Run ID: {run.run_id}"
            + (f" · Failed step: {run.failed_step}" if run.failed_step else "")
        )

    st.subheader("Research")
    research_columns = st.columns(4)
    research_columns[0].metric("Framework", model.research.framework)
    research_columns[1].metric("Current stage", model.research.latest_stage)
    advanced = next(
        (item for item in model.research.factor_decisions if item.decision.value == "ADVANCE"),
        None,
    )
    research_columns[2].metric(
        "Latest factor decision",
        f"{advanced.name}: ADVANCE" if advanced else "UNAVAILABLE",
    )
    replacement = model.research.production_replacement.decision.value
    research_columns[3].metric("Production replacement", replacement)
    st.caption(
        f"Active runners: {len(snapshot.runners)} · Archive isolation: "
        f"{'OK' if snapshot.archive_isolated else 'UNKNOWN'} · Open Research for artifact-backed detail."
    )

    st.subheader("Telegram")
    st.metric("Module/config surface", "AVAILABLE" if snapshot.telegram_module_available else "MISSING")
    st.caption("No connection or Telegram API request is performed.")
