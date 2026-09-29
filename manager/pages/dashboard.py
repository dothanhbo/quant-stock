from __future__ import annotations

from manager.view_models import DashboardViewModel


def render(model: DashboardViewModel) -> None:
    import streamlit as st

    snapshot = model.snapshot
    st.header("Dashboard")
    st.caption("Operational health, deployed state, and current research direction at a glance.")

    if model.doctor_status == "PASS":
        st.success("System checks are passing.")
    elif model.doctor_status == "WARN":
        st.warning("System is usable with warnings. Open Doctor for details.")
    else:
        st.error("System checks require attention. Open Doctor for details.")

    st.subheader("System")
    columns = st.columns(4)
    columns[0].metric("Overall", model.doctor_status)
    columns[1].metric("Version", snapshot.git.tag or model.quantctl_version)
    columns[2].metric("Git HEAD", snapshot.git.head or "UNKNOWN")
    columns[3].metric("Working tree", snapshot.git.working_tree)

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
        state_columns[0].metric("Deployed paper policy", model.production.deployed_strategy_identity)
        state_columns[1].metric("Active store", "UNKNOWN")
        state_columns[2].metric("Open positions", "UNKNOWN")
        state_columns[3].metric("Pending signals", "UNKNOWN")
    else:
        state_columns[0].metric("Deployed paper policy", model.production.deployed_strategy_identity)
        state_columns[1].metric("Active store", active.display_name)
        state_columns[2].metric(
            "Open positions",
            active.open_position_count if active.open_position_count is not None else "UNKNOWN",
        )
        state_columns[3].metric(
            "Pending signals",
            active.pending_signal_count if active.pending_signal_count is not None else "UNKNOWN",
        )

    st.subheader("Forward")
    forward = model.forward
    if not forward.exists:
        forward_value = "MISSING"
    elif not forward.readable or forward.active_protocol_count is None:
        forward_value = "UNKNOWN"
    else:
        forward_value = f"{forward.active_protocol_count} active"
    forward_columns = st.columns(3)
    forward_columns[0].metric("Protocol state", forward_value)
    forward_columns[1].metric(
        "Pending maturities",
        forward.pending_maturity_count if forward.pending_maturity_count is not None else "UNKNOWN",
    )
    forward_columns[2].metric(
        "Outcomes",
        forward.outcome_count if forward.outcome_count is not None else "UNKNOWN",
    )
    st.caption(f"Production role: {model.production.role}. Research status is reported separately.")
    inactive_state = any(
        (store.open_position_count or 0) > 0 or (store.pending_signal_count or 0) > 0
        for store in model.paper.other_stores
    )
    st.caption(f"Inactive paper state: {'PRESENT' if inactive_state else 'NONE'}")
    if inactive_state:
        st.warning("An inactive paper store still contains persisted state. Review Paper & Forward.")

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
        run_columns[2].metric("Started", run.started_at_utc.replace("T", " ")[:19])
        run_columns[3].metric(
            "Duration",
            "UNKNOWN" if run.duration_ms is None else f"{run.duration_ms / 1000:.1f}s",
        )
        if run.failed_step:
            st.error(f"Failed step: {run.failed_step}")
        st.caption(f"Run ID: {run.run_id[:8]}… · Open Run History for full details.")

    st.subheader("Research")
    research_columns = st.columns(4)
    research_columns[0].metric("Framework", model.research.framework)
    research_columns[1].metric("Current stage", model.research.latest_stage)
    research_columns[2].metric("Evidence as of", model.research.as_of or "UNKNOWN")
    replacement = model.research.production_replacement.decision.value
    research_columns[3].metric("Production replacement", replacement)
    st.caption(
        f"Active runners: {len(snapshot.runners)} · Archive isolation: "
        f"{'OK' if snapshot.archive_isolated else 'UNKNOWN'} · Open Research for artifact-backed detail."
    )

    with st.expander("System integration details", expanded=False):
        st.write(f"Telegram module: {'AVAILABLE' if snapshot.telegram_module_available else 'MISSING'}")
        st.write(f"Active research runners: {len(snapshot.runners)}")
        st.write(f"Archive isolation: {'OK' if snapshot.archive_isolated else 'UNKNOWN'}")
