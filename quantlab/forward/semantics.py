from __future__ import annotations

"""Pure formation, maturity, outcome, gap, and status semantics."""

import math
from typing import Mapping, Sequence

from .contracts import (
    AuditEventType,
    ForwardAuditEvent,
    ForwardGapReconciliation,
    ForwardGapResolution,
    ForwardFormation,
    ForwardMaturity,
    ForwardOutcome,
    ForwardPosition,
    ForwardProtocolActivation,
    ForwardValidationProtocol,
    ForwardValidationStatus,
    MaturityStatus,
    OutcomeAvailability,
    finite_positive_or_none,
    identity,
)


def build_forward_formation(
    protocol: ForwardValidationProtocol,
    activation: ForwardProtocolActivation,
    *,
    formation_session: str,
    recorded_at_utc: str,
    completed_market_sessions: Sequence[str],
    ordered_symbols: Sequence[str],
    formation_closes: Mapping[str, float | None],
    benchmark_formation_close: float | None,
    source_snapshot_identity: str,
    selection_policy_identity: str,
    eligible_universe_identity: str,
    selection_identity: str,
) -> ForwardFormation:
    if activation.protocol_id != protocol.protocol_id or activation.protocol_fingerprint != protocol.protocol_fingerprint:
        raise ValueError("activation does not belong to protocol")
    if formation_session <= protocol.activation_market_session_boundary:
        raise ValueError("formation session is at or before historical activation boundary")
    if formation_session <= activation.operational_start_after_session:
        raise ValueError("formation session violates operational activation cutoff")
    if selection_policy_identity != protocol.selection_policy_identity:
        raise ValueError("selection policy identity does not match frozen protocol")
    completed_sessions = tuple(sorted(set(completed_market_sessions)))
    if not completed_sessions or formation_session not in completed_sessions:
        raise ValueError("formation session is not a supplied completed canonical market session")
    if formation_session != completed_sessions[-1]:
        raise ValueError("only the latest completed market session may be formed; older gaps cannot be backfilled")
    completed_session_set_identity = identity(completed_sessions)
    symbols = tuple(str(item).strip().upper() for item in ordered_symbols)
    if not symbols or len(symbols) > protocol.budget or len(symbols) != len(set(symbols)):
        raise ValueError("selected symbols must be nonempty, unique, and within budget")
    weight = 1.0 / len(symbols)
    positions = tuple(
        ForwardPosition(
            symbol, rank, weight,
            finite_positive_or_none(formation_closes.get(symbol), name=f"{symbol} formation close"),
            identity({"protocol": protocol.protocol_id, "session": formation_session, "symbol": symbol, "rank": rank, "weight": weight}),
        )
        for rank, symbol in enumerate(symbols, 1)
    )
    gross = sum(item.weight for item in positions)
    cash = 1.0 - gross
    benchmark_close = finite_positive_or_none(benchmark_formation_close, name="benchmark formation close")
    payload = {
        "protocol_id": protocol.protocol_id, "protocol_fingerprint": protocol.protocol_fingerprint,
        "formation_session": formation_session, "recorded_at_utc": recorded_at_utc,
        "source_snapshot_identity": source_snapshot_identity,
        "completed_session_set_identity": completed_session_set_identity,
        "selection_policy_identity": selection_policy_identity,
        "eligible_universe_identity": eligible_universe_identity,
        "selection_identity": selection_identity, "ordered_symbols": symbols,
        "actual_selected_count": len(symbols), "budget": protocol.budget,
        "weighting_policy": protocol.weighting_policy,
        "positions": tuple(item.position_identity for item in positions),
        "gross_weight": gross, "cash_weight": cash,
        "benchmark_formation_close": benchmark_close,
    }
    return ForwardFormation(
        protocol.protocol_id, protocol.protocol_fingerprint, formation_session, recorded_at_utc,
        source_snapshot_identity, completed_session_set_identity, selection_policy_identity, eligible_universe_identity,
        selection_identity, symbols, len(symbols), protocol.budget, protocol.weighting_policy,
        positions, gross, cash, benchmark_close, identity(payload),
    )


def evaluate_formation_maturities(
    protocol: ForwardValidationProtocol,
    formation: ForwardFormation,
    completed_benchmark_sessions: Sequence[str],
    *,
    recorded_at_utc: str,
) -> tuple[ForwardMaturity, ...]:
    sessions = tuple(sorted(set(completed_benchmark_sessions)))
    later = tuple(item for item in sessions if item > formation.formation_session)
    results = []
    for horizon in protocol.tracked_horizons:
        target = later[horizon - 1] if len(later) >= horizon else None
        status = MaturityStatus.MATURED if target is not None else MaturityStatus.PENDING
        reason = "EXACT_TARGET_SESSION_AVAILABLE" if target is not None else "AWAITING_EXACT_VNINDEX_SESSION"
        payload = {
            "protocol": protocol.protocol_id, "formation": formation.formation_identity,
            "horizon": horizon, "target": target, "status": status.value, "reason": reason,
            "recorded_at_utc": recorded_at_utc,
        }
        results.append(ForwardMaturity(
            protocol.protocol_id, formation.formation_identity, formation.formation_session,
            horizon, target, status, reason, recorded_at_utc, identity(payload),
        ))
    return tuple(results)


