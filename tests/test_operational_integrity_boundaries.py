from __future__ import annotations

from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from core.market_data_integrity import (
    MarketDataIntegrityResult,
    MarketDataIntegrityState,
    require_market_data_integrity,
)
from strategy import scanner
from scripts import run_paper_lifecycle


def _result(state: MarketDataIntegrityState) -> MarketDataIntegrityResult:
    return MarketDataIntegrityResult(
        state=state,
        required_session="2026-09-28",
        required_symbols=("AAA", "VNINDEX"),
        reasons=("fixture",),
        database_path=Path("fixture.db"),
    )


def test_integrity_enforcement_raises_for_non_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.market_data_integrity.check_market_data_integrity",
        lambda **_kwargs: _result(MarketDataIntegrityState.FAIL_CLOSED),
    )

    with pytest.raises(RuntimeError, match="failed closed"):
        require_market_data_integrity(required_symbols=("AAA", "VNINDEX"))


def test_direct_integrity_default_uses_local_session_without_vn100_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "market.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE prices(symbol TEXT,time TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL)"
        )
        connection.executemany(
            "INSERT INTO prices VALUES (?,?,?,?,?,?,?)",
            (
                ("VNINDEX", "2026-09-30", 100.0, 101.0, 99.0, 100.0, 1.0),
                ("AAA", "2026-09-30", 10.0, 11.0, 9.0, 10.0, 1.0),
            ),
        )
    monkeypatch.setattr(
        "core.universe.get_all_symbols",
        lambda: (_ for _ in ()).throw(AssertionError("provider lookup is not allowed")),
    )

    result = require_market_data_integrity(
        database_path=database,
        as_of_date="2026-09-30",
    )

    assert result.state is MarketDataIntegrityState.PASS
    assert result.required_symbols == ("AAA", "VNINDEX")


def test_direct_scanner_fails_before_paper_runtime_on_integrity_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scanner, "get_vn100_symbols", lambda: ["AAA"])
    monkeypatch.setattr(
        scanner,
        "require_market_data_integrity",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("failed closed")),
    )
    initialized = []
    monkeypatch.setattr(
        scanner,
        "initialize_scanner_runtime",
        lambda: initialized.append(True),
    )

    with pytest.raises(RuntimeError, match="failed closed"):
        scanner.run_scan(result_processor=lambda results, stats: (results, stats))

    assert initialized == []


def test_direct_scanner_valid_integrity_reaches_read_only_scan_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scanner, "get_vn100_symbols", lambda: ["AAA"])
    calls: list[str] = []
    monkeypatch.setattr(
        scanner,
        "require_market_data_integrity",
        lambda **_kwargs: calls.append("integrity"),
    )
    monkeypatch.setattr(
        scanner,
        "get_market_regime",
        lambda: {
            "regime": "BULL",
            "min_score": 0.0,
            "min_adx": 0.0,
            "min_volume_ratio": 0.0,
            "min_relative_strength": 0.0,
        },
    )
    monkeypatch.setattr(
        scanner,
        "initialize_scanner_runtime",
        lambda: calls.append("runtime") or SimpleNamespace(
            queue_signals=lambda *_args, **_kwargs: SimpleNamespace(
                enabled=False,
                queued_count=0,
                filled_count=0,
                skipped_count=0,
                rejected_count=0,
                cash=0.0,
                equity=0.0,
            )
        ),
    )
    monkeypatch.setattr(
        scanner,
        "scan_all_symbols",
        lambda **_kwargs: ([], {"watchlist": [], "reference_date": "2026-09-28"}),
    )
    monkeypatch.setattr(scanner, "print_scan_results", lambda *args, **kwargs: None)
    monkeypatch.setattr(scanner, "print_end_of_day_dashboard", lambda *args, **kwargs: None)
    monkeypatch.setattr(scanner, "print_scan_diagnostics", lambda *args, **kwargs: None)
    monkeypatch.setattr(scanner, "persist_scan_telemetry", lambda **kwargs: None)
    monkeypatch.setattr(scanner, "save_signal", lambda _signal: False)
    monkeypatch.setattr(scanner, "build_scan_message", lambda *args, **kwargs: "")

    class _Telegram:
        def send_message(self, _message):
            return SimpleNamespace(success=True, chunks_sent=0, error="")

    monkeypatch.setattr(scanner, "telegram_client", _Telegram())

    scanner.run_scan(result_processor=lambda results, stats: (results, stats))

    assert calls[:2] == ["integrity", "runtime"]


def test_paper_lifecycle_fails_before_pending_or_paper_state_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_paper_lifecycle, "load_dotenv", lambda: None)
    monkeypatch.setattr(
        run_paper_lifecycle,
        "require_market_data_integrity",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("failed closed")),
    )
    monkeypatch.setattr(
        run_paper_lifecycle.PaperSignalExecutor,
        "from_env",
        lambda: (_ for _ in ()).throw(AssertionError("executor must not initialize")),
    )
    monkeypatch.setattr(
        run_paper_lifecycle,
        "PaperBroker",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("broker must not initialize")),
    )

    with pytest.raises(RuntimeError, match="failed closed"):
        run_paper_lifecycle.main()
