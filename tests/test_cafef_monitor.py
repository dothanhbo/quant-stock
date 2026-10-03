from __future__ import annotations

import ast
from datetime import date
from hashlib import sha256
from pathlib import Path
import sqlite3
import zipfile

import pytest

from quantctl.cafef_monitor import build_monitoring_report, write_monitoring_report
from quantctl.cli import main
from quantlab.cafef_manual_eod import AdjustmentClaim, ManualInputError, expected_venue_members


CURRENT_SESSION = date(2026, 10, 2)
PREVIOUS_SESSION = date(2026, 10, 1)
ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "quantctl" / "cafef_monitor.py"

PREVIOUS = {
    "HSX": (
        "TPB,20261001,14.4,14.4,14.4,14.4,16555800",
        "KOS,20261001,15.8,15.8,15.8,15.8,2500",
    ),
    "HNX": ("ANV,20261001,15.9,15.9,15.9,15.9,432500",),
    "UPCOM": ("PNJ,20261001,24.75,24.75,24.75,24.75,1677600",),
}
CURRENT = {
    "HSX": (
        "TPB,20261002,11.9,11.9,11.9,11.9,11178200",
        "KOS,20261002,14.7,14.7,14.7,14.7,5000",
    ),
    "HNX": ("ANV,20261002,17,17,17,17,2821200",),
    "UPCOM": ("PNJ,20261002,23.05,23.05,23.05,23.05,76068800",),
}


def _csv(rows: tuple[str, ...]) -> bytes:
    return (
        "\ufeff<Ticker>,<DTYYYYMMDD>,<Open>,<High>,<Low>,<Close>,<Volume>\n"
        + "\n".join(rows)
        + "\n"
    ).encode("utf-8")


