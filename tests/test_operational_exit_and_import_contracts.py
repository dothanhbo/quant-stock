"""Contracts added by the 2026-10-06 audit batch 1.

* Importing ``strategy.scanner`` (done by research/backtest code) must not
  require Telegram configuration; a production scan still fails before any
  provider read, integrity check or state write when it is missing.
* The standalone market-data updater must not report success when symbols
  remain failed or need backfill.
"""

from __future__ import annotations

import importlib
import sys

import pytest


def _fresh_scanner_import(monkeypatch: pytest.MonkeyPatch):
    module_name = "strategy.scanner"
    previous = sys.modules.pop(module_name, None)

    def restore() -> None:
        sys.modules.pop(module_name, None)
        if previous is not None:
            sys.modules[module_name] = previous

    try:
        return importlib.import_module(module_name), restore
    except BaseException:
        restore()
        raise


def test_scanner_import_does_not_construct_telegram_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram_module = importlib.import_module("services.telegram_client")

    def refuse(*_args, **_kwargs):
        raise AssertionError("TelegramClient must not be built at import time")

    monkeypatch.setattr(telegram_module.TelegramClient, "from_env", classmethod(refuse))
    scanner, restore = _fresh_scanner_import(monkeypatch)
    try:
        assert scanner.telegram_client is None
    finally:
        restore()


def test_run_scan_fails_on_missing_telegram_config_before_any_other_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telegram_module = importlib.import_module("services.telegram_client")
    scanner, restore = _fresh_scanner_import(monkeypatch)
    try:
        def missing(*_args, **_kwargs):
            raise telegram_module.TelegramConfigurationError("missing token")

        monkeypatch.setattr(scanner, "telegram_client", None)
        monkeypatch.setattr(scanner.TelegramClient, "from_env", classmethod(missing))
        for name in (
            "_resolve_scanner_symbols",
            "_require_scanner_integrity",
            "initialize_scanner_runtime",
        ):
            monkeypatch.setattr(
                scanner,
                name,
                lambda *_a, _name=name, **_k: (_ for _ in ()).throw(
                    AssertionError(f"{_name} ran before Telegram configuration")
                ),
            )

        with pytest.raises(telegram_module.TelegramConfigurationError):
            scanner.run_scan(result_processor=lambda results, stats: (results, stats))
    finally:
        restore()


@pytest.fixture
def _no_lingering_provider_imports():
    """scripts.update_data imports vnstock; other tests assert it is absent."""
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        if name == "vnstock" or name.startswith(("vnstock.", "vnai")) or name == "scripts.update_data":
            sys.modules.pop(name, None)


@pytest.mark.parametrize(("issues", "expected"), (([], 0), (["AAA"], 1)))
def test_standalone_update_exit_code_reflects_incomplete_symbols(
    monkeypatch: pytest.MonkeyPatch,
    _no_lingering_provider_imports,
    issues: list[str],
    expected: int,
) -> None:
    update_data = importlib.import_module("scripts.update_data")
    monkeypatch.setattr(
        update_data,
        "update_all_symbols",
        lambda symbols: (len(symbols) - len(issues), list(issues)),
    )
    monkeypatch.setattr(sys, "argv", ["update_data", "--symbols", "AAA", "BBB"])

    assert update_data.main() == expected


def _lifecycle_db(path) -> None:
    import sqlite3

    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE paper_position_lifecycle "
            "(symbol TEXT PRIMARY KEY, entry_date TEXT, maximum_holding_days INTEGER)"
        )
        connection.execute(
            "INSERT INTO paper_position_lifecycle VALUES ('AAA', '2026-09-01', NULL)"
        )


def _max_hold(path):
    import sqlite3

    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT maximum_holding_days FROM paper_position_lifecycle"
        ).fetchone()[0]


def test_policy_migration_refuses_implicit_apply_to_non_active_store(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    migrate = importlib.import_module("scripts.migrate_open_positions_policy")
    generic = tmp_path / "generic.db"
    active = tmp_path / "active-v2.db"
    _lifecycle_db(generic)
    monkeypatch.setattr(migrate, "load_dotenv", lambda: False)
    monkeypatch.setenv("PAPER_DATABASE_PATH", str(generic))
    monkeypatch.setenv("PAPER_V2_DATABASE_PATH", str(active))
    monkeypatch.delenv("PAPER_STRATEGY_VERSION", raising=False)

    assert migrate.main(["--apply"]) == 2
    assert _max_hold(generic) is None
    assert not active.exists()

    # Dry-run on the implicit target still works and writes nothing.
    assert migrate.main([]) == 0
    assert _max_hold(generic) is None

    # Naming the database explicitly is an informed choice and is honoured,
    # but a position without persisted holding evidence stays NULL (P1-3):
    # today's default is never written as if it were historical.
    assert migrate.main(["--apply", "--database", str(generic)]) == 0
    assert _max_hold(generic) is None


def test_policy_migration_applies_to_active_store_without_extra_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    migrate = importlib.import_module("scripts.migrate_open_positions_policy")
    active = tmp_path / "active-v2.db"
    _lifecycle_db(active)
    monkeypatch.setattr(migrate, "load_dotenv", lambda: False)
    monkeypatch.setenv("PAPER_DATABASE_PATH", str(active))
    monkeypatch.setenv("PAPER_V2_DATABASE_PATH", str(active))
    monkeypatch.delenv("PAPER_STRATEGY_VERSION", raising=False)

    assert migrate.main(["--apply"]) == 0
    # No per-record evidence in this legacy row: left NULL, not defaulted.
    assert _max_hold(active) is None
