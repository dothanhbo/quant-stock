from __future__ import annotations

from manager.view_models import ResearchViewModel


def render(model: ResearchViewModel) -> None:
    import streamlit as st

    st.header("Research")
    st.caption("Read-only conclusions from canonical Quant Lab evidence. No research runner is executed.")
    frontier = model.frontier
    st.subheader("Research Frontier")
    columns = st.columns(3)
    columns[0].metric("Framework", frontier.framework)
    columns[1].metric("Current stage", frontier.latest_stage)
    columns[2].metric("As of", frontier.as_of or "UNKNOWN")
    st.subheader("Production Replacement Status")
    st.metric("Decision", frontier.production_replacement.decision.value)
    st.caption(frontier.production_replacement.note)
    st.info("Research decisions do not change the deployed production policy.")

    def decision_table(title, decisions) -> None:
        st.subheader(title)
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
                }
                for item in decisions
            ),
            width="stretch",
            hide_index=True,
        )

    decision_table("Factor Decisions", frontier.factor_decisions)
    decision_table("Policy Decisions", frontier.policy_decisions)
    decision_table("Portfolio / Risk / Decision Gate", frontier.portfolio_risk_decisions)
    for warning in frontier.warnings:
        st.warning(warning)

    with st.expander("Evidence and Active Quant Lab Runners", expanded=False):
        st.write(f"Generation identity: `{frontier.generation_identity}`")
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