def _archive(path: Path, session: date, rows: dict[str, tuple[str, ...]]) -> str:
    members = expected_venue_members(session, AdjustmentClaim.RAW)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for venue in ("HSX", "HNX", "UPCOM"):
            archive.writestr(members[venue], _csv(rows[venue]))
    return sha256(path.read_bytes()).hexdigest()


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE prices(
                symbol TEXT NOT NULL, time TEXT NOT NULL,
                open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                close REAL NOT NULL, volume REAL NOT NULL,
                UNIQUE(symbol, time)
            )
            """
        )
        for venue_rows in PREVIOUS.values():
            for row in venue_rows:
                symbol, _, open_, high, low, close, volume = row.split(",")
                connection.execute(
                    "INSERT INTO prices VALUES(?,?,?,?,?,?,?)",
                    (symbol, PREVIOUS_SESSION.isoformat(), open_, high, low, close, volume),
                )
        connection.execute(
            "INSERT INTO prices VALUES('VNINDEX','2026-10-01',1000,1010,990,1005,1)"
        )
        connection.commit()


def _report(tmp_path: Path, current_rows: dict[str, tuple[str, ...]] = CURRENT):
    database = tmp_path / "market.db"
    _database(database)
    previous = tmp_path / "previous.zip"
    current = tmp_path / "current.zip"
    previous_hash = _archive(previous, PREVIOUS_SESSION, PREVIOUS)
    current_hash = _archive(current, CURRENT_SESSION, current_rows)
    report = build_monitoring_report(
        archive_path=current,
        session=CURRENT_SESSION,
        expected_sha256=current_hash,
        canonical_database=database,
        previous_archive_path=previous,
        previous_session=PREVIOUS_SESSION,
        previous_expected_sha256=previous_hash,
        expected_universe_size=4,
    )
    return report, database, current, current_hash


def test_monitor_has_no_network_provider_or_production_imports() -> None:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    forbidden = {
        "http.client", "httpx", "requests", "socket", "urllib", "urllib3", "vnstock",
        "update_data", "save_price_data",
    }
    assert imports.isdisjoint(forbidden)


def test_read_only_monitor_reports_parity_flags_and_unknown_evidence(tmp_path: Path) -> None:
    report, database, _, _ = _report(tmp_path)

    assert report["integrity"] == {
        "archive_structure": "PASS",
        "ohlcv": "PASS",
        "duplicate_symbol_session": "PASS",
    }
    assert report["universe"]["status"] == "PASS"
    assert report["universe"]["covered"] == 4
    assert report["same_session_parity"]["status"] == "PASS"
    assert report["same_session_parity"]["exact_ohlcv_matches"] == 4
    flags = report["adjacent_monitoring"]["flags"]
    assert {item["symbol"] for item in flags if item["kind"] == "PRICE_CHANGE"} == {
        "ANV", "KOS", "PNJ", "TPB",
    }
    assert {item["symbol"] for item in flags if item["kind"] == "VOLUME_CHANGE"} == {
        "ANV", "PNJ",
    }
    assert all(item["explanation"] == "UNKNOWN" for item in flags)
    assert report["evidence_status"]["publication_completion"] == "UNKNOWN"
    assert report["evidence_status"]["operational_admission"] == "NOT_EVALUATED"
    assert report["evidence_status"]["research_eligible"] is False
    assert report["canonical"]["database_sha256_before"] == report["canonical"]["database_sha256_after"]
    assert not Path(f"{database}-wal").exists()
    assert not Path(f"{database}-shm").exists()


def test_incomplete_universe_fails_coverage_closed(tmp_path: Path) -> None:
    incomplete = {venue: tuple(rows) for venue, rows in CURRENT.items()}
    incomplete["HSX"] = (CURRENT["HSX"][0],)
    report, _, _, _ = _report(tmp_path, incomplete)
    assert report["universe"]["status"] == "INCOMPLETE"
    assert report["universe"]["missing"] == ["KOS"]
    assert report["adjacent_monitoring"]["status"] == "INCOMPLETE"
    assert report["adjacent_monitoring"]["current_missing"] == ["KOS"]


def test_missing_previous_archive_is_explicit_unknown(tmp_path: Path) -> None:
    database = tmp_path / "market.db"
    _database(database)
    current = tmp_path / "current.zip"
    current_hash = _archive(current, CURRENT_SESSION, CURRENT)
    report = build_monitoring_report(
        archive_path=current,
        session=CURRENT_SESSION,
        expected_sha256=current_hash,
        canonical_database=database,
        expected_universe_size=4,
    )
    assert report["previous_archive"]["status"] == "MISSING"
    assert report["adjacent_monitoring"]["status"] == "UNKNOWN"
    assert report["same_session_parity"]["status"] == "UNKNOWN"


def test_hash_mismatch_and_malformed_archive_are_rejected(tmp_path: Path) -> None:
    database = tmp_path / "market.db"
    _database(database)
    current = tmp_path / "current.zip"
    current_hash = _archive(current, CURRENT_SESSION, CURRENT)
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        build_monitoring_report(
            archive_path=current,
            session=CURRENT_SESSION,
            expected_sha256="0" * 64,
            canonical_database=database,
            expected_universe_size=4,
        )
    current.write_bytes(b"not a zip")
    corrupt_hash = sha256(current.read_bytes()).hexdigest()
    with pytest.raises(ManualInputError, match="malformed"):
        build_monitoring_report(
            archive_path=current,
            session=CURRENT_SESSION,
            expected_sha256=corrupt_hash,
            canonical_database=database,
            expected_universe_size=4,
        )
    assert current_hash != corrupt_hash


def test_replay_is_deterministic_and_changed_bytes_change_identity(tmp_path: Path) -> None:
    report, database, current, current_hash = _report(tmp_path)
    replay = build_monitoring_report(
        archive_path=current,
        session=CURRENT_SESSION,
        expected_sha256=current_hash,
        canonical_database=database,
        previous_archive_path=tmp_path / "previous.zip",
        previous_session=PREVIOUS_SESSION,
        previous_expected_sha256=sha256((tmp_path / "previous.zip").read_bytes()).hexdigest(),
        expected_universe_size=4,
    )
    assert replay["report_identity"] == report["report_identity"]

    changed = {venue: tuple(rows) for venue, rows in CURRENT.items()}
    changed["HSX"] = (
        "TPB,20261002,12,12,12,12,11178200",
        CURRENT["HSX"][1],
    )
    changed_hash = _archive(current, CURRENT_SESSION, changed)
    changed_report = build_monitoring_report(
        archive_path=current,
        session=CURRENT_SESSION,
        expected_sha256=changed_hash,
        canonical_database=database,
        previous_archive_path=tmp_path / "previous.zip",
        previous_session=PREVIOUS_SESSION,
        previous_expected_sha256=sha256((tmp_path / "previous.zip").read_bytes()).hexdigest(),
        expected_universe_size=4,
    )
    assert changed_hash != current_hash
    assert changed_report["report_identity"] != report["report_identity"]


def test_report_output_is_unique_and_atomic(tmp_path: Path) -> None:
    report, _, _, _ = _report(tmp_path)
    output = tmp_path / "evidence" / "run-1"
    assert write_monitoring_report(report, output) == output.resolve()
    assert (output / "report.json").is_file()
    assert (output / "report.md").is_file()
    assert not list(output.glob("*.tmp"))
    with pytest.raises(FileExistsError):
        write_monitoring_report(report, output)


def test_previous_session_must_precede_current(tmp_path: Path) -> None:
    database = tmp_path / "market.db"
    _database(database)
    current = tmp_path / "current.zip"
    current_hash = _archive(current, CURRENT_SESSION, CURRENT)
    with pytest.raises(ValueError, match="must precede"):
        build_monitoring_report(
            archive_path=current,
            session=CURRENT_SESSION,
            expected_sha256=current_hash,
            canonical_database=database,
            previous_archive_path=current,
            previous_session=CURRENT_SESSION,
            previous_expected_sha256=current_hash,
            expected_universe_size=4,
        )


def test_quantctl_command_writes_read_only_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "market.db"
    _database(database)
    current = tmp_path / "current.zip"
    current_hash = _archive(current, CURRENT_SESSION, CURRENT)
    output = tmp_path / "reports" / "run-1"
    before = sha256(database.read_bytes()).hexdigest()
    assert main([
        "data", "cafef-monitor",
        "--archive", str(current),
        "--session", CURRENT_SESSION.isoformat(),
        "--expected-sha256", current_hash,
        "--canonical-database", str(database),
        "--output-directory", str(output),
        "--expected-universe-size", "4",
    ]) == 0
    assert str(output.resolve()) in capsys.readouterr().out
    assert (output / "report.json").is_file()
    assert sha256(database.read_bytes()).hexdigest() == before
