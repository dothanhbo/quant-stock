from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import sqlite3

import pytest

from app.daily_pipeline import DailyPipeline
from quantlab.forward import (
    ForwardDailyOperationState,
    ForwardValidationLedger,
    build_forward_formation,
    create_activation,
    load_protocol_spec,
    run_forward_validation_daily,
)


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "research/forward_validation/protocol_v1.json"
PHASE8 = ROOT / "research_results/quantlab_portfolio_research_synthesis_2018-08-07_2026-09-17"


def _sessions(count: int = 130) -> tuple[str, ...]:
    first = date(2026, 6, 1)
    return tuple((first + timedelta(days=index)).isoformat() for index in range(count))


def _market_database(path: Path, sessions: tuple[str, ...]) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE prices (id INTEGER PRIMARY KEY, symbol TEXT, time TEXT, "
            "open REAL, high REAL, low REAL, close REAL, volume INTEGER)"
        )
        rows = []
        for symbol_index, symbol in enumerate(("VNINDEX", "AAA", "BBB", "CCC")):
            for session_index, session in enumerate(sessions):
                close = 100.0 + symbol_index * 10.0 + session_index * (1.0 + symbol_index / 10.0)
                rows.append((symbol, session, close - 0.5, close + 1.0, close - 1.0, close, 1_000 + session_index))
        connection.executemany(
            "INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)",
            rows,
        )


def _activated_ledger(path: Path, cutoff: str):
    protocol = load_protocol_spec(SPEC)
    activation = create_activation(
        protocol,
        activated_at_utc="2026-09-27T12:00:00Z",
        confirmed_latest_completed_session=cutoff,
    )
    ledger = ForwardValidationLedger(path)
    ledger.activate(activation)
    return protocol, activation, ledger


def _run(market: Path, ledger: Path, *, timestamp: str = "2026-10-01T12:00:00Z"):
    assert ledger != ROOT / "data/forward_validation.db"
    return run_forward_validation_daily(
        database_path=market,
        ledger_path=ledger,
        protocol_spec=SPEC,
        phase8_root=PHASE8,
        recorded_at_utc=timestamp,
    )


def _record_prior_formation(protocol, activation, ledger, sessions, formation_index: int):
    session = sessions[formation_index]
    formation = build_forward_formation(
        protocol,
        activation,
        formation_session=session,
        recorded_at_utc="2026-09-28T12:00:00Z",
        completed_market_sessions=sessions[: formation_index + 1],
        ordered_symbols=("AAA", "BBB"),
        formation_closes={"AAA": 100.0, "BBB": 110.0},
        benchmark_formation_close=1_000.0,
        source_snapshot_identity="snapshot-prior",
        selection_policy_identity=protocol.selection_policy_identity,
        eligible_universe_identity="coverage-prior",
        selection_identity="selection-prior",
    )
    ledger.record_formation(formation, protocol.tracked_horizons)
    return formation


def test_cutoff_session_is_no_new_session_and_creates_no_evidence(tmp_path: Path) -> None:
    sessions = _sessions()
    cutoff = sessions[116]
    market, ledger_path = tmp_path / "market.db", tmp_path / "forward.db"
    _market_database(market, sessions[:117])
    protocol, _activation, ledger = _activated_ledger(ledger_path, cutoff)

    result = _run(market, ledger_path)

    assert result.operation_state is ForwardDailyOperationState.NO_NEW_SESSION
    assert result.completed_market_session == cutoff
    assert result.formation_created is False
    assert ledger.formations(protocol.protocol_id) == ()
    assert ledger.latest_maturities(protocol.protocol_id) == ()
    assert ledger.outcomes(protocol.protocol_id) == ()


