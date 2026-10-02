from __future__ import annotations

from manager.charts import bar_chart, line_chart
from manager.formatting import format_percent, format_percentage_points, format_vnd
from manager.theme import status_badge_row_html
from manager.view_models import ForwardEvidenceViewModel
from quantctl.forward_evidence import (
    ForwardEvidenceState,
    ForwardProtocolEvidencePresentation,
    PaperEvidencePresentation,
)


_PAPER_SOURCE = "Paper / Prospective Portfolio"
_FORWARD_SOURCE = "Forward Protocol"


def _money(value: float | None) -> str:
    return format_vnd(value)


def _percentage(value: float | None) -> str:
    return format_percent(value, digits=3)


def _paper_display_mode(model: PaperEvidencePresentation) -> str:
    if not model.points:
        return "EMPTY"
    return "SNAPSHOT" if len(model.points) == 1 else "TIME_SERIES"


def _paper_equity_chart_rows(points: tuple[object, ...]) -> tuple[dict[str, object], ...]:
    return tuple({"Session": item.session, "Series": "Equity", "Value (VND)": item.equity} for item in points)


def _paper_value_chart_rows(
    points: tuple[object, ...],
    fields: tuple[tuple[str, str], ...],
) -> tuple[dict[str, object], ...]:
    return tuple(
        {"Session": item.session, "Series": label, "Value (VND)": getattr(item, field)}
        for item in points
        for label, field in fields
    )


def _empty_state(state: ForwardEvidenceState, detail: str) -> None:
    import streamlit as st

    if state is ForwardEvidenceState.SCHEMA_INCOMPATIBLE:
        st.error(f"SCHEMA INCOMPATIBLE — {detail}")
    elif state in {ForwardEvidenceState.UNAVAILABLE, ForwardEvidenceState.CONTINUITY_GAP}:
        st.warning(f"{state.value.replace('_', ' ')} — {detail}")
    else:
        st.info(f"{state.value.replace('_', ' ')} — {detail}")


def _paper(model: PaperEvidencePresentation) -> None:
    import streamlit as st

    st.markdown(
        status_badge_row_html(
            ("Evidence", model.state.value),
            ("Continuity", model.continuity_state),
            ("Mode", _paper_display_mode(model)),
        ),
        unsafe_allow_html=True,
    )
    if not model.points:
        _empty_state(model.state, model.detail)
        with st.expander("Provenance and limitations", expanded=False):
            st.code(str(model.evidence_database_path), language=None)
            if model.paper_database_path is not None:
                st.code(str(model.paper_database_path), language=None)
            st.caption("Paper evidence is an executed paper-account population; it is not Forward label evidence.")
        return

    sessions = model.sessions
    if len(sessions) >= 2:
        controls = st.columns(2)
        start = controls[0].selectbox("From recorded session", sessions, index=0)
        end_options = tuple(item for item in sessions if item >= start)
        end = controls[1].selectbox("Through recorded session", end_options, index=len(end_options) - 1)
        points = tuple(item for item in model.points if start <= item.session <= end)
    else:
        points = model.points
    latest = points[-1]

    st.subheader("Paper Evidence Summary")
    cards = st.columns(3)
    cards[0].metric("Evidence observations", len(points))
    cards[1].metric("Latest recorded session", latest.session)
    cards[2].metric("Equity", _money(latest.equity))
    cards = st.columns(4)
    cards[0].metric("Cash", _money(latest.cash))
    cards[1].metric("Position value", _money(latest.position_value))
    cards[2].metric("Realized PnL", _money(latest.realized_pnl))
    cards[3].metric("Unrealized PnL", _money(latest.unrealized_pnl))

    st.subheader("Persisted Paper Account Evidence")
    if len(points) == 1:
        st.info(
            "SNAPSHOT — one persisted observation is available. Time-series equity, PnL, and "
            "drawdown charts require at least two recorded sessions."
        )
    else:
        st.markdown("**Paper account equity by recorded session (VND)**")
        st.altair_chart(
            line_chart(_paper_equity_chart_rows(points), category="Session", series="Series", value="Value (VND)", y_title="Equity (VND)", value_format=",.0f", zero=False),
            width="stretch",
        )
        st.markdown("**Cash and position value by recorded session (VND)**")
        st.altair_chart(
            line_chart(
                _paper_value_chart_rows(points, (("Cash", "cash"), ("Position value", "position_value"))),
                category="Session", series="Series", value="Value (VND)", y_title="Value (VND)", value_format=",.0f", zero=True,
            ),
            width="stretch",
        )
        st.markdown("**Realized and unrealized PnL by recorded session (VND)**")
        st.altair_chart(
            line_chart(
                _paper_value_chart_rows(points, (("Realized PnL", "realized_pnl"), ("Unrealized PnL", "unrealized_pnl"))),
                category="Session", series="Series", value="Value (VND)", y_title="PnL (VND)", value_format=",.0f", zero=True,
            ),
            width="stretch",
        )
    drawdowns = tuple(item for item in points if item.drawdown_pct is not None)
    if len(points) == 1 and latest.drawdown_pct is not None:
        st.caption(f"Persisted snapshot drawdown: {format_percent(latest.drawdown_pct, digits=3)}")
    elif len(points) >= 2 and drawdowns:
        st.markdown("**Persisted paper-account drawdown by recorded session (%)**")
        st.altair_chart(
            line_chart(
                tuple({"Session": item.session, "Series": "Drawdown", "Drawdown (%)": item.drawdown_pct} for item in drawdowns),
                category="Session", series="Series", value="Drawdown (%)", y_title="Drawdown (%)", value_format=".3f", zero=True,
            ),
            width="stretch",
        )
    else:
        st.caption("Drawdown: UNAVAILABLE — no persisted drawdown observations exist in this view.")

    st.subheader("Execution and Friction Provenance")
    execution = model.execution
    if execution is None:
        st.info("UNAVAILABLE — no persisted execution provenance exists in this view.")
    else:
        st.dataframe(
            ({
                "Persisted fills": execution.fill_count,
                "Persisted exits": execution.exit_count,
                "Commission": _money(execution.total_commission),
                "Modeled slippage": _money(execution.total_modeled_slippage),
                "Execution classification": ", ".join(execution.execution_classifications) or "UNAVAILABLE",
                "Spread / impact observations": execution.spread_or_market_impact_observation_count,
            },),
            hide_index=True,
            width="stretch",
        )
        st.caption("Only persisted modeled execution fields are shown; no missing friction is estimated.")

    if model.state is ForwardEvidenceState.CONTINUITY_GAP:
        _empty_state(model.state, model.detail)
    if model.records_truncated:
        st.warning(f"Display is bounded to the latest {model.displayed_record_limit} records.")
    for warning in model.warnings:
        st.warning(warning)
    with st.expander("Provenance and limitations", expanded=False):
        st.write(f"Strategy: `{model.strategy_identity or 'UNAVAILABLE'}`")
        st.write(f"Paper store: `{model.store_id or 'UNAVAILABLE'}` · {model.store_display_name or 'UNAVAILABLE'}")
        st.write(f"Account epoch: `{model.account_epoch or 'UNAVAILABLE'}`")
        st.write(f"Source identity: `{model.store_identity or 'UNAVAILABLE'}`")
        st.code(str(model.paper_database_path or "UNAVAILABLE"), language=None)
        st.code(str(model.evidence_database_path), language=None)
        st.caption("This is prospective paper-account evidence. It must not be combined with Forward outcome returns.")
        st.caption("Observation count alone does not establish statistical reliability.")


