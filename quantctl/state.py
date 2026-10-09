from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import json
from pathlib import Path
import sqlite3

from quantctl.registry import PROJECT_ROOT, sqlite_read_only
from quantlab.evidence import inspect_prospective_portfolio_evidence
from config.paper_store import (
    KNOWN_PAPER_STORES,
    configured_paper_environment,
    resolve_active_paper_store,
    resolve_store_definition,
)


# Columns created and consumed by execution.persistence.PaperTradingStore.
# Inspection must validate the runtime contract without initializing/migrating it.
PAPER_REQUIRED_COLUMNS = {
    "paper_metadata": frozenset("key value".split()),
    "paper_orders": frozenset(
        "client_order_id symbol side quantity order_type limit_price reference_price "
        "status filled_quantity average_fill_price rejection_reason created_at updated_at "
        "source_intent_id execution_context".split()
    ),
    "paper_fills": frozenset(
        "id order_id symbol side quantity price gross_value commission slippage_cost "
        "net_cash_flow created_at".split()
    ),
    "paper_positions": frozenset(
        "symbol quantity average_price market_price realized_pnl".split()
    ),
    "paper_portfolio_snapshots": frozenset(
        "id cash positions_value equity realized_pnl unrealized_pnl gross_exposure_pct "
        "open_positions created_at".split()
    ),
    "paper_position_lifecycle": frozenset(
        "symbol entry_date entry_price initial_quantity stop_price take_profit_price "
        "highest_price trailing_stop_price trailing_atr_multiplier maximum_holding_days "
        "updated_at entry_order_id strategy_version policy_fingerprint".split()
    ),
    "paper_closed_trades": frozenset(
        "id symbol entry_date exit_date quantity entry_price exit_price gross_proceeds "
        "commission realized_pnl return_pct holding_days exit_reason order_id created_at".split()
    ),
    "paper_pending_signals": frozenset(
        "id signal_date symbol payload status processed_date reason created_at".split()
    ),
}
PAPER_TABLES = frozenset(PAPER_REQUIRED_COLUMNS)

# Existing immutable ledger fields read by quantlab.forward.ledger and status.
# Optional R3 binding tables may be absent from healthy legacy ledgers.
FORWARD_REQUIRED_COLUMNS = {
    "forward_protocols": frozenset(
        "protocol_id protocol_fingerprint protocol_version activated_at_utc "
        "activation_market_session_boundary operational_start_after_session "
        "source_phase8_manifest_identity source_phase8_manifest_sha256 selection_policy "
        "weighting_policy budget tracked_horizons_json benchmark activation_identity".split()
    ),
    "forward_formations": frozenset(
        "formation_identity protocol_id protocol_fingerprint formation_session recorded_at_utc "
        "source_snapshot_identity completed_session_set_identity selection_policy_identity "
        "eligible_universe_identity selection_identity ordered_selected_symbols_json "
        "actual_selected_count budget weighting_policy gross_weight cash_weight "
        "benchmark_formation_close".split()
    ),
    "forward_positions": frozenset(
        "formation_identity rank symbol weight formation_close position_identity".split()
    ),
    "forward_maturities": frozenset(
        "event_sequence maturity_identity protocol_id formation_identity formation_session "
        "horizon_sessions target_session status reason_code recorded_at_utc".split()
    ),
    "forward_outcomes": frozenset(
        "outcome_identity protocol_id formation_identity horizon_sessions target_session "
        "symbol weight availability stock_forward_return_pct benchmark_forward_return_pct "
        "excess_forward_return_pct_points recorded_at_utc".split()
    ),
    "forward_audit_events": frozenset(
        "event_identity protocol_id event_type market_session reason_code recorded_at_utc".split()
    ),
}
FORWARD_TABLES = frozenset(FORWARD_REQUIRED_COLUMNS)


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    symbol: str
    quantity: int
    average_price: float
    entry_date: str | None
    lifecycle_updated_at: str | None
    status: str = "OPEN"


