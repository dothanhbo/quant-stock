from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from core.paths import (
    DEFAULT_MARKET_DATABASE_PATH,
    PROJECT_ROOT,
    resolve_market_database_path,
)
from scripts import (
    run_paper_lifecycle,
    run_paper_v2_lifecycle,
    run_paper_v3_lifecycle,
)


def test_default_market_path_is_project_anchored_outside_project_cwd(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MARKET_DATABASE_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    assert resolve_market_database_path() == DEFAULT_MARKET_DATABASE_PATH.resolve()


def test_absolute_market_path_environment_is_honored(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configured = (tmp_path / "custom-market.db").resolve()
    monkeypatch.setenv("MARKET_DATABASE_PATH", str(configured))

    assert resolve_market_database_path() == configured


def test_relative_market_path_environment_is_project_anchored(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MARKET_DATABASE_PATH", "state/market.db")

    assert resolve_market_database_path() == (
        PROJECT_ROOT / "state" / "market.db"
    ).resolve()


def test_explicit_market_path_takes_precedence_over_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MARKET_DATABASE_PATH", "configured/market.db")

    assert resolve_market_database_path("explicit/market.db") == (
        PROJECT_ROOT / "explicit" / "market.db"
    ).resolve()


def test_market_path_resolver_does_not_create_files_or_directories(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configured = tmp_path / "missing" / "market.db"
    monkeypatch.setenv("MARKET_DATABASE_PATH", str(configured))

    assert resolve_market_database_path() == configured.resolve()
    assert not configured.exists()
    assert not configured.parent.exists()


def test_lifecycle_passes_canonical_market_path_to_pending_execution(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configured = tmp_path / "market.db"
    captured: dict[str, object] = {}

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args, **_kwargs):
            return self

        def fetchone(self):
            return ("2026-09-21",)

    class FakeExecutor:
        @classmethod
        def from_env(cls):
            return cls()

        def execute_pending_signals(self, **kwargs):
            captured["pending_market_path"] = kwargs["market_database_path"]
            return SimpleNamespace(executions=[])

    class FakeLifecycleManager:
        def __init__(self, **kwargs):
            captured["manager_market_path"] = kwargs["market_database_path"]

        def run(self):
            return SimpleNamespace(
                valuation_date="2026-09-21",
                held=[],
                exited=[],
                missing_states=[],
                missing_prices=[],
                rejected_exits=[],
                cash=0.0,
                equity=0.0,
                realized_pnl=0.0,
                unrealized_pnl=0.0,
                open_positions=0,
            )

    monkeypatch.setenv("MARKET_DATABASE_PATH", str(configured))
    monkeypatch.setattr(run_paper_lifecycle, "load_dotenv", lambda: None)
    monkeypatch.setattr(
        run_paper_lifecycle.sqlite3,
        "connect",
        lambda *_args: FakeConnection(),
    )
    monkeypatch.setattr(run_paper_lifecycle, "PaperSignalExecutor", FakeExecutor)
    monkeypatch.setattr(
        run_paper_lifecycle,
        "TradingPolicy",
        SimpleNamespace(
            from_env=lambda: SimpleNamespace(
                sell_tax_rate=0.001,
                trailing_atr_multiplier=2.0,
            )
        ),
    )
    monkeypatch.setattr(run_paper_lifecycle, "PaperBroker", lambda **_kwargs: object())
    monkeypatch.setattr(run_paper_lifecycle, "OrderManager", lambda **_kwargs: object())
    monkeypatch.setattr(run_paper_lifecycle, "RiskGuard", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(run_paper_lifecycle, "RiskLimits", lambda **_kwargs: object())
    monkeypatch.setattr(
        run_paper_lifecycle,
        "PaperLifecycleManager",
        FakeLifecycleManager,
    )

    run_paper_lifecycle.main()

    expected = configured.resolve()
    assert captured["pending_market_path"] == expected
    assert captured["manager_market_path"] == expected


def test_v2_and_v3_paper_paths_remain_isolated_with_shared_market_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    market_path = tmp_path / "market.db"
    v2_paper_path = tmp_path / "paper-v2.db"
    v3_paper_path = tmp_path / "paper-v3.db"
    monkeypatch.setenv("MARKET_DATABASE_PATH", str(market_path))
    monkeypatch.setenv("PAPER_V2_DATABASE_PATH", str(v2_paper_path))
    monkeypatch.setenv("PAPER_V3_DATABASE_PATH", str(v3_paper_path))
    for name in (
        "PAPER_TRADING_ENABLED",
        "PAPER_DATABASE_PATH",
        "PAPER_ATR_STOP_MULTIPLIER",
        "TRADING_ENTRY_MODEL",
        "TRADING_EXIT_MODEL",
        "TRADING_STOP_ATR_MULTIPLIER",
        "TRADING_TARGET_ATR_MULTIPLIER",
        "PAPER_ATR_TARGET_MULTIPLIER",
        "PAPER_DISABLE_TRAILING",
        "PAPER_V2_DISABLE_TRAILING",
        "PAPER_STRATEGY_VERSION",
    ):
        monkeypatch.setenv(name, os.environ.get(name, ""))
    monkeypatch.setattr(run_paper_lifecycle, "main", lambda: None)

    run_paper_v2_lifecycle.main()
    selected_v2_path = Path(os.environ["PAPER_DATABASE_PATH"])
    run_paper_v3_lifecycle.main()
    selected_v3_path = Path(os.environ["PAPER_DATABASE_PATH"])

    assert selected_v2_path == v2_paper_path
    assert selected_v3_path == v3_paper_path
    assert selected_v2_path != selected_v3_path
    assert resolve_market_database_path() == market_path.resolve()
