from __future__ import annotations

from manager.view_models import ResearchViewModel


def render(model: ResearchViewModel) -> None:
    import streamlit as st

    st.header("Research")
    st.info("READ-ONLY — Research execution is not enabled in M1.5.")
    query = st.text_input("Filter active runners", placeholder="Name or path").strip().lower()
    visible = tuple(
        runner
        for runner in model.runners
        if not query or query in runner.short_name.lower() or query in runner.path.as_posix().lower()
    )
    st.caption(f"Showing {len(visible)} of {len(model.runners)} active Quant Lab runners.")
    for runner in visible:
        with st.expander(runner.short_name, expanded=False):
            st.code(runner.path.as_posix(), language=None)
            st.write(f"Module: `{runner.module}`")
            st.write(f"Availability: **{'AVAILABLE' if runner.available else 'UNAVAILABLE'}**")
            if runner.error:
                st.warning(runner.error)
