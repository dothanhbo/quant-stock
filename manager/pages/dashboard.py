from __future__ import annotations

from manager.view_models import DashboardViewModel
from manager.theme import compact_status_label, status_badge_html, status_badge_row_html
from quantctl.run_history import RunDisplayStatus, classify_run_for_display


def render(model: DashboardViewModel) -> None:
    import streamlit as st

    snapshot = model.snapshot
    st.header("Dashboard")
    st.caption("Operational health, deployed state, and current research direction at a glance.")

    st.markdown(status_badge_html(model.doctor_status, label="System"), unsafe_allow_html=True)
    if model.doctor_status == "PASS":
        st.caption("All represented system checks are passing.")
    elif model.doctor_status == "WARN":
        st.warning("System is usable with warnings. Open Doctor for details.")
    else:
        st.error("System checks require attention. Open Doctor for details.")

    active = model.paper.active_store
    latest = model.latest_run
    latest_run_status = (
        classify_run_for_display(latest.summary).value if latest is not None else "NOT STARTED"
    )
    st.subheader("At a Glance")
    overview_columns = st.columns(3)
    overview_columns[0].metric("System health", model.doctor_status)
    overview_columns[1].metric("Latest market session", snapshot.market.latest_session or "UNKNOWN")
    overview_columns[2].metric("Latest operation", compact_status_label(latest_run_status))
    overview_columns = st.columns(3)
    overview_columns[0].metric("Paper policy", model.production.deployed_strategy_identity)
    overview_columns[1].metric(
        "Prospective evidence",
        "AVAILABLE" if active and active.evidence_schema_status == "OK" else "UNAVAILABLE",
    )
    overview_columns[2].metric(
        "Research readiness",
        compact_status_label(model.research.readiness.readiness),
    )
    st.caption(
        f"Active store: {active.display_name if active else 'UNKNOWN'} · "
        f"Readiness contract: {model.research.readiness.readiness}"
    )

    attention: list[str] = []
    if latest_run_status in {RunDisplayStatus.FAILED.value, RunDisplayStatus.STALE_RUNNING.value}:
        attention.append(f"latest operation is {latest_run_status}")
    if active is None:
        attention.append("active paper store is unresolved")
    elif active.evidence_schema_status != "OK":
        attention.append("prospective portfolio evidence is unavailable")
    if model.research.readiness.open_gap_count:
        attention.append(f"research readiness has {model.research.readiness.open_gap_count} open gaps")
    if attention:
        st.warning("Needs attention: " + "; ".join(attention) + ".")
    else:
        st.caption("No Manager-visible attention items are currently represented.")

    with st.expander("System identity and runtime", expanded=False):
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
    state_columns = st.columns(2)
    if active is None:
        state_columns[0].metric("Deployed paper policy", model.production.deployed_strategy_identity)
        state_columns[1].metric("Active store", "UNKNOWN")
        state_columns = st.columns(2)
        state_columns[0].metric("Open positions", "UNKNOWN")
        state_columns[1].metric("Pending signals", "UNKNOWN")
    else:
        state_columns[0].metric("Deployed paper policy", model.production.deployed_strategy_identity)
        state_columns[1].metric("Active store", active.display_name)
        state_columns = st.columns(2)
        state_columns[0].metric(
            "Open positions",
            active.open_position_count if active.open_position_count is not None else "UNKNOWN",
        )
        state_columns[1].metric(
            "Pending signals",
            active.pending_signal_count if active.pending_signal_count is not None else "UNKNOWN",
        )
        evidence_status = "AVAILABLE" if active.evidence_schema_status == "OK" else "UNAVAILABLE"
        st.markdown(
            status_badge_row_html(
                ("Evidence", evidence_status),
                ("Continuity", active.evidence_continuity_state or "UNKNOWN"),
                (
                    "Capture",
                    "NOT STARTED"
                    if active.evidence_capture_state == "MISSING"
                    else active.evidence_capture_state,
                ),
            ),
            unsafe_allow_html=True,
        )
        st.caption(f"Latest evidence session: {active.latest_evidence_date or 'UNKNOWN'}")

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
    if latest is None:
        st.caption("No operational runs recorded.")
    else:
        run = latest.summary
        display_status = classify_run_for_display(run)
        run_columns = st.columns(2)
        run_columns[0].metric("Operation", run.operation.upper())
        run_columns[1].metric("Status", display_status.value)
        run_columns = st.columns(2)
        run_columns[0].metric("Started", run.started_at_utc.replace("T", " ")[:19])
        run_columns[1].metric(
            "Duration",
            "UNKNOWN" if run.duration_ms is None else f"{run.duration_ms / 1000:.1f}s",
        )
        if run.failed_step:
            st.error(f"Failed step: {run.failed_step}")
        if display_status is RunDisplayStatus.STALE_RUNNING:
            st.warning("The latest operation is still persisted as RUNNING from a prior UTC date.")
        st.caption(f"Run ID: {run.run_id[:8]}… · Open Run History for full details.")

    st.subheader("Research")
    research_columns = st.columns(2)
    research_columns[0].metric("Current stage", model.research.latest_stage)
    research_columns[1].metric("Evidence as of", model.research.as_of or "UNKNOWN")
    replacement = model.research.production_replacement.decision.value
    research_columns = st.columns(2)
    research_columns[0].metric("Production replacement", compact_status_label(replacement))
    research_columns[1].metric(
        "Open readiness gaps",
        model.research.readiness.open_gap_count
        if model.research.readiness.open_gap_count is not None
        else "UNKNOWN",
    )
    st.caption(
        f"Framework: {model.research.framework} · Readiness: {model.research.readiness.readiness} · "
        f"Active runners: {len(snapshot.runners)} · Archive isolation: "
        f"{'OK' if snapshot.archive_isolated else 'UNKNOWN'} · Open Research for artifact-backed detail."
    )

    with st.expander("System integration details", expanded=False):
        st.write(f"Telegram module: {'AVAILABLE' if snapshot.telegram_module_available else 'MISSING'}")
        st.write(f"Active research runners: {len(snapshot.runners)}")
        st.write(f"Archive isolation: {'OK' if snapshot.archive_isolated else 'UNKNOWN'}")