class PaperStoreRole(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE_WITH_STATE = "INACTIVE_WITH_STATE"
    INACTIVE = "INACTIVE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class PaperStoreSnapshot:
    name: str
    store_id: str
    display_name: str
    role: PaperStoreRole
    strategy_identity: str
    writable_by_current_pipeline: bool
    database_path: Path
    exists: bool
    readable: bool
    schema_status: str
    open_position_count: int | None = None
    latest_activity: str | None = None
    strategy_versions: tuple[str, ...] = ()
    positions: tuple[PositionSnapshot, ...] = ()
    order_count: int | None = None
    closed_trade_count: int | None = None
    pending_signal_count: int | None = None
    warnings: tuple[str, ...] = ()
    error: str | None = None
    evidence_schema_status: str = "MISSING"
    evidence_observation_count: int | None = None
    latest_evidence_date: str | None = None
    latest_evidence_strategy_identity: str | None = None
    latest_evidence_configuration_fingerprint: str | None = None
    evidence_capture_state: str = "MISSING"
    evidence_continuity_state: str = "NOT_STARTED"
    evidence_missing_session_count: int | None = None
    evidence_warnings: tuple[str, ...] = ()
    evidence_error: str | None = None


@dataclass(frozen=True, slots=True)
class PaperSystemSnapshot:
    stores: tuple[PaperStoreSnapshot, ...]

    @property
    def active_store(self) -> PaperStoreSnapshot | None:
        return next((store for store in self.stores if store.role is PaperStoreRole.ACTIVE), None)

    @property
    def other_stores(self) -> tuple[PaperStoreSnapshot, ...]:
        return tuple(store for store in self.stores if store.role is not PaperStoreRole.ACTIVE)


@dataclass(frozen=True, slots=True)
class ForwardProtocolSnapshot:
    protocol_id: str
    protocol_version: str
    activated_at_utc: str
    operational_start_after_session: str
    selection_policy: str
    weighting_policy: str
    budget: int
    tracked_horizons: tuple[int, ...]
    benchmark: str


@dataclass(frozen=True, slots=True)
class ForwardSystemSnapshot:
    database_path: Path
    exists: bool
    readable: bool
    schema_status: str
    active_protocol_count: int | None = None
    latest_activity: str | None = None
    protocol_versions: tuple[str, ...] = ()
    protocols: tuple[ForwardProtocolSnapshot, ...] = ()
    formation_count: int | None = None
    position_count: int | None = None
    pending_maturity_count: int | None = None
    matured_maturity_count: int | None = None
    outcome_count: int | None = None
    warnings: tuple[str, ...] = ()
    error: str | None = None


def _tables(connection: sqlite3.Connection) -> frozenset[str]:
    return frozenset(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    )


def _columns(connection: sqlite3.Connection, table: str) -> frozenset[str]:
    return frozenset(str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})"))


def _count(connection: sqlite3.Connection, table: str, where: str = "") -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table} {where}").fetchone()[0])


def _latest_activity(
    connection: sqlite3.Connection,
    tables: frozenset[str],
    candidates: tuple[tuple[str, str], ...],
) -> str | None:
    values: list[str] = []
    for table, column in candidates:
        if table not in tables or column not in _columns(connection, table):
            continue
        value = connection.execute(f"SELECT MAX({column}) FROM {table}").fetchone()[0]
        if value is not None and str(value).strip():
            values.append(str(value))
    return max(values, default=None)


