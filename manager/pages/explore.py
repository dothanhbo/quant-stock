from __future__ import annotations

from manager.charts import bar_chart, line_chart
from manager.formatting import (
    format_fraction_percent,
    format_ic,
    format_percent,
    format_percentage_points,
    format_temporal_block,
)
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
    return format_percent(value) if percentage else format_ic(value)


def _rate(value: float | None) -> str:
    return "UNKNOWN" if value is None else format_fraction_percent(value)


def _temporal_ic_rows(blocks: tuple[object, ...]) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "Block": format_temporal_block(item.name),
            "Mean daily rank IC": item.mean_daily_rank_ic,
        }
        for item in blocks
        if item.mean_daily_rank_ic is not None
    )


def _descriptive_temporal_summary(blocks: tuple[object, ...]) -> str:
    values = tuple(item.mean_daily_rank_ic for item in blocks if item.mean_daily_rank_ic is not None)
    if not values:
        return "No persisted calendar block has a defined mean daily rank IC for this context."
    positive = sum(value > 0 for value in values)
    negative = sum(value < 0 for value in values)
    zero = sum(value == 0 for value in values)
    return (
        f"Persisted block means: {positive} positive, {negative} negative, {zero} zero; "
        f"range {min(values):.4f} to {max(values):.4f}. This is descriptive, not a research decision."
    )


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
    carried = st.session_state.get("quantlab_explore_context", {})
    carried_factor = carried.get("factor") if isinstance(carried, dict) else None
    if carried_factor and carried_factor not in population.factors:
        st.info(
            f"The carried candidate `{carried_factor}` is unavailable in this evidence population; "
            "the unsupported candidate field was reset."
        )
        carried_factor = None
    factor = context[1].selectbox(
        "Factor / candidate",
        population.factors,
        index=population.factors.index(carried_factor) if carried_factor in population.factors else 0,
    )
    carried_horizon = carried.get("horizon_sessions") if isinstance(carried, dict) else None
    horizon = context[2].selectbox(
        "Horizon",
        population.horizons,
        index=population.horizons.index(carried_horizon) if carried_horizon in population.horizons else 0,
        format_func=lambda value: f"{value} sessions",
    )
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
    st.session_state["quantlab_decision_context"] = {
        "candidate_type": "factor",
        "candidate": factor,
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

    st.subheader("Temporal Rank IC")
    if not evidence.blocks:
        st.warning("Temporal block evidence is unavailable for this selection.")
    else:
        st.altair_chart(
            bar_chart(
                _temporal_ic_rows(evidence.blocks),
                category="Block",
                value="Mean daily rank IC",
                y_title="Mean daily rank IC",
                value_format=".4f",
            ),
            width="stretch",
        )
        st.info(_descriptive_temporal_summary(evidence.blocks))

    st.subheader("Spread, Incremental and Redundancy Evidence")
    if evidence.blocks:
        st.dataframe(
            tuple(
                {
                    "Block": format_temporal_block(item.name),
                    "High − low spread": format_percentage_points(item.high_minus_low_spread),
                    "Positive spread-date rate": _rate(item.positive_spread_date_rate),
                    "Spread coverage": format_percent(item.spread_coverage_pct),
                }
                for item in evidence.blocks
            ),
            hide_index=True,
            width="stretch",
        )

    horizon_rows = tuple(
        item
        for item in population.summaries
        if item.factor == factor and item.outcome_field == outcome
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
            st.altair_chart(
                line_chart(
                    tuple({"Signal date": row["Signal date"], "Series": "Rank IC", "Value": row["Rank IC"]} for row in chart_rows),
                    category="Signal date",
                    series="Series",
                    value="Value",
                    y_title="Rank IC",
                    value_format=".4f",
                    zero=True,
                ),
                width="stretch",
            )
            st.markdown("**Daily high-minus-low spread (percentage points)**")
            st.altair_chart(
                bar_chart(
                    chart_rows,
                    category="Signal date",
                    value="High − low spread",
                    y_title="High − low spread (pp)",
                    value_format=".3f",
                ),
                width="stretch",
            )

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
                        "Mean partial rank IC": format_ic(item.mean_partial_rank_ic),
                        "Partial − raw IC": format_ic(item.partial_minus_raw_rank_ic),
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
                        "Correlation": format_ic(item.correlation),
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

    with st.expander("Detailed temporal evidence matrix", expanded=False):
        st.dataframe(
            tuple(
                {
                    "Block": format_temporal_block(item.name),
                    "Exact dates": f"{item.start_date} → {item.end_date}",
                    "Mean rank IC": format_ic(item.mean_daily_rank_ic),
                    "High − low spread": format_percentage_points(item.high_minus_low_spread),
                    "IC coverage": format_percent(item.ic_coverage_pct),
                    "Spread coverage": format_percent(item.spread_coverage_pct),
                    "Direction": item.direction,
                    "Missing evidence": item.undefined_reason or "—",
                }
                for item in evidence.blocks
            ),
            hide_index=True,
            width="stretch",
        )
        st.markdown("**Evidence by horizon**")
        st.dataframe(
            tuple(
                {
                    "Horizon": f"{item.horizon_sessions} sessions",
                    "Defined-date coverage": format_percent(item.ic_coverage_pct),
                    "Mean rank IC": format_ic(item.mean_daily_rank_ic),
                    "Positive spread-date rate": format_fraction_percent(item.positive_spread_date_rate),
                    "High − low spread": format_percentage_points(item.high_minus_low_spread),
                }
                for item in horizon_rows
            ),
            hide_index=True,
            width="stretch",
        )

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
