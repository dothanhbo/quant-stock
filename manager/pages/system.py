from __future__ import annotations

from itertools import groupby

from manager.view_models import SystemViewModel
from manager.theme import status_badge_html


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
    st.caption("Read-only health checks. Secret values are never displayed and no repair is attempted.")
    st.markdown(
        status_badge_html(model.overall_status, label="Overall"),
        unsafe_allow_html=True,
    )
    passed = sum(item.status == "PASS" for item in model.checks)
    warnings = sum(item.status == "WARN" for item in model.checks)
    failures = sum(item.status == "FAIL" for item in model.checks)
    unknown = len(model.checks) - passed - warnings - failures
    columns = st.columns(4)
    columns[0].metric("Passed", passed)
    columns[1].metric("Warnings", warnings)
    columns[2].metric("Failed", failures)
    columns[3].metric("Unknown", unknown)
    st.caption("Detailed checks are grouped below; warning and failure meaning is never color-only.")
    for section, checks in groupby(model.checks, key=lambda item: item.section):
        rows = tuple(checks)
        failures = sum(item.status == "FAIL" for item in rows)
        warnings = sum(item.status == "WARN" for item in rows)
        label = {
            "Market Data": "Data",
            "Persistent State": "Paper / Forward",
        }.get(section, section)
        st.subheader(label)
        st.caption(f"{len(rows)} checks · {failures} failed · {warnings} warnings")
        with st.expander(f"{label} details", expanded=bool(failures)):
            for check in rows:
                detail = f": {check.detail}" if check.detail else ""
                _show_status(check.status, f"{check.status} — {check.name}{detail}")
