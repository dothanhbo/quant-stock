from __future__ import annotations

from datetime import date
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from app.daily_pipeline import DailyPipeline
from core.market_data_integrity import (
    MarketDataIntegrityResult,
    MarketDataIntegrityState,
    check_market_data_integrity,
)
from core.paths import DEFAULT_MARKET_DATABASE_PATH


SESSION = "2026-09-28"
SYMBOLS = ("AAA", "BBB", "CCC", "VNINDEX")


def _create_market(path: Path, *, rows: list[tuple] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                time TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL, volume INTEGER
            )
            """
        )
        connection.executemany(
            "INSERT INTO prices(symbol,time,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)",
            rows
            or [(symbol, SESSION, 10.0, 12.0, 9.0, 11.0, 1000) for symbol in SYMBOLS],
        )


def _check(path: Path, symbols: tuple[str, ...] = SYMBOLS, as_of: str = SESSION):
    return check_market_data_integrity(
        required_symbols=symbols,
        database_path=path,
        as_of_date=as_of,
    )


def _pipeline_result(state: MarketDataIntegrityState, message: str = "fixture"):
    return MarketDataIntegrityResult(
        state=state,
        required_session=SESSION,
        required_symbols=SYMBOLS,
        reasons=(message,),
        database_path=Path("fixture.db"),
    )


def test_core_database_import_is_side_effect_free_and_runtime_init_honors_environment(tmp_path: Path) -> None:
    market = tmp_path / "nested" / "market.db"
    code = (
        "import os,pathlib; from core import database; "
        "p=pathlib.Path(os.environ['MARKET_DATABASE_PATH']); "
        "print(str(database.DATABASE_PATH==p.resolve()),p.exists(),p.parent.exists()); "
        "database.initialize_market_database(); "
        "print(p.exists())"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(os.environ, MARKET_DATABASE_PATH=str(market), PYTHONDONTWRITEBYTECODE="1"),
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.splitlines() == ["True False False", "True"]


def test_market_database_initializer_is_import_safe_and_explicit(tmp_path: Path) -> None:
    market = tmp_path / "initializer" / "market.db"
    code = (
        "import os,pathlib; import scripts.init_db as command; "
        "p=pathlib.Path(os.environ['MARKET_DATABASE_PATH']); "
        "print(p.exists(),p.parent.exists()); command.main(); print(p.exists())"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(
            os.environ,
            MARKET_DATABASE_PATH=str(market),
            PYTHONDONTWRITEBYTECODE="1",
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.splitlines() == [
        "False False",
        "Database created!",
        "True",
    ]


def test_scanner_import_creates_neither_market_nor_paper_database(tmp_path: Path) -> None:
    market = tmp_path / "market-state" / "market.db"
    paper = tmp_path / "paper-state" / "paper.db"
    code = (
        "import pathlib,os; import strategy.scanner as scanner; "
        "m=pathlib.Path(os.environ['MARKET_DATABASE_PATH']); p=pathlib.Path(os.environ['PAPER_DATABASE_PATH']); "
        "print(m.exists(),m.parent.exists(),p.exists(),p.parent.exists(),scanner.paper_signal_executor is None)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=dict(
            os.environ,
            MARKET_DATABASE_PATH=str(market),
            PAPER_DATABASE_PATH=str(paper),
            PYTHONDONTWRITEBYTECODE="1",
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "False False False False True"


def test_integrity_valid_current_session_passes_without_mutation(tmp_path: Path) -> None:
    market = tmp_path / "market.db"
    _create_market(market)
    before = market.read_bytes()
    result = _check(market)
    assert result.state is MarketDataIntegrityState.PASS
    assert result.required_session == SESSION
    assert market.read_bytes() == before


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([row for row in [(symbol, SESSION, 10, 12, 9, 11, 1000) for symbol in SYMBOLS] if row[0] != "VNINDEX"], "VNINDEX"),
        ([(symbol, "2026-09-27" if symbol == "VNINDEX" else SESSION, 10, 12, 9, 11, 1000) for symbol in SYMBOLS], "stale"),
        ([(symbol, SESSION, 10, 12, 9, None if symbol == "AAA" else 11, 1000) for symbol in SYMBOLS], "invalid OHLCV"),
    ],
)
def test_integrity_missing_stale_or_malformed_required_data_fails_closed(
    tmp_path: Path,
    rows: list[tuple],
    message: str,
) -> None:
    market = tmp_path / "market.db"
    _create_market(market, rows=rows)
    result = _check(market)
    assert result.state is MarketDataIntegrityState.FAIL_CLOSED
    assert message.casefold() in result.message.casefold()


def test_integrity_duplicate_required_key_fails_closed(tmp_path: Path) -> None:
    market = tmp_path / "market.db"
    rows = [(symbol, SESSION, 10, 12, 9, 11, 1000) for symbol in SYMBOLS]
    rows.append(("AAA", SESSION + " 15:00:00", 10, 12, 9, 11, 1000))
    _create_market(market, rows=rows)
    result = _check(market)
    assert result.state is MarketDataIntegrityState.FAIL_CLOSED
    assert "duplicate" in result.message


def test_integrity_missing_or_stale_required_equity_fails_closed(tmp_path: Path) -> None:
    market = tmp_path / "market.db"
    rows = [
        (symbol, "2026-09-27" if symbol == "BBB" else SESSION, 10, 12, 9, 11, 1000)
        for symbol in SYMBOLS
    ]
    _create_market(market, rows=rows)
    result = _check(market)
    assert result.state is MarketDataIntegrityState.FAIL_CLOSED
    assert "BBB=2026-09-27" in result.message


def test_integrity_valid_prior_session_is_not_applicable(tmp_path: Path) -> None:
    market = tmp_path / "market.db"
    _create_market(market)
    result = _check(market, as_of="2026-09-29")
    assert result.state is MarketDataIntegrityState.NOT_APPLICABLE
    assert result.required_session == SESSION


def test_skip_update_still_validates_before_stateful_stages() -> None:
    calls: list[str] = []
    pipeline = DailyPipeline(
        update_market_data=lambda: calls.append("update") or (101, []),
        validate_market_data=lambda: calls.append("integrity") or _pipeline_result(MarketDataIntegrityState.PASS),
        run_forward_validation=lambda: calls.append("forward"),
        run_lifecycle=lambda: calls.append("paper"),
        run_scanner=lambda: calls.append("scanner"),
    )
    result = pipeline.run(skip_update=True)
    assert result.success
    assert calls == ["integrity", "paper", "scanner"]


def test_fail_closed_blocks_forward_paper_and_scanner() -> None:
    calls: list[str] = []
    pipeline = DailyPipeline(
        update_market_data=lambda: calls.append("update") or (101, []),
        validate_market_data=lambda: calls.append("integrity") or _pipeline_result(MarketDataIntegrityState.FAIL_CLOSED, "unsafe fixture"),
        run_forward_validation=lambda: calls.append("forward"),
        run_lifecycle=lambda: calls.append("paper"),
        run_scanner=lambda: calls.append("scanner"),
    )
    result = pipeline.run()
    assert not result.success
    assert calls == ["update", "integrity"]
    assert result.stages[-1].name == "Market Data Integrity"


def test_pass_preserves_canonical_stage_order() -> None:
    calls: list[str] = []
    pipeline = DailyPipeline(
        update_market_data=lambda: calls.append("update") or (101, []),
        validate_market_data=lambda: calls.append("integrity") or _pipeline_result(MarketDataIntegrityState.PASS),
        run_forward_validation=lambda: calls.append("forward"),
        run_lifecycle=lambda: calls.append("paper"),
        run_scanner=lambda: calls.append("scanner"),
    )
    result = pipeline.run()
    assert result.success
    assert calls == ["update", "integrity", "forward", "paper", "scanner"]


def test_partial_provider_failure_fails_closed_before_integrity_and_consumers() -> None:
    calls: list[str] = []
    pipeline = DailyPipeline(
        update_market_data=lambda: calls.append("update") or (100, ["BBB"]),
        validate_market_data=lambda: calls.append("integrity") or _pipeline_result(MarketDataIntegrityState.PASS),
        run_forward_validation=lambda: calls.append("forward"),
        run_lifecycle=lambda: calls.append("paper"),
        run_scanner=lambda: calls.append("scanner"),
    )
    result = pipeline.run()
    assert not result.success
    assert calls == ["update"]
    assert result.stages[-1].name == "Market Data Integrity"


def test_github_workflows_do_not_schedule_or_run_production_daily_pipeline() -> None:
    workflow_root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    active = "\n".join(path.read_text(encoding="utf-8") for path in workflow_root.glob("*.y*ml"))
    assert "python main.py" not in active
    assert "scripts.run_daily" not in active
    assert "git add -f market.db" not in active
    assert "schedule:" not in active


def test_canonical_market_default_remains_repository_data_path() -> None:
    assert DEFAULT_MARKET_DATABASE_PATH == Path(__file__).resolve().parents[1] / "data" / "market.db"
