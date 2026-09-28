from __future__ import annotations

import csv
from dataclasses import replace
from dataclasses import FrozenInstanceError
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest

from quantlab.monitoring_history import (
    CATALOG_FILE,
    MANIFEST_FILE,
    REPORT_FILE,
    TRANSITIONS_FILE,
    discover_monitoring_snapshots,
    evaluate_monitoring_history,
    write_monitoring_history_artifacts,
)
import quantlab.monitoring_history as history_module


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _snapshot(
    root: Path,
    session: str,
    *,
    identity: str | None = None,
    timestamp: str | None = None,
    references: dict[str, str] | None = None,
    feature_semantics: str = "feature-v1",
    health: dict[str, str] | None = None,
) -> Path:
    stamp = timestamp or f"{session}T12:00:00Z"
    name_stamp = stamp.replace(":", "").replace("-", "").removesuffix("Z")
    directory = root / f"quantlab_monitoring_snapshot_{session}_{name_stamp}"
    directory.mkdir(parents=True)
    snapshot_identity = identity or f"identity-{session}-{stamp}"
    refs = references or {"phase9_forward_protocol_fingerprint": "protocol-v1", "phase6": "phase6-v1"}
    evidence = [
        ("DATA_HEALTH", "database_coverage_universe", "HEALTHY", "100", "minimum_history=50", "coverage", "ok"),
        ("DATA_HEALTH", "stale_symbol_count", "HEALTHY", "2", "5 sessions", "stale", "ok"),
        ("DATA_HEALTH", "missing_ohlcv_rows", "HEALTHY", "0", "0", "missing", "ok"),
        ("DATA_HEALTH", "duplicate_symbol_date_groups", "HEALTHY", "0", "0", "duplicates", "ok"),
        ("DATA_HEALTH", "invalid_date_rows", "HEALTHY", "0", "0", "invalid", "ok"),
        ("DATA_HEALTH", "latest_session_symbol_count", "HEALTHY", "101", "symbols", "count", "ok"),
        ("FEATURE_HEALTH", "latest_session_ADX_RSI_availability", "HEALTHY", "adx=95;rsi=96;joint=94;eligible_universe=100", "availability", "count", "ok"),
        ("FEATURE_DRIFT", "ADX14_distribution", "DESCRIPTIVE_DRIFT", "n=100;median=22.0;IQR=10.0;missing_rate=0", "", "descriptive", "ok"),
        ("SELECTION_HEALTH", "ADX_ONLY_budget_5", "HEALTHY", "eligible=100;selected=5;fill_ratio=1;previous_overlap=4;Phase5.9_entry_turnover=0.2;Phase6_weight_turnover=0.2", "", "selection", "ok"),
        ("PORTFOLIO_HEALTH", "equal_weight_structure_budget_5", "HEALTHY", "gross=1.0;cash=0.0;selected=5;requested=5;max_weight=0.2;HHI=0.2;effective_N=5.0;Phase6_weight_turnover=0.2", "", "portfolio", "ok"),
        ("FORWARD_PROTOCOL", "formation_and_maturity_status", "NOT_APPLICABLE", f"latest_market={session};formations=2;latest=2026-09-20;pending=1;matured=1;unavailable=0;missing_sessions=0", "", "operational", "counts only"),
    ]
    for name, observed in (health or {}).items():
        for i, item in enumerate(evidence):
            if item[1] == name:
                evidence[i] = (*item[:3], observed, *item[4:])
                break
    snapshot_rows = [{
        "snapshot_identity": snapshot_identity, "status": "DESCRIPTIVE_DRIFT", "observed_at_utc": stamp,
        "observed_market_session": session, "market_database_path": "not-read-by-12b",
        "market_snapshot_id": "market-snap", "specification_fingerprint": "monitor-spec-v1",
    }]
    with (directory / "monitoring_snapshot.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=tuple(snapshot_rows[0]), lineterminator="\n")
        writer.writeheader(); writer.writerows(snapshot_rows)
    evidence_fields = ("dimension", "name", "status", "observed", "reference", "comparison_semantics", "reason")
    with (directory / "monitoring_evidence.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n"); writer.writerow(evidence_fields); writer.writerows(evidence)
    (directory / "monitoring_report.md").write_text("phase 12a persisted report\n", encoding="utf-8")
    semantic = {
        "feature_semantics": feature_semantics,
        "selection_semantics": "ADX_ONLY; budgets 5/10/20",
        "portfolio_semantics": "equal weight; Phase 6 definitions",
        "forward_protocol_identity": refs.get("phase9_forward_protocol_fingerprint"),
    }
    manifest = {
        "contract": "quantlab.monitoring_snapshot", "version": "v1", "result_identity": snapshot_identity,
        "specification_fingerprint": "monitor-spec-v1", "observed_at_utc": stamp,
        "observed_market_session": session, "market_snapshot_id": "market-snap", "reference_identities": refs,
        **semantic, "performance_monitoring": False, "alerting": False, "control_actions": False,
        "artifacts": {name: _hash(directory / name) for name in ("monitoring_snapshot.csv", "monitoring_evidence.csv", "monitoring_report.md")},
    }
    (directory / "monitoring_manifest.json").write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return directory


def test_one_snapshot_is_retained_without_fabricated_transition(tmp_path: Path) -> None:
    _snapshot(tmp_path, "2026-09-25")
    result = evaluate_monitoring_history(tmp_path)
    assert result.history_state == "INSUFFICIENT_HISTORY"
    assert result.catalog.discovered_count == result.catalog.valid_count == 1
    assert result.transitions == ()


def test_two_compatible_snapshots_produce_descriptive_reconciled_deltas(tmp_path: Path) -> None:
    _snapshot(tmp_path, "2026-09-24", identity="one", health={"database_coverage_universe": "98"})
    _snapshot(tmp_path, "2026-09-25", identity="two", health={"database_coverage_universe": "100"})
    result = evaluate_monitoring_history(tmp_path)
    assert result.history_state == "LONGITUDINAL_EVIDENCE_AVAILABLE"
    rows = [row for row in result.transitions if row.metric_name == "database_coverage_universe"]
    assert len(rows) == 1
    assert (rows[0].previous_value, rows[0].current_value, rows[0].delta) == ("98", "100", 2.0)
    assert rows[0].transition_status == "COMPARABLE"
    assert "not a quality judgment" in rows[0].reason


def test_ordering_uses_session_before_timestamp_and_is_deterministic(tmp_path: Path) -> None:
    _snapshot(tmp_path, "2026-09-25", identity="later", timestamp="2026-09-25T18:00:00Z")
    _snapshot(tmp_path, "2026-09-24", identity="earlier", timestamp="2026-09-24T18:00:00Z")
    _snapshot(tmp_path, "2026-09-25", identity="same-day-conflict", timestamp="2026-09-25T19:00:00Z")
    catalog = discover_monitoring_snapshots(tmp_path)
    ordered_valid = [x for x in catalog.snapshots if x.classification == "CONFLICTING_SESSION"]
    assert [x.result_identity for x in ordered_valid] == ["later", "same-day-conflict"]
    assert catalog.conflicting_session_count == 2
    result = evaluate_monitoring_history(tmp_path)
    assert any(item.transition_status == "CONFLICTING_SAME_SESSION" for item in result.transitions)


def test_exact_duplicate_identity_is_explicit_and_collapsed_for_comparison(tmp_path: Path) -> None:
    _snapshot(tmp_path, "2026-09-25", identity="same", timestamp="2026-09-25T12:00:00Z")
    catalog = discover_monitoring_snapshots(tmp_path)
    original = catalog.snapshots[0]
    duplicate = replace(original, directory=original.directory + "_duplicate")
    canonical, count, classified = history_module._deduplicate_snapshots([duplicate, original])
    assert count == 1 and len(canonical) == 1
    assert classified[("same", original.directory)].classification == "VALID"
    assert classified[("same", duplicate.directory)].classification == "EXACT_DUPLICATE"
    assert evaluate_monitoring_history(tmp_path).transitions == ()


def test_malformed_and_tampered_snapshots_are_explicitly_invalid(tmp_path: Path) -> None:
    malformed = tmp_path / "quantlab_monitoring_snapshot_2026-09-23_bad"
    malformed.mkdir(); (malformed / "monitoring_manifest.json").write_text("{bad", encoding="utf-8")
    tampered = _snapshot(tmp_path, "2026-09-24")
    with (tampered / "monitoring_evidence.csv").open("a", encoding="utf-8") as stream:
        stream.write("tampered\n")
    catalog = discover_monitoring_snapshots(tmp_path)
    invalid = [item.invalid_reason for item in catalog.snapshots if item.classification == "INVALID"]
    assert len(invalid) == 2
    assert any("artifact hash mismatch" in reason for reason in invalid)
    assert any("JSON" in reason or "Expecting" in reason for reason in invalid)
    assert catalog.valid_count == 0


def test_reference_change_creates_boundary_without_numeric_deltas(tmp_path: Path) -> None:
    _snapshot(tmp_path, "2026-09-24", identity="one")
    _snapshot(tmp_path, "2026-09-25", identity="two", references={"phase9_forward_protocol_fingerprint": "protocol-v2", "phase6": "phase6-v1"})
    result = evaluate_monitoring_history(tmp_path)
    assert {row.transition_status for row in result.transitions} == {"REFERENCE_BOUNDARY"}
    assert {row.metric_name for row in result.transitions} == {"__transition__"}


def test_forward_deltas_are_operational_and_no_performance_fields_are_emitted(tmp_path: Path) -> None:
    _snapshot(tmp_path, "2026-09-24", identity="one")
    _snapshot(tmp_path, "2026-09-25", identity="two")
    result = evaluate_monitoring_history(tmp_path)
    assert {row.metric_name for row in result.transitions}.isdisjoint({"alpha", "forward_mean_return", "sharpe", "drawdown", "pnl"})
    assert any(row.metric_name == "forward_formation_count" and row.delta == 0 for row in result.transitions)
    assert any(row.metric_name == "stale_symbols_over_5_sessions" and row.delta == 0 for row in result.transitions)
    assert any(row.metric_name == "adx_available_count" and row.delta == 0 for row in result.transitions)
    assert any("operational" in row.reason for row in result.transitions if row.metric_name.startswith("forward_"))
    assert any("not persisted" in item for item in result.limitations)


def test_same_date_metrics_are_not_mistaken_for_selection_identity_continuity(tmp_path: Path) -> None:
    _snapshot(tmp_path, "2026-09-24", identity="one")
    _snapshot(tmp_path, "2026-09-25", identity="two")
    result = evaluate_monitoring_history(tmp_path)
    assert not any("jaccard" in row.metric_name.lower() or "intersection" in row.metric_name.lower() for row in result.transitions)
    assert any("selection identities were not persisted" in item for item in result.limitations)


def test_artifact_projection_is_compact_deterministic_and_has_empty_transition_schema(tmp_path: Path) -> None:
    _snapshot(tmp_path, "2026-09-25")
    first = evaluate_monitoring_history(tmp_path)
    second = evaluate_monitoring_history(tmp_path)
    assert first.identity == second.identity
    output = tmp_path / "history-output"
    hashes = write_monitoring_history_artifacts(first, output)
    assert set(hashes) == {CATALOG_FILE, TRANSITIONS_FILE, MANIFEST_FILE, REPORT_FILE}
    with (output / TRANSITIONS_FILE).open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream); assert reader.fieldnames and tuple(reader) == ()
    manifest = json.loads((output / MANIFEST_FILE).read_text(encoding="utf-8"))
    assert manifest["history_state"] == "INSUFFICIENT_HISTORY"
    assert manifest["performance_monitoring"] is False and manifest["alerting"] is False and manifest["control_actions"] is False
    assert set(manifest["artifacts"]) == {CATALOG_FILE, TRANSITIONS_FILE, REPORT_FILE}
    assert {name: _hash(output / name) for name in (CATALOG_FILE, TRANSITIONS_FILE, REPORT_FILE)} == manifest["artifacts"]
    with pytest.raises(FileExistsError):
        write_monitoring_history_artifacts(first, output)


def test_result_and_catalog_are_immutable_and_import_boundary_is_local_only() -> None:
    code = "import sys, quantlab.monitoring_history; assert 'sqlite3' not in sys.modules; assert 'vnstock' not in sys.modules; assert 'requests' not in sys.modules"
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    from quantlab.monitoring_history import MonitoringSnapshotCatalog, MonitoringHistoryResult
    with pytest.raises(FrozenInstanceError):
        MonitoringSnapshotCatalog((), 0, 0, 0, 0, 0).valid_count = 1  # type: ignore[misc]