def inspect_paper_store(
    path: Path,
    *,
    name: str,
    store_id: str = "unknown",
    display_name: str | None = None,
    strategy_identity: str = "UNKNOWN",
    writable_by_current_pipeline: bool = False,
    evidence_database_path: Path | None = None,
    market_database_path: Path | None = None,
) -> PaperStoreSnapshot:
    path = path.resolve()
    evidence_error: str | None = None
    try:
        evidence = (
            inspect_prospective_portfolio_evidence(
                evidence_database_path=evidence_database_path,
                market_database_path=market_database_path,
                source_store_id=store_id,
                strategy_identity=strategy_identity,
                paper_database_path=path,
            )
            if evidence_database_path is not None
            else None
        )
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        # Evidence inspection reads the source account epoch too. A corrupt
        # source must remain unavailable. Check its schema before classifying
        # an evidence failure, since missing metadata columns can cause it.
        evidence = None
        evidence_error = f"{type(exc).__name__}: {exc}"
    evidence_fields = (
        {
            "evidence_schema_status": evidence.schema_status,
            "evidence_observation_count": evidence.observation_count,
            "latest_evidence_date": evidence.latest_observation_date,
            "latest_evidence_strategy_identity": evidence.latest_strategy_identity,
            "latest_evidence_configuration_fingerprint": evidence.latest_configuration_fingerprint,
            "evidence_capture_state": evidence.capture_state,
            "evidence_continuity_state": evidence.continuity_state,
            "evidence_missing_session_count": len(evidence.missing_sessions),
            "evidence_warnings": evidence.warnings,
            "evidence_error": evidence.error,
        }
        if evidence is not None
        else {}
    )
    if not path.is_file():
        return PaperStoreSnapshot(
            name,
            store_id,
            display_name or name,
            PaperStoreRole.UNKNOWN,
            strategy_identity,
            writable_by_current_pipeline,
            path,
            False,
            False,
            "MISSING",
            error="database file is missing",
            **evidence_fields,
        )

    try:
        with sqlite_read_only(path) as connection:
            connection.row_factory = sqlite3.Row
            tables = _tables(connection)
            missing = tuple(sorted(PAPER_TABLES - tables))
            warnings = tuple(f"missing table: {table}" for table in missing)
            warnings += tuple(
                f"missing column: {table}.{column}"
                for table in sorted(PAPER_TABLES & tables)
                for column in sorted(PAPER_REQUIRED_COLUMNS[table] - _columns(connection, table))
            )
            if warnings:
                return PaperStoreSnapshot(
                    name, store_id, display_name or name, PaperStoreRole.UNKNOWN,
                    strategy_identity, writable_by_current_pipeline, path,
                    True, True, "SCHEMA_MISMATCH", warnings=warnings,
                    **evidence_fields,
                )
            if evidence_error is not None:
                return PaperStoreSnapshot(
                    name, store_id, display_name or name, PaperStoreRole.UNKNOWN,
                    strategy_identity, writable_by_current_pipeline, path,
                    True, False, "UNREADABLE", error=evidence_error,
                )
            schema_status = "OK"

            positions: tuple[PositionSnapshot, ...] = ()
            open_count: int | None = None
            strategy_versions: tuple[str, ...] = ()
            if "paper_positions" in tables:
                lifecycle_columns = (
                    _columns(connection, "paper_position_lifecycle")
                    if "paper_position_lifecycle" in tables
                    else frozenset()
                )
                entry_expression = "l.entry_date" if "entry_date" in lifecycle_columns else "NULL"
                updated_expression = "l.updated_at" if "updated_at" in lifecycle_columns else "NULL"
                join = (
                    "LEFT JOIN paper_position_lifecycle l ON l.symbol=p.symbol"
                    if "paper_position_lifecycle" in tables
                    else ""
                )
                rows = connection.execute(
                    "SELECT p.symbol,p.quantity,p.average_price,"
                    f"{entry_expression} AS entry_date,{updated_expression} AS lifecycle_updated_at "
                    f"FROM paper_positions p {join} WHERE p.quantity > 0 "
                    "ORDER BY UPPER(TRIM(p.symbol))"
                ).fetchall()
                positions = tuple(
                    PositionSnapshot(
                        str(row["symbol"]).strip().upper(),
                        int(row["quantity"]),
                        float(row["average_price"]),
                        None if row["entry_date"] is None else str(row["entry_date"]),
                        None if row["lifecycle_updated_at"] is None else str(row["lifecycle_updated_at"]),
                    )
                    for row in rows
                )
                open_count = len(positions)
                if "strategy_version" in lifecycle_columns:
                    strategy_versions = tuple(
                        str(row[0])
                        for row in connection.execute(
                            "SELECT DISTINCT strategy_version FROM paper_position_lifecycle "
                            "WHERE strategy_version IS NOT NULL AND TRIM(strategy_version) <> '' "
                            "ORDER BY strategy_version"
                        )
                    )

            order_count = _count(connection, "paper_orders") if "paper_orders" in tables else None
            closed_count = (
                _count(connection, "paper_closed_trades")
                if "paper_closed_trades" in tables
                else None
            )
            pending_count = (
                _count(connection, "paper_pending_signals", "WHERE status='PENDING'")
                if "paper_pending_signals" in tables
                else None
            )
            latest = _latest_activity(
                connection,
                tables,
                (
                    ("paper_orders", "updated_at"),
                    ("paper_orders", "created_at"),
                    ("paper_fills", "created_at"),
                    ("paper_portfolio_snapshots", "created_at"),
                    ("paper_position_lifecycle", "updated_at"),
                    ("paper_closed_trades", "created_at"),
                    ("paper_pending_signals", "created_at"),
                    ("paper_pending_signals", "processed_date"),
                ),
            )
        return PaperStoreSnapshot(
            name,
            store_id,
            display_name or name,
            PaperStoreRole.UNKNOWN,
            strategy_identity,
            writable_by_current_pipeline,
            path,
            True,
            True,
            schema_status,
            open_count,
            latest,
            strategy_versions,
            positions,
            order_count,
            closed_count,
            pending_count,
            warnings,
            **evidence_fields,
        )
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        return PaperStoreSnapshot(
            name,
            store_id,
            display_name or name,
            PaperStoreRole.UNKNOWN,
            strategy_identity,
            writable_by_current_pipeline,
            path,
            True,
            False,
            "UNREADABLE",
            error=f"{type(exc).__name__}: {exc}",
            **evidence_fields,
        )


