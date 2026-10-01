from __future__ import annotations

from manager.theme import compact_status_label, status_badge_row_html
from manager.view_models import CompareViewModel
from quantctl.evidence_compare import (
    ComparisonFamily,
    ComparisonState,
    EvidenceComparison,
    compare_frozen_policies,
    compare_neutral_factors,
)
from quantctl.factor_explore import EvidenceState, ExplorePopulation


_OUTCOME_LABELS = {
    "stock_forward_return_pct": "Stock forward return",
    "excess_forward_return_pct_points": "Excess return vs VNINDEX",
}
_POLICY_LABELS = {
    "ADX_ONLY": "ADX",
    "RSI_ONLY": "RSI",
    "ADX_RSI_EQUAL_WEIGHT": "ADX + RSI",
    "ADX_RSI_VOLUME_EQUAL_WEIGHT": "Volume add-on to ADX + RSI",
}


def _number(value: float | None, *, percentage: bool = False) -> str:
    if value is None:
        return "UNKNOWN"
    return f"{value:.2f}%" if percentage else f"{value:.4f}"


def _rate(value: float | None) -> str:
    return "UNKNOWN" if value is None else f"{value * 100.0:.2f}%"


def _scope_label(value: str) -> str:
    return "Whole period" if value == "whole_period" else value.replace("_", " ").title()


def _policy_label(value: str) -> str:
    return _POLICY_LABELS.get(value, value.replace("_", " ").title())


def _common_factor_context(population: object, factor_a: str, factor_b: str) -> tuple[tuple[int, ...], dict[int, tuple[str, ...]]]:
    summaries = getattr(population, "summaries", ())
    keys_a = {
        (item.horizon_sessions, item.outcome_field)
        for item in summaries
        if item.factor == factor_a
    }
    keys_b = {
        (item.horizon_sessions, item.outcome_field)
        for item in summaries
        if item.factor == factor_b
    }
    common = keys_a & keys_b
    horizons = tuple(sorted({item[0] for item in common}))
    outcomes = {
        horizon: tuple(sorted(item[1] for item in common if item[0] == horizon))
        for horizon in horizons
    }
    return horizons, outcomes


def _common_factor_scopes(population: object, factor_a: str, factor_b: str, horizon: int, outcome: str) -> tuple[str, ...]:
    blocks = getattr(population, "blocks", ())
    names_a = {
        block.name
        for factor, row_horizon, row_outcome, block in blocks
        if (factor, row_horizon, row_outcome) == (factor_a, horizon, outcome)
    }
    names_b = {
        block.name
        for factor, row_horizon, row_outcome, block in blocks
        if (factor, row_horizon, row_outcome) == (factor_b, horizon, outcome)
    }
    return ("whole_period",) + tuple(sorted(names_a & names_b))


def _default_index(values: tuple[object, ...], preferred: object | None) -> int:
    try:
        return values.index(preferred) if preferred is not None else 0
    except ValueError:
        return 0


def _paired_card(container: object, label: str, value_a: str, value_b: str, name_a: str, name_b: str) -> None:
    import streamlit as st

    with container:
        with st.container(border=True):
            st.markdown(f"**{label}**")
            st.markdown(f"`A` {value_a}")
            st.caption(name_a)
            st.markdown(f"`B` {value_b}")
            st.caption(name_b)


def _paired_rows(comparison: EvidenceComparison) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "Evidence dimension": item.name,
            "Candidate A": item.candidate_a,
            "Candidate B": item.candidate_b,
            "Comparison support": item.support,
        }
        for item in comparison.dimensions
    )


