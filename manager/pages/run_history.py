from __future__ import annotations

from manager.view_models import RunHistoryViewModel


def _duration(value: int | None) -> str:
    return "UNKNOWN" if value is None else f"{value / 1000:.1f}s"


def _short_id(value: str) -> str:
    return f"{value[:8]}…" if len(value) > 8 else value


def render(model: RunHistoryViewModel) -> None:
    import streamlit as st

    st.header("Run History")
    st.caption("Read-only audit trail for controlled operations.")
    if model.warning:
        st.warning(model.warning)
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
        st.caption(f"Run ID: {_short_id(run.run_id)}")
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
                width="stretch",
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
                "Run ID": _short_id(item.summary.run_id),
            }
            for item in model.recent
        ),
        width="stretch",
        hide_index=True,
    )
    selected = st.selectbox(
        "Inspect run",
        tuple(item.summary.run_id for item in model.recent),
        format_func=_short_id,
    )
    detail = next((item for item in model.recent if item.summary.run_id == selected), None)
    if detail is not None:
        run = detail.summary
        with st.expander(f"Run {_short_id(run.run_id)} details", expanded=False):
            st.markdown("**Overview**")
            st.write(f"Status: **{run.status.value}**")
            st.write(f"Full run ID: `{run.run_id}`")
            st.write(f"Capabilities: {', '.join(run.capabilities) or 'NONE'}")
            if detail.steps:
                st.markdown("**Steps**")
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
                    width="stretch",
                    hide_index=True,
                )
            if run.error_message:
                st.markdown("**Error**")
                st.error(run.error_message)
            if detail.metadata:
                st.markdown("**Technical metadata**")
                st.json(dict(detail.metadata))
