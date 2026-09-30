from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from manager.view_models import build_dashboard_model, build_research_model
from quantctl.cli import main
from quantctl.commands import research
from quantctl.research_status import (
    ResearchDecision,
    inspect_production_policy,
    inspect_production_readiness,
    inspect_research_frontier,
)
from quantctl.registry import GitInfo


def _artifact(
    root: Path,
    directory: str,
    manifest: str,
    summary: str,
    rows: tuple[dict[str, str], ...],
) -> None:
    target = root / "research_results" / directory
    target.mkdir(parents=True)
    (target / manifest).write_text(
        json.dumps({"completed": True, "input_date_range": {"end_date": "2026-09-17"}, "result_identity": directory}),
        encoding="utf-8",
    )
    with (target / summary).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _factor_artifact(root: Path, rows: tuple[dict[str, str], ...]) -> None:
    _artifact(
        root,
        "quantlab_research_decision_gate_2018-08-07_2026-09-17",
        "research_decision_manifest.json",
        "research_decision_summary.csv",
        rows,
    )


def test_production_and_research_are_separate_objects(tmp_path: Path) -> None:
    production = inspect_production_policy(root=tmp_path, environ={})
    research_snapshot = inspect_research_frontier(root=tmp_path)
    assert production.deployed_strategy_identity == "Q70_FROZEN"
    assert production.role == "FROZEN_BASELINE"
    assert research_snapshot.framework == "Neutral Quant Lab"
    assert "Q70" not in research_snapshot.framework
    assert research_snapshot.production_replacement.decision is ResearchDecision.UNKNOWN


def test_current_artifact_decisions_are_resolved_without_winner_language(tmp_path: Path) -> None:
    _factor_artifact(tmp_path, (
        {"candidate_type": "factor", "candidate": "adx_14", "decision": "ADVANCE", "reason_codes": "supported"},
        {"candidate_type": "factor", "candidate": "rsi_14", "decision": "HOLD", "reason_codes": "mixed"},
        {"candidate_type": "factor", "candidate": "atr_percent_14", "decision": "REJECT_FOR_NOW", "reason_codes": "adverse"},
        {"candidate_type": "policy", "candidate": "ADX_ONLY", "decision": "HOLD", "reason_codes": "research only"},
    ))
    snapshot = inspect_research_frontier(root=tmp_path)
    assert [(item.name, item.decision.value) for item in snapshot.factor_decisions] == [
        ("adx_14", "ADVANCE"), ("atr_percent_14", "REJECT_FOR_NOW"), ("rsi_14", "HOLD")
    ]
    assert [(item.name, item.decision.value) for item in snapshot.policy_decisions] == [("ADX_ONLY", "HOLD")]
    assert snapshot.production_replacement.decision is ResearchDecision.NONE_APPROVED
    assert "winner" not in research.render_status(root=tmp_path).lower()


def test_archive_is_excluded_and_missing_artifacts_degrade_gracefully(tmp_path: Path) -> None:
    archive = tmp_path / "research" / "archive" / "fake"
    archive.mkdir(parents=True)
    (archive / "research_decision_summary.csv").write_text(
        "candidate_type,candidate,decision\nfactor,fake,ADVANCE\n", encoding="utf-8"
    )
    snapshot = inspect_research_frontier(root=tmp_path)
    assert snapshot.factor_decisions == ()
    assert snapshot.latest_stage.startswith("UNKNOWN")
    assert snapshot.production_replacement.decision is ResearchDecision.UNKNOWN
    assert snapshot.source_artifacts == ()


def test_conflicting_current_decisions_become_unknown(tmp_path: Path) -> None:
    _factor_artifact(tmp_path, (
        {"candidate_type": "factor", "candidate": "adx_14", "decision": "ADVANCE"},
        {"candidate_type": "factor", "candidate": "adx_14", "decision": "HOLD"},
    ))
    snapshot = inspect_research_frontier(root=tmp_path)
    assert snapshot.factor_decisions[0].decision is ResearchDecision.UNKNOWN
    assert snapshot.production_replacement.decision is ResearchDecision.UNKNOWN
    assert any("conflicting decisions" in warning for warning in snapshot.warnings)


def test_cli_research_status_and_existing_list(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    (tmp_path / "research").mkdir()
    assert research.run_status(root=tmp_path) == 0
    assert "RESEARCH STATUS" in capsys.readouterr().out
    assert research.render_list(root=tmp_path).startswith("ACTIVE QUANT LAB RUNNERS")
    assert main(["research", "status"]) == 0
    assert "RESEARCH STATUS" in capsys.readouterr().out


def test_readiness_adapts_existing_gap_gate_without_runtime_inference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "quantctl.research_status.inspect_git",
        lambda **_kwargs: GitInfo("abcdef1", None, "CLEAN", True),
    )

    readiness = inspect_production_readiness(root=tmp_path)
    rendered = research.render_status(root=tmp_path)

    assert readiness.available
    assert readiness.readiness == "ENGINEERING_CLOSED_EVIDENCE_PENDING"
    assert readiness.open_gap_count == len(readiness.open_gaps) == 4
    assert readiness.source_reference == "quantlab/operations/production_gap_gate.py"
    assert "Production Readiness" in rendered
    assert "Open Readiness Gaps" in rendered
    assert readiness.evidence_identity in rendered


def test_missing_git_identity_keeps_readiness_unavailable_without_fabrication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "quantctl.research_status.inspect_git",
        lambda **_kwargs: GitInfo(None, None, "UNKNOWN", False),
    )

    readiness = inspect_production_readiness(root=tmp_path)

    assert not readiness.available
    assert readiness.readiness == "UNAVAILABLE"
    assert readiness.open_gap_count is None
    assert readiness.open_gaps == ()
    assert readiness.evidence_identity == "UNKNOWN"


def test_manager_models_keep_production_and_research_distinct(tmp_path: Path) -> None:
    (tmp_path / "research").mkdir()
    dashboard = build_dashboard_model(root=tmp_path, environ={})
    research_model = build_research_model(root=tmp_path)
    assert dashboard.production.deployed_strategy_identity == "Q70_FROZEN"
    assert dashboard.research.framework == "Neutral Quant Lab"
    assert research_model.frontier is not dashboard.production


def test_manager_pages_use_deployed_and_research_terminology() -> None:
    root = Path(__file__).resolve().parent.parent
    dashboard = (root / "manager/pages/dashboard.py").read_text(encoding="utf-8")
    research_page = (root / "manager/pages/research.py").read_text(encoding="utf-8")
    assert "Deployed paper policy" in dashboard
    assert "Production replacement" in dashboard
    assert "Research Frontier" in research_page
    assert "Current Decision and Production Readiness" in research_page
    assert "Canonical artifact sources" in research_page
    assert "Active Quant Lab Runners" in research_page
    assert "winner" not in dashboard.lower()


def test_manager_and_legacy_dashboard_ownership_is_explicit() -> None:
    root = Path(__file__).resolve().parent.parent
    manager_app = (root / "manager" / "app.py").read_text(encoding="utf-8")
    legacy_dashboard = (root / "dashboard" / "app.py").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")

    assert "Canonical Management & Research Console" in manager_app
    assert "Legacy Paper Dashboard" in legacy_dashboard
    assert "Legacy paper presentation dashboard" in readme
    assert "Canonical management and research console" in readme
