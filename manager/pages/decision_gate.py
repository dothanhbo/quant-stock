from __future__ import annotations

from manager.theme import (
    compact_status_label,
    decision_panel_html,
    status_badge_row_html,
    summary_panel_html,
)
from manager.view_models import DecisionGateViewModel
from quantctl.decision_gate import (
    DecisionCandidateType,
    DecisionGateCandidate,
    resolve_decision_context,
)


def _label(value: object) -> str:
    return str(value).replace("_", " ").title()


def _request_page(streamlit: object, page: str, *, context_key: str, context: dict[str, object]) -> None:
    streamlit.session_state[context_key] = context
    streamlit.session_state["_quant_manager_nav_request"] = page
    streamlit.rerun()


def _support_page(candidate: DecisionGateCandidate) -> tuple[str, str, dict[str, object]]:
    if candidate.candidate_type is DecisionCandidateType.FACTOR:
        return "Explore", "quantlab_explore_context", {
            "candidate_type": "factor", "factor": candidate.name,
        }
    if candidate.candidate_type is DecisionCandidateType.SELECTION_POLICY:
        return "Compare", "quantlab_compare_context", {
            "candidate_type": "selection_policy",
            "family": "frozen_policies",
            "selection_policy": candidate.name,
        }
    if candidate.candidate_type is DecisionCandidateType.PORTFOLIO_CONFIGURATION:
        _kind, budget, horizon = candidate.key.split(":")
        return "Portfolio & Risk", "quantlab_portfolio_context", {
            "candidate_type": "portfolio_configuration",
            "selection_policy": "ADX_ONLY",
            "budget": int(budget),
            "horizon_sessions": int(horizon),
        }
    return "Portfolio & Risk", "quantlab_portfolio_context", {
        "candidate_type": "risk_policy",
        "risk_policy": candidate.name,
    }


def _decision_context(candidate: DecisionGateCandidate) -> dict[str, object]:
    if candidate.candidate_type is DecisionCandidateType.FACTOR:
        return {"candidate_type": "factor", "candidate": candidate.name}
    if candidate.candidate_type is DecisionCandidateType.SELECTION_POLICY:
        return {"candidate_type": "selection_policy", "candidate": candidate.name}
    if candidate.candidate_type is DecisionCandidateType.PORTFOLIO_CONFIGURATION:
        _kind, budget, horizon = candidate.key.split(":")
        return {
            "candidate_type": "portfolio_configuration",
            "budget": int(budget),
            "horizon_sessions": int(horizon),
        }
    return {"candidate_type": "risk_policy", "candidate": candidate.name}


