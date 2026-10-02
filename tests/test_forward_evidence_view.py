from __future__ import annotations

from dataclasses import FrozenInstanceError
import hashlib
from pathlib import Path
import sqlite3

import pytest

from quantctl.forward_evidence import (
    ForwardEvidenceState,
    inspect_forward_evidence_catalog,
)
from quantlab.evidence import (
    PaperEventCursor,
    ProspectiveFillEvidence,
    ProspectivePortfolioEvidenceLedger,
    ProspectivePortfolioEvidenceRecord,
    paper_store_identity,
)
from quantlab.forward.contracts import (
    AuditEventType,
    ForwardAuditEvent,
    ForwardFormation,
    ForwardMaturity,
    ForwardOutcome,
    ForwardProtocolActivation,
    MaturityStatus,
    OutcomeAvailability,
)
from quantlab.forward.ledger import ForwardValidationLedger


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _paper_source(path: Path, epoch: str = "epoch-a") -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE paper_metadata(key TEXT PRIMARY KEY,value TEXT)")
        connection.execute(
            "INSERT INTO paper_metadata VALUES ('account_epoch_id', ?)",
            (f'"{epoch}"',),
        )


def _paper_record(
    *,
    paper_path: Path,
    session: str,
    equity: float,
    epoch: str = "epoch-a",
    fill: ProspectiveFillEvidence | None = None,
) -> ProspectivePortfolioEvidenceRecord:
    source_identity = paper_store_identity(
        store_id="q70-frozen",
        strategy_identity="Q70_FROZEN",
        database_path=paper_path,
        account_epoch_id=epoch,
    )
    return ProspectivePortfolioEvidenceRecord(
        evidence_version="v1",
        strategy_identity="Q70_FROZEN",
        source_store_id="q70-frozen",
        source_store_identity=source_identity,
        source_account_epoch_id=epoch,
        runtime_configuration_fingerprint="config-a",
        observation_date=session,
        captured_at_utc=f"{session}T10:00:00+00:00",
        paper_database_path=str(paper_path),
        market_database_path="market.db",
        market_database_sha256="market-hash",
        market_data_reference_session=session,
        benchmark_symbol="VNINDEX",
        benchmark_close=1000.0,
        market_regime_label="BULL",
        regime_computation_identity="regime-a",
        cash=equity,
        positions_value=0.0,
        equity=equity,
        realized_pnl=equity - 100.0,
        unrealized_pnl=0.0,
        gross_exposure_pct=0.0,
        open_position_count=0,
        fills_since_previous=() if fill is None else (fill,),
        event_cursor=PaperEventCursor(0 if fill is None else fill.fill_id, 0),
        daily_pnl=None if session == "2026-01-02" else equity - 100.0,
        daily_return_pct=None if session == "2026-01-02" else equity - 100.0,
        running_equity_peak=100.0,
        drawdown_pct=(equity / 100.0 - 1.0) * 100.0,
    )


def _activation() -> ForwardProtocolActivation:
    return ForwardProtocolActivation(
        "protocol-a",
        "fingerprint-a",
        "V1",
        "2026-01-01T00:00:00Z",
        "2025-12-31",
        "2026-01-01",
        "phase8-id",
        "phase8-hash",
        "ADX_ONLY",
        "EQUAL_WEIGHT",
        5,
        (5, 10),
        "VNINDEX",
        "activation-a",
    )


def _formation(session: str = "2026-01-02") -> ForwardFormation:
    return ForwardFormation(
        "protocol-a",
        "fingerprint-a",
        session,
        f"{session}T10:00:00Z",
        "snapshot-a",
        "sessions-a",
        "policy-a",
        "universe-a",
        "selection-a",
        (),
        0,
        5,
        "EQUAL_WEIGHT",
        (),
        0.0,
        1.0,
        1000.0,
        f"formation-{session}",
    )


