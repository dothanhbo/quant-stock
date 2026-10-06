"""Phase 2 (2026-10-06): one canonical strategy-aware scan path.

Golden references in ``tests/fixtures/scan_parity/daily_*.json`` were
captured from the daily scanner stage *before* this change (lifecycle
wrapper configures the environment, then ``run_daily.run_strategy_scanner``)
using the deterministic harness in ``tests/scan_parity_support.py``. Every
operational entrypoint must now reproduce them exactly — same policy, same
accepted/rejected signals and reasons, same queue/Telegram payloads and the
same order of side effects — under a deliberately hostile environment
(stop 3×/target 9× ATR, paper trading disabled) that the old standalone
path used verbatim.

No real database, provider, Telegram or lifecycle is touched.
"""

from __future__ import annotations

import json
import runpy
import sqlite3
import sys
from pathlib import Path

import pytest

from tests.scan_parity_support import (
    Recorder,
    decision_summary,
    fresh_scanner,
    isolate_environment,
    restore_scanner_module,
    wrap_processors,
)

pytestmark = pytest.mark.usefixtures("restore_scanner_module")

FIXTURES = Path(__file__).parent / "fixtures" / "scan_parity"
CASES = (
    ("Q70_FROZEN", 80.0),
    ("V3_BREADTH_40_60", 55.0),  # V3 half exposure
    ("V3_BREADTH_40_60", 30.0),  # V3 breadth block
)
SIDE_EFFECTS = ("telemetry_write", "signal_row_write", "paper_queue", "telegram_send")


# Approved contract transitions applied ON TOP of the byte-identical
# pre-Phase-2 golden files (the files themselves stay historical evidence).
# 2026-10-06 owner decision (docs/audit/2026-10-06-owner-contract-resolution.md):
# canonical maximum holding is 20 market sessions (was the 30 default when the
# goldens were captured). This is the ONLY approved difference: every decision,
# level, routing and side-effect ordering must still match exactly.
APPROVED_CONTRACT_TRANSITIONS = {("policy", "maximum_holding_days"): (30, 20)}


def _golden(strategy: str, breadth: float) -> dict:
    golden = json.loads(
        (FIXTURES / f"daily_{strategy}_{int(breadth)}.json").read_text(encoding="utf-8")
    )
    for (block, field), (before, after) in APPROVED_CONTRACT_TRANSITIONS.items():
        assert golden[block][field] == before, "golden fixture changed unexpectedly"
        golden[block][field] = after
    return golden


def _summary(recorder: Recorder) -> dict:
    summary = decision_summary(recorder)
    summary["executor_database"] = Path(summary["executor_database"]).name
    summary["events"] = list(recorder.events)
    return summary


def _harness_loader(monkeypatch, recorder, breadth):
    return lambda: fresh_scanner(monkeypatch, recorder, breadth=breadth)


# --- entrypoints ------------------------------------------------------------

def _daily(monkeypatch, tmp_path, strategy, breadth) -> Recorder:
    from scripts import run_daily, run_paper_v2_lifecycle, run_paper_v3_lifecycle

    isolate_environment(monkeypatch, tmp_path, strategy)
    monkeypatch.setattr(run_daily, "load_dotenv", lambda *a, **k: False)
    recorder = Recorder()
    wrap_processors(monkeypatch, recorder)
    monkeypatch.setattr(
        "app.strategy_scan._default_scanner_loader",
        _harness_loader(monkeypatch, recorder, breadth),
    )
    # Daily order: lifecycle wrapper first, then the scanner stage.
    (
        run_paper_v3_lifecycle if strategy.startswith("V3") else run_paper_v2_lifecycle
    ).main()
    recorder.result = run_daily.run_strategy_scanner(pending_execution_result=None)
    return recorder


def _scanner_module_main(monkeypatch, tmp_path, strategy, breadth) -> Recorder:
    """`python -m strategy.scanner` — what QuantCtl and Manager launch."""
    isolate_environment(monkeypatch, tmp_path, strategy)
    recorder = Recorder()
    wrap_processors(monkeypatch, recorder)
    monkeypatch.setattr(
        "app.strategy_scan._default_scanner_loader",
        _harness_loader(monkeypatch, recorder, breadth),
    )
    import app.strategy_scan as service

    original_run = service.run_strategy_scan

    def capture(**kwargs):
        recorder.result = original_run(**kwargs)
        return recorder.result

    monkeypatch.setattr(service, "run_strategy_scan", capture)
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("strategy.scanner", run_name="__main__", alter_sys=False)
    assert exit_info.value.code == 0
    return recorder


def _strategy_wrapper(monkeypatch, tmp_path, strategy, breadth) -> Recorder:
    """scripts.run_paper_v2 / scripts.run_paper_v3 compatibility wrappers."""
    isolate_environment(monkeypatch, tmp_path, strategy)
    recorder = Recorder()
    wrap_processors(monkeypatch, recorder)
    monkeypatch.setattr(
        "app.strategy_scan._default_scanner_loader",
        _harness_loader(monkeypatch, recorder, breadth),
    )
    import app.strategy_scan as service

    original_run = service.run_strategy_scan

    def capture(**kwargs):
        recorder.result = original_run(**kwargs)
        return recorder.result

    monkeypatch.setattr(service, "run_strategy_scan", capture)
    if strategy.startswith("V3"):
        from scripts import run_paper_v3 as wrapper
    else:
        from scripts import run_paper_v2 as wrapper
    wrapper.main()
    return recorder


ENTRYPOINTS = {
    "daily": _daily,
    "scanner_module_main": _scanner_module_main,
    "strategy_wrapper": _strategy_wrapper,
}