def _render_candidate(model: DecisionGateViewModel, candidate: DecisionGateCandidate) -> None:
    import streamlit as st

    catalog = model.catalog
    st.subheader("Canonical Disposition")
    st.markdown(
        status_badge_row_html(
            ("Candidate type", candidate.candidate_type.value),
            ("Evidence", "PERSISTED"),
        ),
        unsafe_allow_html=True,
    )
    panels = st.columns((1.35, 1))
    panels[0].markdown(
        decision_panel_html(
            label=candidate.display_name,
            value=candidate.disposition,
            detail=f"{candidate.research_stage} · as of {candidate.as_of or 'UNKNOWN'}",
        ),
        unsafe_allow_html=True,
    )
    panels[1].markdown(
        summary_panel_html(
            label="Production replacement — independent contract",
            value=compact_status_label(catalog.production_replacement.decision.value),
            detail=catalog.production_replacement.note,
        ),
        unsafe_allow_html=True,
    )
    st.caption(candidate.decision_reason)
    st.caption(
        "Contract-bound next step: "
        + (candidate.contract_bound_next_step or "NONE RECORDED")
        + ". This persisted code is not a new recommendation."
    )

    supportive = sum(item.status == "SUPPORTIVE" for item in candidate.dimensions)
    adverse = sum(item.status == "ADVERSE" for item in candidate.dimensions)
    mixed = sum(item.status in {"MIXED", "TRADE_OFF_PRESENT"} for item in candidate.dimensions)
    unavailable = sum(item.availability == "UNAVAILABLE" or item.status == "INSUFFICIENT" for item in candidate.dimensions)
    st.subheader("Evidence State Summary")
    compact = st.columns(4)
    compact[0].metric("Supportive", supportive)
    compact[1].metric("Adverse", adverse)
    compact[2].metric("Mixed / trade-off", mixed)
    compact[3].metric("Unavailable / insufficient", unavailable)
    st.caption("Counts reproduce persisted dimension states; they are not combined into a score.")

    st.subheader("Evidence Dimension Matrix")
    if not candidate.dimensions:
        st.warning("UNAVAILABLE — no compatible persisted evidence dimensions were found.")
    else:
        st.dataframe(
            tuple(
                {
                    "Dimension": _label(item.dimension),
                    "Persisted status": item.status,
                    "Persisted rationale": item.rationale,
                    "Supporting source": item.supporting_source.name,
                    "Availability": item.availability,
                }
                for item in candidate.dimensions
            ),
            hide_index=True,
            width="stretch",
        )

    st.subheader("Blockers and Limitations")
    if candidate.blockers:
        st.error("Adverse persisted dimensions: " + ", ".join(map(_label, candidate.blockers)))
    else:
        st.caption("No adverse evidence dimension is persisted for this candidate.")
    if candidate.insufficient_dimensions:
        st.warning(
            "Insufficient or unavailable dimensions: "
            + ", ".join(map(_label, candidate.insufficient_dimensions))
        )
    if candidate.mixed_or_uncertain_dimensions:
        st.info(
            "Mixed, neutral, trade-off, or non-applicable dimensions: "
            + ", ".join(map(_label, candidate.mixed_or_uncertain_dimensions))
        )
    for limitation in candidate.limitations:
        st.caption(f"Limitation: {limitation}")

    st.subheader("Production Readiness — Separate Operational Gate")
    st.markdown(
        status_badge_row_html(
            ("Deployed", catalog.production.deployed_strategy_identity),
            ("Replacement", catalog.production_replacement.decision.value),
            ("Readiness", catalog.readiness.readiness),
        ),
        unsafe_allow_html=True,
    )
    st.caption(
        "Research disposition and production readiness are independent contracts. "
        "An operational gap is not automatically a candidate rejection."
    )
    readiness_cards = st.columns(3)
    readiness_cards[0].metric("Currently deployed", catalog.production.deployed_strategy_identity)
    readiness_cards[0].caption(f"{catalog.production.role} · {catalog.production.active_store}")
    readiness_cards[1].metric(
        "Production replacement",
        compact_status_label(catalog.production_replacement.decision.value),
    )
    readiness_cards[1].caption(catalog.production_replacement.note)
    readiness_cards[2].metric(
        "Open operational gaps",
        catalog.readiness.open_gap_count
        if catalog.readiness.open_gap_count is not None
        else "UNKNOWN",
    )
    readiness_cards[2].caption(catalog.readiness.conclusion)
    if catalog.readiness.open_gaps:
        with st.expander("Open operational readiness gaps", expanded=False):
            for gap in catalog.readiness.open_gaps:
                st.markdown(f"**{gap.identifier}** — {gap.state} / {gap.decision}")
                st.caption(gap.reason)

    st.subheader("Supporting Evidence Navigation")
    destination, context_key, context = _support_page(candidate)
    columns = st.columns(2)
    if columns[0].button(f"Open {destination}", type="secondary", width="stretch"):
        _request_page(st, destination, context_key=context_key, context=context)
    forward_compatible = (
        candidate.candidate_type is DecisionCandidateType.PORTFOLIO_CONFIGURATION
        and candidate.contract_bound_next_step == "ADVANCE_TO_FORWARD_VALIDATION"
    )
    if forward_compatible:
        _kind, budget, horizon = candidate.key.split(":")
        if columns[1].button("Open Forward Evidence", type="secondary", width="stretch"):
            _request_page(
                st,
                "Forward Evidence",
                context_key="quantlab_forward_context",
                context={
                    "candidate_type": "portfolio_configuration",
                    "selection_policy": "ADX_ONLY",
                    "budget": int(budget),
                    "horizon_sessions": int(horizon),
                },
            )
    else:
        columns[1].caption("Forward Evidence navigation is unavailable for this candidate context.")

    with st.expander("Provenance and technical identities", expanded=False):
        st.write(f"Evidence population: {candidate.evidence_population}")
        st.write(f"As of: `{candidate.as_of or 'UNKNOWN'}`")
        st.code(f"Candidate identity: {candidate.candidate_identity or 'UNAVAILABLE'}", language=None)
        st.code(f"Decision identity: {candidate.decision_identity or 'UNAVAILABLE'}", language=None)
        st.code(f"Artifact identity: {candidate.artifact_identity or 'UNAVAILABLE'}", language=None)
        st.code(candidate.manifest_path.as_posix(), language=None)
        st.code(candidate.summary_path.as_posix(), language=None)
        st.code(candidate.evidence_path.as_posix(), language=None)
        for item in candidate.dimensions:
            st.caption(
                f"{item.dimension}: reason `{item.reason_code}` · {item.supporting_source.as_posix()}"
            )


def render(model: DecisionGateViewModel) -> None:
    import streamlit as st

    st.header("Decision Gate")
    st.caption("What does the existing evidence establish, what remains uncertain, and what prevents this research candidate from advancing?")
    st.caption("Persisted canonical decisions only — no score, ranking, approval, or promotion is generated here.")
    st.info(
        "Evidence boundaries: Q70_FROZEN is the deployed paper baseline; ADX_ONLY is a neutral "
        "research control where represented; historical returns are retrospective descriptions; "
        "paper PnL is simulated execution/accounting evidence; Forward outcomes are research "
        "observations, not portfolio PnL."
    )

    catalog = model.catalog
    carried = st.session_state.get("quantlab_decision_context", {})
    resolution = resolve_decision_context(catalog, carried if isinstance(carried, dict) else None)
    if resolution.notice:
        st.info(resolution.notice)

    available_types = tuple(
        candidate_type
        for candidate_type in DecisionCandidateType
        if catalog.candidates_for(candidate_type)
    )
    if not available_types:
        st.error("UNAVAILABLE — no compatible canonical decision artifacts are available.")
        for artifact in catalog.artifacts:
            st.caption(f"{artifact.family}: {artifact.state.value} — {artifact.detail}")
        return

    preferred_type = resolution.candidate.candidate_type if resolution.candidate else available_types[0]
    controls = st.columns((1, 1.6))
    candidate_type = controls[0].selectbox(
        "Candidate type",
        available_types,
        index=available_types.index(preferred_type),
        format_func=lambda value: value.value,
    )
    options = catalog.candidates_for(candidate_type)
    preferred_key = (
        resolution.candidate.key
        if resolution.candidate is not None and resolution.candidate.candidate_type is candidate_type
        else None
    )
    index = next((i for i, item in enumerate(options) if item.key == preferred_key), 0)
    selected_key = controls[1].selectbox(
        "Persisted candidate",
        tuple(item.key for item in options),
        index=index,
        format_func=lambda key: next(item.display_name for item in options if item.key == key),
    )
    candidate = catalog.candidate(selected_key)
    assert candidate is not None
    st.session_state["quantlab_decision_context"] = _decision_context(candidate)
    _render_candidate(model, candidate)


__all__ = ("render",)
