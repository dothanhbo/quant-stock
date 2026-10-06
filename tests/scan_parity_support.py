"""Deterministic harness for the canonical strategy-aware scan path.

Everything that touches the outside world is replaced: universe resolution,
the integrity gate, market regime, the raw scan (``scan_all_symbols``),
scan telemetry / signal rows, the paper executor (unless a real executor is
requested against temporary stores) and Telegram. The strategy decision
itself — the Q70 gate (``PaperV2Scanner``) or V3 breadth processor
(``PaperV3Scanner``) selected by each entrypoint, and the frozen policy the
scanner module resolves at import — runs for real.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

REFERENCE_DATE = "2026-09-28"

# (symbol, base-entry status, score, RS20, ADX, close, ATR14)
_UNIVERSE_ROWS = (
    ("AAA", "PASSED", 92.0, 14.0, 41.0, 25.0, 0.80),
    ("BBB", "PASSED", 88.0, 9.0, 36.0, 40.0, 1.10),
    ("CCC", "PASSED", 71.0, 2.0, 24.0, 18.5, 0.55),
    ("DDD", "PASSED", 64.0, -1.0, 19.0, 60.0, 1.90),
    ("EEE", "REJECTED", 55.0, 4.0, 30.0, 33.0, 0.95),
    ("FFF", "REJECTED", 40.0, -6.0, 15.0, 12.0, 0.40),
    ("GGG", "WATCHLIST", 66.0, 1.0, 22.0, 21.0, 0.60),
)

BULL_MARKET_CONFIG = {
    "regime": "BULL",
    "min_score": 60,
    "min_adx": 18,
    "min_volume_ratio": 1.0,
    "min_relative_strength": 0.0,
}

# Environment a careless operator could leave in `.env`. The daily path
# pins these before the scanner resolves its policy; the old standalone
# path silently used them.
HOSTILE_ENVIRONMENT = {
    "TRADING_STOP_ATR_MULTIPLIER": "3.0",
    "TRADING_TARGET_ATR_MULTIPLIER": "9.0",
    "PAPER_TRADING_ENABLED": "false",
    "PAPER_ATR_STOP_MULTIPLIER": "3.0",
}


def _evaluation(row, *, breadth: float, policy) -> dict[str, Any]:
    symbol, status, score, rs, adx, close, atr = row
    stop, target = policy.calculate_levels(entry_price=close, atr=atr)
    return {
        "symbol": symbol,
        "status": status,
        "reason": "passed" if status == "PASSED" else "score",
        "regime": "BULL",
        "market_state": "TEST",
        "breadth_ema50_pct": breadth,
        "breadth_ema50_change_10d": 1.5,
        "score": score,
        "relative_strength_20d": rs,
        "adx": adx,
        "volume_ratio": 1.4,
        "rsi": 61.0,
        "atr": atr,
        "entry": round(close, 2),
        "stop_loss": round(stop, 2),
        "take_profit": round(target, 2),
        "stop_atr_multiplier": policy.stop_atr_multiplier,
        "target_atr_multiplier": policy.target_atr_multiplier,
    }


class Recorder:
    """Ordered log of every boundary crossing during one scan."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.queued: list[dict[str, Any]] = []
        self.messages: list[str] = []
        self.saved_signals: list[str] = []
        self.policy: dict[str, Any] = {}
        self.executor_enabled: bool | None = None
        self.executor_database: str | None = None
        self.result: tuple[list[dict], dict] | None = None


class _FakeTelegram:
    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder

    def send_message(self, message: str):
        self.recorder.events.append("telegram_send")
        self.recorder.messages.append(message)
        return SimpleNamespace(success=True, chunks_sent=1, error="")