def _absolute_store_path(path: Path, *, root: Path) -> Path:
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def inspect_paper_system(
    *,
    root: Path = PROJECT_ROOT,
    environ: dict[str, str] | None = None,
) -> PaperSystemSnapshot:
    configured = configured_paper_environment(root=root, environ=environ)
    # Diagnostic snapshots may target a temporary/alternate repository root.
    # Convert relative overrides to that inspection root before passing them to
    # the runtime resolver, whose production contract is repository-anchored.
    for name in ("PAPER_DATABASE_PATH", "PAPER_V2_DATABASE_PATH", "PAPER_V3_DATABASE_PATH"):
        value = str(configured.get(name, "")).strip()
        if value and not Path(value).expanduser().is_absolute():
            configured[name] = str((root / value).resolve())
    active = resolve_active_paper_store(configured, root=root)
    inspected: list[PaperStoreSnapshot] = []
    technical_names = {
        "generic-paper": "Generic",
        "q70-frozen": "Q70",
        "v3-breadth-40-60": "V3",
    }
    for definition in KNOWN_PAPER_STORES:
        writable = definition.store_id == active.store_id
        resolved = resolve_store_definition(
            definition,
            configured,
            writable_by_current_pipeline=writable,
            root=root,
        )
        snapshot = inspect_paper_store(
            _absolute_store_path(resolved.database_path, root=root),
            name=technical_names[definition.store_id],
            store_id=resolved.store_id,
            display_name=resolved.display_name,
            strategy_identity=resolved.strategy_identity,
            writable_by_current_pipeline=writable,
            evidence_database_path=(root / "data" / "prospective_portfolio_evidence.db"),
            market_database_path=(root / "data" / "market.db"),
        )
        if writable:
            role = PaperStoreRole.ACTIVE
        elif snapshot.open_position_count or snapshot.pending_signal_count:
            role = PaperStoreRole.INACTIVE_WITH_STATE
        elif snapshot.readable:
            role = PaperStoreRole.INACTIVE
        else:
            role = PaperStoreRole.UNKNOWN
        inspected.append(replace(snapshot, role=role))
    inspected.sort(key=lambda item: (item.role is not PaperStoreRole.ACTIVE, item.store_id))
    return PaperSystemSnapshot(tuple(inspected))