def test_first_new_session_records_once_and_restart_replay_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = _sessions()
    cutoff, latest = sessions[116], sessions[117]
    market, ledger_path = tmp_path / "market.db", tmp_path / "forward.db"
    _market_database(market, sessions[:118])
    protocol, _activation, _ledger = _activated_ledger(ledger_path, cutoff)
    monkeypatch.setattr(
        "core.universe.get_vn100_symbols",
        lambda: (_ for _ in ()).throw(AssertionError("network universe path used")),
    )

    created = _run(market, ledger_path, timestamp="2026-09-28T01:00:00Z")
    replayed = _run(market, ledger_path, timestamp="2026-09-28T02:00:00Z")
    restarted = ForwardValidationLedger(ledger_path)

    assert created.operation_state is ForwardDailyOperationState.FORMATION_CREATED
    assert replayed.operation_state is ForwardDailyOperationState.FORMATION_EXISTING
    assert created.latest_recorded_formation == latest
    assert created.formation_count == 1
    assert replayed.status_identity == created.status_identity
    assert len(restarted.formations(protocol.protocol_id)) == 1
    assert len(restarted.latest_maturities(protocol.protocol_id)) == 2
    assert restarted.outcomes(protocol.protocol_id) == ()
    assert restarted.audit_events(protocol.protocol_id) == ()


def test_multiple_elapsed_sessions_record_latest_only_and_surface_gaps(tmp_path: Path) -> None:
    sessions = _sessions()
    cutoff, latest = sessions[116], sessions[120]
    market, ledger_path = tmp_path / "market.db", tmp_path / "forward.db"
    _market_database(market, sessions[:121])
    protocol, _activation, ledger = _activated_ledger(ledger_path, cutoff)

    result = _run(market, ledger_path)

    assert tuple(item.formation_session for item in ledger.formations(protocol.protocol_id)) == (latest,)
    assert result.missing_formation_sessions == sessions[117:120]
    assert tuple(item.market_session for item in ledger.audit_events(protocol.protocol_id)) == sessions[117:120]


@pytest.mark.parametrize(
    ("later_session_count", "expected_h5", "expected_h10"),
    ((4, 0, 0), (5, 1, 0), (9, 1, 0), (10, 1, 1)),
)
def test_exact_session_maturity_boundaries(
    tmp_path: Path,
    later_session_count: int,
    expected_h5: int,
    expected_h10: int,
) -> None:
    sessions = _sessions()
    cutoff, formation_index = sessions[116], 117
    latest_index = formation_index + later_session_count
    market, ledger_path = tmp_path / "market.db", tmp_path / "forward.db"
    _market_database(market, sessions[: latest_index + 1])
    protocol, activation, ledger = _activated_ledger(ledger_path, cutoff)
    prior = _record_prior_formation(protocol, activation, ledger, sessions, formation_index)

    result = _run(market, ledger_path)

    assert result.matured_count_by_horizon[5] == expected_h5
    assert result.matured_count_by_horizon[10] == expected_h10
    expected_outcomes = 2 * (expected_h5 + expected_h10)
    prior_outcomes = tuple(
        item for item in ledger.outcomes(protocol.protocol_id)
        if item.formation_identity == prior.formation_identity
    )
    assert len(prior_outcomes) == expected_outcomes


def test_matured_outcomes_are_immutable_and_replay_adds_nothing(tmp_path: Path) -> None:
    sessions = _sessions()
    cutoff, formation_index = sessions[116], 117
    market, ledger_path = tmp_path / "market.db", tmp_path / "forward.db"
    _market_database(market, sessions[: formation_index + 11])
    protocol, activation, ledger = _activated_ledger(ledger_path, cutoff)
    _record_prior_formation(protocol, activation, ledger, sessions, formation_index)

    first = _run(market, ledger_path, timestamp="2026-10-10T01:00:00Z")
    outcomes = ledger.outcomes(protocol.protocol_id)
    second = _run(market, ledger_path, timestamp="2026-10-10T02:00:00Z")

    assert first.maturity_events_created == 2
    assert first.outcomes_created == 4
    assert second.maturity_events_created == 0
    assert second.outcomes_created == 0
    assert ledger.outcomes(protocol.protocol_id) == outcomes
    assert second.status_identity == first.status_identity


