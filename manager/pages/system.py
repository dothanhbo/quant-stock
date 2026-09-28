from __future__ import annotations

from itertools import groupby

from manager.view_models import SystemViewModel


def _show_status(status: str, text: str) -> None:
    import streamlit as st

    if status == "PASS":
        st.success(text)
    elif status == "FAIL":
        st.error(text)
    elif status == "WARN":
        st.warning(text)
    else:
        st.info(text)


def render(model: SystemViewModel) -> None:
    import streamlit as st

    st.header("System / Doctor")
    st.caption("Read-only diagnostics. Secret values are never displayed.")
    _show_status(model.overall_status, f"OVERALL: {model.overall_status}")
    for section, checks in groupby(model.checks, key=lambda item: item.section):
        st.subheader(section)
        for check in checks:
            detail = f": {check.detail}" if check.detail else ""
            _show_status(check.status, f"{check.status} — {check.name}{detail}")
