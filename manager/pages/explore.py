from __future__ import annotations

from manager.theme import compact_status_label, status_badge_row_html
from manager.view_models import ExploreViewModel
from quantctl.factor_explore import (
    EvidenceState,
    ExplorePopulation,
    load_daily_factor_evidence,
    select_factor_evidence,
)


_OUTCOME_LABELS = {
    "stock_forward_return_pct": "Stock forward return",
    "excess_forward_return_pct_points": "Excess return vs VNINDEX",
}


def _number(value: float | None, *, percentage: bool = False) -> str:
    if value is None:
        return "UNKNOWN"
    return f"{value:.2f}%" if percentage else f"{value:.4f}"


def _rate(value: float | None) -> str:
    return "UNKNOWN" if value is None else f"{value * 100.0:.2f}%"


def _yes_no_unknown(value: bool | None) -> str:
    if value is None:
        return "UNKNOWN"
    return "YES" if value else "NO"


def _bool_label(value: bool | None) -> str:
    if value is None:
        return "UNKNOWN"
    return "CONSISTENT" if value else "MIXED"


def render(model: ExploreViewModel) -> None:
    import streamlit as st

    st.header("Explore")
    st.caption("Does this factor or candidate show useful and stable evidence?")

    populations = tuple(item for item in model.catalog.populations if item.state is not EvidenceState.UNAVAILABLE)
    if not populations:
        st.error("Canonical factor evidence is unavailable or incompatible.")
        for item in model.catalog.populations:
            st.caption(f"{item.label}: {item.detail}")
        return

    st.markdown("**Evidence context**")
    context = st.columns((1.35, 1.25, .72, 1.25, 1.25))
    population_value = context[0].selectbox(
        "Evidence population",
        tuple(item.population.value for item in populations),
        format_func=lambda value: next(
            item.label for item in populations if item.population.value == value
        ),
    )
    population = model.catalog.population(population_value)
    assert population is not None
    factor = context[1].selectbox("Factor / candidate", population.factors)
    horizon = context[2].selectbox("Horizon", population.horizons, format_func=lambda value: f"{value} sessions")
    outcome = context[3].selectbox(
        "Outcome",
        population.outcomes,
        format_func=lambda value: _OUTCOME_LABELS.get(value, value),
    )
    available_blocks = tuple(
        dict.fromkeys(
            item.name
            for row_factor, row_horizon, row_outcome, item in population.blocks
            if (row_factor, row_horizon, row_outcome) == (factor, horizon, outcome)
        )
    )
    scope = context[4].selectbox(
        "Temporal scope",
        ("whole_period",) + available_blocks,
        format_func=lambda value: "Whole period" if value == "whole_period" else value.replace("_", " ").title(),
    )
    st.caption(f"Fixed universe: {population.universe}")

    evidence = select_factor_evidence(
        model.catalog,
        population=population_value,
        factor=factor,
        horizon_sessions=horizon,
        outcome_field=outcome,
        temporal_scope=scope,
    )
    st.session_state["quantlab_compare_context"] = {
        "family": "neutral_factors" if population.population is ExplorePopulation.NEUTRAL_PIT else None,
        "population": population.population.value,
        "factor": factor,
        "horizon": horizon,
        "outcome": outcome,
        "scope": scope,
    }
    if evidence.state is EvidenceState.UNAVAILABLE or evidence.summary is None:
        st.warning(evidence.detail)
        return

    summary = evidence.summary
    block = evidence.selected_block
    coverage = block.ic_coverage_pct if block is not None else summary.ic_coverage_pct
    mean_ic = block.mean_daily_rank_ic if block is not None else summary.mean_daily_rank_ic
    positive_spread = (
        block.positive_spread_date_rate if block is not None else summary.positive_spread_date_rate
    )
    temporal_label = block.direction if block is not None else _bool_label(evidence.temporal_support)
    cards = st.columns(5)
    cards[0].metric("Current gate / disposition", evidence.gate_disposition or "UNAVAILABLE")
    cards[0].caption("No disposition is inferred when no canonical gate link exists.")
    cards[1].metric("Defined-date coverage", _number(coverage, percentage=True))
    cards[1].caption("Share of persisted signal dates with defined rank IC.")
    cards[2].metric("Mean daily rank IC", _number(mean_ic))
    cards[2].caption("Cross-sectional association between factor rank and future return.")
    cards[3].metric("Positive spread-date rate", _rate(positive_spread))
    cards[3].caption("Share of defined dates where the high bucket exceeded the low bucket.")
    cards[4].metric("Temporal consistency", compact_status_label(temporal_label))
    cards[4].caption("Descriptive consistency across persisted calendar blocks.")

    st.subheader("Temporal Evidence Matrix")
    if not evidence.blocks:
        st.warning("Temporal block evidence is unavailable for this selection.")
    else:
        st.dataframe(
            tuple(
                {
                    "Block": item.name.replace("_", " ").title(),
                    "Dates": f"{item.start_date} → {item.end_date}",
                    "Mean rank IC": _number(item.mean_daily_rank_ic),
                    "High − low spread": _number(item.high_minus_low_spread),
                    "IC coverage": _number(item.ic_coverage_pct, percentage=True),
                    "Spread coverage": _number(item.spread_coverage_pct, percentage=True),
                    "Direction": item.direction,
                    "Missing evidence": item.undefined_reason or "—",
                }
                for item in evidence.blocks
            ),
            hide_index=True,
            width="stretch",
        )

    st.subheader("Evidence by Horizon")
    horizon_rows = tuple(
        item
        for item in population.summaries
        if item.factor == factor and item.outcome_field == outcome
    )
    st.dataframe(
        tuple(
            {
                "Horizon": f"{item.horizon_sessions} sessions",
                "Defined-date coverage": _number(item.ic_coverage_pct, percentage=True),
                "Mean rank IC": _number(item.mean_daily_rank_ic),
                "Positive spread-date rate": _rate(item.positive_spread_date_rate),
                "High − low spread": _number(item.high_minus_low_spread),
            }
            for item in horizon_rows
        ),
        hide_index=True,
        width="stretch",
    )

    if st.checkbox("Load persisted daily IC and spread detail", value=False):
        daily = load_daily_factor_evidence(
            model.catalog,
            population=population_value,
            factor=factor,
            horizon_sessions=horizon,
            outcome_field=outcome,
        )
        if not daily:
            st.warning("Daily detail is unavailable for this selection.")
        else:
            chart_rows = tuple(
                {
                    "Signal date": item.signal_date,
                    "Rank IC": item.rank_ic,
                    "High − low spread": item.high_minus_low_spread,
                }
                for item in daily
            )
            st.markdown("**Daily rank IC**")
            st.line_chart(chart_rows, x="Signal date", y="Rank IC")
            st.markdown("**Daily high-minus-low spread**")
            st.bar_chart(chart_rows, x="Signal date", y="High − low spread")

    evidence_columns = st.columns(2)
    with evidence_columns[0]:
        st.markdown("**Incremental evidence beyond frozen controls**")
        if evidence.incremental:
            st.dataframe(
                tuple(
                    {
                        "Hypothesis": item.hypothesis,
                        "Controls": item.controls,
                        "Partial IC coverage": _number(item.partial_ic_coverage_pct, percentage=True),
                        "Mean partial rank IC": _number(item.mean_partial_rank_ic),
                        "Partial − raw IC": _number(item.partial_minus_raw_rank_ic),
                        "All blocks positive": _yes_no_unknown(item.all_blocks_positive),
                    }
                    for item in evidence.incremental
                ),
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption("UNAVAILABLE — this evidence population has no canonical incremental test.")
    with evidence_columns[1]:
        st.markdown("**Redundancy / association evidence**")
        if evidence.redundancy:
            st.dataframe(
                tuple(
                    {
                        "Other factor": item.other_factor,
                        "Correlation": _number(item.correlation),
                        "Coverage": _number(item.correlation_coverage_pct, percentage=True),
                        "Scope": item.scope,
                    }
                    for item in evidence.redundancy
                ),
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption("UNAVAILABLE — no canonical pairwise evidence exists for this selection.")

    with st.expander("Provenance and limitations", expanded=False):
        st.markdown(
            status_badge_row_html(
                ("Evidence", evidence.state.value),
                ("Population", evidence.population_label),
            ),
            unsafe_allow_html=True,
        )
        st.caption(f"As of: {evidence.as_of or 'UNKNOWN'}")
        st.caption(f"Universe: {evidence.universe}")
        for identity in evidence.artifact_identities:
            st.code(f"Identity: {identity}", language=None)
        for path in evidence.artifact_paths:
            st.code(path.as_posix(), language=None)
        for limitation in evidence.limitations:
            st.caption(f"Limitation: {limitation}")