def _render_comparison(comparison: EvidenceComparison) -> None:
    import streamlit as st

    st.markdown(
        status_badge_row_html(
            ("Comparison", comparison.state.value),
            ("Population", comparison.evidence_population),
        ),
        unsafe_allow_html=True,
    )
    if comparison.state in {ComparisonState.UNAVAILABLE, ComparisonState.INCOMPATIBLE}:
        method = st.error if comparison.state is ComparisonState.INCOMPATIBLE else st.warning
        method(comparison.detail)
        return
    a, b = comparison.evidence_a, comparison.evidence_b
    if a is None or b is None:
        st.warning("Comparable evidence is unavailable.")
        return

    cards = st.columns(5)
    _paired_card(cards[0], "Gate / disposition", a.disposition or "UNAVAILABLE", b.disposition or "UNAVAILABLE", a.name, b.name)
    _paired_card(cards[1], "Mean daily rank IC", _number(a.mean_rank_ic), _number(b.mean_rank_ic), a.name, b.name)
    _paired_card(cards[2], "Positive spread-date rate", _rate(a.positive_spread_rate), _rate(b.positive_spread_rate), a.name, b.name)
    _paired_card(cards[3], "Temporal support", compact_status_label(a.temporal_support), compact_status_label(b.temporal_support), a.name, b.name)
    _paired_card(cards[4], "Defined-date coverage", _number(a.defined_date_coverage_pct, percentage=True), _number(b.defined_date_coverage_pct, percentage=True), a.name, b.name)

    st.subheader("Comparable Evidence Matrix")
    st.dataframe(_paired_rows(comparison), hide_index=True, width="stretch")

    if comparison.blocks:
        st.subheader("Paired Temporal Blocks")
        block_rows = tuple(
            {
                "Block": item.name.replace("_", " ").title(),
                f"{a.name} mean rank IC": item.a_mean_rank_ic,
                f"{b.name} mean rank IC": item.b_mean_rank_ic,
                f"{a.name} spread": item.a_spread,
                f"{b.name} spread": item.b_spread,
                f"{a.name} coverage": item.a_coverage_pct,
                f"{b.name} coverage": item.b_coverage_pct,
            }
            for item in comparison.blocks
        )
        st.dataframe(block_rows, hide_index=True, width="stretch")
        charts = st.columns(2)
        with charts[0]:
            st.markdown("**Mean rank IC by block**")
            st.bar_chart(
                tuple({"Block": row["Block"], "A": row[f"{a.name} mean rank IC"], "B": row[f"{b.name} mean rank IC"]} for row in block_rows),
                x="Block",
                y=("A", "B"),
            )
        with charts[1]:
            st.markdown("**High-minus-low spread by block**")
            st.bar_chart(
                tuple({"Block": row["Block"], "A": row[f"{a.name} spread"], "B": row[f"{b.name} spread"]} for row in block_rows),
                x="Block",
                y=("A", "B"),
            )

    st.subheader("Descriptive Differences")
    if comparison.descriptive_differences:
        for item in comparison.descriptive_differences:
            st.info(item)
    else:
        st.caption("No descriptive difference is available for the persisted metrics.")
    if comparison.incremental_notes:
        st.markdown("**Incremental evidence**")
        for item in comparison.incremental_notes:
            st.caption(item)
    else:
        st.caption("Incremental evidence is unavailable for this exact pair and scope.")
    if comparison.redundancy_correlation is not None:
        st.caption(f"Persisted same-date factor correlation: {comparison.redundancy_correlation:.4f}.")
    else:
        st.caption("Redundancy evidence is unavailable for this exact pair.")

    if comparison.overlap:
        st.markdown("**Selection overlap**")
        st.dataframe(
            tuple(
                {
                    "Budget": item.budget,
                    "Scope": _scope_label(item.scope),
                    "Mean overlap coefficient": _number(item.mean_overlap_coefficient),
                    "Mean Jaccard similarity": _number(item.mean_jaccard_similarity),
                    "Exact set equality rate": _rate(item.exact_set_equality_rate),
                }
                for item in comparison.overlap
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("Selection overlap comparison is unavailable for this pair.")
    if comparison.turnover:
        st.markdown("**Persisted selection turnover**")
        st.dataframe(
            tuple(
                {
                    "Policy": _policy_label(item.policy),
                    "Budget": item.budget,
                    "Scope": _scope_label(item.scope),
                    "Mean one-way turnover": _number(item.mean_one_way_turnover),
                    "Total entries": item.total_entry_count,
                }
                for item in comparison.turnover
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("Turnover comparison is unavailable for this pair.")

    st.caption("Cost sensitivity: UNAVAILABLE — no compatible persisted cost contrast exists.")
    st.caption("Portfolio consequence: UNAVAILABLE — no compatible persisted portfolio contrast exists.")
    st.warning(
        "Descriptive differences are not a canonical preference. Gate dispositions, when shown, "
        "remain separate persisted research decisions."
    )

    with st.expander("Provenance and limitations", expanded=False):
        st.caption(comparison.detail)
        st.caption(
            f"Context: {comparison.horizon_sessions} sessions · "
            f"{_OUTCOME_LABELS.get(comparison.outcome_field, comparison.outcome_field)} · "
            f"{_scope_label(comparison.temporal_scope)}"
        )
        for identity in comparison.artifact_identities:
            st.code(f"Identity: {identity}", language=None)
        for path in comparison.artifact_paths:
            st.code(path.as_posix(), language=None)
        for limitation in comparison.limitations:
            st.caption(f"Limitation: {limitation}")


def render(model: CompareViewModel) -> None:
    import streamlit as st

    st.header("Compare")
    st.caption("How does this candidate differ from another candidate or baseline?")
    st.caption("Only semantically compatible, already-persisted evidence can be compared.")

    carried = st.session_state.get("quantlab_compare_context", {})
    if (
        isinstance(carried, dict)
        and carried.get("population")
        and carried.get("population") != ExplorePopulation.NEUTRAL_PIT.value
    ):
        st.info(
            "The carried Explore selection uses a selection-conditioned candidate population. "
            "Cross-population comparison is incompatible, so only that context was reset."
        )
        carried = {}
    family_options = (ComparisonFamily.NEUTRAL_FACTORS, ComparisonFamily.FROZEN_POLICIES)
    preferred_family = carried.get("family") if isinstance(carried, dict) else None
    family = st.selectbox(
        "Comparison family",
        family_options,
        index=_default_index(family_options, preferred_family),
        format_func=lambda item: "Neutral factors" if item is ComparisonFamily.NEUTRAL_FACTORS else "Frozen policies",
    )

    if family is ComparisonFamily.NEUTRAL_FACTORS:
        population = model.catalog.factors.population(ExplorePopulation.NEUTRAL_PIT)
        if population is None or population.state is EvidenceState.UNAVAILABLE or len(population.factors) < 2:
            st.error("MISSING DATA — at least two supported neutral factors are required.")
            return
        controls = st.columns((1.2, 1.2, .7, 1.15, 1.15))
        preferred_factor = carried.get("factor") if isinstance(carried, dict) else None
        factor_a = controls[0].selectbox(
            "Candidate A",
            population.factors,
            index=_default_index(population.factors, preferred_factor),
        )
        factors_b = tuple(item for item in population.factors if item != factor_a)
        factor_b = controls[1].selectbox("Candidate B", factors_b)
        horizons, outcomes_by_horizon = _common_factor_context(population, factor_a, factor_b)
        if not horizons:
            st.error("INCOMPATIBLE COMPARISON — these factors have no shared horizon and outcome evidence.")
            return
        preferred_horizon = carried.get("horizon") if isinstance(carried, dict) else None
        horizon = controls[2].selectbox(
            "Horizon",
            horizons,
            index=_default_index(horizons, preferred_horizon),
            format_func=lambda value: f"{value} sessions",
        )
        outcomes = outcomes_by_horizon[horizon]
        preferred_outcome = carried.get("outcome") if isinstance(carried, dict) else None
        outcome = controls[3].selectbox(
            "Outcome",
            outcomes,
            index=_default_index(outcomes, preferred_outcome),
            format_func=lambda value: _OUTCOME_LABELS.get(value, value),
        )
        scopes = _common_factor_scopes(population, factor_a, factor_b, horizon, outcome)
        preferred_scope = carried.get("scope") if isinstance(carried, dict) else None
        scope = controls[4].selectbox(
            "Temporal scope",
            scopes,
            index=_default_index(scopes, preferred_scope),
            format_func=_scope_label,
        )
        comparison = compare_neutral_factors(
            model.catalog,
            factor_a=factor_a,
            factor_b=factor_b,
            horizon_a=horizon,
            horizon_b=horizon,
            outcome_a=outcome,
            outcome_b=outcome,
            temporal_scope=scope,
        )
    else:
        source = model.catalog.policies
        if source.state is ComparisonState.UNAVAILABLE or not source.supported_pairs:
            st.error("MISSING DATA — canonical frozen-policy contrasts are unavailable.")
            st.caption(source.detail)
            return
        controls = st.columns((1.2, 1.2, .7, 1.15, 1.15))
        candidates_a = tuple(dict.fromkeys(item[0] for item in source.supported_pairs))
        policy_a = controls[0].selectbox("Candidate A", candidates_a, format_func=_policy_label)
        candidates_b = tuple(item[1] for item in source.supported_pairs if item[0] == policy_a)
        policy_b = controls[1].selectbox("Candidate B", candidates_b, format_func=_policy_label)
        compatible_contrasts = tuple(
            item for item in source.contrasts
            if (item.variant, item.reference) == (policy_a, policy_b)
        )
        horizons = tuple(sorted({item.horizon_sessions for item in compatible_contrasts}))
        if not horizons:
            st.error("INCOMPATIBLE COMPARISON — this pair has no canonical persisted contrast.")
            return
        horizon = controls[2].selectbox("Horizon", horizons, format_func=lambda value: f"{value} sessions")
        outcomes = tuple(sorted({item.outcome_field for item in compatible_contrasts if item.horizon_sessions == horizon}))
        outcome = controls[3].selectbox("Outcome", outcomes, format_func=lambda value: _OUTCOME_LABELS.get(value, value))
        scopes = tuple(
            sorted(
                {item.scope for item in compatible_contrasts if item.horizon_sessions == horizon and item.outcome_field == outcome},
                key=lambda item: (item != "whole_period", item),
            )
        )
        scope = controls[4].selectbox("Temporal scope", scopes, format_func=_scope_label)
        comparison = compare_frozen_policies(
            model.catalog,
            policy_a=policy_a,
            policy_b=policy_b,
            horizon_a=horizon,
            horizon_b=horizon,
            outcome_a=outcome,
            outcome_b=outcome,
            temporal_scope=scope,
        )
        st.session_state["quantlab_portfolio_context"] = {"selection_policy": policy_a}

    _render_comparison(comparison)
