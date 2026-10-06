"""Canonical strategy-aware scan path (single source of truth).

Every operational scan entrypoint goes through :func:`run_strategy_scan`:

* daily pipeline scanner stage (``scripts.run_daily.run_strategy_scanner``),
* ``python -m strategy.scanner`` — which is what ``quantctl scan`` and the
  Manager "scan" operation launch as a subprocess,
* the compatibility wrappers ``scripts.run_paper_v2`` / ``scripts.run_paper_v3``.

Order of operations::

    resolve strategy identity (PAPER_STRATEGY_VERSION, or explicit)
      -> pin that strategy's frozen runtime environment (the same functions
         the daily lifecycle wrappers use)
      -> import the scanner (its TradingPolicy is resolved now) and verify
         the policy it froze equals the pinned policy (fail closed otherwise)
      -> strategy.scanner.run_scan(result_processor=<Q70 or V3 processor>)
             raw scan -> strategy decision (Q70 gate / V3 breadth)
             -> side effects: telemetry, signal rows, paper queue, Telegram

Strategy semantics live in ``strategy.paper_v2_gate``,
``strategy.paper_v2_scanner``, ``strategy.paper_v3_scanner`` and
``strategy.breadth_exposure``; this module only selects and wires them. It
contains no thresholds other than the frozen 0.70 Q70 cut that
``scripts.run_daily`` already passed explicitly.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any, Callable

from config.paper_store import (
    Q70_STRATEGY_IDENTITY,
    V3_STRATEGY_IDENTITY,
    effective_paper_strategy,
)

# Frozen Q70 quality cut, previously the literal in scripts.run_daily.
Q70_QUALITY_THRESHOLD = 0.70

ScannerLoader = Callable[[], ModuleType]


class ScanConfigurationError(RuntimeError):
    """The scanner would not run with the selected strategy's frozen policy."""


def resolve_scan_strategy(strategy_identity: str | None = None) -> str:
    """Return the canonical strategy identity (Q70 unless V3 is selected)."""
    if strategy_identity is None:
        return effective_paper_strategy()
    return effective_paper_strategy({"PAPER_STRATEGY_VERSION": strategy_identity})


def configure_strategy_runtime(strategy_identity: str) -> None:
    """Pin the frozen runtime environment of ``strategy_identity``.

    Delegates to the exact functions the daily lifecycle wrappers call, so a
    scan without a preceding lifecycle (standalone, ``quantctl scan``,
    ``--skip-lifecycle``) resolves the same policy as the daily run.
    """
    if strategy_identity == V3_STRATEGY_IDENTITY:
        from scripts.run_paper_v3_lifecycle import configure_v3_environment

        configure_v3_environment()
    elif strategy_identity == Q70_STRATEGY_IDENTITY:
        from scripts.run_paper_v2_lifecycle import configure_v2_environment

        configure_v2_environment()
    else:  # pragma: no cover - resolve_scan_strategy only yields the two
        raise ScanConfigurationError(f"unknown strategy identity: {strategy_identity}")


def build_scan_processor(strategy_identity: str):
    """Return the strategy decision processor for ``strategy_identity``."""
    if strategy_identity == V3_STRATEGY_IDENTITY:
        from strategy.paper_v3_scanner import PaperV3Scanner

        processor = PaperV3Scanner(threshold=Q70_QUALITY_THRESHOLD)
    else:
        from strategy.paper_v2_scanner import PaperV2Scanner

        processor = PaperV2Scanner(threshold=Q70_QUALITY_THRESHOLD)
    if processor.VERSION != strategy_identity:
        raise ScanConfigurationError(
            f"processor {type(processor).__name__} implements {processor.VERSION}, "
            f"not the selected strategy {strategy_identity}"
        )
    return processor


def _default_scanner_loader() -> ModuleType:
    return importlib.import_module("strategy.scanner")


def _verify_scanner_policy(scanner: ModuleType) -> None:
    """Fail closed if the scanner froze its policy before strategy pinning.

    ``strategy.scanner`` resolves ``TradingPolicy`` once, at import. If it was
    imported before :func:`configure_strategy_runtime` ran with different
    values, the stop/target levels written to signals and the paper queue
    would not be the frozen strategy's levels.
    """
    from config.trading_policy import TradingPolicy

    expected = TradingPolicy.from_env()
    if scanner.TRADING_POLICY != expected:
        raise ScanConfigurationError(
            "strategy.scanner resolved its TradingPolicy before the strategy "
            f"runtime was configured ({scanner.TRADING_POLICY!r} != {expected!r}); "
            "refusing to scan with a policy that is not the selected strategy's."
        )


def run_strategy_scan(
    *,
    pending_execution_result: Any = None,
    strategy_identity: str | None = None,
    scanner_loader: ScannerLoader | None = None,
) -> tuple[list[dict], dict]:
    """Run the one canonical strategy-aware scan and its side effects."""
    identity = resolve_scan_strategy(strategy_identity)
    configure_strategy_runtime(identity)
    processor = build_scan_processor(identity)
    scanner = (scanner_loader or _default_scanner_loader)()
    _verify_scanner_policy(scanner)
    return scanner.run_scan(
        pending_execution_result=pending_execution_result,
        result_processor=processor.process,
    )


def main() -> int:
    """Standalone operational scan (``python -m strategy.scanner``)."""
    from dotenv import load_dotenv

    load_dotenv()
    run_strategy_scan()
    return 0


__all__ = (
    "Q70_QUALITY_THRESHOLD",
    "ScanConfigurationError",
    "build_scan_processor",
    "configure_strategy_runtime",
    "main",
    "resolve_scan_strategy",
    "run_strategy_scan",
)
