from __future__ import annotations

from pathlib import Path

from quantctl.registry import PROJECT_ROOT
from quantctl.research_status import inspect_production_policy
from quantctl.state import (
    ForwardSystemSnapshot,
    PaperStoreSnapshot,
    inspect_forward_system,
    inspect_paper_system,
)


def _availability(*, exists: bool, readable: bool) -> str:
    if not exists:
        return "MISSING"
    return "AVAILABLE" if readable else "UNREADABLE"


def _known(value: object | None) -> str:
    return "UNKNOWN" if value is None else str(value)


def _paper_store_lines(store: PaperStoreSnapshot, *, include_role: bool) -> list[str]:
    lines = [
        store.display_name,
        *((f"  Role: {store.role.value}",) if include_role else ()),
        f"  Strategy: {store.strategy_identity}",
        f"  Path: {store.database_path}",
        f"  Database: {_availability(exists=store.exists, readable=store.readable)}",
        f"  Readable: {'YES' if store.readable else 'NO'}",
        f"  Schema: {store.schema_status}",
        f"  Open positions: {_known(store.open_position_count)}",
        f"  Latest activity: {store.latest_activity or 'UNKNOWN'}",
        "  Strategy/version: " + (", ".join(store.strategy_versions) or "UNKNOWN"),
        f"  Orders: {_known(store.order_count)}",
        f"  Closed trades: {_known(store.closed_trade_count)}",
        f"  Pending signals: {_known(store.pending_signal_count)}",
        "  Prospective evidence",
        f"    Schema: {store.evidence_schema_status}",
        f"    Capture: {store.evidence_capture_state}",
        f"    Observations: {_known(store.evidence_observation_count)}",
        f"    Latest date: {store.latest_evidence_date or 'UNKNOWN'}",
        "    Latest strategy: "
        + (store.latest_evidence_strategy_identity or "UNKNOWN"),
        "    Latest configuration: "
        + (store.latest_evidence_configuration_fingerprint or "UNKNOWN"),
        f"    Continuity: {store.evidence_continuity_state}",
        f"    Missing sessions: {_known(store.evidence_missing_session_count)}",
    ]
    if store.positions:
        lines.extend(("  Positions", "    Symbol  Quantity  Average price  Opened  Status"))
        lines.extend(
            "    "
            f"{item.symbol:<7} {item.quantity:>8}  {item.average_price:>13.4f}  "
            f"{item.entry_date or 'UNKNOWN':<10}  {item.status}"
            for item in store.positions
        )
    lines.extend(f"  Warning: {warning}" for warning in store.warnings)
    lines.extend(f"  Evidence warning: {warning}" for warning in store.evidence_warnings)
    if store.error:
        lines.append(f"  Error: {store.error}")
    return lines


def render_paper_status(*, root: Path = PROJECT_ROOT) -> str:
    snapshot = inspect_paper_system(root=root)
    production = inspect_production_policy(root=root)
    lines = [
        "PAPER TRADING STATUS", "",
        "Deployed Paper Policy",
        f"  {production.deployed_strategy_identity}",
        f"  Role: {production.role}",
        "",
        "Active Store",
    ]
    active = snapshot.active_store
    if active is None:
        lines.append("  UNKNOWN")
    else:
        lines.extend(_paper_store_lines(active, include_role=False))
    lines.extend(("", "Other Stores"))
    for store in snapshot.other_stores:
        lines.append("")
        lines.extend(_paper_store_lines(store, include_role=True))
        if store.open_position_count:
            lines.append("  Warning: Inactive store contains persisted open positions.")
    return "\n".join(lines)


def run_paper_status(*, root: Path = PROJECT_ROOT) -> int:
    print(render_paper_status(root=root))
    return 0


def _forward_lines(snapshot: ForwardSystemSnapshot) -> list[str]:
    lines = [
        "FORWARD VALIDATION STATUS",
        "",
        f"Database: {_availability(exists=snapshot.exists, readable=snapshot.readable)}",
        f"Readable: {'YES' if snapshot.readable else 'NO'}",
        f"Schema: {snapshot.schema_status}",
        f"Active protocols: {_known(snapshot.active_protocol_count)}",
        f"Latest activity: {snapshot.latest_activity or 'UNKNOWN'}",
        "Strategy/version: " + (", ".join(snapshot.protocol_versions) or "UNKNOWN"),
        f"Formations: {_known(snapshot.formation_count)}",
        f"Persisted positions: {_known(snapshot.position_count)}",
        f"Pending maturities: {_known(snapshot.pending_maturity_count)}",
        f"Matured latest states: {_known(snapshot.matured_maturity_count)}",
        f"Outcomes: {_known(snapshot.outcome_count)}",
    ]
    for protocol in snapshot.protocols:
        lines.extend(
            (
                "",
                f"Protocol {protocol.protocol_id}",
                f"  Version: {protocol.protocol_version}",
                f"  Activated: {protocol.activated_at_utc}",
                f"  Operational after: {protocol.operational_start_after_session}",
                f"  Selection: {protocol.selection_policy}",
                f"  Weighting: {protocol.weighting_policy}",
                f"  Budget: {protocol.budget}",
                "  Horizons: " + ", ".join(str(item) for item in protocol.tracked_horizons),
                f"  Benchmark: {protocol.benchmark}",
            )
        )
    lines.extend(f"Warning: {warning}" for warning in snapshot.warnings)
    if snapshot.error:
        lines.append(f"Error: {snapshot.error}")
    return lines


def render_forward_status(*, root: Path = PROJECT_ROOT) -> str:
    return "\n".join(_forward_lines(inspect_forward_system(root=root)))


def run_forward_status(*, root: Path = PROJECT_ROOT) -> int:
    print(render_forward_status(root=root))
    return 0
