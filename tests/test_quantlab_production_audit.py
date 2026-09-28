from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from quantlab.operations import (
    AuditSeverity,
    ProductionAuditSpec,
    RetrySafety,
    build_production_audit,
    write_production_audit_artifacts,
)
from research.run_quantlab_production_audit import _read_ledger_observation


COMMIT = "33d4ee09fb65117621b28784e00eb44f6a8922c1"
OBSERVATION = {
    "present": True,
    "schema_complete": True,
    "protocol_count": 1,
    "activation_count": 1,
    "formation_count": 4,
    "pending_maturity_count": 2,
    "matured_maturity_count": 6,
    "unavailable_maturity_count": 0,
    "outcome_count": 24,
    "missing_formation_event_count": 0,
    "identity_match": True,
}


def _result(**overrides):
    kwargs = {
        "repository_commit_identity": COMMIT,
        "forward_ledger_observation": OBSERVATION,
        "tracked_deployment_evidence": (".github/workflows/daily_scan.yml",),
    }
    kwargs.update(overrides)
    return build_production_audit(**kwargs)


def test_audit_result_order_and_identity_are_deterministic() -> None:
    first = _result()
    second = _result(tracked_deployment_evidence=(".github/workflows/daily_scan.yml",))

    assert first.identity == second.identity
    observed_order = tuple((row.classification, row.name) for row in first.entry_points)
    assert observed_order == tuple(sorted(observed_order))
    assert first.entry_points == second.entry_points
    assert first.forward_ledger_observation["formation_count"] == 4


def test_state_ownership_and_side_effect_boundaries_are_explicit() -> None:
    result = _result()

    assert all(store.owners and store.readers for store in result.state_stores)
    assert any(item.effect == "DATABASE_WRITE/SCHEMA_CREATE" and item.possible for item in result.side_effects)
    assert any(item.effect == "BROKER_ACTION" and not item.possible for item in result.side_effects)


def test_unknown_retry_classification_fails_closed_and_taxonomy_is_explicit() -> None:
    result = _result()

    assert tuple(item.value for item in RetrySafety) == (
        "IDEMPOTENT", "RETRY_SAFE", "CONDITIONALLY_RETRY_SAFE", "NOT_RETRY_SAFE", "UNKNOWN"
    )
    assert any(row.retry_safety is RetrySafety.UNKNOWN for row in result.failures)
    assert ProductionAuditSpec().read_only is True


def test_forward_recovery_conclusion_records_duplicate_and_interruption_guards() -> None:
    result = _result()

    assert "protocol+session uniqueness" in result.forward_recovery_conclusion
    assert "transactional" in result.forward_recovery_conclusion
    assert "counts only" in result.forward_recovery_conclusion
    assert result.forward_ledger_observation["identity_match"] is True


def test_paper_replay_risk_is_not_upgraded_without_idempotency_evidence() -> None:
    result = _result()

    assert "NOT_RETRY_SAFE" in result.paper_recovery_conclusion
    assert "random order UUID" in result.paper_recovery_conclusion
    assert "UNKNOWN" in result.paper_recovery_conclusion


def test_presentation_failure_is_separate_from_canonical_state_failure() -> None:
    result = _result()
    telegram_failure = next(row for row in result.failures if row.scenario == "Telegram broadcast/query failure")

    assert telegram_failure.retry_safety is RetrySafety.CONDITIONALLY_RETRY_SAFE
    assert "preceding market/forward/paper/signal writes remain" in telegram_failure.already_committed
    assert "Message delivery only" in telegram_failure.remains_uncommitted


def test_missing_tracked_runtime_units_are_reported_as_partial() -> None:
    result = _result(tracked_deployment_evidence=(".github/workflows/daily_scan.yml",))
    assert result.deployment_provenance == "DEPLOYMENT_RUNTIME_PROVENANCE_PARTIAL"


def test_secret_values_are_never_read_or_serialized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = "DO_NOT_SERIALIZE_SECRET_91ef"
    monkeypatch.setenv("TELEGRAM_TOKEN", sentinel)
    monkeypatch.setenv("CHAT_ID", sentinel)
    result = _result()
    output = tmp_path / "audit"
    write_production_audit_artifacts(result, str(output))
    serialized = "".join(path.read_text(encoding="utf-8") for path in output.iterdir())

    assert sentinel not in serialized
    assert any(item.name == "TELEGRAM_TOKEN" for item in result.configuration)
    assert all(sentinel not in item.note and sentinel not in item.scope for item in result.configuration)


