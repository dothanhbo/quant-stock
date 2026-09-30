from __future__ import annotations

from manager.view_models import ResearchViewModel
from manager.theme import compact_status_label, status_badge_row_html


def render(model: ResearchViewModel) -> None:
    import streamlit as st

    st.header("Research")
    st.caption("Read-only conclusions from canonical Quant Lab evidence. No research runner is executed.")
    frontier = model.frontier
    st.subheader("Research Frontier")
    columns = st.columns(2)
    columns[0].metric("Framework", frontier.framework)
    columns[1].metric("As of", frontier.as_of or "UNKNOWN")
    st.metric("Current stage", frontier.latest_stage)
    st.subheader("Current Decision and Production Readiness")
    st.markdown(
        status_badge_row_html(
            ("Replacement", frontier.production_replacement.decision.value),
            ("Readiness", frontier.readiness.readiness),
        ),
        unsafe_allow_html=True,
    )
    readiness_columns = st.columns(2)
    readiness_columns[0].metric(
        "Production replacement",
        compact_status_label(frontier.production_replacement.decision.value),
    )
    readiness_columns[1].metric("Readiness", compact_status_label(frontier.readiness.readiness))
    readiness_columns = st.columns(2)
    readiness_columns[0].metric("Gap conclusion", frontier.readiness.conclusion)
    readiness_columns[1].metric(
        "Open gaps",
        frontier.readiness.open_gap_count
        if frontier.readiness.open_gap_count is not None
        else "UNKNOWN",
    )
    st.caption(frontier.production_replacement.note)
    st.info("Research decisions are read-only and do not change the deployed production policy.")

    def decision_table(title, decisions) -> None:
        with st.expander(title, expanded=False):
            if not decisions:
                st.warning("Canonical artifact evidence is unavailable.")
                return
            st.dataframe(
                tuple(
                    {
                        "Item": item.name,
                        "Level": item.level.value,
                        "Decision": item.decision.value,
                        "As of": item.as_of or "UNKNOWN",
                        "Evidence / limitation": item.note or "UNSPECIFIED",
                        "Source": item.source_artifact.as_posix() if item.source_artifact else "UNAVAILABLE",
                    }
                    for item in decisions
                ),
                width="stretch",
                hide_index=True,
            )

    st.subheader("Supporting Evidence")
    decision_table("Factor Decisions", frontier.factor_decisions)
    decision_table("Policy Decisions", frontier.policy_decisions)
    decision_table("Portfolio / Risk / Decision Gate", frontier.portfolio_risk_decisions)

    st.subheader("Limitations and Open Gaps")
    if not frontier.readiness.available:
        st.warning("Canonical production readiness is unavailable.")
    elif frontier.readiness.open_gaps:
        st.dataframe(
            tuple(
                {
                    "Gap": item.identifier,
                    "Component": item.component,
                    "Severity": item.severity,
                    "State": item.state,
                    "Decision": item.decision,
                    "Evidence-bound limitation": item.reason,
                }
                for item in frontier.readiness.open_gaps
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.caption("No open gaps are represented by the canonical readiness contract.")
    for limitation in frontier.readiness.limitations:
        st.caption(f"Limitation: {limitation}")
    for warning in frontier.warnings:
        st.warning(warning)

    with st.expander("Artifact provenance and Active Quant Lab Runners", expanded=False):
        st.write(f"Generation identity: `{frontier.generation_identity}`")
        st.write(f"Readiness identity: `{frontier.readiness.evidence_identity}`")
        st.write(f"Readiness source: `{frontier.readiness.source_reference}`")
        st.markdown("**Canonical artifact sources**")
        if frontier.source_artifacts:
            for path in frontier.source_artifacts:
                st.code(path.as_posix(), language=None)
        else:
            st.caption("Canonical artifact sources are unavailable.")
        query = st.text_input("Filter active runners", placeholder="Name or path").strip().lower()
        visible = tuple(
            runner
            for runner in model.runners
            if not query or query in runner.short_name.lower() or query in runner.path.as_posix().lower()
        )
        st.caption(f"Showing {len(visible)} of {len(model.runners)} active Quant Lab runners.")
        for runner in visible:
            st.markdown(f"**{runner.short_name}** — {'AVAILABLE' if runner.available else 'UNAVAILABLE'}")
            st.code(runner.path.as_posix(), language=None)
            if runner.error:
                st.warning(runner.error)