def _forward_protocol(model: ForwardProtocolEvidencePresentation) -> None:
    import streamlit as st

    st.markdown(
        status_badge_row_html(
            ("Evidence", model.state.value),
            ("Protocol", model.protocol.protocol_version),
        ),
        unsafe_allow_html=True,
    )
    if model.state is not ForwardEvidenceState.AVAILABLE:
        _empty_state(model.state, model.detail)

    st.subheader("Forward Evidence Summary")
    cards = st.columns(5)
    cards[0].metric("Formations", model.displayed_formation_count)
    cards[1].metric("Pending maturities", model.pending_maturity_count)
    cards[2].metric("Matured horizons", model.matured_maturity_count)
    cards[3].metric("Outcome observations", model.outcome_count)
    cards[4].metric("Latest formation", model.latest_formation_session or "NOT STARTED")

    st.subheader("Formation to Outcome Maturity")
    st.altair_chart(
        bar_chart(
            (
            {"Stage": "Formations", "Count": model.displayed_formation_count},
            {"Stage": "Pending", "Count": model.pending_maturity_count},
            {"Stage": "Matured", "Count": model.matured_maturity_count},
            {"Stage": "Unavailable", "Count": model.unavailable_maturity_count},
            ),
            category="Stage", value="Count", y_title="Formation-horizon count", value_format=".0f",
        ),
        width="stretch",
    )

    st.subheader("Mature Outcome Summary")
    summary_rows = tuple(
        {
            "Horizon": f"{item.horizon_sessions} sessions",
            "Pending formation-horizons": item.pending_maturity_count,
            "Matured formation-horizons": item.maturity_count,
            "Outcome observations": item.outcome_observation_count,
            "Available observations": item.available_outcome_count,
            "Mean stock return": _percentage(item.mean_stock_forward_return_pct),
            "Mean benchmark return": _percentage(item.mean_benchmark_forward_return_pct),
            "Mean excess return": format_percentage_points(item.mean_excess_forward_return_pct_points),
        }
        for item in model.summaries
    )
    st.dataframe(summary_rows, hide_index=True, width="stretch")
    if not any(item.available_outcome_count for item in model.summaries):
        st.info("SAMPLE IMMATURE — no available matured outcome exists for the selected horizon.")
    st.caption("Means describe available persisted symbol outcomes; they are not trade returns or a reliability claim.")

    if model.missing_formation_sessions:
        st.warning(
            "CONTINUITY GAP — missing formation sessions: "
            + ", ".join(model.missing_formation_sessions)
        )
    if model.records_truncated:
        st.warning(f"Display is bounded to the latest {model.displayed_record_limit} formations.")
    with st.expander("Protocol provenance and limitations", expanded=False):
        st.write(f"Protocol: `{model.protocol.protocol_id}`")
        st.write(f"Version: `{model.protocol.protocol_version}`")
        st.write(f"Selection: `{model.protocol.selection_policy}`")
        st.write(f"Weighting: `{model.protocol.weighting_policy}`")
        st.write(f"Budget: `{model.protocol.budget}`")
        st.write(f"Tracked horizons: `{model.protocol.tracked_horizons}`")
        st.write(f"Benchmark: `{model.protocol.benchmark}`")
        st.write(f"Operational start after: `{model.protocol.operational_start_after_session}`")
        st.code(str(model.database_path), language=None)
        for limitation in model.limitations:
            st.caption(f"Limitation: {limitation}")


