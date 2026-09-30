from __future__ import annotations

from manager.view_models import RunHistoryViewModel
from manager.theme import status_badge_html
from quantctl.run_history import RunDisplayStatus, classify_run_for_display


def _duration(value: int | None) -> str:
    return "UNKNOWN" if value is None else f"{value / 1000:.1f}s"


def _short_id(value: str) -> str:
    return f"{value[:8]}…" if len(value) > 8 else value


def _attention(status: RunDisplayStatus) -> str:
    if status is RunDisplayStatus.STALE_RUNNING:
        return "INSPECT BEFORE RETRY"
    if status is RunDisplayStatus.FAILED:
        return "REVIEW FAILURE"
    if status is RunDisplayStatus.RUNNING:
        return "IN PROGRESS"
    return "NONE"


def render(model: RunHistoryViewModel) -> None:
    import streamlit as st

    st.header("Run History")
    st.caption("Read-only audit trail for controlled operations.")
    with st.expander("Status meanings", expanded=False):
        st.write("**COMPLETED** — operation finished successfully.")
        st.write("**FAILED** — operation finished with an error; review its failed step.")
        st.write("**RUNNING** — persisted as active and started on the current UTC date.")
        st.write(
            "**STALE_RUNNING** — persisted as active from a prior UTC date; history is not auto-repaired."
        )
    if model.warning:
        st.warning(model.warning)
    st.subheader("Latest Operational Run")
    if model.latest is None:
        st.caption("No operational runs recorded.")
    else:
        run = model.latest.summary
        display_status = classify_run_for_display(run)
        st.markdown(
            status_badge_html(display_status.value, label="Latest status"),
            unsafe_allow_html=True,
        )
        columns = st.columns(2)
        columns[0].metric("Operation", run.operation.upper())
        columns[1].metric("Status", display_status.value)
        columns = st.columns(2)
        columns[0].metric("Duration", _duration(run.duration_ms))
        columns[1].metric("Exit code", run.exit_code if run.exit_code is not None else "UNKNOWN")
        st.caption(f"Attention: {_attention(display_status)}")
        st.caption(f"Started: {run.started_at_utc} · Finished: {run.finished_at_utc or 'UNKNOWN'}")
        st.caption(f"Run ID: {_short_id(run.run_id)}")
        if display_status is RunDisplayStatus.STALE_RUNNING:
            st.warning(
                "This run is still persisted as RUNNING but began on a prior UTC date. "
                "History was not modified; inspect the operation before retrying."
            )
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
                "Status": classify_run_for_display(item.summary).value,
                "Attention": _attention(classify_run_for_display(item.summary)),
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
        display_status = classify_run_for_display(run)
        with st.expander(f"Run {_short_id(run.run_id)} details", expanded=False):
            st.markdown("**Overview**")
            st.write(f"Status: **{display_status.value}**")
            st.write(f"Persisted status: **{run.status.value}**")
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
