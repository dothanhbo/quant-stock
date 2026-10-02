from __future__ import annotations

from manager.charts import bar_chart, line_chart
from manager.formatting import (
    format_basis_points,
    format_count,
    format_fraction_percent,
    format_number,
    format_percent,
    format_percentage_points,
    format_ratio,
)
from manager.theme import status_badge_row_html, summary_panel_html
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
    return format_fraction_percent(value) if percentage else format_number(value)


def _percentage_points(value: float | None) -> str:
    return format_percentage_points(value)


def _risk_profile_rows(
    profiles: tuple[object, ...],
    *,
    field: str,
    label: str,
    scale: float = 1.0,
) -> tuple[dict[str, object], ...]:
    return tuple(
        {"Budget": f"Top {item.budget}", label: None if getattr(item, field) is None else getattr(item, field) * scale}
        for item in profiles
    )


def _cost_chart_rows(points: tuple[object, ...]) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for item in points:
        rate = format_basis_points(item.cost_rate_bps)
        rows.extend(
            (
                {"Hypothetical cost rate": rate, "Series": "Gross excess return", "Excess return (pp)": item.mean_gross_excess_return_pct_points},
                {"Hypothetical cost rate": rate, "Series": "Net excess return", "Excess return (pp)": item.mean_net_excess_return_pct_points},
            )
        )
    return tuple(rows)


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
    cards = st.columns(3)
    cards[0].markdown(
        summary_panel_html(
            label="Structure",
            value=(
                "UNAVAILABLE"
                if structure is None
                else f"{format_count(structure.mean_selected_count)} selected · {format_fraction_percent(structure.mean_fill_ratio)} fill"
            ),
            detail=(
                "Maximum single-name weight "
                + format_fraction_percent(None if structure is None else structure.max_single_name_weight)
                + " · effective holdings "
                + format_count(None if structure is None else structure.effective_holdings)
            ),
        ),
        unsafe_allow_html=True,
    )
    cards[1].markdown(
        summary_panel_html(
            label="Risk",
            value=format_fraction_percent(None if risk is None else risk.annualized_volatility),
            detail="Annualized volatility · beta " + format_ratio(None if risk is None else risk.benchmark_beta),
        ),
        unsafe_allow_html=True,
    )
    cards[2].markdown(
        summary_panel_html(
            label="Outcome",
            value=format_percentage_points(None if outcome is None else outcome.mean_gross_excess_return_pct_points),
            detail=(
                "Mean excess return · coverage "
                + format_percent(None if outcome is None else outcome.coverage_pct)
            ),
        ),
        unsafe_allow_html=True,
    )

    st.subheader("Risk Profile by Budget")
    if evidence.risk_profiles:
        rows = tuple(
            {
                "Budget": item.budget,
                "Annualized volatility": format_fraction_percent(item.annualized_volatility),
                "Aggregate correlation": format_ratio(item.aggregate_pairwise_correlation),
                "Benchmark beta": format_ratio(item.benchmark_beta),
                "Max component risk share": format_fraction_percent(item.maximum_component_risk_share),
                "Effective risk contributors": format_count(item.effective_risk_contributors),
            }
            for item in evidence.risk_profiles
        )
        st.dataframe(rows, hide_index=True, width="stretch")
        charts = st.columns(3)
        with charts[0]:
            st.markdown("**Annualized volatility by budget (%)**")
            st.altair_chart(bar_chart(_risk_profile_rows(evidence.risk_profiles, field="annualized_volatility", label="Volatility (%)", scale=100.0), category="Budget", value="Volatility (%)", y_title="Volatility (%)", value_format=".2f"), width="stretch")
        with charts[1]:
            st.markdown("**Aggregate pairwise correlation by budget**")
            st.altair_chart(bar_chart(_risk_profile_rows(evidence.risk_profiles, field="aggregate_pairwise_correlation", label="Correlation"), category="Budget", value="Correlation", y_title="Correlation", value_format=".3f"), width="stretch")
        with charts[2]:
            st.markdown("**Benchmark beta by budget**")
            st.altair_chart(bar_chart(_risk_profile_rows(evidence.risk_profiles, field="benchmark_beta", label="Beta"), category="Budget", value="Beta", y_title="Beta", value_format=".3f"), width="stretch")
        concentration = st.columns(2)
        with concentration[0]:
            st.markdown("**Maximum component risk share by budget (%)**")
            st.altair_chart(bar_chart(_risk_profile_rows(evidence.risk_profiles, field="maximum_component_risk_share", label="Risk share (%)", scale=100.0), category="Budget", value="Risk share (%)", y_title="Risk share (%)", value_format=".2f"), width="stretch")
        with concentration[1]:
            st.markdown("**Effective risk contributors by budget (count)**")
            st.altair_chart(bar_chart(_risk_profile_rows(evidence.risk_profiles, field="effective_risk_contributors", label="Contributors"), category="Budget", value="Contributors", y_title="Contributors", value_format=".2f"), width="stretch")
    else:
        st.warning("Risk-profile summaries are unavailable for this configuration.")

    st.subheader("Portfolio Structure")
    st.dataframe(
        ({
            "Budget": evidence.budget,
            "Mean selected count": format_count(None if structure is None else structure.mean_selected_count),
            "Mean fill ratio": format_fraction_percent(None if structure is None else structure.mean_fill_ratio),
            "Max single-name weight": format_fraction_percent(None if structure is None else structure.max_single_name_weight),
            "Effective holdings": format_count(None if structure is None else structure.effective_holdings),
            "Herfindahl concentration": format_ratio(None if structure is None else structure.herfindahl_concentration),
            "Portfolio one-way turnover": format_fraction_percent(None if structure is None else structure.one_way_turnover),
            "Risk-policy transformation turnover": format_fraction_percent(None if structure is None else structure.risk_policy_transformation_turnover),
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
                "Coverage": format_percent(outcome.coverage_pct),
                "Mean gross portfolio return": format_percent(outcome.mean_gross_return_pct),
                "Mean gross excess return": _percentage_points(outcome.mean_gross_excess_return_pct_points),
                "Positive excess rate": format_fraction_percent(outcome.positive_excess_rate),
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
                    "Coverage": format_percent(item.coverage_pct),
                    "Mean gross return": format_percent(item.mean_gross_return_pct),
                    "Mean gross excess return": _percentage_points(item.mean_gross_excess_return_pct_points),
                    "Positive excess rate": format_fraction_percent(item.positive_excess_rate),
                }
                for item in evidence.risk_policy_outcomes
            ),
            hide_index=True,
            width="stretch",
        )
    if evidence.cost_sensitivity:
        cost_rows = tuple(
            {
                "Hypothetical cost rate": format_basis_points(item.cost_rate_bps),
                "Gross excess return (pp)": item.mean_gross_excess_return_pct_points,
                "Net excess return (pp)": item.mean_net_excess_return_pct_points,
                "Mean cost deduction (pp)": item.mean_cost_deduction_pct_points,
            }
            for item in evidence.cost_sensitivity
        )
        st.markdown("**Gross and net excess return at persisted hypothetical cost rates (pp)**")
        st.altair_chart(
            line_chart(
                _cost_chart_rows(evidence.cost_sensitivity),
                category="Hypothetical cost rate",
                series="Series",
                value="Excess return (pp)",
                y_title="Excess return (pp)",
                value_format=".3f",
                zero=True,
            ),
            width="stretch",
        )
        st.dataframe(cost_rows, hide_index=True, width="stretch")
        contracts = tuple(dict.fromkeys(item.label for item in evidence.cost_sensitivity))
        st.caption("Persisted sensitivity contract(s): " + "; ".join(contracts))
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
                "Timing reference coverage": format_percent(execution.timing_coverage_pct),
                "Normalized participation coverage": format_percent(execution.capacity_coverage_pct),
                "Mean close-to-next-open reference gap": format_fraction_percent(execution.mean_reference_gap_decimal),
                "Mean adverse-direction gap": format_fraction_percent(execution.mean_adverse_gap_decimal),
                "Mean normalized participation": format_percent(execution.mean_normalized_participation_pct, digits=4),
                "P95 normalized participation": format_percent(execution.p95_normalized_participation_pct, digits=4),
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
        comparison_candidates = (
            tuple(carried.get("comparison_candidates", ())) if isinstance(carried, dict) else ()
        )
        compatible = tuple(
            item for item in comparison_candidates if item in catalog.selection_policies
        )
        if len(compatible) == 1:
            st.info(
                f"The carried comparison policy `{carried_policy}` has no persisted Phase 6 portfolio. "
                f"The separately carried compatible comparison candidate `{compatible[0]}` was preserved; "
                "the candidates were not treated as equivalent."
            )
            carried_policy = compatible[0]
        else:
            st.info(
                f"The carried comparison policy `{carried_policy}` has no persisted Phase 6 portfolio. "
                "That unsupported field was reset; no alternative policy was treated as equivalent."
            )
            carried_policy = None
    policy_index = catalog.selection_policies.index(carried_policy) if carried_policy in catalog.selection_policies else 0
    carried_budget = carried.get("budget") if isinstance(carried, dict) else None
    carried_horizon = (
        carried.get("horizon_sessions", carried.get("horizon"))
        if isinstance(carried, dict)
        else None
    )
    carried_risk = carried.get("risk_policy") if isinstance(carried, dict) else None
    if carried_budget is not None and carried_budget not in catalog.budgets:
        st.info(
            f"The carried budget `{carried_budget}` has no persisted portfolio evidence. "
            "That unsupported field was reset."
        )
    if carried_horizon is not None and carried_horizon not in catalog.horizons:
        st.info(
            f"The carried horizon `{carried_horizon}` has no persisted portfolio evidence. "
            "That unsupported field was reset."
        )
    if carried_risk is not None and carried_risk not in catalog.risk_policies:
        st.info(
            f"The carried risk policy `{carried_risk}` has no persisted portfolio evidence. "
            "That unsupported field was reset."
        )
    controls = st.columns((1.4, .75, .75, 1.35))
    policy = controls[0].selectbox("Selection policy", catalog.selection_policies, index=policy_index, format_func=_label)
    budget = controls[1].selectbox(
        "Budget", catalog.budgets,
        index=catalog.budgets.index(carried_budget) if carried_budget in catalog.budgets else 0,
        format_func=lambda value: f"Top {value}",
    )
    horizon = controls[2].selectbox(
        "Horizon", catalog.horizons,
        index=catalog.horizons.index(carried_horizon) if carried_horizon in catalog.horizons else 0,
        format_func=lambda value: f"{value} sessions",
    )
    risk_policy = controls[3].selectbox(
        "Risk-policy variant", catalog.risk_policies,
        index=catalog.risk_policies.index(carried_risk) if carried_risk in catalog.risk_policies else 0,
        format_func=_label,
    )
    st.session_state["quantlab_forward_context"] = {
        "selection_policy": policy,
        "horizon_sessions": horizon,
    }
    if isinstance(carried, dict) and carried.get("candidate_type") == "risk_policy":
        st.session_state["quantlab_decision_context"] = {
            "candidate_type": "risk_policy",
            "candidate": risk_policy,
        }
    else:
        st.session_state["quantlab_decision_context"] = {
            "candidate_type": "portfolio_configuration",
            "budget": budget,
            "horizon_sessions": horizon,
        }

    evidence = select_portfolio_risk_evidence(
        catalog,
        selection_policy=policy,
        budget=budget,
        horizon_sessions=horizon,
        risk_policy=risk_policy,
    )
    _render_evidence(evidence)
