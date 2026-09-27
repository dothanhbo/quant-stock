from __future__ import annotations

"""Operate the explicit, append-only Quant Lab V1 prospective ledger."""

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quantlab.forward import (
    ForwardValidationLedger,
    build_forward_formation,
    build_forward_status,
    build_matured_outcomes,
    create_activation,
    detect_missing_formations,
    evaluate_formation_maturities,
    load_protocol_spec,
    verify_phase8_authorization,
)
from quantlab.forward.contracts import MaturityStatus


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROTOCOL_SPEC = PROJECT_ROOT / "research/forward_validation/protocol_v1.json"
DEFAULT_PHASE8_ROOT = PROJECT_ROOT / "research_results/quantlab_portfolio_research_synthesis_2018-08-07_2026-09-17"
DEFAULT_LEDGER = PROJECT_ROOT / "data/forward_validation.db"


def _payload(path: str | Path) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("operational evidence file must contain one JSON object")
    return value


def _context(arguments):
    protocol = load_protocol_spec(arguments.protocol_spec)
    verify_phase8_authorization(protocol, arguments.phase8_root)
    return protocol, ForwardValidationLedger(arguments.ledger)


def _activate(arguments) -> dict:
    protocol, ledger = _context(arguments)
    activation = create_activation(
        protocol, activated_at_utc=arguments.activated_at_utc,
        confirmed_latest_completed_session=arguments.confirmed_latest_completed_session,
    )
    created = ledger.activate(activation)
    return {
        "command": "activate", "protocol_id": protocol.protocol_id,
        "protocol_fingerprint": protocol.protocol_fingerprint,
        "activation_created": created,
        "operational_start_after_session": activation.operational_start_after_session,
    }


def _record(arguments) -> dict:
    protocol, ledger = _context(arguments)
    activation = ledger.activation(protocol.protocol_id)
    if activation is None:
        raise ValueError("explicit protocol activation is required before record")
    evidence = _payload(arguments.formation_json)
    formation = build_forward_formation(
        protocol, activation,
        formation_session=evidence["formation_session"],
        recorded_at_utc=evidence["recorded_at_utc"],
        completed_market_sessions=evidence["completed_benchmark_sessions"],
        ordered_symbols=evidence["ordered_selected_symbols"],
        formation_closes=evidence["formation_closes"],
        benchmark_formation_close=evidence.get("benchmark_formation_close"),
        source_snapshot_identity=evidence["source_snapshot_identity"],
        selection_policy_identity=evidence["selection_policy_identity"],
        eligible_universe_identity=evidence["eligible_universe_identity"],
        selection_identity=evidence["selection_identity"],
    )
    created = ledger.record_formation(formation, protocol.tracked_horizons)
    return {
        "command": "record", "protocol_id": protocol.protocol_id,
        "formation_session": formation.formation_session,
        "formation_identity": formation.formation_identity, "formation_created": created,
    }


def _mature(arguments) -> dict:
    protocol, ledger = _context(arguments)
    activation = ledger.activation(protocol.protocol_id)
    if activation is None:
        raise ValueError("explicit protocol activation is required before mature")
    evidence = _payload(arguments.market_evidence_json)
    sessions = tuple(evidence["completed_benchmark_sessions"])
    recorded_at = evidence["recorded_at_utc"]
    closes = evidence.get("target_closes_by_session", {})
    maturity_insertions = outcome_insertions = 0
    for formation in ledger.formations(protocol.protocol_id):
        maturities = evaluate_formation_maturities(protocol, formation, sessions, recorded_at_utc=recorded_at)
        maturity_insertions += ledger.record_maturities(maturities)
        for maturity in maturities:
            if maturity.status is not MaturityStatus.MATURED:
                continue
            exact = closes.get(maturity.target_session, {})
            outcomes = build_matured_outcomes(
                formation, maturity, target_stock_closes=exact.get("stock_closes", {}),
                target_benchmark_close=exact.get("benchmark_close"), recorded_at_utc=recorded_at,
            )
            outcome_insertions += ledger.record_outcomes(outcomes)
    formation_sessions = tuple(item.formation_session for item in ledger.formations(protocol.protocol_id))
    gaps = detect_missing_formations(
        activation, sessions, formation_sessions, recorded_at_utc=recorded_at,
    )
    gap_insertions = ledger.record_audit_events(gaps)
    return {
        "command": "mature", "protocol_id": protocol.protocol_id,
        "maturity_events_created": maturity_insertions,
        "outcomes_created": outcome_insertions, "gap_events_created": gap_insertions,
    }


def _status(arguments) -> dict:
    protocol, ledger = _context(arguments)
    activation = ledger.activation(protocol.protocol_id)
    if activation is None:
        return {
            "command": "status", "protocol_id": protocol.protocol_id, "active": False,
            "runtime_ledger_exists": ledger.path.exists(),
        }
    sessions = ()
    if arguments.completed_sessions_json:
        sessions = tuple(_payload(arguments.completed_sessions_json)["completed_benchmark_sessions"])
    formations = ledger.formations(protocol.protocol_id)
    missing = detect_missing_formations(
        activation, sessions, tuple(item.formation_session for item in formations),
        recorded_at_utc=arguments.inspected_at_utc,
    ) if sessions else ledger.audit_events(protocol.protocol_id)
    status = build_forward_status(
        activation,
        latest_completed_market_session=max(sessions, default=None),
        formation_sessions=tuple(item.formation_session for item in formations),
        latest_maturities=ledger.latest_maturities(protocol.protocol_id),
        outcomes=ledger.outcomes(protocol.protocol_id), missing_events=missing,
    )
    return {
        "command": "status", "protocol_id": status.protocol_id, "active": status.active,
        "latest_completed_market_session": status.latest_completed_market_session,
        "latest_recorded_formation": status.latest_recorded_formation,
        "formation_count": status.formation_count,
        "pending_maturity_count": status.pending_maturity_count,
        "matured_count_by_horizon": dict(status.matured_count_by_horizon),
        "outcome_unavailable_count": status.outcome_unavailable_count,
        "missing_formation_sessions": list(status.missing_formation_sessions),
        "status_identity": status.status_identity,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol-spec", default=str(DEFAULT_PROTOCOL_SPEC))
    parser.add_argument("--phase8-root", default=str(DEFAULT_PHASE8_ROOT))
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    commands = parser.add_subparsers(dest="command", required=True)
    activate = commands.add_parser("activate")
    activate.add_argument("--activated-at-utc", required=True)
    activate.add_argument("--confirmed-latest-completed-session", required=True)
    activate.set_defaults(handler=_activate)
    record = commands.add_parser("record")
    record.add_argument("--formation-json", required=True)
    record.set_defaults(handler=_record)
    mature = commands.add_parser("mature")
    mature.add_argument("--market-evidence-json", required=True)
    mature.set_defaults(handler=_mature)
    status = commands.add_parser("status")
    status.add_argument("--completed-sessions-json")
    status.add_argument("--inspected-at-utc", default="STATUS_INSPECTION_ONLY")
    status.set_defaults(handler=_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    print(json.dumps(arguments.handler(arguments), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
