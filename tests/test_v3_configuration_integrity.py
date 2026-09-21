"""Controlled V3 configuration-integrity specifications.

These tests deliberately exercise configuration boundaries only.  They do not
submit orders, alter strategy logic, or run against the repository paper DBs.
Known integrity gaps are strict xfails so the suite documents the required
contract without masking the current production behavior.
"""

from __future__ import annotations

import importlib
import os
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from config.strategy_config import V3_BREADTH_PAPER
from scripts import run_daily, run_paper_lifecycle, run_paper_v3_lifecycle


def _run_v3_lifecycle_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apply the V3 lifecycle wrapper without executing lifecycle I/O."""
    with monkeypatch.context() as lifecycle_patch:
        lifecycle_patch.setattr(
            run_paper_lifecycle,
            "main",
            lambda: None,
        )
        run_paper_v3_lifecycle.main()


def _import_scanner_with_safe_telegram(monkeypatch: pytest.MonkeyPatch):
    """Import scanner afresh so its import-time policy/executor are observable."""
    telegram_module = importlib.import_module("services.telegram_client")
    monkeypatch.setattr(
        telegram_module.TelegramClient,
        "from_env",
        classmethod(lambda _cls, **_kwargs: SimpleNamespace()),
    )

    module_name = "strategy.scanner"
    previous = sys.modules.pop(module_name, None)
    try:
        return importlib.import_module(module_name)
    finally:
        # Do not leave a test-configured scanner singleton installed for other
        # tests in this process.
        sys.modules.pop(module_name, None)
        if previous is not None:
            sys.modules[module_name] = previous


def _configure_isolated_v3_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    v3_database = tmp_path / "paper-v3.db"
    monkeypatch.setenv("PAPER_V3_DATABASE_PATH", str(v3_database))
    monkeypatch.setenv("PAPER_ATR_TARGET_MULTIPLIER", "5.0")
    monkeypatch.setenv("TRADING_ENTRY_MODEL", "hybrid")
    return v3_database


def test_full_v3_daily_order_instantiates_intended_scanner_policy_and_executor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Lifecycle configuration must exist before the scanner singleton imports."""
    v3_database = _configure_isolated_v3_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("PAPER_STRATEGY_VERSION", "V3_BREADTH_40_60")
    monkeypatch.setattr(run_daily, "update_market_data", lambda: (101, []))
    monkeypatch.setattr(
        run_daily,
        "get_market_date",
        lambda: date.today().isoformat(),
    )
    monkeypatch.setattr(
        run_paper_lifecycle,
        "main",
        lambda: None,
    )

    captured: dict[str, object] = {}

    def scanner_stage(*, pending_execution_result=None):
        scanner = _import_scanner_with_safe_telegram(monkeypatch)
        captured["pending"] = pending_execution_result
        captured["policy"] = scanner.TRADING_POLICY
        captured["strategy"] = scanner.strategy
        captured["executor"] = scanner.paper_signal_executor
        return [], {}

    monkeypatch.setattr(run_daily, "run_strategy_scanner", scanner_stage)
    monkeypatch.setattr("sys.argv", ["run_daily"])

    assert run_daily.main() == 0

    policy = captured["policy"]
    executor = captured["executor"]
    strategy = captured["strategy"]

    assert V3_BREADTH_PAPER.entry_model == "hybrid_trend_donchian"
    assert V3_BREADTH_PAPER.quality_enabled is True
    assert V3_BREADTH_PAPER.quality_threshold == 0.70
    assert policy.entry_model == "hybrid"
    assert strategy.__class__.__name__ == "HybridTrendDonchianEntryModel"
    assert policy.stop_atr_multiplier == 2.0
    assert policy.target_atr_multiplier == 5.0
    assert executor.config.enabled is True
    assert executor.config.database_path == v3_database
    assert executor.config.atr_stop_multiplier == 2.0
    assert executor.config.target_atr_multiplier == 5.0
    assert executor.policy.stop_atr_multiplier == 2.0
    assert executor.policy.target_atr_multiplier == 5.0
    assert os.environ["PAPER_V2_DISABLE_TRAILING"] == "true"
    assert os.environ["PAPER_DISABLE_TRAILING"] == "true"
    assert captured["pending"] is None


