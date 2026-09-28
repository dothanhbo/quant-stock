from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from quantctl.cli import main
from quantctl.commands import doctor, research, status, version
from quantctl.registry import EXPECTED_ACTIVE_RUNNER_COUNT, discover_active_runners


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _market_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE prices (symbol TEXT, time TEXT, close REAL)")
        connection.executemany(
            "INSERT INTO prices VALUES (?, ?, ?)",
            (("AAA", "2026-01-02", 10.0), ("VNINDEX", "2026-01-02", 1000.0)),
        )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_cli_version_smoke(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    output = capsys.readouterr().out
    assert "QUANT SYSTEM VERSION" in output
    assert "quantctl: M1" in output


def test_module_entrypoint_version_smoke() -> None:
    completed = subprocess.run(
        (sys.executable, "-m", "quantctl", "version"),
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "QUANT SYSTEM VERSION" in completed.stdout


def test_research_list_discovers_only_active_root_runners(tmp_path: Path) -> None:
    research_root = tmp_path / "research"
    archive_root = research_root / "archive"
    archive_root.mkdir(parents=True)
    (research_root / "run_quantlab_alpha.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    (archive_root / "run_quantlab_old.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    (research_root / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")

    runners = discover_active_runners(root=tmp_path)
    assert tuple(item.short_name for item in runners) == ("alpha",)
    output = research.render_list(root=tmp_path)
    assert "research/run_quantlab_alpha.py" in output
    assert "old" not in output


def test_runner_discovery_count_matches_active_repository() -> None:
    runners = discover_active_runners(root=PROJECT_ROOT)
    expected = tuple(sorted((PROJECT_ROOT / "research").glob("run_quantlab_*.py")))
    assert len(runners) == len(expected) == EXPECTED_ACTIVE_RUNNER_COUNT
    assert all(item.available for item in runners)
    assert all(not item.archive_imports for item in runners)


def test_status_handles_missing_databases_without_creating_them(tmp_path: Path) -> None:
    (tmp_path / "research").mkdir()
    output = status.render(root=tmp_path)
    assert "Database: MISSING" in output
    assert "Forward: MISSING" in output
    assert not (tmp_path / "data").exists()


def test_doctor_never_exposes_environment_secret_values(tmp_path: Path) -> None:
    secret_token = "super-secret-token-value"
    secret_chat = "987654321"
    output = doctor.render(
        root=tmp_path,
        environ={"TELEGRAM_TOKEN": secret_token, "CHAT_ID": secret_chat},
    )
    assert "TELEGRAM_TOKEN: CONFIGURED" in output
    assert "CHAT_ID: CONFIGURED" in output
    assert secret_token not in output
    assert secret_chat not in output


def test_git_unavailable_fallback_does_not_crash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("quantctl.registry.run_git", lambda *args, **kwargs: None)
    output = version.render(root=tmp_path)
    assert "HEAD: UNKNOWN" in output
    assert "Tag: UNKNOWN" in output
    assert "Working tree: UNKNOWN" in output


def test_status_and_doctor_open_databases_without_mutation(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    market = data_root / "market.db"
    _market_database(market)
    for _, filename in status.STATE_DATABASES:
        with sqlite3.connect(data_root / filename) as connection:
            connection.execute("CREATE TABLE marker (value INTEGER)")
    before = {path.name: _digest(path) for path in data_root.iterdir()}

    status_output = status.render(root=tmp_path)
    doctor_output = doctor.render(root=tmp_path, environ={})

    assert "Latest session: 2026-01-02" in status_output
    assert "database readable: 2026-01-02" in doctor_output
    assert before == {path.name: _digest(path) for path in data_root.iterdir()}
    assert not tuple(data_root.glob("*.db-wal"))
    assert not tuple(data_root.glob("*.db-shm"))


def test_cli_exposes_only_m1_read_only_commands() -> None:
    parser = __import__("quantctl.cli", fromlist=["build_parser"]).build_parser()
    help_text = parser.format_help()
    assert "version" in help_text
    assert "status" in help_text
    assert "doctor" in help_text
    assert "research" in help_text
    for forbidden in ("update", "scan", "daily", "paper", "forward", "run"):
        assert f"{{{forbidden}" not in help_text.lower()
