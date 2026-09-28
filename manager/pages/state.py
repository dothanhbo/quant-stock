from __future__ import annotations

from manager.view_models import StateViewModel
from quantctl.state import ForwardSystemSnapshot, PaperStoreSnapshot


def _availability(*, exists: bool, readable: bool) -> str:
    if not exists:
        return "MISSING"
    return "AVAILABLE" if readable else "UNREADABLE"


def _paper_store(store: PaperStoreSnapshot) -> None:
    import streamlit as st

    columns = st.columns(4)
    columns[0].metric("Database", _availability(exists=store.exists, readable=store.readable))
    columns[1].metric("Schema", store.schema_status)
    columns[2].metric(
        "Open positions",
        store.open_position_count if store.open_position_count is not None else "UNKNOWN",
    )
    columns[3].metric("Strategy", store.strategy_identity)
    st.caption(
        f"Role: {store.role.value} · Persisted position identity: "
        f"{', '.join(store.strategy_versions) or 'UNKNOWN'}"
    )
    st.caption(f"Database path: {store.database_path}")
    st.caption(f"Latest persisted activity: {store.latest_activity or 'UNKNOWN'}")
    st.caption(
        "Orders: "
        f"{store.order_count if store.order_count is not None else 'UNKNOWN'} · "
        "Closed trades: "
        f"{store.closed_trade_count if store.closed_trade_count is not None else 'UNKNOWN'} · "
        "Pending signals: "
        f"{store.pending_signal_count if store.pending_signal_count is not None else 'UNKNOWN'}"
    )
    if store.positions:
        st.dataframe(
            tuple(
                {
                    "Symbol": item.symbol,
                    "Quantity": item.quantity,
                    "Average price": item.average_price,
                    "Opened": item.entry_date or "UNKNOWN",
                    "Status": item.status,
                }
                for item in store.positions
            ),
            use_container_width=True,
            hide_index=True,
        )
    for warning in store.warnings:
        st.warning(warning)
    if store.error:
        st.error(store.error)


def _forward(snapshot: ForwardSystemSnapshot) -> None:
    import streamlit as st

    columns = st.columns(4)
    columns[0].metric("Database", _availability(exists=snapshot.exists, readable=snapshot.readable))
    columns[1].metric("Schema", snapshot.schema_status)
    columns[2].metric(
        "Active protocols",
        snapshot.active_protocol_count if snapshot.active_protocol_count is not None else "UNKNOWN",
    )
    columns[3].metric("Strategy/version", ", ".join(snapshot.protocol_versions) or "UNKNOWN")
    st.caption(f"Latest persisted activity: {snapshot.latest_activity or 'UNKNOWN'}")
    detail_columns = st.columns(4)
    detail_columns[0].metric(
        "Formations", snapshot.formation_count if snapshot.formation_count is not None else "UNKNOWN"
    )
    detail_columns[1].metric(
        "Pending maturities",
        snapshot.pending_maturity_count if snapshot.pending_maturity_count is not None else "UNKNOWN",
    )
    detail_columns[2].metric(
        "Matured states",
        snapshot.matured_maturity_count if snapshot.matured_maturity_count is not None else "UNKNOWN",
    )
    detail_columns[3].metric(
        "Outcomes", snapshot.outcome_count if snapshot.outcome_count is not None else "UNKNOWN"
    )
    if snapshot.protocols:
        st.dataframe(
            tuple(
                {
                    "Protocol": item.protocol_id,
                    "Version": item.protocol_version,
                    "Activated": item.activated_at_utc,
                    "Operational after": item.operational_start_after_session,
                    "Selection": item.selection_policy,
                    "Weighting": item.weighting_policy,
                    "Budget": item.budget,
                    "Horizons": ", ".join(str(value) for value in item.tracked_horizons),
                    "Benchmark": item.benchmark,
                }
                for item in snapshot.protocols
            ),
            use_container_width=True,
            hide_index=True,
        )
    for warning in snapshot.warnings:
        st.warning(warning)
    if snapshot.error:
        st.error(snapshot.error)


def render(model: StateViewModel) -> None:
    import streamlit as st

    st.header("State")
    st.info("READ-ONLY — persisted state only; no lifecycle or execution actions are available.")
    st.subheader("Paper Trading")
    active = model.paper.active_store
    st.markdown("### Active Paper Store")
    if active is None:
        st.warning("Active paper store could not be resolved.")
    else:
        st.markdown(f"**{active.display_name}**")
        _paper_store(active)

    with st.expander("Other Paper Stores", expanded=False):
        for store in model.paper.other_stores:
            st.markdown(f"#### {store.display_name}")
            _paper_store(store)
            if store.open_position_count:
                st.warning("Inactive store contains persisted open positions.")

    st.subheader("Forward Validation")
    _forward(model.forward)