def test_v3_daily_selects_the_frozen_q70_processor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The selected V3 processor, not merely environment text, owns Q70."""
    _configure_isolated_v3_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("PAPER_STRATEGY_VERSION", "V3_BREADTH_40_60")
    _run_v3_lifecycle_configuration(monkeypatch)
    scanner = _import_scanner_with_safe_telegram(monkeypatch)
    # ``run_strategy_scanner`` imports this module by name.  Keep this exact
    # instantiated singleton visible for that call rather than importing a
    # second scanner with potentially different globals.
    monkeypatch.setitem(sys.modules, "strategy.scanner", scanner)
    captured: dict[str, object] = {}

    def fake_run_scan(*, pending_execution_result=None, result_processor=None):
        captured["pending"] = pending_execution_result
        captured["processor"] = result_processor
        return [], {}

    monkeypatch.setattr(scanner, "run_scan", fake_run_scan)
    run_daily.run_strategy_scanner(pending_execution_result="pending")

    processor = captured["processor"]
    assert processor.__self__.__class__.__name__ == "PaperV3Scanner"
    assert processor.__self__.gate.threshold == 0.70
    assert captured["pending"] == "pending"


def test_v3_conflicting_entry_model_cannot_change_effective_scanner_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_isolated_v3_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("TRADING_ENTRY_MODEL", "trend")
    _run_v3_lifecycle_configuration(monkeypatch)

    scanner = _import_scanner_with_safe_telegram(monkeypatch)

    assert scanner.TRADING_POLICY.entry_model == "hybrid"
    assert scanner.strategy.__class__.__name__ == "HybridTrendDonchianEntryModel"


def test_v3_detects_and_prevents_paper_target_multiplier_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_isolated_v3_environment(monkeypatch, tmp_path)
    monkeypatch.setenv("PAPER_ATR_TARGET_MULTIPLIER", "9.0")
    _run_v3_lifecycle_configuration(monkeypatch)

    scanner = _import_scanner_with_safe_telegram(monkeypatch)

    assert scanner.TRADING_POLICY.target_atr_multiplier == 5.0
    assert scanner.paper_signal_executor.config.target_atr_multiplier == 5.0
    assert (
        scanner.paper_signal_executor.policy.target_atr_multiplier
        == scanner.TRADING_POLICY.target_atr_multiplier
    )


def test_v3_skip_lifecycle_still_resolves_frozen_scanner_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    v3_database = tmp_path / "paper-v3.db"
    monkeypatch.setenv("PAPER_STRATEGY_VERSION", "V3_BREADTH_40_60")
    monkeypatch.setenv("PAPER_V3_DATABASE_PATH", str(v3_database))
    monkeypatch.setenv("PAPER_TRADING_ENABLED", "false")
    monkeypatch.setenv("PAPER_DATABASE_PATH", str(tmp_path / "generic-paper.db"))
    monkeypatch.setenv("TRADING_ENTRY_MODEL", "hybrid")
    monkeypatch.setattr(run_daily, "update_market_data", lambda: (101, []))
    monkeypatch.setattr(run_daily, "get_market_date", lambda: date.today().isoformat())

    captured: dict[str, object] = {}

    def scanner_stage(*, pending_execution_result=None):
        scanner = _import_scanner_with_safe_telegram(monkeypatch)
        captured["executor"] = scanner.paper_signal_executor
        return [], {}

    monkeypatch.setattr(run_daily, "run_strategy_scanner", scanner_stage)
    monkeypatch.setattr("sys.argv", ["run_daily", "--skip-update", "--skip-lifecycle"])

    assert run_daily.main() == 0
    executor = captured["executor"]
    assert executor.config.enabled is True
    assert executor.config.database_path == v3_database
    assert executor.config.atr_stop_multiplier == 2.0
    assert executor.config.target_atr_multiplier == 5.0


def test_paper_disable_trailing_alone_is_not_the_operational_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Document the actual contract consumed by run_paper_lifecycle today."""
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

        def execute_pending_signals(self, **_kwargs):
            return SimpleNamespace(executions=[])

    class FakeLifecycleManager:
        def __init__(self, **kwargs):
            captured["manager_kwargs"] = kwargs

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

    monkeypatch.setenv("PAPER_DISABLE_TRAILING", "true")
    monkeypatch.delenv("PAPER_V2_DISABLE_TRAILING", raising=False)
    monkeypatch.setattr(run_paper_lifecycle, "load_dotenv", lambda: None)
    monkeypatch.setattr(run_paper_lifecycle.sqlite3, "connect", lambda *_args: FakeConnection())
    monkeypatch.setattr(run_paper_lifecycle, "PaperSignalExecutor", FakeExecutor)
    monkeypatch.setattr(
        run_paper_lifecycle,
        "TradingPolicy",
        SimpleNamespace(from_env=lambda: SimpleNamespace(sell_tax_rate=0.001, trailing_atr_multiplier=2.0)),
    )
    monkeypatch.setattr(run_paper_lifecycle, "PaperBroker", lambda **_kwargs: object())
    monkeypatch.setattr(run_paper_lifecycle, "OrderManager", lambda **_kwargs: object())
    monkeypatch.setattr(run_paper_lifecycle, "RiskGuard", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(run_paper_lifecycle, "RiskLimits", lambda **_kwargs: object())
    monkeypatch.setattr(run_paper_lifecycle, "PaperLifecycleManager", FakeLifecycleManager)

    run_paper_lifecycle.main()

    exit_engine = captured["manager_kwargs"]["exit_engine"]
    assert exit_engine.config.enable_trailing_stop is True
    assert captured["manager_kwargs"]["default_trailing_atr_multiplier"] == 2.0

    # The V3 wrapper currently works because it also sets the legacy-named
    # variable that run_paper_lifecycle actually consumes.
    _run_v3_lifecycle_configuration(monkeypatch)
    run_paper_lifecycle.main()

    exit_engine = captured["manager_kwargs"]["exit_engine"]
    assert exit_engine.config.enable_trailing_stop is False
    assert captured["manager_kwargs"]["default_trailing_atr_multiplier"] is None
