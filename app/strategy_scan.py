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
      -> verify any executor retained by a reused scanner module belongs to
         the selected strategy/store (fail closed otherwise)
      -> enforce the canonical strategy contract (quantlab.strategy_contract;
         fail closed on any enforced deviation, before any side effect)

Daily additionally runs :func:`preflight_strategy_contract` before its first
mutation (market bootstrap/update, forward evidence, lifecycle, scan).
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


def _verify_retained_executor(scanner: ModuleType, strategy_identity: str) -> None:
    """Fail closed if a retained paper executor belongs to another runtime (P1-2).

    ``strategy.scanner`` keeps its PaperSignalExecutor for the life of the
    process. In a reused process (Manager, notebooks, tests) a Q70 executor
    could otherwise receive V3 signals, or vice versa, while the contract
    reconstructed from the environment still matches. A fresh process has no
    retained executor and is unaffected; nothing is reset or replaced here.
    """
    executor = getattr(scanner, "paper_signal_executor", None)
    if executor is None:
        return
    from dataclasses import asdict, replace
    from pathlib import Path

    from backtesting.regime_policy import RegimePortfolioPolicy
    from config.paper_store import resolve_active_paper_store
    from config.trading_policy import TradingPolicy
    from execution.signal_executor import PaperExecutionConfig
    from quantlab.strategy_identity import describe_regime_portfolio_policy

    problems: list[str] = []
    expected_store = resolve_active_paper_store(strategy_identity=strategy_identity)
    expected_path = Path(expected_store.database_path).resolve()
    retained_config = getattr(executor, "config", None)
    if retained_config is None:
        problems.append("retained executor has no configuration")
    else:
        retained_path = Path(retained_config.database_path).resolve()
        if retained_path != expected_path:
            problems.append(
                f"paper store {retained_path} is not the {strategy_identity} store {expected_path}"
            )
        expected_config = PaperExecutionConfig.from_env()
        retained_values = asdict(retained_config)
        expected_values = asdict(expected_config)
        for name in sorted(expected_values):
            if name == "database_path":
                continue
            if retained_values.get(name) != expected_values[name]:
                problems.append(
                    f"executor.{name}={retained_values.get(name)!r} "
                    f"(selected runtime: {expected_values[name]!r})"
                )
        broker_store = getattr(getattr(executor, "broker", None), "_store", None)
        broker_path = getattr(broker_store, "database_path", None)
        if broker_path is not None and Path(broker_path).resolve() != expected_path:
            problems.append(f"broker store {Path(broker_path).resolve()} is not {expected_path}")
        expected_policy = replace(
            TradingPolicy.from_env(),
            stop_atr_multiplier=expected_config.atr_stop_multiplier,
            target_atr_multiplier=expected_config.target_atr_multiplier,
        )
        if getattr(executor, "policy", None) != expected_policy:
            problems.append("executor fill policy differs from the selected runtime policy")
        # The ACTUAL sizer object, not only the config label (P2-3): it must be
        # exactly what a fresh executor builds from the selected configuration
        # (same production builder, dataclass equality = same type and every
        # active parameter, e.g. AtrRiskSizer risk 1%, ATR stop 2, cap 20%).
        from types import SimpleNamespace

        from execution.signal_executor import PaperSignalExecutor

        expected_sizer = PaperSignalExecutor._build_position_sizer(
            SimpleNamespace(config=expected_config)
        )
        retained_sizer = getattr(executor, "position_sizer", None)
        if retained_sizer != expected_sizer:
            problems.append(
                f"position sizer {retained_sizer!r} is not the selected runtime sizer "
                f"{expected_sizer!r}"
            )
    regime_policy = getattr(executor, "regime_policy", None)
    if regime_policy is not None and describe_regime_portfolio_policy(
        regime_policy
    ) != describe_regime_portfolio_policy(RegimePortfolioPolicy()):
        problems.append("executor regime overlay is not REGIME_CAPS_V1")
    if problems:
        raise ScanConfigurationError(
            f"strategy.scanner retains a paper executor that does not belong to "
            f"{strategy_identity}: " + "; ".join(problems) + ". Refusing to scan "
            "(no telemetry, signal, paper or Telegram side effect). Start a fresh "
            "process for the selected strategy."
        )


def preflight_strategy_contract(strategy_identity: str | None = None):
    """Daily-level preflight (P1-1): pin the selected strategy's runtime and
    enforce the canonical contract before ANY Daily mutation.

    Uses the same pinning (``configure_strategy_runtime``) and the same
    enforcement (``quantlab.strategy_contract.enforce_strategy_contract``) as
    the scan and lifecycle boundaries, which keep their own checks as defense
    in depth. Pinning only sets process environment variables that the
    lifecycle and scan stages set anyway; no file or database is touched.
    """
    from quantlab.strategy_contract import enforce_strategy_contract

    identity = resolve_scan_strategy(strategy_identity)
    configure_strategy_runtime(identity)
    return enforce_strategy_contract(identity)


def enforce_scan_strategy_contract(
    strategy_identity: str,
    scanner: ModuleType,
    processor: Any,
):
    """Fail closed unless this scan runs the canonical strategy contract (B6).

    The identity is built from the scanner's own constructed TradingPolicy and
    entry model and the processor's gate, so it describes exactly what the scan
    is about to run. Called after the scanner policy check and BEFORE
    ``run_scan``: no telemetry, signal row, paper queue write or Telegram
    message can happen on a mismatch. The same enforcement function guards the
    paper lifecycle (scripts.run_paper_lifecycle).
    """
    from quantlab.strategy_contract import enforce_strategy_contract
    from quantlab.strategy_identity_runtime import collect_production_strategy_identity

    return enforce_strategy_contract(
        strategy_identity,
        collector=lambda: collect_production_strategy_identity(
            strategy_identity,
            signal_policy=scanner.TRADING_POLICY,
            entry_model=scanner.strategy,
            quality_threshold=processor.gate.threshold,
        ),
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
    _verify_retained_executor(scanner, identity)
    enforce_scan_strategy_contract(identity, scanner, processor)
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
    "enforce_scan_strategy_contract",
    "preflight_strategy_contract",
    "resolve_scan_strategy",
    "run_strategy_scan",
)
