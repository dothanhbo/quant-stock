from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sqlite3

import pytest

from quantlab.forward import (
    ForwardValidationLedger,
    MaturityStatus,
    OutcomeAvailability,
    build_forward_formation,
    build_forward_status,
    build_matured_outcomes,
    create_activation,
    detect_missing_formations,
    evaluate_formation_maturities,
    load_protocol_spec,
    verify_phase8_authorization,
)
from quantlab.forward.contracts import identity
from research.run_quantlab_forward_validation import build_parser


ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "research/forward_validation/protocol_v1.json"
PHASE8_ROOT = ROOT / "research_results/quantlab_portfolio_research_synthesis_2018-08-07_2026-09-17"


@pytest.fixture()
def protocol():
    return load_protocol_spec(SPEC_PATH)


@pytest.fixture()
def activation(protocol):
    return create_activation(
        protocol,
        activated_at_utc="2026-09-27T12:00:00Z",
        confirmed_latest_completed_session="2026-09-17",
    )


def _formation(protocol, activation, *, session="2026-09-18", completed=None, symbols=("BBB", "AAA")):
    completed = completed or (session,)
    return build_forward_formation(
        protocol, activation,
        formation_session=session,
        recorded_at_utc="2026-09-27T12:01:00Z",
        completed_market_sessions=completed,
        ordered_symbols=symbols,
        formation_closes={symbol: 100.0 + index for index, symbol in enumerate(symbols)},
        benchmark_formation_close=1000.0,
        source_snapshot_identity="snapshot-1",
        selection_policy_identity=protocol.selection_policy_identity,
        eligible_universe_identity="universe-1",
        selection_identity="selection-1",
    )


def test_protocol_fingerprint_and_phase8_authorization_are_deterministic(protocol):
    again = load_protocol_spec(SPEC_PATH)
    assert protocol.protocol_fingerprint == again.protocol_fingerprint
    assert protocol.protocol_id == again.protocol_id
    verified = verify_phase8_authorization(protocol, PHASE8_ROOT)
    assert verified["phase8_result_identity"] == protocol.source_phase8_result_identity
    assert protocol.activation_market_session_boundary == "2026-09-17"


def test_activation_is_immutable_and_duplicate_activation_is_idempotent(tmp_path, protocol, activation):
    ledger = ForwardValidationLedger(tmp_path / "forward.db")
    assert ledger.activate(activation) is True
    assert ledger.activate(activation) is False
    assert ledger.activation(protocol.protocol_id) == activation
    conflicting = replace(activation, activated_at_utc="2026-09-28T12:00:00Z", activation_identity=identity({"changed": True}))
    with pytest.raises(ValueError, match="conflicting activation"):
        ledger.activate(conflicting)
    with pytest.raises(sqlite3.DatabaseError):
        with ledger._connect() as connection:
            connection.execute("UPDATE forward_protocols SET benchmark='OTHER'")


def test_historical_and_operational_boundaries_are_both_enforced(protocol, activation):
    with pytest.raises(ValueError, match="historical activation boundary"):
        _formation(protocol, activation, session="2026-09-17", completed=("2026-09-17",))
    later_activation = create_activation(
        protocol, activated_at_utc="2026-09-27T12:00:00Z",
        confirmed_latest_completed_session="2026-09-18",
    )
    with pytest.raises(ValueError, match="operational activation cutoff"):
        _formation(protocol, later_activation, session="2026-09-18", completed=("2026-09-18",))
    with pytest.raises(ValueError, match="older gaps"):
        _formation(protocol, activation, session="2026-09-18", completed=("2026-09-18", "2026-09-19"))
    accepted = _formation(protocol, activation)
    assert accepted.formation_session == "2026-09-18"
    assert accepted.ordered_selected_symbols == ("BBB", "AAA")
    assert all(item.weight == pytest.approx(0.5) for item in accepted.positions)
    assert not hasattr(accepted, "forward_return")


def test_formation_replay_is_idempotent_and_conflict_rolls_back(tmp_path, protocol, activation):
    ledger = ForwardValidationLedger(tmp_path / "forward.db")
    ledger.activate(activation)
    formation = _formation(protocol, activation)
    assert ledger.record_formation(formation, protocol.tracked_horizons) is True
    assert ledger.record_formation(formation, protocol.tracked_horizons) is False
    conflicting = _formation(protocol, activation, symbols=("AAA", "BBB"))
    with pytest.raises(ValueError, match="conflicting"):
        ledger.record_formation(conflicting, protocol.tracked_horizons)
    assert len(ledger.formations(protocol.protocol_id)) == 1
    assert len(ledger.latest_maturities(protocol.protocol_id)) == 2


def test_exact_horizon_sessions_remain_pending_until_target_exists(protocol, activation):
    formation = _formation(protocol, activation)
    before = evaluate_formation_maturities(protocol, formation, ("2026-09-18", "2026-09-19", "2026-09-20"), recorded_at_utc="t")
    assert all(item.status is MaturityStatus.PENDING for item in before)
    sessions = tuple(f"2026-09-{18 + index:02d}" for index in range(0, 12))
    matured = evaluate_formation_maturities(protocol, formation, sessions, recorded_at_utc="t")
    assert [(item.horizon_sessions, item.target_session) for item in matured] == [(5, "2026-09-23"), (10, "2026-09-28")]
    assert all(item.status is MaturityStatus.MATURED for item in matured)


