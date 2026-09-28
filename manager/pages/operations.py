from __future__ import annotations

from manager.view_models import OperationsViewModel
from quantctl.operations import OperationResult, execute_operation


_RESULT_KEY = "quant_manager_last_operation_result"


def _display_result(result: OperationResult) -> None:
    import streamlit as st

    if result.success:
        st.success(result.message)
    else:
        st.error(result.message)
    if result.details:
        st.json(dict(result.details))
    if result.stdout:
        st.code(result.stdout, language=None)
    if result.stderr:
        st.code(result.stderr, language=None)


def render(model: OperationsViewModel) -> None:
    import streamlit as st

    st.header("Operations")
    st.warning("Operations run synchronously and only after an explicit button event.")
    for item in model.operations:
        spec = item.spec
        st.subheader(spec.name.replace("-", " ").title())
        st.write(spec.description)
        st.caption("Capabilities: " + ", ".join(capability.value for capability in spec.capabilities))
        st.write(f"Availability: **{'AVAILABLE' if item.available else 'UNAVAILABLE'}**")

        confirmed = True
        if spec.confirmation_required:
            confirmed = st.checkbox(
                "I understand this operation may modify state or call external services.",
                key=f"confirm_{spec.name}",
            )
        clicked = st.button(
            f"Run {spec.name.replace('-', ' ').title()}",
            key=f"run_{spec.name}",
            disabled=not item.available or not confirmed,
        )
        if clicked:
            st.session_state[_RESULT_KEY] = execute_operation(spec.name)
        st.divider()

    result = st.session_state.get(_RESULT_KEY)
    if result is not None:
        st.subheader("Most Recent Result")
        _display_result(result)
