from __future__ import annotations

from manager.theme import status_badge_row_html
from manager.view_models import PortfolioRiskViewModel
from quantctl.portfolio_risk import (
    PortfolioEvidenceState,
    PortfolioRiskEvidence,
    select_portfolio_risk_evidence,
)


_POLICY_LABELS = {
    "ADX_ONLY": "ADX selection policy",
    "NO_RISK_POLICY": "No risk policy / control",
    "VOLATILITY_SCALING": "Volatility scaling",
}


def _label(value: str) -> str:
    return _POLICY_LABELS.get(value, value.replace("_", " ").title())


def _number(value: float | None, *, percentage: bool = False) -> str:
    if value is None:
        return "UNAVAILABLE"
    return f"{value * 100.0:.2f}%" if percentage else f"{value:.4f}"


def _percentage_points(value: float | None) -> str:
    return "UNAVAILABLE" if value is None else f"{value:.4f} pp"


def _render_evidence(evidence: PortfolioRiskEvidence) -> None:
    import streamlit as st

    st.markdown(
        status_badge_row_html(
            ("Evidence", evidence.state.value),
            ("Selection", evidence.selection_policy),
            ("Risk policy", evidence.risk_policy),
        ),
        unsafe_allow_html=True,
    )
    if evidence.state in {PortfolioEvidenceState.UNAVAILABLE, PortfolioEvidenceState.INCOMPATIBLE}:
        method = st.error if evidence.state is PortfolioEvidenceState.INCOMPATIBLE else st.warning
        method(evidence.detail)
        return

    structure, risk, outcome = evidence.structure, evidence.risk, evidence.outcome
    cards = st.columns(6)
    cards[0].metric(
        "Selected / fill ratio",
        "UNAVAILABLE" if structure is None else f"{_number(structure.mean_selected_count)} / {_number(structure.mean_fill_ratio, percentage=True)}",
    )
    cards[0].caption("Whole-period mean selected count and requested-budget fill.")
    cards[1].metric("Max single-name weight", _number(None if structure is None else structure.max_single_name_weight, percentage=True))
    cards[1].caption("Persisted mean maximum weight for the selected risk-policy variant.")
    cards[2].metric("Effective holdings", _number(None if structure is None else structure.effective_holdings))
    cards[2].caption("Inverse concentration measure; not a sector-diversification claim.")
    cards[3].metric("Annualized volatility", _number(None if risk is None else risk.annualized_volatility, percentage=True))
    cards[3].caption("Historical descriptive estimate, not a production limit.")
    cards[4].metric("Benchmark beta", _number(None if risk is None else risk.benchmark_beta))
    cards[4].caption("Unavailable after scaling unless directly persisted.")
    cards[5].metric("Cost-adjusted excess evidence", "CURVE AVAILABLE" if evidence.cost_sensitivity else "UNAVAILABLE")
    cards[5].caption("No single hypothetical cost rate is promoted as canonical.")

    st.subheader("Risk Profile by Budget")
    if evidence.risk_profiles:
        rows = tuple(
            {
                "Budget": item.budget,
                "Annualized volatility": item.annualized_volatility,
                "Aggregate correlation": item.aggregate_pairwise_correlation,
                "Benchmark beta": item.benchmark_beta,
                "Max component risk share": item.maximum_component_risk_share,
                "Effective risk contributors": item.effective_risk_contributors,
            }
            for item in evidence.risk_profiles
        )
        st.dataframe(rows, hide_index=True, width="stretch")
        charts = st.columns(2)
        with charts[0]:
            st.markdown("**Volatility, aggregate correlation, and beta**")
            st.bar_chart(rows, x="Budget", y=("Annualized volatility", "Aggregate correlation", "Benchmark beta"))
        with charts[1]:
            st.markdown("**Risk concentration**")
            st.bar_chart(rows, x="Budget", y=("Max component risk share", "Effective risk contributors"))
    else:
        st.warning("Risk-profile summaries are unavailable for this configuration.")

    st.subheader("Portfolio Structure")
    st.dataframe(
        ({
            "Budget": evidence.budget,
            "Mean selected count": _number(None if structure is None else structure.mean_selected_count),
            "Mean fill ratio": _number(None if structure is None else structure.mean_fill_ratio, percentage=True),
            "Max single-name weight": _number(None if structure is None else structure.max_single_name_weight, percentage=True),
            "Effective holdings": _number(None if structure is None else structure.effective_holdings),
            "Herfindahl concentration": _number(None if structure is None else structure.herfindahl_concentration),
            "Portfolio one-way turnover": _number(None if structure is None else structure.one_way_turnover),
            "Risk-policy transformation turnover": _number(None if structure is None else structure.risk_policy_transformation_turnover),
        },),
        hide_index=True,
        width="stretch",
    )
    st.caption("Point-in-time sector concentration: UNAVAILABLE — canonical historical sector identity is absent.")

    st.subheader("Outcome and Hypothetical Cost Evidence")
    if outcome is None:
        st.warning("Compatible persisted outcome evidence is unavailable.")
    else:
        st.dataframe(
            ({
                "Horizon": f"{evidence.horizon_sessions} sessions",
                "Coverage": _number(outcome.coverage_pct),
                "Mean gross portfolio return": _percentage_points(outcome.mean_gross_return_pct),
                "Mean gross excess return": _percentage_points(outcome.mean_gross_excess_return_pct_points),
                "Positive excess rate": _number(outcome.positive_excess_rate, percentage=True),
            },),
            hide_index=True,
            width="stretch",
        )
    if evidence.risk_policy_outcomes:
        st.markdown("**Persisted risk-policy outcome comparison**")
        st.dataframe(
            tuple(
                {
                    "Risk policy": _label(item.risk_policy),
                    "Coverage": _number(item.coverage_pct),
                    "Mean gross return": _percentage_points(item.mean_gross_return_pct),
                    "Mean gross excess return": _percentage_points(item.mean_gross_excess_return_pct_points),
                    "Positive excess rate": _number(item.positive_excess_rate, percentage=True),
                }
                for item in evidence.risk_policy_outcomes
            ),
            hide_index=True,
            width="stretch",
        )
    if evidence.cost_sensitivity:
        cost_rows = tuple(
            {
                "Hypothetical cost rate (bps)": item.cost_rate_bps,
                "Gross excess return (pp)": item.mean_gross_excess_return_pct_points,
                "Net excess return (pp)": item.mean_net_excess_return_pct_points,
                "Mean cost deduction (pp)": item.mean_cost_deduction_pct_points,
                "Persisted sensitivity contract": item.label,
            }
            for item in evidence.cost_sensitivity
        )
        st.line_chart(cost_rows, x="Hypothetical cost rate (bps)", y=("Gross excess return (pp)", "Net excess return (pp)"))
        st.dataframe(cost_rows, hide_index=True, width="stretch")
        st.warning("Hypothetical cost sensitivity is not a realized execution-cost estimate.")
    else:
        st.caption("Cost sensitivity: UNAVAILABLE for this exact configuration.")

    st.subheader("Execution Evidence")
    execution = evidence.execution
    if execution is None:
        st.warning("Timing and normalized participation evidence is unavailable.")
    else:
        st.dataframe(
            ({
                "Timing reference coverage": _number(execution.timing_coverage_pct),
                "Normalized participation coverage": _number(execution.capacity_coverage_pct),
                "Mean close-to-next-open reference gap": _number(execution.mean_reference_gap_decimal, percentage=True),
                "Mean adverse-direction gap": _number(execution.mean_adverse_gap_decimal, percentage=True),
                "Mean normalized participation": _number(execution.mean_normalized_participation_pct),
                "P95 normalized participation": _number(execution.p95_normalized_participation_pct),
            },),
            hide_index=True,
            width="stretch",
        )
        st.warning(
            "PARTIAL — normalized volume participation is scale-linear descriptive evidence, "
            "not monetary capacity, fill probability, spread, queue, or market-impact evidence."
        )

    st.markdown("**Execution capability checklist**")
    if evidence.execution_capabilities:
        st.dataframe(
            tuple(
                {
                    "Area": item.area,
                    "Capability": item.capability.replace("_", " ").title(),
                    "State": item.state,
                    "Evidence": item.evidence,
                    "Limitation": item.limitation,
                }
                for item in evidence.execution_capabilities
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("Execution capability evidence is unavailable.")

    st.subheader("Evidence Availability")
    st.dataframe(
        tuple(
            {"Dimension": item.name, "State": item.state.value, "Interpretation boundary": item.detail}
            for item in evidence.dimensions
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption("Aggregate correlation is not a pairwise correlation matrix; no heatmap is rendered.")
    st.caption("Beta and correlation are historical descriptive evidence, not production limits.")

    with st.expander("Provenance and limitations", expanded=False):
        st.caption(evidence.detail)
        for item in evidence.provenance:
            st.markdown(status_badge_row_html((item.family, item.state.value)), unsafe_allow_html=True)
            st.caption(f"As of: {item.as_of or 'UNKNOWN'}")
            st.code(f"Identity: {item.result_identity or 'UNAVAILABLE'}", language=None)
            st.code(item.path.as_posix(), language=None)
        for limitation in evidence.limitations:
            st.caption(f"Limitation: {limitation}")


def render(model: PortfolioRiskViewModel) -> None:
    import streamlit as st

    st.header("Portfolio & Risk")
    st.caption("What portfolio-level consequences does this persisted selection create?")
    st.caption("Descriptive persisted evidence only — no portfolio is reconstructed or optimized.")

    catalog = model.catalog
    if not catalog.selection_policies or not catalog.budgets or not catalog.horizons or not catalog.risk_policies:
        st.error("UNAVAILABLE — required canonical portfolio/risk summaries are missing or incompatible.")
        for item in catalog.provenance:
            if item.state is PortfolioEvidenceState.UNAVAILABLE:
                st.caption(f"{item.family}: {item.detail}")
        return

    carried = st.session_state.get("quantlab_portfolio_context", {})
    carried_policy = carried.get("selection_policy") if isinstance(carried, dict) else None
    if carried_policy and carried_policy not in catalog.selection_policies:
        st.info(
            f"The carried comparison policy `{carried_policy}` has no persisted Phase 6 portfolio. "
            "That field was reset; no alternative policy was treated as equivalent."
        )
        carried_policy = None
    policy_index = catalog.selection_policies.index(carried_policy) if carried_policy in catalog.selection_policies else 0
    controls = st.columns((1.4, .75, .75, 1.35))
    policy = controls[0].selectbox("Selection policy", catalog.selection_policies, index=policy_index, format_func=_label)
    budget = controls[1].selectbox("Budget", catalog.budgets, format_func=lambda value: f"Top {value}")
    horizon = controls[2].selectbox("Horizon", catalog.horizons, format_func=lambda value: f"{value} sessions")
    risk_policy = controls[3].selectbox("Risk-policy variant", catalog.risk_policies, format_func=_label)

    evidence = select_portfolio_risk_evidence(
        catalog,
        selection_policy=policy,
        budget=budget,
        horizon_sessions=horizon,
        risk_policy=risk_policy,
    )
    _render_evidence(evidence)
