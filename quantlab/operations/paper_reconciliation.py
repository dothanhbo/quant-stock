from __future__ import annotations

"""Read-only consistency checks for the SQLite paper-trading store.

This module deliberately does not import PaperTradingStore: constructing that
class creates directories, initializes/migrates schema, and opens writable
connections.  The audit uses SQLite URI ``mode=ro`` and exposes aggregate
findings only; it never returns account identifiers or row payloads.
"""

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import sqlite3
from hashlib import sha256
from typing import Any

from quantlab.identity import canonical_identity_value, canonical_json


CONTRACT = "quantlab.paper_reconciliation_audit"
VERSION = "v1"


class PaperVersion(str, Enum):
    V1 = "V1"
    V2 = "V2"
    V3 = "V3"


class FindingState(str, Enum):
    PASS = "PASS"
    ISSUE = "ISSUE"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_AUDITABLE = "NOT_AUDITABLE"


class FindingSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    MATERIAL = "MATERIAL"
    MINOR = "MINOR"
    INFORMATIONAL = "INFORMATIONAL"


class ReconciliationCapability(str, Enum):
    AUTO_REPAIR_SAFE = "AUTO_REPAIR_SAFE"
    REPAIR_WITH_FROZEN_CONTEXT = "REPAIR_WITH_FROZEN_CONTEXT"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    NOT_REPAIRABLE = "NOT_REPAIRABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class PaperConsistencyCheck:
    invariant: str
    state: FindingState
    severity: FindingSeverity
    capability: ReconciliationCapability
    affected_count: int | None
    evidence: str


@dataclass(frozen=True, slots=True)
class PaperReconciliationAuditResult:
    paper_version: PaperVersion
    database_path: str
    schema_tables: tuple[str, ...]
    source_intent_column_present: bool
    source_intent_unique_index_present: bool
    checks: tuple[PaperConsistencyCheck, ...]
    read_only: bool
    identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_tables", tuple(sorted(self.schema_tables)))
        object.__setattr__(self, "checks", tuple(sorted(self.checks, key=lambda row: row.invariant)))


_REQUIRED_TABLES = frozenset({
    "paper_metadata", "paper_orders", "paper_fills", "paper_positions",
    "paper_portfolio_snapshots", "paper_position_lifecycle",
    "paper_closed_trades", "paper_pending_signals",
})


