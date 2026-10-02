from __future__ import annotations

from collections import defaultdict

from manager.theme import (
    compact_status_label,
    research_progression_html,
    summary_panel_html,
    status_badge_row_html,
)
from manager.view_models import ResearchViewModel
from quantctl.research_home import EvidenceState


_DECISION_GROUPS = (
    ("ADVANCE", "Advance"),
    ("HOLD", "Hold"),
    ("INSUFFICIENT_EVIDENCE", "Insufficient evidence"),
    ("REJECT_FOR_NOW", "Reject for now"),
)


def _forward_protocol_label(model: ResearchViewModel) -> str:
    if not model.forward.protocols:
        return "UNAVAILABLE"
    protocol = model.forward.protocols[-1]
    horizons = "/".join(str(item) for item in protocol.tracked_horizons)
    return (
        f"{protocol.selection_policy} · {protocol.weighting_policy.replace('_', ' ').lower()} · "
        f"budget {protocol.budget} · horizons {horizons}"
    )


def _forward_stage(model: ResearchViewModel) -> tuple[EvidenceState, str | None, str]:
    if not model.forward.exists or not model.forward.readable:
        return EvidenceState.UNAVAILABLE, None, "Canonical Forward state is unavailable."
    if not model.forward.protocols:
        return EvidenceState.INSUFFICIENT, model.forward.latest_activity, "No active Forward protocol."
    outcomes = model.forward.outcome_count or 0
    state = EvidenceState.AVAILABLE if outcomes > 0 else EvidenceState.INSUFFICIENT
    return (
        state,
        model.forward.latest_activity,
        f"{_forward_protocol_label(model)} · {outcomes} persisted outcomes",
    )


