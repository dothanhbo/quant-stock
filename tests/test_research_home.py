from __future__ import annotations

import csv
from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from manager.view_models import build_research_model
from quantctl.research_home import EvidenceState, inspect_research_home_catalog


def _write_artifact(
    root: Path,
    directory: str,
    manifest_name: str,
    summary_name: str,
    manifest: dict[str, object],
    columns: tuple[str, ...],
) -> None:
    target = root / "research_results" / directory
    target.mkdir(parents=True)
    (target / manifest_name).write_text(json.dumps(manifest), encoding="utf-8")
    with (target / summary_name).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerow({column: "value" for column in columns})


def _valid_artifacts(root: Path) -> None:
    _write_artifact(
        root,
        "quantlab_neutral_panel_factor_evaluation_2018-08-07_2026-09-17",
        "experiment_manifest.json",
        "factor_summary.csv",
        {
            "completed": True,
            "evaluation": {"result_identity": "factor-id"},
            "requested_bounds": {"end_date": "2026-09-17"},
            "limitations": ["database coverage is not historical membership"],
        },
        ("factor", "horizon_sessions", "outcome_field", "identity"),
    )
    _write_artifact(
        root,
        "quantlab_research_decision_gate_2018-08-07_2026-09-17",
        "research_decision_manifest.json",
        "research_decision_summary.csv",
        {
            "completed": True,
            "decision_result_identity": "gate-id",
            "input_date_range": {"end_date": "2026-09-17"},
        },
        ("candidate_type", "candidate", "decision", "identity"),
    )
    _write_artifact(
        root,
        "quantlab_portfolio_research_synthesis_2018-08-07_2026-09-17",
        "portfolio_research_synthesis_manifest.json",
        "portfolio_research_decisions.csv",
        {
            "completed": True,
            "result_identity": "portfolio-id",
            "temporal_partition": [{"end_date": "2026-09-17"}],
        },
        ("requested_budget", "horizon_sessions", "decision", "identity"),
    )
    _write_artifact(
        root,
        "quantlab_risk_policy_decision_2018-08-07_2026-09-17",
        "risk_policy_decision_manifest.json",
        "risk_policy_decision_summary.csv",
        {"completed": True, "result_identity": "risk-id"},
        ("policy", "decision", "identity"),
    )


def _monitoring(root: Path, count: int) -> Path:
    target = (
        root
        / "research_results"
        / "quantlab_monitoring_history_2026-09-25_f48bc402c781"
    )
    target.mkdir(parents=True)
    path = target / "monitoring_longitudinal_manifest.json"
    path.write_text(
        json.dumps(
            {
                "contract": "quantlab.monitoring_history",
                "result_identity": "monitoring-id",
                "valid_snapshot_count": count,
                "limitations": ["descriptive only"],
            }
        ),
        encoding="utf-8",
    )
    with (target / "monitoring_snapshot_catalog.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "result_identity",
                "specification_fingerprint",
                "observed_market_session",
                "classification",
            ),
        )
        writer.writeheader()
        for index in range(count):
            writer.writerow(
                {
                    "result_identity": f"snapshot-{index}",
                    "specification_fingerprint": "compatible-spec",
                    "observed_market_session": f"2026-09-{24 + index:02d}",
                    "classification": "VALID",
                }
            )
    with (target / "monitoring_transitions.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "previous_identity",
                "current_identity",
                "previous_session",
                "current_session",
                "previous_status",
                "current_status",
                "metric_name",
            ),
        )
        writer.writeheader()
        if count >= 2:
            writer.writerow(
                {
                    "previous_identity": f"snapshot-{count - 2}",
                    "current_identity": f"snapshot-{count - 1}",
                    "previous_session": f"2026-09-{22 + count:02d}",
                    "current_session": f"2026-09-{23 + count:02d}",
                    "previous_status": "STABLE",
                    "current_status": "DESCRIPTIVE_DRIFT",
                    "metric_name": "factor_coverage",
                }
            )
    return path