def isolate_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    strategy_identity: str,
    *,
    hostile: bool = True,
) -> dict[str, Path]:
    stores = {
        "generic": tmp_path / "generic.db",
        "q70": tmp_path / "q70.db",
        "v3": tmp_path / "v3.db",
    }
    for name in tuple(
        key for key in list(__import__("os").environ)
        if key.startswith(("PAPER_", "TRADING_"))
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PAPER_STRATEGY_VERSION", strategy_identity)
    monkeypatch.setenv("PAPER_DATABASE_PATH", str(stores["generic"]))
    monkeypatch.setenv("PAPER_V2_DATABASE_PATH", str(stores["q70"]))
    monkeypatch.setenv("PAPER_V3_DATABASE_PATH", str(stores["v3"]))
    monkeypatch.setenv("QUANT_OPERATION_RUN_ID", "test-run-no-ledger")
    if hostile:
        for key, value in HOSTILE_ENVIRONMENT.items():
            monkeypatch.setenv(key, value)
    # Never read a developer `.env` (it may hold real tokens/paths).
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    # Lifecycle wrappers only configure the environment in this harness.
    monkeypatch.setattr("scripts.run_paper_lifecycle.main", lambda: None)
    return stores


def fresh_scanner(
    monkeypatch: pytest.MonkeyPatch,
    recorder: Recorder,
    *,
    breadth: float,
    real_executor: bool = False,
    signal_date: str | None = None,
):
    """Import ``strategy.scanner`` afresh (policy resolved *now*) and stub I/O."""
    telegram_module = importlib.import_module("services.telegram_client")
    monkeypatch.setattr(
        telegram_module.TelegramClient,
        "from_env",
        classmethod(lambda _cls, **_k: _FakeTelegram(recorder)),
    )
    # Callers must restore sys.modules["strategy.scanner"] afterwards (see
    # the ``restore_scanner_module`` fixture) so this policy-specific module
    # never leaks into later tests.
    sys.modules.pop("strategy.scanner", None)
    scanner = importlib.import_module("strategy.scanner")

    policy = scanner.TRADING_POLICY
    recorder.policy = {
        "entry_model": policy.entry_model,
        "stop_atr_multiplier": policy.stop_atr_multiplier,
        "target_atr_multiplier": policy.target_atr_multiplier,
        "trailing_atr_multiplier": policy.trailing_atr_multiplier,
        "maximum_holding_days": policy.maximum_holding_days,
    }
    evaluations = [_evaluation(row, breadth=breadth, policy=policy) for row in _UNIVERSE_ROWS]
    if signal_date is not None:
        # The real paper queue requires the signal date (real scans set it).
        for item in evaluations:
            item["date"] = signal_date
    signals = [item for item in evaluations if item["status"] == "PASSED"]
    watchlist = [item for item in evaluations if item["status"] == "WATCHLIST"]

    def fake_scan_all_symbols(**_kwargs):
        recorder.events.append("raw_scan")
        stats = {
            "reference_date": REFERENCE_DATE,
            "total_symbols": len(evaluations),
            "fresh_count": len(evaluations),
            "stale_count": 0,
            "stale_symbols": [],
            "error_count": 0,
            "scan_errors": [],
            "reject_stats": {},
            "condition_fail_stats": {},
            "watchlist": [dict(item) for item in watchlist],
            "evaluations": [dict(item) for item in evaluations],
            "market_config": dict(BULL_MARKET_CONFIG),
            "market_state": None,
        }
        return [dict(item) for item in signals], stats

    monkeypatch.setattr(scanner, "_resolve_scanner_symbols", lambda: tuple(r[0] for r in _UNIVERSE_ROWS))
    monkeypatch.setattr(
        scanner,
        "_require_scanner_integrity",
        lambda _symbols: recorder.events.append("integrity"),
    )
    monkeypatch.setattr(scanner, "get_market_regime", lambda **_k: dict(BULL_MARKET_CONFIG))
    monkeypatch.setattr(scanner, "scan_all_symbols", fake_scan_all_symbols)
    for name in ("print_scan_results", "print_end_of_day_dashboard", "print_scan_diagnostics"):
        monkeypatch.setattr(scanner, name, lambda *a, **k: None)
    monkeypatch.setattr(
        scanner,
        "persist_scan_telemetry",
        lambda **_k: recorder.events.append("telemetry_write"),
    )

    def fake_save_signal(signal):
        recorder.events.append("signal_row_write")
        recorder.saved_signals.append(signal["symbol"])
        return True

    monkeypatch.setattr(scanner, "save_signal", fake_save_signal)

    if not real_executor:
        from execution.signal_executor import PaperExecutionConfig

        def fake_runtime():
            config = PaperExecutionConfig.from_env()
            recorder.events.append("paper_runtime_init")
            recorder.executor_enabled = config.enabled
            recorder.executor_database = str(config.database_path)

            def queue_signals(results, *, report_date=None):
                recorder.events.append("paper_queue")
                recorder.queued = [
                    {
                        key: item.get(key)
                        for key in (
                            "symbol",
                            "entry",
                            "stop_loss",
                            "take_profit",
                            "paper_v2_quality",
                            "paper_v2_gate",
                            "breadth_exposure_multiplier",
                            "paper_v3_breadth_gate",
                        )
                    }
                    for item in results
                ]
                return SimpleNamespace(
                    enabled=config.enabled,
                    queued_count=len(results) if config.enabled else 0,
                    filled_count=0,
                    skipped_count=0,
                    rejected_count=0,
                    cash=0.0,
                    equity=0.0,
                    executions=[],
                    positions=[],
                    closed_today=[],
                )

            return SimpleNamespace(config=config, queue_signals=queue_signals)

        monkeypatch.setattr(scanner, "initialize_scanner_runtime", fake_runtime)
        monkeypatch.setattr(scanner, "build_paper_execution_message", lambda *a, **k: "")

    return scanner


def wrap_processors(monkeypatch: pytest.MonkeyPatch, recorder: Recorder) -> None:
    """Log when the strategy decision (Q70 / V3 processor) starts and ends."""
    from strategy.paper_v2_scanner import PaperV2Scanner
    from strategy.paper_v3_scanner import PaperV3Scanner

    for cls in (PaperV2Scanner, PaperV3Scanner):
        original = cls.process

        def process(self, signals, stats, _original=original, _name=cls.__name__):
            recorder.events.append(f"decision_start:{_name}")
            result = _original(self, signals, stats)
            recorder.events.append(f"decision_end:{_name}")
            return result

        monkeypatch.setattr(cls, "process", process)


def decision_summary(recorder: Recorder) -> dict[str, Any]:
    """The pre-side-effect decision plus what reached each side effect."""
    results, stats = recorder.result
    rejected_key = next(
        (key for key in ("paper_v3_rejected", "paper_v2_rejected") if key in stats),
        None,
    )
    rejected = stats.get(rejected_key, []) if rejected_key else []
    blocked = stats.get("paper_v3_breadth_blocked", [])
    return {
        "policy": recorder.policy,
        "accepted": [
            {
                "symbol": item["symbol"],
                "stop_loss": item.get("stop_loss"),
                "take_profit": item.get("take_profit"),
                "quality": item.get("paper_v2_quality"),
                "gate": item.get("paper_v2_gate"),
                "exposure": item.get("breadth_exposure_multiplier"),
                "v3_gate": item.get("paper_v3_breadth_gate"),
            }
            for item in results
        ],
        "rejected": [
            {
                "symbol": item["symbol"],
                "reason": item.get("paper_v2_gate"),
                "quality": item.get("paper_v2_quality"),
            }
            for item in rejected
        ],
        "breadth_blocked": [item["symbol"] for item in blocked],
        "strategy_version": stats.get("paper_v3_version") or stats.get("paper_v2_version"),
        "executor_enabled": recorder.executor_enabled,
        "executor_database": recorder.executor_database,
        "queued": recorder.queued,
        "saved_signals": recorder.saved_signals,
        "telegram_messages": recorder.messages,
    }


@pytest.fixture
def restore_scanner_module():
    """Put back whatever ``strategy.scanner`` module existed before the test."""
    import strategy

    name = "strategy.scanner"
    previous = sys.modules.get(name)
    previous_attribute = getattr(strategy, "scanner", None)
    yield
    if previous is None:
        sys.modules.pop(name, None)
    else:
        sys.modules[name] = previous
    # `import strategy.scanner as x` resolves through the package attribute,
    # so restore it too, or later tests would patch a stale module object.
    if previous_attribute is None:
        if hasattr(strategy, "scanner"):
            delattr(strategy, "scanner")
    else:
        strategy.scanner = previous_attribute


EntryPoint = Callable[[pytest.MonkeyPatch, Recorder, float], None]