def test_missing_exact_target_close_uses_unavailable_outcome_semantics(tmp_path: Path) -> None:
    sessions = _sessions()
    cutoff, formation_index = sessions[116], 117
    target_index = formation_index + 5
    market, ledger_path = tmp_path / "market.db", tmp_path / "forward.db"
    _market_database(market, sessions[: target_index + 1])
    with sqlite3.connect(market) as connection:
        connection.execute(
            "DELETE FROM prices WHERE symbol='BBB' AND date(time)=?",
            (sessions[target_index],),
        )
    protocol, activation, ledger = _activated_ledger(ledger_path, cutoff)
    prior = _record_prior_formation(protocol, activation, ledger, sessions, formation_index)

    result = _run(market, ledger_path)
    outcomes = tuple(
        item for item in ledger.outcomes(protocol.protocol_id)
        if item.formation_identity == prior.formation_identity
    )

    assert result.matured_count_by_horizon[5] == 1
    assert result.outcome_unavailable_count == 1
    assert {item.symbol: item.availability.value for item in outcomes}["BBB"] == "MISSING_STOCK_TARGET_CLOSE"


def test_eligible_formation_failure_is_visible_and_does_not_fake_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = _sessions()
    market, ledger_path = tmp_path / "market.db", tmp_path / "forward.db"
    _market_database(market, sessions[:118])
    protocol, _activation, ledger = _activated_ledger(ledger_path, sessions[116])
    monkeypatch.setattr(
        "quantlab.forward.daily._formation_evidence",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("selection failed")),
    )

    with pytest.raises(RuntimeError, match="selection failed"):
        _run(market, ledger_path)

    assert ledger.formations(protocol.protocol_id) == ()


def test_pipeline_runs_forward_between_complete_update_and_paper_operations() -> None:
    calls: list[str] = []
    result = DailyPipeline(
        update_market_data=lambda: calls.append("update") or (101, []),
        run_forward_validation=lambda: calls.append("forward"),
        run_lifecycle=lambda: calls.append("paper"),
        run_scanner=lambda: calls.append("scanner"),
    ).run()
    assert result.success
    assert calls == ["update", "forward", "paper", "scanner"]


def test_pipeline_update_or_forward_failure_prevents_downstream_operations() -> None:
    partial_calls: list[str] = []
    partial = DailyPipeline(
        update_market_data=lambda: partial_calls.append("update") or (100, ["AAA"]),
        run_forward_validation=lambda: partial_calls.append("forward"),
        run_lifecycle=lambda: partial_calls.append("paper"),
        run_scanner=lambda: partial_calls.append("scanner"),
    ).run()
    assert not partial.success
    assert partial_calls == ["update"]
    assert partial.stages[-1].name == "Forward Validation"

    failed_calls: list[str] = []

    def fail_forward():
        failed_calls.append("forward")
        raise RuntimeError("record failed")

    failed = DailyPipeline(
        update_market_data=lambda: failed_calls.append("update") or (101, []),
        run_forward_validation=fail_forward,
        run_lifecycle=lambda: failed_calls.append("paper"),
        run_scanner=lambda: failed_calls.append("scanner"),
    ).run()
    assert not failed.success
    assert failed_calls == ["update", "forward"]
    assert "record failed" in failed.stages[-1].error


def test_skip_update_never_runs_forward_but_preserves_manual_paper_flow() -> None:
    calls: list[str] = []
    result = DailyPipeline(
        update_market_data=lambda: calls.append("update") or (101, []),
        run_forward_validation=lambda: calls.append("forward"),
        run_lifecycle=lambda: calls.append("paper"),
        run_scanner=lambda: calls.append("scanner"),
    ).run(skip_update=True)
    assert result.success
    assert calls == ["paper", "scanner"]
    assert result.stages[1].name == "Forward Validation"
    assert result.stages[1].warning
