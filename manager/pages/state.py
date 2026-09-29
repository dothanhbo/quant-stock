from __future__ import annotations

from manager.view_models import StateViewModel
from quantctl.state import ForwardSystemSnapshot, PaperStoreSnapshot


def _availability(*, exists: bool, readable: bool) -> str:
    if not exists:
        return "MISSING"
    return "AVAILABLE" if readable else "UNREADABLE"


def _paper_store(store: PaperStoreSnapshot, *, active: bool = False) -> None:
    import streamlit as st

    columns = st.columns(4)
    columns[0].metric("Policy", store.strategy_identity)
    columns[1].metric(
        "Open positions",
        store.open_position_count if store.open_position_count is not None else "UNKNOWN",
    )
    columns[2].metric(
        "Pending signals",
        store.pending_signal_count if store.pending_signal_count is not None else "UNKNOWN",
    )
    columns[3].metric("Latest activity", store.latest_activity or "UNKNOWN")
    if not store.exists:
        st.info("This optional paper store has not been created.")
    elif not store.readable:
        st.error("Persisted paper state is not readable.")
    elif active and store.open_position_count == 0:
        st.caption("The active store currently has no open positions.")
    elif store.open_position_count == 0:
        st.caption("No persisted open positions in this store.")
    with st.expander("Store technical details", expanded=False):
        st.write(f"Database: {_availability(exists=store.exists, readable=store.readable)}")
        st.write(f"Schema: {store.schema_status}")
        st.write(f"Role: {store.role.value}")
        st.write(f"Database path: `{store.database_path}`")
        st.write(f"Persisted strategy identities: {', '.join(store.strategy_versions) or 'UNKNOWN'}")
        st.write(f"Orders: {store.order_count if store.order_count is not None else 'UNKNOWN'}")
        st.write(
            "Closed trades: "
            f"{store.closed_trade_count if store.closed_trade_count is not None else 'UNKNOWN'}"
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
            width="stretch",
            hide_index=True,
        )
    for warning in store.warnings:
        st.warning(warning)
    if store.error:
        st.error(store.error)


def _forward(snapshot: ForwardSystemSnapshot) -> None:
    import streamlit as st

    columns = st.columns(4)
    columns[0].metric(
        "Active protocols",
        snapshot.active_protocol_count if snapshot.active_protocol_count is not None else "UNKNOWN",
    )
    columns[1].metric(
        "Formations", snapshot.formation_count if snapshot.formation_count is not None else "UNKNOWN"
    )
    columns[2].metric(
        "Pending maturities",
        snapshot.pending_maturity_count if snapshot.pending_maturity_count is not None else "UNKNOWN",
    )
    columns[3].metric(
        "Outcomes", snapshot.outcome_count if snapshot.outcome_count is not None else "UNKNOWN"
    )
    if not snapshot.exists:
        st.info("Forward validation has no persisted database yet.")
    elif snapshot.active_protocol_count == 0:
        st.caption("No active Forward protocol is persisted.")
    with st.expander("Forward protocol and database details", expanded=False):
        st.write(f"Database: {_availability(exists=snapshot.exists, readable=snapshot.readable)}")
        st.write(f"Schema: {snapshot.schema_status}")
        st.write(f"Latest activity: {snapshot.latest_activity or 'UNKNOWN'}")
        st.write(f"Matured states: {snapshot.matured_maturity_count if snapshot.matured_maturity_count is not None else 'UNKNOWN'}")
        if snapshot.protocols:
            st.dataframe(
                tuple(
                    {
                        "Protocol": item.protocol_id,
                        "Version": item.protocol_version,
                        "Selection": item.selection_policy,
                        "Weighting": item.weighting_policy,
                        "Budget": item.budget,
                        "Horizons": ", ".join(str(value) for value in item.tracked_horizons),
                        "Benchmark": item.benchmark,
                    }
                    for item in snapshot.protocols
                ),
                width="stretch",
                hide_index=True,
            )
    for warning in snapshot.warnings:
        st.warning(warning)
    if snapshot.error:
        st.error(snapshot.error)


def render(model: StateViewModel) -> None:
    import streamlit as st

    st.header("Paper & Forward")
    st.caption("Read-only persisted production and prospective-validation state.")
    st.subheader("Active Paper")
    active = model.paper.active_store
    st.markdown("### Active Paper Store")
    if active is None:
        st.warning("Active paper store could not be resolved.")
    else:
        st.markdown(f"**{active.display_name}**")
        _paper_store(active, active=True)

    with st.expander("Other Paper Stores", expanded=False):
        for store in model.paper.other_stores:
            st.markdown(f"#### {store.display_name}")
            _paper_store(store)
            if store.open_position_count:
                st.warning("Inactive store contains persisted open positions.")

    st.subheader("Forward Validation")
    _forward(model.forward)