def _root_with_evidence(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path
    data = root / "data"
    data.mkdir()
    paper = data / "paper_trading_v2.db"
    evidence = data / "prospective_portfolio_evidence.db"
    forward = data / "forward_validation.db"
    _paper_source(paper)
    ProspectivePortfolioEvidenceLedger(evidence).initialize()
    return root, paper, forward


def test_missing_evidence_is_read_only_and_does_not_create_databases(tmp_path: Path) -> None:
    catalog = inspect_forward_evidence_catalog(root=tmp_path, environ={})

    assert catalog.paper.state is ForwardEvidenceState.NOT_STARTED
    assert catalog.forward_state is ForwardEvidenceState.NOT_STARTED
    assert not (tmp_path / "data").exists()
    assert catalog.paper.points == () and catalog.protocols == ()


def test_schema_incompatible_evidence_fails_closed_without_zero_values(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    with sqlite3.connect(data / "prospective_portfolio_evidence.db") as connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
    with sqlite3.connect(data / "forward_validation.db") as connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")

    catalog = inspect_forward_evidence_catalog(root=tmp_path, environ={})

    assert catalog.paper.state is ForwardEvidenceState.SCHEMA_INCOMPATIBLE
    assert catalog.paper.observation_count is None
    assert catalog.paper.points == ()
    assert catalog.forward_state is ForwardEvidenceState.SCHEMA_INCOMPATIBLE
    assert catalog.protocols == ()


def test_paper_projection_preserves_epoch_accounting_drawdown_and_friction(tmp_path: Path) -> None:
    root, paper, _forward = _root_with_evidence(tmp_path)
    evidence = root / "data" / "prospective_portfolio_evidence.db"
    fill = ProspectiveFillEvidence(
        1, "order-a", "intent-a", None, "2026-01-02", 10.0, "AAA", "BUY", 10,
        10.1, 10.2, 102.0, 1.0, 0.5, -103.0, "2026-01-02T09:00:00Z",
    )
    ledger = ProspectivePortfolioEvidenceLedger(evidence)
    ledger.append(_paper_record(paper_path=paper, session="2026-01-02", equity=100.0, fill=fill))
    ledger.append(_paper_record(paper_path=paper, session="2026-01-03", equity=95.0))
    before = (_digest(paper), _digest(evidence))

    catalog = inspect_forward_evidence_catalog(
        root=root,
        environ={"PAPER_V2_DATABASE_PATH": str(paper)},
    )

    assert catalog.paper.state is ForwardEvidenceState.AVAILABLE
    assert catalog.paper.account_epoch == "epoch-a"
    assert catalog.paper.latest_session == "2026-01-03"
    assert catalog.paper.observation_count == 2
    assert [(item.equity, item.cash, item.position_value) for item in catalog.paper.points] == [
        (100.0, 100.0, 0.0),
        (95.0, 95.0, 0.0),
    ]
    assert catalog.paper.points[-1].drawdown_pct == pytest.approx(-5.0)
    assert catalog.paper.execution is not None
    assert (catalog.paper.execution.fill_count, catalog.paper.execution.total_commission) == (1, 1.0)
    assert catalog.paper.execution.total_modeled_slippage == 0.5
    assert (_digest(paper), _digest(evidence)) == before
    with pytest.raises(FrozenInstanceError):
        catalog.paper.points[0].equity = 0.0  # type: ignore[misc]


def test_account_epoch_prevents_old_evidence_aliasing(tmp_path: Path) -> None:
    root, paper, _forward = _root_with_evidence(tmp_path)
    evidence = root / "data" / "prospective_portfolio_evidence.db"
    ProspectivePortfolioEvidenceLedger(evidence).append(
        _paper_record(paper_path=paper, session="2026-01-02", equity=100.0, epoch="old-epoch")
    )

    catalog = inspect_forward_evidence_catalog(
        root=root,
        environ={"PAPER_V2_DATABASE_PATH": str(paper)},
    )

    assert catalog.paper.account_epoch == "epoch-a"
    assert catalog.paper.points == ()
    assert catalog.paper.state is ForwardEvidenceState.NOT_STARTED


def test_forward_protocol_reports_immature_then_mature_horizon_evidence(tmp_path: Path) -> None:
    root, _paper, forward = _root_with_evidence(tmp_path)
    ledger = ForwardValidationLedger(forward)
    ledger.activate(_activation())
    formation = _formation()
    ledger.record_formation(formation, (5, 10))

    immature = inspect_forward_evidence_catalog(root=root, environ={})
    assert len(immature.protocols) == 1
    view = immature.protocols[0]
    assert view.state is ForwardEvidenceState.SAMPLE_IMMATURE
    assert view.pending_maturity_count == 2
    assert all(item.mean_stock_forward_return_pct is None for item in view.summaries)

    maturity = ForwardMaturity(
        "protocol-a", formation.formation_identity, formation.formation_session, 5,
        "2026-01-07", MaturityStatus.MATURED, "EXACT_SESSION", "2026-01-07T10:00:00Z", "mature-5",
    )
    ledger.record_maturities((maturity,))
    outcome = ForwardOutcome(
        "protocol-a", formation.formation_identity, 5, "2026-01-07", "AAA", 1.0,
        OutcomeAvailability.AVAILABLE, 5.0, 2.0, 3.0, "2026-01-07T10:00:00Z", "outcome-a",
    )
    ledger.record_outcomes((outcome,))
    before = _digest(forward)

    mature = inspect_forward_evidence_catalog(root=root, environ={})

    assert _digest(forward) == before
    view = mature.protocols[0]
    assert view.state is ForwardEvidenceState.AVAILABLE
    assert view.formation_count == 1
    summary = next(item for item in view.summaries if item.horizon_sessions == 5)
    assert (summary.maturity_count, summary.available_outcome_count) == (1, 1)
    assert summary.mean_stock_forward_return_pct == 5.0
    assert summary.mean_benchmark_forward_return_pct == 2.0
    assert summary.mean_excess_forward_return_pct_points == 3.0
    assert next(item for item in view.summaries if item.horizon_sessions == 10).available_outcome_count == 0


def test_forward_continuity_gap_is_explicit_and_not_backfilled(tmp_path: Path) -> None:
    root, _paper, forward = _root_with_evidence(tmp_path)
    ledger = ForwardValidationLedger(forward)
    ledger.activate(_activation())
    ledger.record_formation(_formation(), (5, 10))
    ledger.record_audit_events(
        (
            ForwardAuditEvent(
                "protocol-a", AuditEventType.MISSING_FORMATION, "2026-01-03",
                "MISSED_OPERATIONAL_SESSION", "2026-01-04T00:00:00Z", "gap-a",
            ),
        )
    )

    view = inspect_forward_evidence_catalog(root=root, environ={}).protocols[0]

    assert view.state is ForwardEvidenceState.CONTINUITY_GAP
    assert view.missing_formation_sessions == ("2026-01-03",)
    assert view.formation_sessions == ("2026-01-02",)


def test_protocol_and_horizon_compatibility_and_bounded_presentation(tmp_path: Path) -> None:
    root, _paper, forward = _root_with_evidence(tmp_path)
    ledger = ForwardValidationLedger(forward)
    ledger.activate(_activation())
    for session in ("2026-01-02", "2026-01-03", "2026-01-04"):
        ledger.record_formation(_formation(session), (5, 10))

    filtered = inspect_forward_evidence_catalog(
        root=root,
        environ={},
        horizon_sessions=5,
        max_records=2,
    )

    view = filtered.protocols[0]
    assert view.formation_count == 3
    assert view.displayed_formation_count == 2
    assert view.records_truncated is True
    assert view.formation_sessions == ("2026-01-03", "2026-01-04")
    assert tuple(item.horizon_sessions for item in view.summaries) == (5,)
    assert view.pending_maturity_count == 2
    assert inspect_forward_evidence_catalog(
        root=root, environ={}, horizon_sessions=20
    ).protocols == ()


def test_paper_and_forward_models_cannot_be_misrepresented_as_one_series(tmp_path: Path) -> None:
    root, paper, forward = _root_with_evidence(tmp_path)
    ProspectivePortfolioEvidenceLedger(root / "data" / "prospective_portfolio_evidence.db").append(
        _paper_record(paper_path=paper, session="2026-01-02", equity=101.0)
    )
    ledger = ForwardValidationLedger(forward)
    ledger.activate(_activation())
    ledger.record_formation(_formation(), (5, 10))

    catalog = inspect_forward_evidence_catalog(
        root=root,
        environ={"PAPER_V2_DATABASE_PATH": str(paper)},
    )

    assert catalog.paper.points[0].equity == 101.0
    assert not hasattr(catalog.paper.points[0], "stock_forward_return_pct")
    assert not hasattr(catalog.protocols[0], "equity")
    assert any("not executed trades" in item for item in catalog.protocols[0].limitations)
