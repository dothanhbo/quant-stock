from __future__ import annotations

"""Append-only SQLite persistence for prospective validation evidence."""

import json
from pathlib import Path
import sqlite3
from typing import Iterable

from .contracts import (
    AuditEventType,
    ForwardAuditEvent,
    ForwardFormation,
    ForwardMaturity,
    ForwardOutcome,
    ForwardPosition,
    ForwardProtocolActivation,
    MaturityStatus,
    OutcomeAvailability,
)


TABLES = (
    "forward_protocols", "forward_formations", "forward_positions",
    "forward_maturities", "forward_outcomes", "forward_audit_events",
)


class ForwardValidationLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS forward_protocols (
                    protocol_id TEXT PRIMARY KEY,
                    protocol_fingerprint TEXT NOT NULL UNIQUE,
                    protocol_version TEXT NOT NULL,
                    activated_at_utc TEXT NOT NULL,
                    activation_market_session_boundary TEXT NOT NULL,
                    operational_start_after_session TEXT NOT NULL,
                    source_phase8_manifest_identity TEXT NOT NULL,
                    source_phase8_manifest_sha256 TEXT NOT NULL,
                    selection_policy TEXT NOT NULL,
                    weighting_policy TEXT NOT NULL,
                    budget INTEGER NOT NULL,
                    tracked_horizons_json TEXT NOT NULL,
                    benchmark TEXT NOT NULL,
                    activation_identity TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS forward_formations (
                    formation_identity TEXT PRIMARY KEY,
                    protocol_id TEXT NOT NULL REFERENCES forward_protocols(protocol_id),
                    protocol_fingerprint TEXT NOT NULL,
                    formation_session TEXT NOT NULL,
                    recorded_at_utc TEXT NOT NULL,
                    source_snapshot_identity TEXT NOT NULL,
                    completed_session_set_identity TEXT NOT NULL,
                    selection_policy_identity TEXT NOT NULL,
                    eligible_universe_identity TEXT NOT NULL,
                    selection_identity TEXT NOT NULL,
                    ordered_selected_symbols_json TEXT NOT NULL,
                    actual_selected_count INTEGER NOT NULL,
                    budget INTEGER NOT NULL,
                    weighting_policy TEXT NOT NULL,
                    gross_weight REAL NOT NULL,
                    cash_weight REAL NOT NULL,
                    benchmark_formation_close REAL,
                    UNIQUE(protocol_id, formation_session)
                );
                CREATE TABLE IF NOT EXISTS forward_positions (
                    formation_identity TEXT NOT NULL REFERENCES forward_formations(formation_identity),
                    rank INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    weight REAL NOT NULL,
                    formation_close REAL,
                    position_identity TEXT NOT NULL UNIQUE,
                    PRIMARY KEY(formation_identity, rank),
                    UNIQUE(formation_identity, symbol)
                );
                CREATE TABLE IF NOT EXISTS forward_maturities (
                    event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    maturity_identity TEXT NOT NULL UNIQUE,
                    protocol_id TEXT NOT NULL REFERENCES forward_protocols(protocol_id),
                    formation_identity TEXT NOT NULL REFERENCES forward_formations(formation_identity),
                    formation_session TEXT NOT NULL,
                    horizon_sessions INTEGER NOT NULL,
                    target_session TEXT,
                    status TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    recorded_at_utc TEXT NOT NULL,
                    UNIQUE(formation_identity, horizon_sessions, status)
                );
                CREATE TABLE IF NOT EXISTS forward_outcomes (
                    outcome_identity TEXT PRIMARY KEY,
                    protocol_id TEXT NOT NULL REFERENCES forward_protocols(protocol_id),
                    formation_identity TEXT NOT NULL REFERENCES forward_formations(formation_identity),
                    horizon_sessions INTEGER NOT NULL,
                    target_session TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    weight REAL NOT NULL,
                    availability TEXT NOT NULL,
                    stock_forward_return_pct REAL,
                    benchmark_forward_return_pct REAL,
                    excess_forward_return_pct_points REAL,
                    recorded_at_utc TEXT NOT NULL,
                    UNIQUE(formation_identity, horizon_sessions, symbol)
                );
                CREATE TABLE IF NOT EXISTS forward_audit_events (
                    event_identity TEXT PRIMARY KEY,
                    protocol_id TEXT NOT NULL REFERENCES forward_protocols(protocol_id),
                    event_type TEXT NOT NULL,
                    market_session TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    recorded_at_utc TEXT NOT NULL,
                    UNIQUE(protocol_id, event_type, market_session)
                );
            """)
            for table in TABLES:
                connection.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, 'append-only table: {table}'); END"
                )
                connection.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, 'append-only table: {table}'); END"
                )

    def activate(self, activation: ForwardProtocolActivation) -> bool:
        self.initialize()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM forward_protocols WHERE protocol_id=?",
                (activation.protocol_id,),
            ).fetchone()
            if existing is not None:
                if existing["activation_identity"] == activation.activation_identity:
                    return False
                raise ValueError("conflicting activation for immutable protocol")
            connection.execute(
                "INSERT INTO forward_protocols VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    activation.protocol_id, activation.protocol_fingerprint, activation.protocol_version,
                    activation.activated_at_utc, activation.activation_market_session_boundary,
                    activation.operational_start_after_session, activation.source_phase8_manifest_identity,
                    activation.source_phase8_manifest_sha256, activation.selection_policy,
                    activation.weighting_policy, activation.budget,
                    json.dumps(activation.tracked_horizons, separators=(",", ":")),
                    activation.benchmark, activation.activation_identity,
                ),
            )
        return True

    def activation(self, protocol_id: str) -> ForwardProtocolActivation | None:
        if not self.path.exists():
            return None
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM forward_protocols WHERE protocol_id=?", (protocol_id,)).fetchone()
        if row is None:
            return None
        return ForwardProtocolActivation(
            row["protocol_id"], row["protocol_fingerprint"], row["protocol_version"],
            row["activated_at_utc"], row["activation_market_session_boundary"],
            row["operational_start_after_session"], row["source_phase8_manifest_identity"],
            row["source_phase8_manifest_sha256"], row["selection_policy"], row["weighting_policy"],
            row["budget"], tuple(json.loads(row["tracked_horizons_json"])), row["benchmark"],
            row["activation_identity"],
        )

    def record_formation(self, formation: ForwardFormation, horizons: tuple[int, ...]) -> bool:
        activation = self.activation(formation.protocol_id)
        if activation is None:
            raise ValueError("protocol must be activated before recording formations")
        if formation.formation_session <= activation.operational_start_after_session:
            raise ValueError("formation violates prospective operational cutoff")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT formation_identity FROM forward_formations WHERE protocol_id=? AND formation_session=?",
                (formation.protocol_id, formation.formation_session),
            ).fetchone()
            if existing is not None:
                if existing["formation_identity"] == formation.formation_identity:
                    return False
                raise ValueError("conflicting immutable formation replay")
            try:
                connection.execute(
                    "INSERT INTO forward_formations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        formation.formation_identity, formation.protocol_id, formation.protocol_fingerprint,
                        formation.formation_session, formation.recorded_at_utc, formation.source_snapshot_identity,
                        formation.completed_session_set_identity,
                        formation.selection_policy_identity, formation.eligible_universe_identity,
                        formation.selection_identity,
                        json.dumps(formation.ordered_selected_symbols, separators=(",", ":")),
                        formation.actual_selected_count, formation.budget, formation.weighting_policy,
                        formation.gross_weight, formation.cash_weight, formation.benchmark_formation_close,
                    ),
                )
                connection.executemany(
                    "INSERT INTO forward_positions VALUES (?,?,?,?,?,?)",
                    [
                        (
                            formation.formation_identity, item.rank, item.symbol, item.weight,
                            item.formation_close, item.position_identity,
                        ) for item in formation.positions
                    ],
                )
                for horizon in horizons:
                    payload = {
                        "protocol": formation.protocol_id, "formation": formation.formation_identity,
                        "horizon": horizon, "target": None, "status": MaturityStatus.PENDING.value,
                        "reason": "AWAITING_EXACT_VNINDEX_SESSION", "recorded_at_utc": formation.recorded_at_utc,
                    }
                    from .contracts import identity
                    maturity_identity = identity(payload)
                    connection.execute(
                        "INSERT INTO forward_maturities "
                        "(maturity_identity,protocol_id,formation_identity,formation_session,horizon_sessions,target_session,status,reason_code,recorded_at_utc) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            maturity_identity, formation.protocol_id, formation.formation_identity,
                            formation.formation_session, horizon, None, MaturityStatus.PENDING.value,
                            "AWAITING_EXACT_VNINDEX_SESSION", formation.recorded_at_utc,
                        ),
                    )
            except Exception:
                connection.rollback()
                raise
        return True

    def formations(self, protocol_id: str) -> tuple[ForwardFormation, ...]:
        if not self.path.exists():
            return ()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM forward_formations WHERE protocol_id=? ORDER BY formation_session",
                (protocol_id,),
            ).fetchall()
            results = []
            for row in rows:
                position_rows = connection.execute(
                    "SELECT * FROM forward_positions WHERE formation_identity=? ORDER BY rank",
                    (row["formation_identity"],),
                ).fetchall()
                positions = tuple(ForwardPosition(
                    item["symbol"], item["rank"], item["weight"], item["formation_close"], item["position_identity"],
                ) for item in position_rows)
                results.append(ForwardFormation(
                    row["protocol_id"], row["protocol_fingerprint"], row["formation_session"],
                    row["recorded_at_utc"], row["source_snapshot_identity"], row["completed_session_set_identity"],
                    row["selection_policy_identity"],
                    row["eligible_universe_identity"], row["selection_identity"],
                    tuple(json.loads(row["ordered_selected_symbols_json"])), row["actual_selected_count"],
                    row["budget"], row["weighting_policy"], positions, row["gross_weight"], row["cash_weight"],
                    row["benchmark_formation_close"], row["formation_identity"],
                ))
        return tuple(results)

    def record_maturities(self, maturities: Iterable[ForwardMaturity]) -> int:
        inserted = 0
        with self._connect() as connection:
            for item in maturities:
                if item.status is MaturityStatus.PENDING:
                    continue
                existing = connection.execute(
                    "SELECT * FROM forward_maturities WHERE formation_identity=? AND horizon_sessions=? AND status=?",
                    (item.formation_identity, item.horizon_sessions, item.status.value),
                ).fetchone()
                if existing is not None:
                    if existing["maturity_identity"] == item.maturity_identity:
                        continue
                    raise ValueError("conflicting immutable maturity event")
                connection.execute(
                    "INSERT INTO forward_maturities "
                    "(maturity_identity,protocol_id,formation_identity,formation_session,horizon_sessions,target_session,status,reason_code,recorded_at_utc) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        item.maturity_identity, item.protocol_id, item.formation_identity,
                        item.formation_session, item.horizon_sessions, item.target_session,
                        item.status.value, item.reason_code, item.recorded_at_utc,
                    ),
                )
                inserted += 1
        return inserted

    def latest_maturities(self, protocol_id: str) -> tuple[ForwardMaturity, ...]:
        if not self.path.exists():
            return ()
        query = """
            SELECT m.* FROM forward_maturities m
            JOIN (
                SELECT formation_identity,horizon_sessions,MAX(event_sequence) AS seq
                FROM forward_maturities GROUP BY formation_identity,horizon_sessions
            ) latest ON latest.seq=m.event_sequence
            WHERE m.protocol_id=? ORDER BY m.formation_session,m.horizon_sessions
        """
        with self._connect() as connection:
            rows = connection.execute(query, (protocol_id,)).fetchall()
        return tuple(ForwardMaturity(
            row["protocol_id"], row["formation_identity"], row["formation_session"],
            row["horizon_sessions"], row["target_session"], MaturityStatus(row["status"]),
            row["reason_code"], row["recorded_at_utc"], row["maturity_identity"],
        ) for row in rows)

    def record_outcomes(self, outcomes: Iterable[ForwardOutcome]) -> int:
        values = tuple(outcomes)
        if not values:
            return 0
        inserted = 0
        with self._connect() as connection:
            for item in values:
                maturity = connection.execute(
                    "SELECT * FROM forward_maturities WHERE formation_identity=? AND horizon_sessions=? AND status=?",
                    (item.formation_identity, item.horizon_sessions, MaturityStatus.MATURED.value),
                ).fetchone()
                if maturity is None or maturity["target_session"] != item.target_session:
                    raise ValueError("outcome target has not matured exactly")
                existing = connection.execute(
                    "SELECT * FROM forward_outcomes WHERE formation_identity=? AND horizon_sessions=? AND symbol=?",
                    (item.formation_identity, item.horizon_sessions, item.symbol),
                ).fetchone()
                if existing is not None:
                    if existing["outcome_identity"] == item.outcome_identity:
                        continue
                    raise ValueError("conflicting immutable outcome replay")
                connection.execute(
                    "INSERT INTO forward_outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        item.outcome_identity, item.protocol_id, item.formation_identity,
                        item.horizon_sessions, item.target_session, item.symbol, item.weight,
                        item.availability.value, item.stock_forward_return_pct,
                        item.benchmark_forward_return_pct, item.excess_forward_return_pct_points,
                        item.recorded_at_utc,
                    ),
                )
                inserted += 1
        return inserted

    def outcomes(self, protocol_id: str) -> tuple[ForwardOutcome, ...]:
        if not self.path.exists():
            return ()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM forward_outcomes WHERE protocol_id=? ORDER BY formation_identity,horizon_sessions,symbol",
                (protocol_id,),
            ).fetchall()
        return tuple(ForwardOutcome(
            row["protocol_id"], row["formation_identity"], row["horizon_sessions"],
            row["target_session"], row["symbol"], row["weight"], OutcomeAvailability(row["availability"]),
            row["stock_forward_return_pct"], row["benchmark_forward_return_pct"],
            row["excess_forward_return_pct_points"], row["recorded_at_utc"], row["outcome_identity"],
        ) for row in rows)

    def record_audit_events(self, events: Iterable[ForwardAuditEvent]) -> int:
        inserted = 0
        with self._connect() as connection:
            for item in events:
                existing = connection.execute(
                    "SELECT event_identity FROM forward_audit_events WHERE protocol_id=? AND event_type=? AND market_session=?",
                    (item.protocol_id, item.event_type.value, item.market_session),
                ).fetchone()
                if existing is not None:
                    if existing["event_identity"] == item.event_identity:
                        continue
                    raise ValueError("conflicting audit event")
                connection.execute(
                    "INSERT INTO forward_audit_events VALUES (?,?,?,?,?,?)",
                    (
                        item.event_identity, item.protocol_id, item.event_type.value,
                        item.market_session, item.reason_code, item.recorded_at_utc,
                    ),
                )
                inserted += 1
        return inserted

    def audit_events(self, protocol_id: str) -> tuple[ForwardAuditEvent, ...]:
        if not self.path.exists():
            return ()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM forward_audit_events WHERE protocol_id=? ORDER BY market_session,event_type",
                (protocol_id,),
            ).fetchall()
        return tuple(ForwardAuditEvent(
            row["protocol_id"], AuditEventType(row["event_type"]), row["market_session"],
            row["reason_code"], row["recorded_at_utc"], row["event_identity"],
        ) for row in rows)