def render(model: ForwardEvidenceViewModel) -> None:
    import streamlit as st

    st.header("Forward Evidence")
    st.caption("What prospective evidence has accumulated, and what can I actually conclude from it?")
    st.warning(
        "Paper portfolio PnL and Forward protocol returns are separate evidence populations. "
        "They are never combined on this page."
    )
    source = st.radio("Evidence source", (_PAPER_SOURCE, _FORWARD_SOURCE), horizontal=True)
    if source == _PAPER_SOURCE:
        _paper(model.catalog.paper)
        return

    if not model.catalog.protocols:
        _empty_state(model.catalog.forward_state, model.catalog.forward_detail)
        with st.expander("Forward source details", expanded=False):
            st.code(str(model.catalog.forward_database_path), language=None)
            st.caption("No database is initialized by this read-only page.")
        return

    protocols = model.catalog.protocols
    protocol_ids = tuple(item.protocol.protocol_id for item in protocols)
    carried = st.session_state.get("quantlab_forward_context", {})
    carried_policy = carried.get("selection_policy") if isinstance(carried, dict) else None
    matching_protocols = tuple(
        item for item in protocols
        if carried_policy is not None and item.protocol.selection_policy == carried_policy
    )
    if carried_policy and not matching_protocols:
        st.info(
            f"The carried selection policy `{carried_policy}` is not represented by a persisted "
            "Forward protocol. That unsupported field was reset; no protocol was substituted as equivalent."
        )
    default_index = next(
        (
            index
            for index, item in enumerate(protocols)
            if carried_policy is not None and item.protocol.selection_policy == carried_policy
        ),
        0,
    )
    if len(protocol_ids) > 1:
        selected_id = st.selectbox("Protocol", protocol_ids, index=default_index)
    else:
        selected_id = protocol_ids[0]
        st.caption(f"Protocol: `{selected_id}`")
    selected = next(item for item in protocols if item.protocol.protocol_id == selected_id)
    horizons = selected.protocol.tracked_horizons
    carried_horizon = carried.get("horizon_sessions") if isinstance(carried, dict) else None
    if carried_horizon is not None and carried_horizon not in horizons:
        st.info(
            f"The carried horizon `{carried_horizon}` is not tracked by this protocol. "
            "That unsupported field was reset; no horizon was treated as equivalent."
        )
    horizon_index = horizons.index(carried_horizon) if carried_horizon in horizons else 0
    horizon = st.selectbox(
        "Persisted horizon",
        horizons,
        index=horizon_index,
        format_func=lambda value: f"{value} sessions",
    )
    st.session_state["quantlab_decision_context"] = {
        "candidate_type": "portfolio_configuration",
        "budget": selected.protocol.budget,
        "horizon_sessions": horizon,
    }
    filtered = next(
        (
            item
            for item in selected.summaries
            if item.horizon_sessions == horizon
        ),
        None,
    )
    if filtered is not None:
        filtered_state = selected.state
        filtered_detail = selected.detail
        if (
            selected.state is not ForwardEvidenceState.CONTINUITY_GAP
            and filtered.available_outcome_count == 0
        ):
            filtered_state = ForwardEvidenceState.SAMPLE_IMMATURE
            filtered_detail = "No mature available outcome is present for the selected horizon."
        selected = ForwardProtocolEvidencePresentation(
            filtered_state,
            filtered_detail,
            selected.protocol,
            selected.formation_count,
            selected.displayed_formation_count,
            filtered.pending_maturity_count,
            filtered.maturity_count,
            filtered.unavailable_maturity_count,
            filtered.outcome_observation_count,
            selected.latest_formation_session,
            selected.formation_sessions,
            selected.missing_formation_sessions,
            (filtered,),
            selected.database_path,
            selected.displayed_record_limit,
            selected.records_truncated,
            selected.limitations,
        )
    _forward_protocol(selected)


__all__ = ("render",)