def audit_paper_database(
    database_path: str | Path,
    *,
    paper_version: PaperVersion | str,
) -> PaperReconciliationAuditResult:
    """Inspect one paper DB without initialization, migration, or writes."""
    version = PaperVersion(paper_version)
    path = Path(database_path).expanduser().resolve(strict=True)
    uri = f"{path.as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        table_names = tuple(
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        )
        tables = set(table_names)
        checks: list[PaperConsistencyCheck] = []

        def add(
            invariant: str,
            count: int | None,
            *,
            severity: FindingSeverity = FindingSeverity.MATERIAL,
            capability: ReconciliationCapability = ReconciliationCapability.MANUAL_REVIEW_REQUIRED,
            evidence: str,
            state: FindingState | None = None,
        ) -> None:
            resolved_state = state or (
                FindingState.PASS if count == 0 else FindingState.ISSUE
            )
            checks.append(PaperConsistencyCheck(
                invariant=invariant,
                state=resolved_state,
                severity=severity,
                capability=capability,
                affected_count=count,
                evidence=evidence,
            ))

        missing_tables = _REQUIRED_TABLES - tables
        add(
            "required_tables_present", len(missing_tables),
            severity=FindingSeverity.MATERIAL,
            capability=ReconciliationCapability.NOT_REPAIRABLE,
            evidence="Required paper state tables absent; audit skips checks needing those tables.",
        )
        if missing_tables:
            # A partial/unknown schema must not be mistaken for a clean audit.
            for name in sorted(missing_tables):
                checks.append(PaperConsistencyCheck(
                    invariant=f"missing_table:{name}", state=FindingState.NOT_AUDITABLE,
                    severity=FindingSeverity.MATERIAL,
                    capability=ReconciliationCapability.MANUAL_REVIEW_REQUIRED,
                    affected_count=None, evidence="Table is absent; no migration was attempted.",
                ))

        source_column = False
        source_index = False
        execution_context_column = False
        if "paper_orders" in tables:
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(paper_orders)")}
            source_column = "source_intent_id" in columns
            execution_context_column = "execution_context" in columns
            for index_row in connection.execute("PRAGMA index_list(paper_orders)"):
                if str(index_row[1]) != "uq_paper_orders_source_intent":
                    continue
                indexed_columns = [
                    str(column[2])
                    for column in connection.execute(
                        "PRAGMA index_info(uq_paper_orders_source_intent)"
                    )
                ]
                source_index = (
                    int(index_row[2]) == 1
                    and int(index_row[4]) == 1
                    and indexed_columns == ["source_intent_id"]
                )
            schema_state = FindingState.PASS if source_column and source_index else FindingState.ISSUE
            add(
                "source_intent_uniqueness_schema", 0 if schema_state is FindingState.PASS else 1,
                severity=FindingSeverity.MINOR,
                capability=ReconciliationCapability.AUTO_REPAIR_SAFE,
                evidence="Read-only check of nullable source_intent_id and its partial unique index; no migration run.",
                state=schema_state,
            )

        if {"paper_orders", "paper_fills"} <= tables:
            missing_fill, multiple_fill, fill_without_order = connection.execute(
                """
                WITH fill_counts AS (
                    SELECT order_id, COUNT(*) AS n FROM paper_fills GROUP BY order_id
                )
                SELECT
                    (SELECT COUNT(*) FROM paper_orders o LEFT JOIN fill_counts f
                     ON f.order_id=o.client_order_id WHERE o.status='FILLED' AND COALESCE(f.n,0)<>1),
                    (SELECT COUNT(*) FROM (SELECT order_id FROM paper_fills GROUP BY order_id HAVING COUNT(*)>1)),
                    (SELECT COUNT(*) FROM paper_fills f LEFT JOIN paper_orders o
                     ON o.client_order_id=f.order_id WHERE o.client_order_id IS NULL)
                """
            ).fetchone()
            add("filled_order_exactly_one_fill", int(missing_fill or 0), evidence="FILLED order must have exactly one persisted fill.")
            add("one_fill_per_order", int(multiple_fill or 0), evidence="PaperBroker currently emits one full fill per market order; multiple fill rows are not modeled as partial fills.")
            add("fill_references_existing_order", int(fill_without_order or 0), evidence="Foreign-key-compatible order_id link checked explicitly; SQLite foreign-key enforcement is connection-local.")
            open_sell_rows = connection.execute(
                "SELECT " + ("source_intent_id " if source_column else "NULL AS source_intent_id ") + ", "
                + ("execution_context " if execution_context_column else "NULL AS execution_context ")
                + "FROM paper_orders WHERE side='SELL' AND status IN ('NEW','ACCEPTED','OPEN')"
            ).fetchall()
            open_sells = 0
            for row in open_sell_rows:
                try:
                    context = json.loads(row["execution_context"]) if row["execution_context"] else None
                except (TypeError, json.JSONDecodeError):
                    context = None
                exit_context = context.get("exit") if isinstance(context, dict) else None
                recoverable = (
                    source_column
                    and str(row["source_intent_id"] or "").startswith("paper_exit:")
                    and isinstance(exit_context, dict)
                    and {"entry_date", "entry_price", "exit_date", "holding_days", "exit_reason"} <= set(exit_context)
                )
                if not recoverable:
                    open_sells += 1
            add("unresolved_sell_order", open_sells,
                severity=FindingSeverity.MATERIAL,
                capability=ReconciliationCapability.MANUAL_REVIEW_REQUIRED,
                evidence="Legacy or incomplete accepted SELLs lack a valid paper_exit key/context and require review; valid durable exits are resumable and are not classified as unresolved.")

        if "paper_orders" in tables and source_column:
            duplicate_sources = int(connection.execute(
                "SELECT COUNT(*) FROM (SELECT source_intent_id FROM paper_orders "
                "WHERE source_intent_id IS NOT NULL GROUP BY source_intent_id HAVING COUNT(*)>1)"
            ).fetchone()[0])
            add("source_intent_unique", duplicate_sources, evidence="Non-NULL source keys are grouped independently of whether the partial index exists.")

            if "paper_pending_signals" in tables:
                terminal_missing = int(connection.execute(
                    """
                    SELECT COUNT(*) FROM paper_pending_signals p
                    LEFT JOIN paper_orders o ON o.source_intent_id='pending_signal:' || p.id
                    LEFT JOIN paper_fills f ON f.order_id=o.client_order_id
                    WHERE p.status IN ('FILLED','RESUMED','ALREADY_EXECUTED')
                      AND (o.client_order_id IS NULL OR o.status<>'FILLED' OR f.id IS NULL)
                    """
                ).fetchone()[0])
                add("completed_pending_has_filled_source_execution", terminal_missing, evidence="Only statuses used for successful execution completion are required to link to a filled source order and fill.")
                missing_pending = int(connection.execute(
                    """
                    SELECT COUNT(*) FROM paper_orders o
                    LEFT JOIN paper_pending_signals p
                      ON o.source_intent_id='pending_signal:' || p.id
                    WHERE o.source_intent_id LIKE 'pending_signal:%' AND p.id IS NULL
                    """
                ).fetchone()[0])
                add("source_execution_has_pending_origin", missing_pending,
                    severity=FindingSeverity.MINOR,
                    capability=ReconciliationCapability.MANUAL_REVIEW_REQUIRED,
                    evidence="A missing pending row may reflect external reset/retention or broken provenance; origin must not be guessed.")

        if {"paper_positions", "paper_position_lifecycle"} <= tables:
            missing_lifecycle = int(connection.execute(
                "SELECT COUNT(*) FROM paper_positions p LEFT JOIN paper_position_lifecycle l "
                "ON l.symbol=p.symbol WHERE p.quantity>0 AND l.symbol IS NULL"
            ).fetchone()[0])
            orphan_lifecycle = int(connection.execute(
                "SELECT COUNT(*) FROM paper_position_lifecycle l LEFT JOIN paper_positions p "
                "ON p.symbol=l.symbol WHERE p.symbol IS NULL OR p.quantity<=0"
            ).fetchone()[0])
            add("open_position_has_lifecycle", missing_lifecycle,
                evidence="An open position without lifecycle lacks durable exit/risk state; strategy/config context is not inferred.")
            add("lifecycle_has_open_position", orphan_lifecycle,
                evidence="An orphan lifecycle can represent a crash during exit cleanup; automatic deletion would discard stop/target provenance.")

        if {"paper_positions", "paper_fills"} <= tables:
            fill_balances = {
                str(row["symbol"]): int(row["net_quantity"])
                for row in connection.execute(
                    """
                    SELECT symbol, SUM(CASE side WHEN 'BUY' THEN quantity
                                                  WHEN 'SELL' THEN -quantity ELSE 0 END) AS net_quantity
                    FROM paper_fills GROUP BY symbol
                    """
                )
            }
            position_rows = connection.execute(
                "SELECT symbol,quantity FROM paper_positions WHERE quantity>0"
            ).fetchall()
            quantity_differences = sum(
                int(row["quantity"]) != fill_balances.get(str(row["symbol"]), 0)
                for row in position_rows
            ) + sum(
                symbol not in {str(row["symbol"]) for row in position_rows} and quantity != 0
                for symbol, quantity in fill_balances.items()
            )
            add("position_quantity_vs_fill_net", int(quantity_differences),
                severity=FindingSeverity.MINOR,
                capability=ReconciliationCapability.MANUAL_REVIEW_REQUIRED,
                evidence="Net fills omit any pre-ledger opening inventory; any difference is AMBIGUOUS, not a safe repair signal.",
                state=FindingState.AMBIGUOUS if quantity_differences else FindingState.PASS)

        if {"paper_closed_trades", "paper_fills", "paper_orders"} <= tables:
            closed_ref = int(connection.execute(
                """
                SELECT COUNT(*) FROM paper_closed_trades c
                LEFT JOIN paper_orders o ON o.client_order_id=c.order_id
                LEFT JOIN paper_fills f ON f.order_id=c.order_id
                WHERE o.client_order_id IS NULL OR o.side<>'SELL' OR f.order_id IS NULL
                """
            ).fetchone()[0])
            add("closed_trade_has_sell_order_and_fill", closed_ref,
                evidence="Closed-trade order_id must reference a persisted SELL order/fill; this does not prove lifecycle cleanup completed.")
            missing_closed = int(connection.execute(
                """
                SELECT COUNT(*) FROM paper_fills f
                JOIN paper_orders o ON o.client_order_id=f.order_id
                LEFT JOIN paper_closed_trades c ON c.order_id=o.client_order_id
                WHERE f.side='SELL' AND c.order_id IS NULL
                """
            ).fetchone()[0])
            add("sell_fill_has_closed_trade_record", missing_closed,
                severity=FindingSeverity.MATERIAL,
                capability=ReconciliationCapability.MANUAL_REVIEW_REQUIRED,
                evidence="Lifecycle manager persists a close record after SELL fill; missing record can be a crash window, but reconstructing all context is not automatic.")

        if "paper_orders" in tables and source_column:
            unlinked_sell_count = int(connection.execute(
                "SELECT COUNT(*) FROM paper_orders WHERE side='SELL' "
                "AND (source_intent_id IS NULL OR source_intent_id NOT LIKE 'paper_exit:%')"
            ).fetchone()[0])
            add(
                "exit_intent_durable_link", unlinked_sell_count,
                severity=FindingSeverity.MATERIAL,
                capability=ReconciliationCapability.MANUAL_REVIEW_REQUIRED,
                evidence="New protected exits use unique paper_exit source keys; legacy/unrecognized SELL rows remain unlinked and are not assigned fabricated provenance.",
                state=(FindingState.AMBIGUOUS if unlinked_sell_count else FindingState.PASS),
            )
        elif "paper_orders" in tables:
            checks.append(PaperConsistencyCheck(
                invariant="exit_intent_durable_link",
                state=FindingState.NOT_AUDITABLE,
                severity=FindingSeverity.MATERIAL,
                capability=ReconciliationCapability.MANUAL_REVIEW_REQUIRED,
                affected_count=None,
                evidence="The legacy paper_orders schema has no source-intent column; exit provenance is not inferred or backfilled.",
            ))

        if "paper_position_lifecycle" in tables:
            lifecycle_columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(paper_position_lifecycle)")
            }
            provenance_columns = {"entry_order_id", "strategy_version", "policy_fingerprint"}
            if provenance_columns <= lifecycle_columns:
                missing_context = int(connection.execute(
                    "SELECT COUNT(*) FROM paper_positions p "
                    "LEFT JOIN paper_position_lifecycle l ON l.symbol=p.symbol "
                    "WHERE p.quantity>0 AND (l.symbol IS NULL OR l.entry_order_id IS NULL "
                    "OR l.strategy_version IS NULL OR l.policy_fingerprint IS NULL)"
                ).fetchone()[0])
                add(
                    "lifecycle_reconstruction_context_persisted", missing_context,
                    severity=FindingSeverity.MATERIAL,
                    capability=ReconciliationCapability.REPAIR_WITH_FROZEN_CONTEXT,
                    evidence="New lifecycle rows persist entry order, strategy version and frozen policy fingerprint; legacy rows with missing provenance require review and are not reconstructed from current policy.",
                    state=(FindingState.AMBIGUOUS if missing_context else FindingState.PASS),
                )
            else:
                checks.append(PaperConsistencyCheck(
                    invariant="lifecycle_reconstruction_context_persisted",
                    state=FindingState.AMBIGUOUS,
                    severity=FindingSeverity.MATERIAL,
                    capability=ReconciliationCapability.REPAIR_WITH_FROZEN_CONTEXT,
                    affected_count=None,
                    evidence="Lifecycle schema predates frozen provenance columns; legacy values cannot be inferred from current configuration.",
                ))

        if {"paper_metadata", "paper_fills"} <= tables:
            metadata = {
                str(row["key"]): row["value"]
                for row in connection.execute(
                    "SELECT key,value FROM paper_metadata WHERE key IN ('cash','initial_cash')"
                )
            }
            if "cash" not in metadata or "initial_cash" not in metadata:
                add("cash_reconstructable_from_durable_fills", None,
                    severity=FindingSeverity.MATERIAL,
                    capability=ReconciliationCapability.MANUAL_REVIEW_REQUIRED,
                    evidence="Current/base cash metadata is missing; no configured fallback is substituted.",
                    state=FindingState.NOT_AUDITABLE)
            else:
                try:
                    cash = float(json.loads(metadata["cash"]))
                    initial_cash = float(json.loads(metadata["initial_cash"]))
                    net_cash_flow = float(connection.execute(
                        "SELECT COALESCE(SUM(net_cash_flow),0.0) FROM paper_fills"
                    ).fetchone()[0])
                    matches = abs((initial_cash + net_cash_flow) - cash) <= max(0.01, abs(cash) * 1e-10)
                except (ValueError, TypeError, json.JSONDecodeError):
                    matches = False
                add("cash_reconstructable_from_durable_fills", 0 if matches else 1,
                    severity=FindingSeverity.MATERIAL,
                    capability=ReconciliationCapability.MANUAL_REVIEW_REQUIRED,
                    evidence="Compare persisted cash to initial_cash plus all durable fill net_cash_flow; mismatch is not auto-repaired.",
                    state=FindingState.PASS if matches else FindingState.AMBIGUOUS)

        payload = {
            "contract": CONTRACT, "version": VERSION, "paper_version": version.value,
            "schema_tables": sorted(table_names), "source_intent_column_present": source_column,
            "source_intent_unique_index_present": source_index,
            "checks": [
                {"invariant": check.invariant, "state": check.state.value,
                 "severity": check.severity.value, "capability": check.capability.value,
                 "affected_count": check.affected_count, "evidence": check.evidence}
                for check in sorted(checks, key=lambda row: row.invariant)
            ],
            "limitations": ("read_only", "no_context_guessing", "no_automatic_repair"),
        }
        identity = sha256(canonical_json(canonical_identity_value(payload))).hexdigest()
        return PaperReconciliationAuditResult(
            paper_version=version, database_path=str(path), schema_tables=table_names,
            source_intent_column_present=source_column,
            source_intent_unique_index_present=source_index,
            checks=tuple(checks), read_only=True, identity=identity,
        )
    finally:
        connection.close()


__all__ = (
    "FindingSeverity", "FindingState", "PaperConsistencyCheck", "PaperReconciliationAuditResult",
    "PaperVersion", "ReconciliationCapability", "audit_paper_database",
)