def test_catalog_reads_only_fixed_canonical_families(tmp_path: Path) -> None:
    _valid_artifacts(tmp_path)
    unrelated = tmp_path / "research_results" / "arbitrary_new_result"
    unrelated.mkdir(parents=True)
    (unrelated / "manifest.json").write_text("{}", encoding="utf-8")

    catalog = inspect_research_home_catalog(root=tmp_path)

    assert tuple(item.key for item in catalog.artifacts) == (
        "factor_evidence",
        "candidate_gate",
        "portfolio_evidence",
        "risk_gate",
    )
    assert all(item.state is EvidenceState.AVAILABLE for item in catalog.artifacts)
    assert all("arbitrary_new_result" not in item.root_path.parts for item in catalog.artifacts)


def test_missing_partial_and_incompatible_artifacts_are_explicit(tmp_path: Path) -> None:
    partial = (
        tmp_path
        / "research_results"
        / "quantlab_research_decision_gate_2018-08-07_2026-09-17"
    )
    partial.mkdir(parents=True)
    (partial / "research_decision_manifest.json").write_text("{}", encoding="utf-8")
    _write_artifact(
        tmp_path,
        "quantlab_risk_policy_decision_2018-08-07_2026-09-17",
        "risk_policy_decision_manifest.json",
        "risk_policy_decision_summary.csv",
        {"completed": True, "result_identity": "risk-id"},
        ("wrong",),
    )

    states = {item.key: item.state for item in inspect_research_home_catalog(root=tmp_path).artifacts}

    assert states == {
        "factor_evidence": EvidenceState.UNAVAILABLE,
        "candidate_gate": EvidenceState.PARTIAL,
        "portfolio_evidence": EvidenceState.UNAVAILABLE,
        "risk_gate": EvidenceState.UNAVAILABLE,
    }


def test_monitoring_requires_two_compatible_snapshots_for_change(tmp_path: Path) -> None:
    _monitoring(tmp_path, 1)

    monitoring = inspect_research_home_catalog(root=tmp_path).monitoring

    assert monitoring.state is EvidenceState.INSUFFICIENT
    assert monitoring.compatible_snapshot_count == 1
    assert monitoring.message == (
        "Change history unavailable — at least two compatible snapshots are required."
    )


def test_monitoring_change_uses_persisted_compatible_transition_only(tmp_path: Path) -> None:
    _monitoring(tmp_path, 2)

    monitoring = inspect_research_home_catalog(root=tmp_path).monitoring

    assert monitoring.state is EvidenceState.AVAILABLE
    assert monitoring.compatible_snapshot_count == 2
    assert monitoring.transition_count == 1
    assert "STABLE → DESCRIPTIVE_DRIFT" in monitoring.message


def test_catalog_contracts_are_immutable(tmp_path: Path) -> None:
    catalog = inspect_research_home_catalog(root=tmp_path)
    with pytest.raises(FrozenInstanceError):
        catalog.evidence_end_date = "2099-01-01"  # type: ignore[misc]


def test_research_model_does_not_discover_or_execute_runners(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "run_quantlab_should_not_run.py").write_text(
        "raise RuntimeError('must not execute')\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        "quantctl.registry.discover_active_runners",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("runner discovery is forbidden")),
    )

    model = build_research_model(root=tmp_path, environ={})

    assert model.catalog.artifacts
    assert model.frontier.factor_decisions == ()
    assert not hasattr(model, "runners")


def test_research_home_source_has_no_research_execution_or_filters() -> None:
    root = Path(__file__).resolve().parent.parent
    source = (root / "manager" / "pages" / "research.py").read_text(encoding="utf-8")
    model_source = (root / "quantctl" / "research_home.py").read_text(encoding="utf-8")

    forbidden = (
        "run_backtest",
        "run_walk_forward",
        "prepare_historical",
        "build_point_in_time",
        "run_quantlab",
        "discover_active_runners",
        "text_input",
        "selectbox",
        "multiselect",
    )
    assert all(item not in source for item in forbidden)
    assert "rglob(" not in model_source
    assert "glob(" not in model_source
