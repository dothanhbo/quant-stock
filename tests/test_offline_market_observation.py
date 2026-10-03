from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path
import socket

import pytest

from quantlab.offline_market_observation import (
    ObservationContractError,
    record_offline_observations,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "offline_market_observation" / "synthetic_two_observations.json"
CALENDAR_FIXTURE = ROOT / "tests" / "fixtures" / "offline_market_observation" / "synthetic_calendar_2026-09-03.json"
MODULE = ROOT / "quantlab" / "offline_market_observation.py"


def _contract() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _read_result(directory: Path) -> dict:
    return json.loads((directory / "result.json").read_text(encoding="utf-8"))


def _deny_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    attempted: list[str] = []

    def blocked(*args, **kwargs):
        attempted.append(repr((args, kwargs)))
        raise AssertionError("network access is forbidden in offline observation")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    return attempted


def test_calendar_fixture_digest_is_attributable() -> None:
    from hashlib import sha256

    contract = _contract()
    calendar_fixture = json.loads(CALENDAR_FIXTURE.read_text(encoding="utf-8"))
    assert contract["calendar_snapshot"]["content_sha256"] == sha256(
        CALENDAR_FIXTURE.read_bytes()
    ).hexdigest()
    assert contract["calendar_snapshot"]["evidence_kind"] == calendar_fixture["evidence_kind"]
    assert contract["calendar_snapshot"]["sessions"] == calendar_fixture["sessions"]


def test_runner_has_no_transport_database_or_production_imports() -> None:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    forbidden = {
        "requests", "urllib3", "socket", "sqlite3", "vnstock",
        "quantlab.transactional_market_data", "quantlab.operational_admission",
        "quantlab.market_data_shadow_adapter",
    }
    assert imports.isdisjoint(forbidden)


def test_network_is_not_attempted_and_evidence_directories_are_unique(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempted = _deny_network(monkeypatch)
    first = record_offline_observations(_contract(), tmp_path)
    second = record_offline_observations(_contract(), tmp_path)
    assert first != second
    assert first.parent == second.parent == tmp_path.resolve()
    assert attempted == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.pop("symbol_to_venue"),
        lambda value: value.pop("calendar_snapshot"),
        lambda value: value.pop("publication_delay_seconds"),
        lambda value: value["calendar_snapshot"].update({"source_references": []}),
        lambda value: value["observations"][0].pop("raw_provenance"),
        lambda value: value["observations"][0].pop("normalized_provenance"),
    ],
)
def test_missing_evidence_fails_closed(tmp_path: Path, mutation) -> None:
    contract = _contract()
    mutation(contract)
    with pytest.raises(ObservationContractError):
        record_offline_observations(contract, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_non_disabled_transport_fails_closed(tmp_path: Path) -> None:
    contract = _contract()
    contract["transport"]["mode"] = "HTTP"
    with pytest.raises(ObservationContractError, match="DISABLED"):
        record_offline_observations(contract, tmp_path)


def test_identical_observations_are_preserved_without_becoming_admission(
    tmp_path: Path,
) -> None:
    directory = record_offline_observations(_contract(), tmp_path)
    records = [json.loads(line) for line in (directory / "observations.jsonl").read_text().splitlines()]
    result = _read_result(directory)
    assert [item["observation_id"] for item in records] == [
        "synthetic-aaa-20260903-a", "synthetic-aaa-20260903-b"
    ]
    assert records[0]["normalized_row_fingerprint"] == records[1]["normalized_row_fingerprint"]
    assert result["comparisons"][0]["all_identical"] is True
    assert result["admission_decision"] is None
    assert result["completed_session_status"] is None
    assert result["completed_session_inferred"] is False
    assert result["database_mutation_attempted"] is False


def test_different_observations_are_preserved(tmp_path: Path) -> None:
    contract = deepcopy(_contract())
    contract["observations"][1]["raw_payload"] = (
        "{\"data\":[[1788397200,10.0,10.5,9.9,10.3,126000]]}"
    )
    contract["observations"][1]["normalized_row"]["close"] = 10.3
    contract["observations"][1]["normalized_row"]["volume"] = 126000
    directory = record_offline_observations(contract, tmp_path)
    records = [json.loads(line) for line in (directory / "observations.jsonl").read_text().splitlines()]
    result = _read_result(directory)
    assert len(records) == 2
    assert records[0]["raw_payload"] != records[1]["raw_payload"]
    assert result["comparisons"][0]["distinct_fingerprint_count"] == 2
    assert result["comparisons"][0]["all_identical"] is False


def test_publication_delay_has_no_default(tmp_path: Path) -> None:
    directory = record_offline_observations(_contract(), tmp_path)
    assert _read_result(directory)["publication_delay_seconds"] is None