def test_outcome_cannot_attach_early_and_exact_benchmark_excess_reconcile(protocol, activation):
    formation = _formation(protocol, activation)
    pending = evaluate_formation_maturities(protocol, formation, ("2026-09-18",), recorded_at_utc="t")[0]
    with pytest.raises(ValueError, match="before exact maturity"):
        build_matured_outcomes(formation, pending, target_stock_closes={"AAA": 110, "BBB": 110}, target_benchmark_close=1010, recorded_at_utc="t")
    maturity = evaluate_formation_maturities(
        protocol, formation, tuple(f"2026-09-{18 + index:02d}" for index in range(12)), recorded_at_utc="t"
    )[0]
    outcomes = build_matured_outcomes(
        formation, maturity, target_stock_closes={"AAA": 110, "BBB": 120},
        target_benchmark_close=1100, recorded_at_utc="t",
    )
    assert all(item.availability is OutcomeAvailability.AVAILABLE for item in outcomes)
    assert outcomes[0].benchmark_forward_return_pct == pytest.approx(10.0)
    assert outcomes[0].excess_forward_return_pct_points == pytest.approx(10.0)


def test_missing_target_is_explicit_and_no_nearest_session_substitution(protocol, activation):
    formation = _formation(protocol, activation)
    maturity = evaluate_formation_maturities(
        protocol, formation, tuple(f"2026-09-{18 + index:02d}" for index in range(12)), recorded_at_utc="t"
    )[0]
    outcomes = build_matured_outcomes(
        formation, maturity, target_stock_closes={"AAA": 110}, target_benchmark_close=1100, recorded_at_utc="t"
    )
    assert any(item.availability is OutcomeAvailability.MISSING_STOCK_TARGET_CLOSE for item in outcomes)
    assert all(item.target_session == "2026-09-23" for item in outcomes)


def test_outcome_rows_are_append_only_and_conflicting_replay_fails(tmp_path, protocol, activation):
    ledger = ForwardValidationLedger(tmp_path / "forward.db")
    ledger.activate(activation)
    formation = _formation(protocol, activation)
    ledger.record_formation(formation, protocol.tracked_horizons)
    maturities = evaluate_formation_maturities(
        protocol, formation, tuple(f"2026-09-{18 + index:02d}" for index in range(12)), recorded_at_utc="t"
    )
    ledger.record_maturities(maturities)
    outcomes = build_matured_outcomes(
        formation, maturities[0], target_stock_closes={"AAA": 110, "BBB": 120}, target_benchmark_close=1100, recorded_at_utc="t"
    )
    assert ledger.record_outcomes(outcomes) == 2
    assert ledger.record_outcomes(outcomes) == 0
    changed = replace(outcomes[0], recorded_at_utc="different", outcome_identity=identity({"different": True}))
    with pytest.raises(ValueError, match="conflicting"):
        ledger.record_outcomes((changed, outcomes[1]))
    assert len(ledger.outcomes(protocol.protocol_id)) == 2


def test_gap_detection_records_missing_events_without_backfill(protocol, activation):
    events = detect_missing_formations(
        activation, ("2026-09-18", "2026-09-19", "2026-09-20"), ("2026-09-18",), recorded_at_utc="t"
    )
    assert tuple(item.market_session for item in events) == ("2026-09-19", "2026-09-20")
    assert all(item.event_type.value == "MISSING_FORMATION" for item in events)
    assert len(events) == 2


def test_protocol_version_change_isolated_and_schema_initialization_idempotent(tmp_path, protocol, activation):
    changed = replace(protocol, protocol_version="V2")
    assert changed.protocol_fingerprint != protocol.protocol_fingerprint
    ledger = ForwardValidationLedger(tmp_path / "forward.db")
    ledger.initialize(); ledger.initialize()
    ledger.activate(activation)
    assert ledger.activation(protocol.protocol_id) == activation
    assert ledger.path == (tmp_path / "forward.db").resolve()
    assert ledger.path != (ROOT / "data/forward_validation.db").resolve()


def test_status_is_deterministic_and_contains_no_performance_conclusion(tmp_path, protocol, activation):
    ledger = ForwardValidationLedger(tmp_path / "forward.db")
    ledger.activate(activation)
    formation = _formation(protocol, activation)
    ledger.record_formation(formation, protocol.tracked_horizons)
    status = build_forward_status(
        activation, latest_completed_market_session="2026-09-18",
        formation_sessions=(formation.formation_session,),
        latest_maturities=ledger.latest_maturities(protocol.protocol_id),
        outcomes=(), missing_events=(),
    )
    again = build_forward_status(
        activation, latest_completed_market_session="2026-09-18",
        formation_sessions=(formation.formation_session,),
        latest_maturities=ledger.latest_maturities(protocol.protocol_id),
        outcomes=(), missing_events=(),
    )
    assert status == again
    for field in ("pnl", "sharpe", "cagr", "winner", "ranking"):
        assert not hasattr(status, field)


def test_cli_has_explicit_modes_but_no_backfill_or_historical_range_switches():
    parser = build_parser()
    options = {option for action in parser._actions for option in action.option_strings}
    assert {"activate", "record", "mature", "status"}.issubset(set(parser._subparsers._group_actions[0].choices))
    assert not any(option in options for option in ("--backfill", "--from-date", "--start-date", "--historical-forward"))

