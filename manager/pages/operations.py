from __future__ import annotations

from manager.view_models import OperationsViewModel
from quantctl.operations import OperationResult, execute_operation


_RESULT_KEY = "quant_manager_last_operation_result"

_PRESENTATION = {
    "data-status": (
        "Data Status",
        "Inspect canonical market-data coverage without changing state.",
        ("Reads the canonical market database",),
    ),
    "update": (
        "Update Market Data",
        "Fetch and persist the latest market data using the canonical updater.",
        ("Calls an external market-data provider", "Writes the market database"),
    ),
    "scan": (
        "Run Scanner",
        "Evaluate the deployed scanner and its configured paper/notification path.",
        ("Reads current market data", "May mutate Paper state", "May send Telegram"),
    ),
    "daily": (
        "Run Daily Pipeline",
        "Run the complete end-of-day operational sequence.",
        (
            "Updates market data through an external provider",
            "Mutates Paper and Forward state",
            "Runs the scanner and may send Telegram",
        ),
    ),
}

_CAPABILITY_LABELS = {
    "READ_ONLY": "Read only",
    "LOCAL_WRITE": "Local state write",
    "MARKET_DATA_WRITE": "Market-data write",
    "PAPER_WRITE": "Paper-state write",
    "FORWARD_WRITE": "Forward-state write",
    "EXTERNAL_CALL": "External provider call",
    "TELEGRAM_SEND": "Telegram send",
}


def _display_result(result: OperationResult) -> None:
    import streamlit as st

    title = _PRESENTATION.get(result.operation, (result.operation.replace("-", " ").title(),))[0]
    if result.success:
        st.success(f"{title} completed successfully.")
    elif result.status == "CANCELLED":
        st.warning(f"{title} was cancelled.")
    else:
        st.error(f"{title} failed.")
        st.write(result.message)
    if result.run_id:
        columns = st.columns(3)
        columns[0].metric("Status", result.status or "UNKNOWN")
        columns[1].metric(
            "Duration",
            f"{result.duration_ms / 1000:.1f}s" if result.duration_ms is not None else "UNKNOWN",
        )
        columns[2].metric("Run ID", f"{result.run_id[:8]}…")
    with st.expander("Technical details", expanded=not result.success):
        st.caption(result.message)
        if result.run_id:
            st.code(result.run_id, language=None)
        if result.details:
            st.json(dict(result.details))
        if result.stdout:
            st.markdown("**Standard output**")
            st.code(result.stdout, language=None)
        if result.stderr:
            st.markdown("**Standard error**")
            st.code(result.stderr, language=None)
    if result.run_id:
        st.caption("Open Run History for the durable audit record.")


def render(model: OperationsViewModel) -> None:
    import streamlit as st

    st.header("Operations")
    st.caption("Inspect data or run controlled operational workflows with explicit confirmation.")
    result = st.session_state.get(_RESULT_KEY)
    if result is not None:
        st.subheader("Latest operation result")
        _display_result(result)
        st.divider()
    for item in model.operations:
        spec = item.spec
        title, purpose, effects = _PRESENTATION[spec.name]
        st.subheader(title)
        st.write(purpose)
        st.write("**Will:**")
        for effect in effects:
            st.write(f"- {effect}")
        human_capabilities = ", ".join(
            _CAPABILITY_LABELS[capability.value] for capability in spec.capabilities
        )
        st.caption(f"Capabilities: {human_capabilities}")
        st.write(f"Availability: **{'AVAILABLE' if item.available else 'UNAVAILABLE'}**")

        confirmed = True
        if spec.confirmation_required:
            confirmed = st.checkbox(
                f"I understand that {title} may modify state or contact external services.",
                key=f"confirm_{spec.name}",
            )
        clicked = st.button(
            f"Run {spec.name.replace('-', ' ').title()}",
            key=f"run_{spec.name}",
            disabled=not item.available or not confirmed,
        )
        if clicked:
            with st.spinner(f"Running {title}…"):
                st.session_state[_RESULT_KEY] = execute_operation(spec.name)
            st.rerun()
        st.divider()
