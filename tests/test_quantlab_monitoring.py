from __future__ import annotations

import csv
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

import quantlab.monitoring as monitoring
from quantlab.monitoring import (
    MonitoringEvidence,
    MonitoringStatus,
    collect_monitoring_snapshot,
    load_frozen_monitoring_references,
    write_monitoring_artifacts,
)


def _database(path: Path, *, duplicate_latest: bool = False, history: int = 220) -> tuple[str, str]:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE prices (id INTEGER PRIMARY KEY, symbol TEXT, time TEXT, open REAL, high REAL, low REAL, close REAL, volume INTEGER)")
    import datetime as dt
    start = dt.date(2025, 1, 1)
    sessions = [(start + dt.timedelta(days=i)).isoformat() for i in range(history)]
    rows = []
    for i, session in enumerate(sessions):
        close = 100.0 + i * .2 + (i % 7) * .3
        rows.append(("VNINDEX", session, close, close + 1, close - 1, close, 100000))
        for offset, symbol in enumerate(("AAA", "BBB", "CCC")):
            price = 20.0 + i * (.1 + offset * .02) + ((i + offset) % 5) * .2
            rows.append((symbol, session, price, price + 1, price - 1, price, 10000 + offset))
    connection.executemany("INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)", rows)
    if duplicate_latest:
        last = sessions[-1]
        connection.execute("INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)", ("AAA", last, 1, 2, .5, 1, 10))
    connection.commit(); connection.close()
    return sessions[0], sessions[-1]


def _reference_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(monitoring, "_load_phase6_summary", lambda path, budget: {
        "mean_selected_count": "4.8", "mean_fill_ratio": ".96",
        "mean_effective_n": "4.8", "mean_max_single_name_weight": ".21",
    })


def test_integrity_duplicate_market_row_degrades_but_drift_is_separate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reference_summary(monkeypatch)
    database = tmp_path / "market.db"; _database(database, duplicate_latest=True)
    result = collect_monitoring_snapshot(database_path=database, ledger_path=tmp_path / "absent-forward.db", observed_at_utc="2026-09-28T00:00:00Z", references={"phase6": "frozen"})
    evidence = {(item.dimension, item.name): item for item in result.snapshot.evidence}
    assert evidence[("DATA_HEALTH", "duplicate_symbol_date_groups")].status is MonitoringStatus.DEGRADED
    assert evidence[("DATA_HEALTH", "latest_vnindex_row_count")].status is MonitoringStatus.HEALTHY
    assert evidence[("FEATURE_DRIFT", "ADX14_distribution")].status is MonitoringStatus.DESCRIPTIVE_DRIFT
    assert result.snapshot.overall_status is MonitoringStatus.DEGRADED


def test_latest_feature_health_and_frozen_selection_metrics_are_observed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reference_summary(monkeypatch)
    database = tmp_path / "market.db"; first, latest = _database(database)
    result = collect_monitoring_snapshot(database_path=database, ledger_path=tmp_path / "absent-forward.db", observed_at_utc="2026-09-28T00:00:00Z", references={"phase6": "frozen"})
    evidence = {(item.dimension, item.name): item for item in result.snapshot.evidence}
    assert result.snapshot.observed_market_session == latest
    assert first < latest
    assert evidence[("FEATURE_HEALTH", "latest_session_ADX_RSI_availability")].observed.endswith("joint=3;eligible_universe=3")
    assert evidence[("SELECTION_HEALTH", "ADX_ONLY_budget_5")].observed.startswith("eligible=3;selected=3")
    assert evidence[("PORTFOLIO_HEALTH", "equal_weight_structure_budget_5")].observed.startswith("gross=1.0;cash=0.0")
    assert result.details["portfolio_5_hhi"] == str(1 / 3)


def test_descriptive_feature_shift_never_creates_integrity_degradation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reference_summary(monkeypatch)
    database = tmp_path / "market.db"; _database(database)
    result = collect_monitoring_snapshot(database_path=database, ledger_path=tmp_path / "absent-forward.db", observed_at_utc="2026-09-28T00:00:00Z", references={"phase6": "frozen"})
    drift = next(item for item in result.snapshot.evidence if item.name == "ADX14_distribution")
    assert drift.status is MonitoringStatus.DESCRIPTIVE_DRIFT
    assert drift.reference is None
    assert "no frozen raw ADX distribution" in drift.reason
    assert not any(item.status is MonitoringStatus.DEGRADED and item.dimension == "FEATURE_DRIFT" for item in result.snapshot.evidence)


def test_missing_required_ohlcv_is_an_explicit_integrity_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reference_summary(monkeypatch)
    database = tmp_path / "market.db"; _database(database)
    connection = sqlite3.connect(database); connection.execute("UPDATE prices SET volume=NULL WHERE symbol='BBB' AND time=(SELECT MAX(time) FROM prices WHERE symbol='BBB')"); connection.commit(); connection.close()
    result = collect_monitoring_snapshot(database_path=database, ledger_path=tmp_path / "absent.db", observed_at_utc="2026-09-28T00:00:00Z", references={"phase6": "frozen"})
    evidence = next(item for item in result.snapshot.evidence if item.name == "latest_session_missing_ohlcv")
    assert evidence.status is MonitoringStatus.DEGRADED
    assert evidence.observed == "1"


