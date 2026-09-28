from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from quantlab.operations.production_gap_gate import (
    BundleDecision,
    Decision,
    LifecycleState,
    PHASE_13E_GAPS,
    ProductionGap,
    Readiness,
    Severity,
    build_production_hardening_gate,
    write_production_hardening_gate_artifacts,
)


COMMIT = "ee96270"


def _audit():
    return build_production_hardening_gate(COMMIT)


def test_canonical_register_has_expected_phase13a_severity_counts() -> None:
    result = _audit()
    counts = {item.value: sum(gap.original_severity is item for gap in result.gaps) for item in Severity}
    assert counts == {"CRITICAL": 0, "MATERIAL": 5, "MINOR": 3, "INFORMATIONAL": 2}
    assert len(result.gaps) == 10


def test_phase13b_to_13d_paper_closure_is_evidence_bound() -> None:
    result = _audit()
    paper = [gap for gap in result.gaps if gap.component == "PAPER_RECOVERY"]
    assert len(paper) == 2
    assert all(gap.state is LifecycleState.CLOSED for gap in paper)
    assert all(gap.decision is Decision.NO_ACTION_REQUIRED and gap.closure_evidence for gap in paper)
    assert all("13D" in " ".join(gap.closure_evidence) for gap in paper)


def test_closed_paper_gap_cannot_reopen_without_new_regression_evidence() -> None:
    closed = next(gap for gap in PHASE_13E_GAPS if gap.state is LifecycleState.CLOSED)
    with pytest.raises(ValueError, match="reopening.*requires concrete"):
        replace(closed, state=LifecycleState.OPEN)
    reopened = replace(closed, state=LifecycleState.OPEN, reopening_evidence=("reproduced duplicate sell on protected path",))
    assert reopened.reopening_evidence


def test_import_and_data_gate_are_distinct_open_material_gaps() -> None:
    by_id = {gap.identifier: gap for gap in _audit().gaps}
    assert by_id["13A-M1-import-time-database-initialization"].state is LifecycleState.OPEN
    assert by_id["13A-M1-import-time-database-initialization"].current_severity is Severity.MATERIAL
    assert by_id["13A-M2-post-update-integrity-gate"].state is LifecycleState.OPEN
    assert "not an all-symbol data-integrity result" in by_id["13A-M2-post-update-integrity-gate"].consequence
    assert "duplicate keys" in by_id["13A-M2-post-update-integrity-gate"].consequence


def test_workflow_path_mismatch_is_supported_by_current_path_contract() -> None:
    gap = next(gap for gap in _audit().gaps if gap.identifier.endswith("database-path-mismatch"))
    assert gap.current_severity is Severity.MATERIAL
    assert "data/market.db" in gap.consequence
    assert "root market.db" in gap.consequence
    assert gap.decision is Decision.FIX_BEFORE_V1_CONSOLIDATION


def test_fix_before_requires_concrete_open_gap_and_informationals_do_not_force_fix() -> None:
    informational = next(gap for gap in PHASE_13E_GAPS if gap.current_severity is Severity.INFORMATIONAL)
    with pytest.raises(ValueError, match="informational"):
        replace(informational, decision=Decision.FIX_BEFORE_V1_CONSOLIDATION)
    material = next(gap for gap in PHASE_13E_GAPS if gap.decision is Decision.FIX_BEFORE_V1_CONSOLIDATION)
    assert material.consequence and material.evidence
    with pytest.raises(ValueError, match="open or partially"):
        replace(material, state=LifecycleState.CLOSED, closure_evidence=("closed",))


def test_minor_and_informational_gaps_are_deferred_not_promoted() -> None:
    result = _audit()
    residual = [gap for gap in result.gaps if gap.original_severity in {Severity.MINOR, Severity.INFORMATIONAL}]
    assert len(residual) == 5
    assert all(gap.current_severity is gap.original_severity for gap in residual)
    assert all(gap.decision is Decision.DEFER_WITH_DOCUMENTATION for gap in residual)


def test_bundle_and_readiness_are_deterministic_and_paper_is_excluded() -> None:
    result = _audit()
    assert result.bundling_decision is BundleDecision.ONE_BOUNDED_HARDENING_PACKAGE
    assert result.readiness is Readiness.ONE_HARDENING_PACKAGE_REMAINS
    assert all(gap.decision is not Decision.FIX_BEFORE_V1_CONSOLIDATION for gap in result.gaps if gap.component == "PAPER_RECOVERY")
    assert result.audit_complete and result.no_production_mutation and result.no_network_or_provider_calls


def test_identity_and_artifacts_are_deterministic_and_do_not_read_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = "DO_NOT_SERIALIZE_PHASE13E_SECRET"
    monkeypatch.setenv("TELEGRAM_TOKEN", sentinel)
    first = _audit()
    second = _audit()
    assert first.identity == second.identity
    assert first.gaps == second.gaps
    out = tmp_path / "gate"
    hashes = write_production_hardening_gate_artifacts(first, out)
    assert len(hashes) == 4
    assert all(hashlib.sha256((out / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())
    manifest = json.loads((out / "production_hardening_gate_manifest.json").read_text(encoding="utf-8"))
    assert manifest["result_identity"] == first.identity
    assert manifest["no_network_or_provider_calls"] is True
    assert sentinel not in "".join(path.read_text(encoding="utf-8") for path in out.iterdir())
    with pytest.raises(FileExistsError):
        write_production_hardening_gate_artifacts(first, out)


def test_fresh_process_import_is_pure_and_does_not_load_operational_modules() -> None:
    code = (
        "import sys; import quantlab.operations.production_gap_gate; "
        "blocked={'sqlalchemy','requests','vnstock','core.database','strategy.scanner','execution.paper_broker'}; "
        "print(','.join(sorted(blocked & set(sys.modules))))"
    )
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    completed = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env, check=True, capture_output=True, text=True)
    assert completed.stdout.strip() == ""