def test_fresh_process_import_has_no_database_network_or_operational_imports() -> None:
    code = (
        "import sys; import quantlab.operations; "
        "forbidden={'sqlite3','requests','vnstock','sqlalchemy','strategy.scanner','core.database'}; "
        "print(','.join(sorted(forbidden & set(sys.modules))))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if key not in {"PYTHONPATH"}},
    )
    assert completed.stdout.strip() == ""


def test_artifact_schemas_hashes_and_existing_output_protection(tmp_path: Path) -> None:
    result = _result()
    output = tmp_path / "audit"
    returned_hashes = write_production_audit_artifacts(result, str(output))

    expected = {
        "production_entrypoints.csv", "production_state_inventory.csv",
        "production_failure_matrix.csv", "production_recovery_gaps.csv",
        "production_audit_manifest.json", "production_audit_report.md",
    }
    assert set(returned_hashes) == expected
    for name in expected:
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == returned_hashes[name]
    manifest = json.loads((output / "production_audit_manifest.json").read_text(encoding="utf-8"))
    assert manifest["result_identity"] == result.identity
    assert manifest["no_mutation_no_network"] is True
    with (output / "production_failure_matrix.csv").open(encoding="utf-8", newline="") as stream:
        rows = tuple(csv.DictReader(stream))
    assert len(rows) == len(result.failures)
    with pytest.raises(FileExistsError):
        write_production_audit_artifacts(result, str(output))


def test_ledger_observation_rejects_unapproved_or_malformed_fields() -> None:
    with pytest.raises(ValueError, match="allowlist"):
        _result(forward_ledger_observation={**OBSERVATION, "secret": "hidden"})
    with pytest.raises(ValueError, match="non-negative"):
        _result(forward_ledger_observation={**OBSERVATION, "formation_count": -1})
    with pytest.raises(TypeError, match="boolean"):
        _result(forward_ledger_observation={**OBSERVATION, "present": 1})


def test_severity_counts_are_deterministic_and_gaps_are_concrete() -> None:
    result = _result()
    counts = {severity.value: sum(gap.severity is severity for gap in result.recovery_gaps) for severity in AuditSeverity}

    assert counts == {"CRITICAL": 0, "MATERIAL": 5, "MINOR": 3, "INFORMATIONAL": 2}
    assert any("workflow force-adds root market.db" in gap.consequence.casefold() for gap in result.recovery_gaps)


def test_canonical_ledger_observer_reads_counts_without_modifying_fixture(tmp_path: Path) -> None:
    database = tmp_path / "forward.db"
    protocol_path = tmp_path / "protocol.json"
    fingerprint = "f" * 64
    protocol_path.write_text(json.dumps({"protocol_fingerprint": fingerprint}), encoding="utf-8")
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE forward_protocols (protocol_fingerprint TEXT);
            CREATE TABLE forward_formations (formation_identity TEXT);
            CREATE TABLE forward_positions (position_identity TEXT);
            CREATE TABLE forward_maturities (status TEXT);
            CREATE TABLE forward_outcomes (outcome_identity TEXT);
            CREATE TABLE forward_audit_events (event_type TEXT);
            INSERT INTO forward_protocols VALUES ('ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff');
            INSERT INTO forward_formations VALUES ('formation-1');
            INSERT INTO forward_maturities VALUES ('PENDING');
            INSERT INTO forward_maturities VALUES ('MATURED');
            INSERT INTO forward_maturities VALUES ('OUTCOME_UNAVAILABLE');
            INSERT INTO forward_outcomes VALUES ('outcome-1');
            INSERT INTO forward_audit_events VALUES ('MISSING_FORMATION');
            """
        )
    before = database.read_bytes()

    observed = _read_ledger_observation(database, protocol_path)

    assert observed == {
        "present": True,
        "schema_complete": True,
        "protocol_count": 1,
        "activation_count": 1,
        "formation_count": 1,
        "pending_maturity_count": 1,
        "matured_maturity_count": 1,
        "unavailable_maturity_count": 1,
        "outcome_count": 1,
        "missing_formation_event_count": 1,
        "identity_match": True,
    }
    assert database.read_bytes() == before