def build_matured_outcomes(
    formation: ForwardFormation,
    maturity: ForwardMaturity,
    *,
    target_stock_closes: Mapping[str, float | None],
    target_benchmark_close: float | None,
    recorded_at_utc: str,
) -> tuple[ForwardOutcome, ...]:
    if maturity.status is not MaturityStatus.MATURED or maturity.target_session is None:
        raise ValueError("outcomes cannot attach before exact maturity")
    benchmark_target = finite_positive_or_none(target_benchmark_close, name="benchmark target close")
    outcomes = []
    for position in formation.positions:
        stock_target = finite_positive_or_none(target_stock_closes.get(position.symbol), name=f"{position.symbol} target close")
        if position.formation_close is None:
            availability = OutcomeAvailability.MISSING_STOCK_FORMATION_CLOSE
        elif stock_target is None:
            availability = OutcomeAvailability.MISSING_STOCK_TARGET_CLOSE
        elif formation.benchmark_formation_close is None:
            availability = OutcomeAvailability.MISSING_BENCHMARK_FORMATION_CLOSE
        elif benchmark_target is None:
            availability = OutcomeAvailability.MISSING_BENCHMARK_TARGET_CLOSE
        else:
            availability = OutcomeAvailability.AVAILABLE
        if availability is OutcomeAvailability.AVAILABLE:
            stock_return = (float(stock_target) / float(position.formation_close) - 1.0) * 100.0
            benchmark_return = (float(benchmark_target) / float(formation.benchmark_formation_close) - 1.0) * 100.0
            excess = stock_return - benchmark_return
        else:
            stock_return = benchmark_return = excess = None
        payload = {
            "protocol": formation.protocol_id, "formation": formation.formation_identity,
            "horizon": maturity.horizon_sessions, "target": maturity.target_session,
            "symbol": position.symbol, "weight": position.weight,
            "availability": availability.value, "stock": stock_return,
            "benchmark": benchmark_return, "excess": excess,
            "recorded_at_utc": recorded_at_utc,
        }
        outcomes.append(ForwardOutcome(
            formation.protocol_id, formation.formation_identity, maturity.horizon_sessions,
            maturity.target_session, position.symbol, position.weight, availability,
            stock_return, benchmark_return, excess, recorded_at_utc, identity(payload),
        ))
    return tuple(outcomes)


def detect_missing_formations(
    activation: ForwardProtocolActivation,
    completed_benchmark_sessions: Sequence[str],
    recorded_formation_sessions: Sequence[str],
    *,
    recorded_at_utc: str,
) -> tuple[ForwardAuditEvent, ...]:
    recorded = set(recorded_formation_sessions)
    expected = tuple(sorted({item for item in completed_benchmark_sessions if item > activation.operational_start_after_session}))
    return tuple(
        ForwardAuditEvent(
            activation.protocol_id, AuditEventType.MISSING_FORMATION, session,
            "COMPLETED_SESSION_HAS_NO_FORMATION_RECORD", recorded_at_utc,
            identity({"protocol": activation.protocol_id, "event": "MISSING_FORMATION", "session": session}),
        )
        for session in expected if session not in recorded
    )


def reconcile_missing_formations(
    events: Sequence[ForwardAuditEvent],
) -> tuple[ForwardGapReconciliation, ...]:
    """Classify persisted gaps without fabricating prospective evidence.

    A missed formation cannot be backfilled after its session has passed.  The
    existing append-only audit event is therefore the deterministic operator
    acknowledgement; recovery means continuing from the next valid session.
    """
    return tuple(
        ForwardGapReconciliation(
            event.protocol_id,
            event.market_session,
            ForwardGapResolution.ACKNOWLEDGED_UNRECOVERABLE,
            "DO_NOT_BACKFILL; continue at next valid session",
            event.reason_code,
            event.event_identity,
        )
        for event in sorted(events, key=lambda item: (item.market_session, item.event_identity))
        if event.event_type is AuditEventType.MISSING_FORMATION
    )


def build_forward_status(
    activation: ForwardProtocolActivation,
    *,
    latest_completed_market_session: str | None,
    formation_sessions: Sequence[str],
    latest_maturities: Sequence[ForwardMaturity],
    outcomes: Sequence[ForwardOutcome],
    missing_events: Sequence[ForwardAuditEvent],
) -> ForwardValidationStatus:
    pending = sum(item.status is MaturityStatus.PENDING for item in latest_maturities)
    matured = {
        horizon: sum(item.status is MaturityStatus.MATURED and item.horizon_sessions == horizon for item in latest_maturities)
        for horizon in activation.tracked_horizons
    }
    unavailable = sum(item.availability is not OutcomeAvailability.AVAILABLE for item in outcomes)
    missing = tuple(sorted({item.market_session for item in missing_events}))
    payload = {
        "protocol": activation.protocol_id, "active": True,
        "latest_completed": latest_completed_market_session,
        "latest_formation": max(formation_sessions, default=None), "formation_count": len(formation_sessions),
        "pending": pending, "matured": {str(key): value for key, value in matured.items()},
        "unavailable": unavailable, "missing": missing,
    }
    return ForwardValidationStatus(
        activation.protocol_id, True, latest_completed_market_session,
        max(formation_sessions, default=None), len(formation_sessions), pending, matured,
        unavailable, missing, identity(payload),
    )
