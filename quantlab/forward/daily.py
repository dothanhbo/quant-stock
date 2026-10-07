from __future__ import annotations

"""Thin daily orchestration for the activated prospective V1 ledger."""

from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Any

from core.database_coverage import build_database_coverage_index
from core.paths import resolve_market_database_path
from quantlab.catalog import build_market_data_snapshot
from quantlab.evaluation import (
    NEUTRAL_ADX_RSI_SELECTION_DIAGNOSTICS_V1,
    rank_panel_policy_candidates,
)
from quantlab.features import (
    FeatureRegistry,
    FeatureRequest,
    PointInTimeUniverseContext,
    builtin_definitions,
)

from .contracts import (
    ForwardDailyOperationResult,
    ForwardDailyOperationState,
    MaturityStatus,
    identity,
)
from .ledger import ForwardValidationLedger
from .protocol import load_protocol_spec, verify_phase8_authorization
from .semantics import (
    build_forward_formation,
    build_forward_status,
    build_matured_outcomes,
    detect_missing_formations,
    evaluate_formation_maturities,
    reconcile_missing_formations,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROTOCOL_SPEC = PROJECT_ROOT / "research/forward_validation/protocol_v1.json"
DEFAULT_PHASE8_ROOT = PROJECT_ROOT / "research_results/quantlab_portfolio_research_synthesis_2018-08-07_2026-09-17"
DEFAULT_LEDGER_PATH = PROJECT_ROOT / "data/forward_validation.db"
MINIMUM_HISTORY_SESSIONS = 50
MAXIMUM_STALENESS_SESSIONS = 5


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _exact_row(frame: Any, session: str) -> Any | None:
    if frame.empty:
        return None
    dates = frame["time"].dt.date.astype(str)
    rows = frame.loc[dates == session]
    return None if rows.empty else rows.iloc[-1]


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _formation_evidence(
    *,
    protocol,
    snapshot,
    session: str,
    database_path: Path,
) -> dict[str, Any]:
    coverage = build_database_coverage_index(
        session,
        session,
        minimum_history_sessions=MINIMUM_HISTORY_SESSIONS,
        maximum_staleness_sessions=MAXIMUM_STALENESS_SESSIONS,
        database_path=database_path,
    )
    context = PointInTimeUniverseContext.from_coverage_index(coverage)
    members = tuple(sorted(context.members_as_of(session)))
    if not members:
        raise ValueError("latest completed session has no database-coverage members")

    request = FeatureRequest("historical_candidate_per_symbol_subset", "v2")
    features = FeatureRegistry(builtin_definitions()).compute(
        request,
        snapshot,
        (*members, protocol.benchmark),
        start_date=session,
        through_date=session,
    )
    eligible: list[tuple[str, float, float]] = []
    formation_closes: dict[str, float | None] = {}
    for symbol in members:
        row = _exact_row(features.frame_for(symbol), session)
        if row is None:
            continue
        close = _finite(row["close"])
        adx = _finite(row["ADX14"])
        rsi = _finite(row["RSI"])
        if close is None or close <= 0 or adx is None or rsi is None:
            continue
        eligible.append((symbol, adx, rsi))
        formation_closes[symbol] = close

    policy = next(
        item
        for item in NEUTRAL_ADX_RSI_SELECTION_DIAGNOSTICS_V1.policies
        if item.name == protocol.selection_policy
    )
    if policy.fingerprint != protocol.selection_policy_identity:
        raise ValueError("activated selection policy identity is not the frozen ADX policy")
    ordering = rank_panel_policy_candidates(tuple(eligible), policy)
    selected = tuple(symbol for symbol, _score in ordering[: protocol.budget])
    if not selected:
        raise ValueError("latest completed session has no eligible ADX selection")

    benchmark_row = _exact_row(features.frame_for(protocol.benchmark), session)
    benchmark_close = None if benchmark_row is None else _finite(benchmark_row["close"])
    selection_identity = identity({
        "contract": "quantlab.forward_daily_selection_evidence",
        "version": "v1",
        "snapshot_id": snapshot.snapshot_id,
        "session": session,
        "eligible_universe_identity": context.membership_identity,
        "selection_policy_identity": policy.fingerprint,
        "budget": protocol.budget,
        "eligibility": "finite same-date ADX14 and RSI14 within database coverage 50/5",
        "complete_eligible_factor_evidence": tuple(eligible),
        "complete_policy_score_ordering": ordering,
        "ordered_selected_symbols": selected,
    })
    return {
        "ordered_symbols": selected,
        "formation_closes": formation_closes,
        "benchmark_formation_close": benchmark_close,
        "eligible_universe_identity": context.membership_identity,
        "selection_identity": selection_identity,
    }


def _target_closes(snapshot, symbols: tuple[str, ...], session: str) -> dict[str, float | None]:
    bundle = snapshot.load_ohlcv(symbols, start_date=session, through_date=session)
    result: dict[str, float | None] = {}
    for symbol in symbols:
        row = _exact_row(bundle.frame_for(symbol), session)
        result[symbol] = None if row is None else _finite(row["close"])
    return result


def run_forward_validation_daily(
    *,
    database_path: str | Path | None = None,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
    protocol_spec: str | Path = DEFAULT_PROTOCOL_SPEC,
    phase8_root: str | Path = DEFAULT_PHASE8_ROOT,
    recorded_at_utc: str | None = None,
) -> ForwardDailyOperationResult:
    """Record only the latest eligible formation, mature prior evidence, and report status."""
    recorded_at = recorded_at_utc or _utc_now()
    protocol = load_protocol_spec(protocol_spec)
    verify_phase8_authorization(protocol, phase8_root)
    ledger = ForwardValidationLedger(ledger_path)
    activation = ledger.activation(protocol.protocol_id)
    if activation is None:
        raise ValueError("explicit protocol activation is required before daily operation")
    if (
        activation.protocol_id != protocol.protocol_id
        or activation.protocol_fingerprint != protocol.protocol_fingerprint
    ):
        raise ValueError("runtime activation does not match the frozen protocol")

    database = resolve_market_database_path(database_path)
    # Forward formation consumes the eligible universe derived from this
    # database, so any unresolved admission block, pending application or
    # lineage drift refuses the run before a snapshot is built.
    from core.market_provenance_gate import require_market_provenance

    require_market_provenance(database)
    snapshot = build_market_data_snapshot(database)
    benchmark_bundle = snapshot.load_ohlcv((protocol.benchmark,))
    benchmark_frame = benchmark_bundle.frame_for(protocol.benchmark)
    completed_sessions = tuple(
        sorted(benchmark_frame["time"].dt.date.astype(str).unique())
    )
    if not completed_sessions:
        raise ValueError("canonical market database has no completed benchmark sessions")
    latest_session = completed_sessions[-1]
    if latest_session < activation.operational_start_after_session:
        raise ValueError("canonical market database predates the operational activation cutoff")

    formations = ledger.formations(protocol.protocol_id)
    formation_sessions = tuple(item.formation_session for item in formations)
    formation_created = False
    formation_existing = latest_session in formation_sessions
    if latest_session <= activation.operational_start_after_session:
        state = ForwardDailyOperationState.NO_NEW_SESSION
    elif formation_existing:
        state = ForwardDailyOperationState.FORMATION_EXISTING
    else:
        evidence = _formation_evidence(
            protocol=protocol,
            snapshot=snapshot,
            session=latest_session,
            database_path=database,
        )
        formation = build_forward_formation(
            protocol,
            activation,
            formation_session=latest_session,
            recorded_at_utc=recorded_at,
            completed_market_sessions=completed_sessions,
            ordered_symbols=evidence["ordered_symbols"],
            formation_closes=evidence["formation_closes"],
            benchmark_formation_close=evidence["benchmark_formation_close"],
            source_snapshot_identity=snapshot.snapshot_id,
            selection_policy_identity=protocol.selection_policy_identity,
            eligible_universe_identity=evidence["eligible_universe_identity"],
            selection_identity=evidence["selection_identity"],
        )
        formation_created = ledger.record_formation(
            formation,
            protocol.tracked_horizons,
        )
        formation_existing = not formation_created
        state = (
            ForwardDailyOperationState.FORMATION_CREATED
            if formation_created
            else ForwardDailyOperationState.FORMATION_EXISTING
        )

    formations = ledger.formations(protocol.protocol_id)
    maturity_events_created = 0
    outcomes_created = 0
    outcome_keys = {
        (item.formation_identity, item.horizon_sessions, item.symbol)
        for item in ledger.outcomes(protocol.protocol_id)
    }
    latest_by_key = {
        (item.formation_identity, item.horizon_sessions): item
        for item in ledger.latest_maturities(protocol.protocol_id)
    }
    for formation in formations:
        evaluated = evaluate_formation_maturities(
            protocol,
            formation,
            completed_sessions,
            recorded_at_utc=recorded_at,
        )
        for maturity in evaluated:
            existing = latest_by_key.get(
                (maturity.formation_identity, maturity.horizon_sessions)
            )
            if existing is None or existing.status is MaturityStatus.PENDING:
                maturity_events_created += ledger.record_maturities((maturity,))
                effective = maturity
            else:
                effective = existing
            if effective.status is not MaturityStatus.MATURED or effective.target_session is None:
                continue
            missing_symbols = tuple(
                item.symbol
                for item in formation.positions
                if (formation.formation_identity, effective.horizon_sessions, item.symbol)
                not in outcome_keys
            )
            if not missing_symbols:
                continue
            closes = _target_closes(
                snapshot,
                (*tuple(item.symbol for item in formation.positions), protocol.benchmark),
                effective.target_session,
            )
            outcomes = build_matured_outcomes(
                formation,
                effective,
                target_stock_closes={
                    item.symbol: closes.get(item.symbol)
                    for item in formation.positions
                },
                target_benchmark_close=closes.get(protocol.benchmark),
                recorded_at_utc=recorded_at,
            )
            outcomes_created += ledger.record_outcomes(outcomes)
            outcome_keys.update(
                (item.formation_identity, item.horizon_sessions, item.symbol)
                for item in outcomes
            )

    formation_sessions = tuple(item.formation_session for item in formations)
    gaps = detect_missing_formations(
        activation,
        completed_sessions,
        formation_sessions,
        recorded_at_utc=recorded_at,
    )
    gap_events_created = ledger.record_audit_events(gaps)
    audit_events = ledger.audit_events(protocol.protocol_id)
    gap_reconciliation = reconcile_missing_formations(audit_events)
    outcomes = ledger.outcomes(protocol.protocol_id)
    status = build_forward_status(
        activation,
        latest_completed_market_session=latest_session,
        formation_sessions=formation_sessions,
        latest_maturities=ledger.latest_maturities(protocol.protocol_id),
        outcomes=outcomes,
        missing_events=audit_events,
    )
    return ForwardDailyOperationResult(
        protocol_id=status.protocol_id,
        completed_market_session=latest_session,
        operation_state=state,
        formation_created=formation_created,
        formation_existing=formation_existing,
        latest_recorded_formation=status.latest_recorded_formation,
        formation_count=status.formation_count,
        pending_maturity_count=status.pending_maturity_count,
        matured_count_by_horizon=status.matured_count_by_horizon,
        outcome_unavailable_count=status.outcome_unavailable_count,
        missing_formation_sessions=status.missing_formation_sessions,
        maturity_events_created=maturity_events_created,
        outcomes_created=outcomes_created,
        gap_events_created=gap_events_created,
        status_identity=status.status_identity,
        gap_reconciliation=gap_reconciliation,
    )