def inspect_forward_system(*, root: Path = PROJECT_ROOT) -> ForwardSystemSnapshot:
    path = (root / "data" / "forward_validation.db").resolve()
    if not path.is_file():
        return ForwardSystemSnapshot(path, False, False, "MISSING", error="database file is missing")

    try:
        with sqlite_read_only(path) as connection:
            connection.row_factory = sqlite3.Row
            tables = _tables(connection)
            missing = tuple(sorted(FORWARD_TABLES - tables))
            warnings = list(f"missing table: {table}" for table in missing)
            warnings.extend(
                f"missing column: {table}.{column}"
                for table in sorted(FORWARD_TABLES & tables)
                for column in sorted(FORWARD_REQUIRED_COLUMNS[table] - _columns(connection, table))
            )
            if warnings:
                return ForwardSystemSnapshot(
                    path, True, True, "SCHEMA_MISMATCH", warnings=tuple(warnings),
                )
            schema_status = "OK"

            protocols: tuple[ForwardProtocolSnapshot, ...] = ()
            if "forward_protocols" in tables:
                protocol_rows = connection.execute(
                    "SELECT protocol_id,protocol_version,activated_at_utc,"
                    "operational_start_after_session,selection_policy,weighting_policy,"
                    "budget,tracked_horizons_json,benchmark FROM forward_protocols "
                    "ORDER BY activated_at_utc,protocol_id"
                ).fetchall()
                parsed: list[ForwardProtocolSnapshot] = []
                for row in protocol_rows:
                    try:
                        horizons = tuple(int(value) for value in json.loads(row["tracked_horizons_json"]))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        horizons = ()
                        warnings.append(f"invalid tracked horizons: {row['protocol_id']}")
                    parsed.append(
                        ForwardProtocolSnapshot(
                            str(row["protocol_id"]),
                            str(row["protocol_version"]),
                            str(row["activated_at_utc"]),
                            str(row["operational_start_after_session"]),
                            str(row["selection_policy"]),
                            str(row["weighting_policy"]),
                            int(row["budget"]),
                            horizons,
                            str(row["benchmark"]),
                        )
                    )
                protocols = tuple(parsed)

            pending_count: int | None = None
            matured_count: int | None = None
            if "forward_maturities" in tables:
                status_rows = connection.execute(
                    "SELECT m.status,COUNT(*) FROM forward_maturities m JOIN ("
                    "SELECT formation_identity,horizon_sessions,MAX(event_sequence) AS sequence "
                    "FROM forward_maturities GROUP BY formation_identity,horizon_sessions"
                    ") latest ON latest.sequence=m.event_sequence GROUP BY m.status"
                ).fetchall()
                statuses = {str(row[0]): int(row[1]) for row in status_rows}
                pending_count = statuses.get("PENDING", 0)
                matured_count = statuses.get("MATURED", 0)

            latest = _latest_activity(
                connection,
                tables,
                (
                    ("forward_protocols", "activated_at_utc"),
                    ("forward_formations", "recorded_at_utc"),
                    ("forward_maturities", "recorded_at_utc"),
                    ("forward_outcomes", "recorded_at_utc"),
                    ("forward_audit_events", "recorded_at_utc"),
                ),
            )
            audit_columns = (
                _columns(connection, "forward_audit_events")
                if "forward_audit_events" in tables
                else frozenset()
            )
            if "event_type" in audit_columns:
                missing_gap_count = int(connection.execute(
                    "SELECT COUNT(*) FROM forward_audit_events "
                    "WHERE event_type='MISSING_FORMATION'"
                ).fetchone()[0])
                if missing_gap_count:
                    warnings.append(
                        f"Forward reconciliation: {missing_gap_count} "
                        "formation gap(s) acknowledged as unrecoverable; "
                        "historical backfill is prohibited."
                    )
            formation_count = (
                _count(connection, "forward_formations")
                if "forward_formations" in tables
                else None
            )
            position_count = (
                _count(connection, "forward_positions")
                if "forward_positions" in tables
                else None
            )
            outcome_count = (
                _count(connection, "forward_outcomes")
                if "forward_outcomes" in tables
                else None
            )
        return ForwardSystemSnapshot(
            path,
            True,
            True,
            schema_status,
            len(protocols) if "forward_protocols" in tables else None,
            latest,
            tuple(sorted({item.protocol_version for item in protocols})),
            protocols,
            formation_count,
            position_count,
            pending_count,
            matured_count,
            outcome_count,
            tuple(warnings),
        )
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        return ForwardSystemSnapshot(
            path,
            True,
            False,
            "UNREADABLE",
            error=f"{type(exc).__name__}: {exc}",
        )
