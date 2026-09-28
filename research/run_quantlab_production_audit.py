"""Run the read-only Phase 13A operational-surface audit once."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quantlab.operations.production_audit import (  # noqa: E402
    build_production_audit,
    write_production_audit_artifacts,
)


_LEDGER_TABLES = {
    "forward_protocols",
    "forward_formations",
    "forward_positions",
    "forward_maturities",
    "forward_outcomes",
    "forward_audit_events",
}


def _commit_identity() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _tracked_deployment_evidence() -> tuple[str, ...]:
    completed = subprocess.run(
        ["git", "ls-files"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    selected = []
    for path in completed.stdout.splitlines():
        normalized = path.replace("\\", "/")
        lower = normalized.lower()
        if (
            lower.startswith(".github/workflows/")
            or "/systemd/" in f"/{lower}/"
            or lower.endswith((".service", ".timer", "dockerfile", "docker-compose.yml", "docker-compose.yaml"))
            or "deploy" in lower
        ):
            selected.append(normalized)
    return tuple(sorted(selected))


def _read_ledger_observation(database_path: Path, protocol_path: Path) -> dict[str, Any]:
    """Read only a bounded set of ledger counts; never initialize or mutate it."""
    if not database_path.exists():
        return {
            "present": False,
            "schema_complete": False,
            "protocol_count": 0,
            "activation_count": 0,
            "formation_count": 0,
            "pending_maturity_count": 0,
            "matured_maturity_count": 0,
            "unavailable_maturity_count": 0,
            "outcome_count": 0,
            "missing_formation_event_count": 0,
            "identity_match": False,
        }

    uri = "file:" + quote(database_path.resolve().as_posix(), safe="/:") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        complete = _LEDGER_TABLES.issubset(tables)
        observation: dict[str, Any] = {
            "present": True,
            "schema_complete": complete,
            "protocol_count": 0,
            "activation_count": 0,
            "formation_count": 0,
            "pending_maturity_count": 0,
            "matured_maturity_count": 0,
            "unavailable_maturity_count": 0,
            "outcome_count": 0,
            "missing_formation_event_count": 0,
            "identity_match": False,
        }
        if not complete:
            return observation

        counts = {
            "protocol_count": ("forward_protocols", None),
            # The activation row is persisted in forward_protocols; there is
            # no separate forward_activations table in the ledger contract.
            "activation_count": ("forward_protocols", None),
            "formation_count": ("forward_formations", None),
            "outcome_count": ("forward_outcomes", None),
            "missing_formation_event_count": (
                "forward_audit_events",
                "event_type = 'MISSING_FORMATION'",
            ),
        }
        for output_key, (table, predicate) in counts.items():
            sql = f"SELECT COUNT(*) FROM {table}"
            if predicate:
                sql += f" WHERE {predicate}"
            observation[output_key] = int(connection.execute(sql).fetchone()[0])

        maturity_counts = connection.execute(
            "SELECT status, COUNT(*) FROM forward_maturities GROUP BY status"
        ).fetchall()
        for status, count in maturity_counts:
            normalized_status = str(status).upper()
            if normalized_status == "OUTCOME_UNAVAILABLE":
                observation["unavailable_maturity_count"] = int(count)
            elif normalized_status in {"PENDING", "MATURED"}:
                observation[f"{normalized_status.lower()}_maturity_count"] = int(count)

        try:
            protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
            expected_fingerprint = protocol.get("protocol_fingerprint")
            stored = {
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT protocol_fingerprint FROM forward_protocols"
                )
            }
            observation["identity_match"] = bool(
                expected_fingerprint and stored == {expected_fingerprint}
            )
        except (OSError, json.JSONDecodeError, AttributeError):
            observation["identity_match"] = False
        return observation
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        help="Exact new audit directory (defaults under research_results using HEAD).",
    )
    args = parser.parse_args(argv)

    commit = _commit_identity()
    ledger_path = PROJECT_ROOT / "data" / "forward_validation.db"
    protocol_path = PROJECT_ROOT / "research" / "forward_validation" / "protocol_v1.json"
    observation = _read_ledger_observation(ledger_path, protocol_path)
    result = build_production_audit(
        commit,
        forward_ledger_observation=observation,
        tracked_deployment_evidence=_tracked_deployment_evidence(),
    )
    output = args.output_root or (
        PROJECT_ROOT / "research_results" / f"quantlab_production_audit_{commit[:12]}"
    )
    write_production_audit_artifacts(result, str(output))
    print(f"Audit identity: {result.identity}")
    print(f"Ledger present: {observation['present']}; schema complete: {observation['schema_complete']}")
    print(f"Artifacts: {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
