from __future__ import annotations

from manager.view_models import RunHistoryViewModel


def _duration(value: int | None) -> str:
    return "UNKNOWN" if value is None else f"{value / 1000:.1f}s"


def render(model: RunHistoryViewModel) -> None:
    import streamlit as st

    st.header("Run History")
    st.info("READ-ONLY — operational history only; no operation can be started here.")
    st.subheader("Latest Operational Run")
    if model.latest is None:
        st.caption("No operational runs recorded.")
    else:
        run = model.latest.summary
        columns = st.columns(4)
        columns[0].metric("Operation", run.operation.upper())
        columns[1].metric("Status", run.status.value)
        columns[2].metric("Duration", _duration(run.duration_ms))
        columns[3].metric("Exit code", run.exit_code if run.exit_code is not None else "UNKNOWN")
        st.caption(f"Started: {run.started_at_utc} · Finished: {run.finished_at_utc or 'UNKNOWN'}")
        st.caption(f"Run ID: {run.run_id} · Capabilities: {', '.join(run.capabilities)}")
        if run.error_message:
            st.error(run.error_message)
        if model.latest.steps:
            st.dataframe(
                tuple(
                    {
                        "Sequence": item.sequence,
                        "Step": item.step_name,
                        "Status": item.status.value,
                        "Duration": _duration(item.duration_ms),
                        "Error": item.error_message or "",
                    }
                    for item in model.latest.steps
                ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("Step detail unavailable for this operation.")

    st.subheader("Recent Runs")
    if not model.recent:
        st.caption("No operational runs recorded.")
        return
    st.dataframe(
        tuple(
            {
                "Time": item.summary.started_at_utc,
                "Operation": item.summary.operation.upper(),
                "Status": item.summary.status.value,
                "Duration": _duration(item.summary.duration_ms),
                "Failed step": item.summary.failed_step or "",
                "Run ID": item.summary.run_id,
            }
            for item in model.recent
        ),
        use_container_width=True,
        hide_index=True,
    )
    selected = st.selectbox("Inspect run", tuple(item.summary.run_id for item in model.recent))
    detail = next((item for item in model.recent if item.summary.run_id == selected), None)
    if detail is not None:
        run = detail.summary
        with st.expander(f"Run {run.run_id}", expanded=False):
            st.write(f"Status: **{run.status.value}**")
            st.write(f"Capabilities: {', '.join(run.capabilities)}")
            if detail.steps:
                st.dataframe(
                    tuple(
                        {
                            "Sequence": item.sequence,
                            "Step": item.step_name,
                            "Status": item.status.value,
                            "Duration": _duration(item.duration_ms),
                            "Error": item.error_message or "",
                        }
                        for item in detail.steps
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
            if run.error_message:
                st.error(run.error_message)