@pytest.mark.parametrize(("strategy", "breadth"), CASES)
@pytest.mark.parametrize("entrypoint", tuple(ENTRYPOINTS))
def test_every_entrypoint_reproduces_the_pre_change_daily_decision(
    monkeypatch, tmp_path, entrypoint, strategy, breadth
):
    recorder = ENTRYPOINTS[entrypoint](monkeypatch, tmp_path, strategy, breadth)

    assert _summary(recorder) == _golden(strategy, breadth)


@pytest.mark.parametrize(("strategy", "breadth"), CASES)
def test_side_effects_only_after_strategy_decision(monkeypatch, tmp_path, strategy, breadth):
    recorder = _scanner_module_main(monkeypatch, tmp_path, strategy, breadth)
    events = recorder.events
    decision_end = max(i for i, e in enumerate(events) if e.startswith("decision_end:"))
    effects = [i for i, e in enumerate(events) if e in SIDE_EFFECTS]

    assert effects and min(effects) > decision_end
    assert "telegram_send" in events and "paper_queue" in events


@pytest.mark.parametrize(("strategy", "processor_class"), (
    ("Q70_FROZEN", "PaperV2Scanner"),
    ("V3_BREADTH_40_60", "PaperV3Scanner"),
))
def test_failed_strategy_decision_produces_no_side_effects(
    monkeypatch, tmp_path, strategy, processor_class
):
    isolate_environment(monkeypatch, tmp_path, strategy)
    recorder = Recorder()
    module = __import__(
        "strategy.paper_v3_scanner" if processor_class == "PaperV3Scanner" else "strategy.paper_v2_scanner",
        fromlist=[processor_class],
    )

    def explode(self, signals, stats):
        recorder.events.append("decision_failed")
        raise RuntimeError("strategy decision failed")

    monkeypatch.setattr(getattr(module, processor_class), "process", explode)
    from app.strategy_scan import run_strategy_scan

    with pytest.raises(RuntimeError, match="strategy decision failed"):
        run_strategy_scan(scanner_loader=_harness_loader(monkeypatch, recorder, 80.0))

    assert "decision_failed" in recorder.events
    assert not any(event in SIDE_EFFECTS for event in recorder.events)
    assert recorder.messages == [] and recorder.queued == []


def test_low_level_run_scan_refuses_to_run_without_strategy_processor(monkeypatch, tmp_path):
    isolate_environment(monkeypatch, tmp_path, "Q70_FROZEN")
    recorder = Recorder()
    scanner = fresh_scanner(monkeypatch, recorder, breadth=80.0)

    with pytest.raises(ValueError, match="result_processor"):
        scanner.run_scan()

    assert recorder.events == [] and recorder.messages == []


def test_scanner_policy_frozen_before_strategy_configuration_fails_closed(monkeypatch, tmp_path):
    from app.strategy_scan import ScanConfigurationError, run_strategy_scan

    isolate_environment(monkeypatch, tmp_path, "Q70_FROZEN")
    recorder = Recorder()
    # Imported under the hostile environment (stop 3x ATR), i.e. *before*
    # the Q70 runtime is pinned: exactly the old standalone failure mode.
    stale = fresh_scanner(monkeypatch, recorder, breadth=80.0)
    assert stale.TRADING_POLICY.stop_atr_multiplier == 3.0

    with pytest.raises(ScanConfigurationError):
        run_strategy_scan(scanner_loader=lambda: stale)

    assert recorder.events == [] and recorder.messages == []


def test_quantctl_and_manager_scan_launch_the_canonical_module():
    from quantctl.operations import OperationCapability, get_operation

    spec = get_operation("scan")
    assert spec.module_target == "strategy.scanner"
    assert OperationCapability.PAPER_WRITE in spec.capabilities
    assert OperationCapability.TELEGRAM_SEND in spec.capabilities
    source = (Path(__file__).parents[1] / "strategy" / "scanner.py").read_text(encoding="utf-8")
    main_block = source.split('if __name__ == "__main__":', 1)[1]
    assert "app.strategy_scan" in main_block
    assert "run_scan" not in main_block.replace("run_canonical_strategy_scan", "")


# --- routing with the real paper executor on temporary stores ---------------

def _pending_rows(path: Path) -> list[str]:
    with sqlite3.connect(path) as connection:
        return [row[0] for row in connection.execute(
            "SELECT symbol FROM paper_pending_signals ORDER BY symbol"
        )]


@pytest.mark.parametrize(("strategy", "breadth", "own", "others", "expected"), (
    ("Q70_FROZEN", 80.0, "q70", ("v3", "generic"), ["AAA", "BBB"]),
    ("V3_BREADTH_40_60", 55.0, "v3", ("q70", "generic"), ["AAA", "BBB"]),
))
def test_scan_routes_paper_writes_only_to_the_strategy_store(
    monkeypatch, tmp_path, strategy, breadth, own, others, expected
):
    stores = isolate_environment(monkeypatch, tmp_path, strategy)
    recorder = Recorder()
    wrap_processors(monkeypatch, recorder)
    # Observational sector-RS telemetry runs when a signal has a date; keep
    # it offline (it never affects the Q70 decision).
    monkeypatch.setattr("strategy.paper_v2_gate.fetch_sector_mapping", lambda: {})
    monkeypatch.setattr(
        "strategy.paper_v2_gate.calculate_sector_relative_strength",
        lambda *a, **k: {"available": False},
    )
    from app.strategy_scan import run_strategy_scan

    run_strategy_scan(
        scanner_loader=lambda: fresh_scanner(
            monkeypatch,
            recorder,
            breadth=breadth,
            real_executor=True,
            signal_date="2026-09-28",
        )
    )

    assert _pending_rows(stores[own]) == expected
    for other in others:
        assert not stores[other].exists()
    assert recorder.messages, "Telegram (stubbed) should have been reached"