def test_stale_symbol_is_reported_against_vnindex_session_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reference_summary(monkeypatch)
    database = tmp_path / "market.db"; _database(database, history=220)
    connection = sqlite3.connect(database)
    latest = connection.execute("SELECT MAX(time) FROM prices WHERE symbol='VNINDEX'").fetchone()[0]
    connection.execute("DELETE FROM prices WHERE symbol='AAA' AND time>(SELECT time FROM prices WHERE symbol='VNINDEX' ORDER BY time DESC LIMIT 1 OFFSET 6)")
    connection.commit(); connection.close()
    result = collect_monitoring_snapshot(database_path=database, ledger_path=tmp_path / "absent.db", observed_at_utc="2026-09-28T00:00:00Z", references={"phase6": "frozen"})
    stale = next(item for item in result.snapshot.evidence if item.name == "symbol_latest_session_AAA")
    assert stale.status is MonitoringStatus.DEGRADED
    assert int(result.details["stale_symbols_over_5_sessions"]) >= 1
    assert latest in stale.reference


def test_forward_ledger_missing_is_not_created_and_zero_formations_are_not_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reference_summary(monkeypatch)
    database = tmp_path / "market.db"; _database(database)
    ledger = tmp_path / "forward.db"
    result = collect_monitoring_snapshot(database_path=database, ledger_path=ledger, observed_at_utc="2026-09-28T00:00:00Z", references={"phase6": "frozen"})
    assert not ledger.exists()
    item = next(value for value in result.snapshot.evidence if value.name == "ledger_status")
    assert item.status is MonitoringStatus.NOT_APPLICABLE
    assert "no activation or formations" in item.reason


def test_forward_identity_mismatch_is_surfaced_as_degraded(tmp_path: Path) -> None:
    protocol = monitoring.load_protocol_spec(monitoring.DEFAULT_PROTOCOL_PATH)
    ledger = tmp_path / "forward.db"
    connection = sqlite3.connect(ledger)
    connection.execute("CREATE TABLE forward_protocols(protocol_id TEXT, protocol_fingerprint TEXT, activation_market_session_boundary TEXT, operational_start_after_session TEXT)")
    connection.execute("CREATE TABLE forward_formations(protocol_id TEXT, formation_identity TEXT, formation_session TEXT)")
    connection.execute("CREATE TABLE forward_maturities(protocol_id TEXT, formation_identity TEXT, horizon_sessions INTEGER, event_sequence INTEGER, status TEXT)")
    connection.execute("CREATE TABLE forward_outcomes(protocol_id TEXT, availability TEXT)")
    connection.execute("CREATE TABLE forward_audit_events(protocol_id TEXT, event_type TEXT)")
    connection.execute("INSERT INTO forward_protocols VALUES(?,?,?,?)", (protocol.protocol_id, "wrong", protocol.activation_market_session_boundary, "2026-09-18")); connection.commit(); connection.close()
    evidence, _details = monitoring._read_forward_status(ledger, monitoring.DEFAULT_PROTOCOL_PATH, "2026-09-25")
    assert next(item for item in evidence if item.name == "activation_identity").status is MonitoringStatus.DEGRADED


def test_snapshot_identity_is_deterministic_and_binds_observation_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reference_summary(monkeypatch)
    database = tmp_path / "market.db"; _database(database)
    kwargs = {"database_path": database, "ledger_path": tmp_path / "absent.db", "references": {"phase6": "frozen"}}
    first = collect_monitoring_snapshot(**kwargs, observed_at_utc="2026-09-28T00:00:00Z")
    again = collect_monitoring_snapshot(**kwargs, observed_at_utc="2026-09-28T00:00:00Z")
    later = collect_monitoring_snapshot(**kwargs, observed_at_utc="2026-09-28T00:00:01Z")
    assert first.snapshot.identity == again.snapshot.identity
    assert first.snapshot.identity != later.snapshot.identity
    assert tuple((x.dimension, x.name) for x in first.snapshot.evidence) == tuple(sorted((x.dimension, x.name) for x in first.snapshot.evidence))


def test_artifact_projection_is_compact_and_hashes_all_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _reference_summary(monkeypatch)
    database = tmp_path / "market.db"; _database(database)
    result = collect_monitoring_snapshot(database_path=database, ledger_path=tmp_path / "absent.db", observed_at_utc="2026-09-28T00:00:00Z", references={"phase6": "frozen"})
    output = tmp_path / "snapshot"
    hashes = write_monitoring_artifacts(result, output)
    assert set(hashes) == {"monitoring_snapshot.csv", "monitoring_evidence.csv", "monitoring_manifest.json", "monitoring_report.md"}
    manifest = json.loads((output / "monitoring_manifest.json").read_text(encoding="utf-8"))
    assert manifest["performance_monitoring"] is False and manifest["control_actions"] is False
    assert set(manifest["artifacts"]) == {"monitoring_snapshot.csv", "monitoring_evidence.csv", "monitoring_report.md"}
    with (output / "monitoring_evidence.csv").open(encoding="utf-8", newline="") as handle:
        rows = tuple(csv.DictReader(handle))
    assert rows and all("comparison_semantics" in row for row in rows)
    with pytest.raises(FileExistsError):
        write_monitoring_artifacts(result, output)


def test_reference_loader_fails_closed_on_noncanonical_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="canonical research_results"):
        load_frozen_monitoring_references(tmp_path)


def test_canonical_reference_chain_is_complete_and_contains_phase_11a_to_e() -> None:
    references = load_frozen_monitoring_references()
    assert references["phase6_portfolio_structure"]
    assert references["phase9_forward_protocol_fingerprint"]
    assert references["phase11a_capability_result_identity"]
    assert references["phase11b_timing_result_identity"]
    assert references["phase11e_friction_result_identity"]


def test_module_import_has_no_network_or_operational_callable_import() -> None:
    completed = subprocess.run([sys.executable, "-c", "import sys, quantlab.monitoring; assert 'vnstock' not in sys.modules; assert 'services.telegram_client' not in sys.modules; assert 'scripts.run_daily' not in sys.modules"], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