def render(model: ResearchViewModel) -> None:
    import streamlit as st

    frontier = model.frontier
    catalog = model.catalog
    candidate_gate_available = any(
        item.key == "candidate_gate" and item.state is EvidenceState.AVAILABLE
        for item in catalog.artifacts
    )
    all_decisions = (
        frontier.factor_decisions + frontier.policy_decisions
        if candidate_gate_available
        else ()
    )
    latest_artifact = next(
        (item for item in reversed(catalog.artifacts) if item.state is EvidenceState.AVAILABLE),
        None,
    )

    st.header("Quant Lab")
    st.caption(
        "Read-only research evidence · fixed window "
        f"{catalog.evidence_start_date} to {catalog.evidence_end_date} · "
        f"{catalog.universe_label}"
    )
    st.markdown(
        status_badge_row_html(
            ("Deployed paper", model.production.deployed_strategy_identity),
            ("Neutral control", "ADX_ONLY"),
            ("Forward protocol", _forward_protocol_label(model)),
        ),
        unsafe_allow_html=True,
    )
    st.caption(
        "Q70_FROZEN is the deployed paper baseline. ADX_ONLY is the neutral research "
        "control and also the current Forward selection policy; Forward evidence is not paper execution."
    )

    cards = st.columns(3)
    cards[0].markdown(
        summary_panel_html(
            label="Current research frontier",
            value=latest_artifact.label if latest_artifact is not None else "UNAVAILABLE",
            detail=f"As of {latest_artifact.as_of if latest_artifact is not None else 'UNKNOWN'} · {len(all_decisions)} canonical candidates",
        ),
        unsafe_allow_html=True,
    )
    cards[1].markdown(
        summary_panel_html(
            label="Production readiness",
            value=compact_status_label(frontier.readiness.readiness),
            detail="Replacement: " + compact_status_label(frontier.production_replacement.decision.value),
        ),
        unsafe_allow_html=True,
    )
    cards[2].markdown(
        summary_panel_html(
            label="Open evidence gaps",
            value=(frontier.readiness.open_gap_count if frontier.readiness.open_gap_count is not None else "UNKNOWN"),
            detail=compact_status_label(frontier.readiness.conclusion),
        ),
        unsafe_allow_html=True,
    )

    st.subheader("Research Progression")
    forward_state, forward_as_of, _forward_detail = _forward_stage(model)
    st.markdown(
        research_progression_html(
            tuple((artifact.label, artifact.state.value, artifact.as_of) for artifact in catalog.artifacts)
            + (("Forward Evidence", forward_state.value, forward_as_of),)
        ),
        unsafe_allow_html=True,
    )
    st.caption(
        "AVAILABLE means persisted evidence exists; it does not mean a candidate is approved, promoted, or production-ready."
    )

    st.subheader("Candidate Decisions")
    if not all_decisions:
        st.warning("Canonical candidate-gate evidence is unavailable.")
    else:
        grouped = defaultdict(list)
        for item in all_decisions:
            grouped[item.decision.value].append(item)
        columns = st.columns(4)
        for column, (decision, label) in zip(columns, _DECISION_GROUPS, strict=True):
            items = tuple(sorted(grouped.get(decision, ()), key=lambda item: (item.level.value, item.name)))
            with column.container(border=True):
                st.markdown(f"**{label}**")
                st.caption(f"{len(items)} canonical candidate(s)")
                if items:
                    for item in items:
                        st.markdown(f"`{item.name}`")
                else:
                    st.caption("None")
        other = tuple(
            item
            for item in all_decisions
            if item.decision.value not in {decision for decision, _label in _DECISION_GROUPS}
        )
        if other:
            st.caption(
                "Other canonical dispositions: "
                + ", ".join(f"{item.name} ({item.decision.value})" for item in other)
            )

    st.subheader("Open Readiness Gaps")
    if not frontier.readiness.available:
        st.warning("Canonical production-readiness evidence is unavailable.")
    elif not frontier.readiness.open_gaps:
        st.caption("No open gaps are represented by the canonical readiness contract.")
    else:
        by_component = defaultdict(list)
        for gap in frontier.readiness.open_gaps:
            by_component[gap.component].append(gap)
        for component in sorted(by_component):
            with st.expander(f"{component} · {len(by_component[component])} open", expanded=False):
                for gap in sorted(by_component[component], key=lambda item: item.identifier):
                    st.markdown(f"**{gap.identifier}** — {gap.state} / {gap.decision}")
                    st.caption(gap.reason)

    monitoring = catalog.monitoring
    with st.expander("Recent meaningful change", expanded=False):
        if monitoring.state is EvidenceState.AVAILABLE:
            st.info(monitoring.message)
        else:
            st.caption(monitoring.message)

    navigation = st.columns(3)
    if navigation[0].button("Explore factor evidence", type="secondary", width="stretch"):
        st.session_state["_quant_manager_nav_request"] = "Explore"
        st.rerun()
    if navigation[1].button("Compare candidates", type="secondary", width="stretch"):
        st.session_state["_quant_manager_nav_request"] = "Compare"
        st.rerun()
    if navigation[2].button("Open Decision Gate", type="secondary", width="stretch"):
        st.session_state["_quant_manager_nav_request"] = "Decision Gate"
        st.rerun()

    with st.expander("Provenance and limitations", expanded=False):
        st.markdown("**Canonical artifact evidence**")
        for artifact in catalog.artifacts:
            st.markdown(
                status_badge_row_html((artifact.label, artifact.state.value)),
                unsafe_allow_html=True,
            )
            st.caption(f"As of: {artifact.as_of or 'UNKNOWN'}")
            st.code(f"Identity: {artifact.result_identity or 'UNAVAILABLE'}", language=None)
            st.code(artifact.root_path.as_posix(), language=None)
            for limitation in artifact.limitations:
                st.caption(f"Limitation: {limitation}")
        st.markdown("**Monitoring history**")
        st.code(f"Identity: {monitoring.result_identity or 'UNAVAILABLE'}", language=None)
        st.code(monitoring.source_path.as_posix(), language=None)
        st.caption(f"Compatible persisted snapshots: {monitoring.compatible_snapshot_count}")
        st.caption(f"Persisted transition records: {monitoring.transition_count}")
        for limitation in monitoring.limitations:
            st.caption(f"Limitation: {limitation}")
        st.markdown("**Operational sources**")
        st.code(f"Paper policy: {model.production.source_evidence}", language=None)
        st.code(f"Paper store: {model.production.database_path.as_posix()}", language=None)
        st.code(f"Forward state: {model.forward.database_path.as_posix()}", language=None)
        for limitation in frontier.readiness.limitations:
            st.caption(f"Limitation: {limitation}")
        for warning in frontier.warnings:
            st.warning(warning)
