from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from importlib.util import find_spec
from pathlib import Path
import os
import subprocess
import sys
from time import perf_counter
from types import MappingProxyType
from typing import Any, Callable, Mapping

from quantctl.registry import PROJECT_ROOT, inspect_system
from quantctl.run_history import (
    HISTORY_PATH_ENV,
    RUN_ID_ENV,
    OperationHistoryStore,
    RunStatus,
    history_path as resolve_history_path,
    sanitize_captured_output,
    sanitize_diagnostic,
)


class OperationCapability(str, Enum):
    READ_ONLY = "READ_ONLY"
    LOCAL_WRITE = "LOCAL_WRITE"
    MARKET_DATA_WRITE = "MARKET_DATA_WRITE"
    PAPER_WRITE = "PAPER_WRITE"
    FORWARD_WRITE = "FORWARD_WRITE"
    EXTERNAL_CALL = "EXTERNAL_CALL"
    TELEGRAM_SEND = "TELEGRAM_SEND"


@dataclass(frozen=True, slots=True)
class OperationSpec:
    name: str
    description: str
    capabilities: tuple[OperationCapability, ...]
    module_target: str | None = None

    @property
    def confirmation_required(self) -> bool:
        return any(capability is not OperationCapability.READ_ONLY for capability in self.capabilities)


@dataclass(frozen=True, slots=True)
class OperationResult:
    operation: str
    success: bool
    exit_code: int
    message: str
    started_at_utc: str
    finished_at_utc: str
    details: Mapping[str, Any]
    stdout: str = ""
    stderr: str = ""
    run_id: str | None = None
    duration_ms: int | None = None
    status: str | None = None


_OPERATION_SPECS = (
    OperationSpec(
        "data-status",
        "Inspect canonical market-data availability and coverage facts.",
        (OperationCapability.READ_ONLY,),
    ),
    OperationSpec(
        "update",
        "Run the canonical incremental market-data updater.",
        (
            OperationCapability.LOCAL_WRITE,
            OperationCapability.MARKET_DATA_WRITE,
            OperationCapability.EXTERNAL_CALL,
        ),
        "scripts.update_data",
    ),
    OperationSpec(
        "scan",
        "Run the canonical production scanner and notification path.",
        (
            OperationCapability.LOCAL_WRITE,
            OperationCapability.PAPER_WRITE,
            OperationCapability.EXTERNAL_CALL,
            OperationCapability.TELEGRAM_SEND,
        ),
        "strategy.scanner",
    ),
    OperationSpec(
        "daily",
        "Run the canonical end-of-day production pipeline.",
        (
            OperationCapability.LOCAL_WRITE,
            OperationCapability.MARKET_DATA_WRITE,
            OperationCapability.PAPER_WRITE,
            OperationCapability.FORWARD_WRITE,
            OperationCapability.EXTERNAL_CALL,
            OperationCapability.TELEGRAM_SEND,
        ),
        "scripts.run_daily",
    ),
)
_OPERATIONS = MappingProxyType({spec.name: spec for spec in _OPERATION_SPECS})


def list_operations() -> tuple[OperationSpec, ...]:
    return _OPERATION_SPECS


def get_operation(name: str) -> OperationSpec:
    try:
        return _OPERATIONS[name]
    except KeyError as exc:
        raise ValueError(f"unknown quantctl operation: {name}") from exc


def operation_available(spec: OperationSpec, *, root: Path = PROJECT_ROOT) -> bool:
    if spec.module_target is None:
        return True
    relative = Path(*spec.module_target.split(".")).with_suffix(".py")
    if not (root / relative).is_file():
        return False
    return find_spec(spec.module_target) is not None


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _data_status(*, root: Path, started: str) -> OperationResult:
    market = inspect_system(root=root).market
    details = MappingProxyType(
        {
            "database": "AVAILABLE" if market.readable else ("MISSING" if not market.exists else "UNKNOWN"),
            "latest_session": market.latest_session,
            "session_count": market.session_count,
            "symbol_count": market.symbol_count,
            "latest_session_symbol_count": market.latest_session_symbol_count,
        }
    )
    return OperationResult(
        "data-status",
        True,
        0,
        "Market-data status inspected read-only.",
        started,
        _timestamp(),
        details,
    )


ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


def execute_operation(
    name: str,
    *,
    root: Path = PROJECT_ROOT,
    process_runner: ProcessRunner = subprocess.run,
    history_path: Path | None = None,
) -> OperationResult:
    spec = get_operation(name)
    started = _timestamp()
    if spec.name == "data-status":
        return _data_status(root=root, started=started)
    store_path = resolve_history_path(root=root, path=history_path)
    store = OperationHistoryStore(store_path)
    run_id = store.begin_run(
        spec.name,
        (capability.value for capability in spec.capabilities),
        started_at_utc=started,
        metadata={"module_target": spec.module_target},
    )
    monotonic_started = perf_counter()
    if not operation_available(spec, root=root):
        finished = _timestamp()
        duration_ms = round((perf_counter() - monotonic_started) * 1000)
        message = f"Canonical module is unavailable: {spec.module_target}"
        store.finish_run(
            run_id,
            status=RunStatus.FAILED,
            exit_code=127,
            duration_ms=duration_ms,
            finished_at_utc=finished,
            error_type="ModuleNotFoundError",
            error_message=message,
            metadata={"module_target": spec.module_target},
        )
        return OperationResult(
            spec.name,
            False,
            127,
            message,
            started,
            finished,
            MappingProxyType({"module_target": spec.module_target}),
            run_id=run_id,
            duration_ms=duration_ms,
            status=RunStatus.FAILED.value,
        )

    command = (sys.executable, "-m", str(spec.module_target))
    child_environment = dict(os.environ)
    child_environment["PYTHONIOENCODING"] = "utf-8"
    child_environment["PYTHONUTF8"] = "1"
    child_environment[RUN_ID_ENV] = run_id
    child_environment[HISTORY_PATH_ENV] = str(store_path)
    try:
        completed = process_runner(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_environment,
        )
    except KeyboardInterrupt:
        finished = _timestamp()
        duration_ms = round((perf_counter() - monotonic_started) * 1000)
        store.finish_run(
            run_id,
            status=RunStatus.CANCELLED,
            exit_code=130,
            duration_ms=duration_ms,
            finished_at_utc=finished,
            error_type="KeyboardInterrupt",
            error_message="Operation cancelled by user.",
            metadata={"module_target": spec.module_target},
        )
        raise
    except OSError as exc:
        finished = _timestamp()
        duration_ms = round((perf_counter() - monotonic_started) * 1000)
        message = f"Failed to start canonical operation: {type(exc).__name__}: {exc}"
        store.finish_run(
            run_id,
            status=RunStatus.FAILED,
            exit_code=127,
            duration_ms=duration_ms,
            finished_at_utc=finished,
            error_type=type(exc).__name__,
            error_message=message,
            metadata={"module_target": spec.module_target},
        )
        return OperationResult(
            spec.name,
            False,
            127,
            message,
            started,
            finished,
            MappingProxyType({"module_target": spec.module_target}),
            run_id=run_id,
            duration_ms=duration_ms,
            status=RunStatus.FAILED.value,
        )

    success = completed.returncode == 0
    final_status = (
        RunStatus.SUCCESS
        if success
        else (RunStatus.CANCELLED if completed.returncode == 130 else RunStatus.FAILED)
    )
    finished = _timestamp()
    duration_ms = round((perf_counter() - monotonic_started) * 1000)
    interim = store.get_run(run_id)
    failed_step = next(
        (item.step_name for item in (interim.steps if interim else ()) if item.status is RunStatus.FAILED),
        None,
    )
    persisted_error = sanitize_diagnostic(completed.stderr) if not success else None
    store.finish_run(
        run_id,
        status=final_status,
        exit_code=int(completed.returncode),
        duration_ms=duration_ms,
        finished_at_utc=finished,
        failed_step=failed_step,
        error_type=None if success else ("KeyboardInterrupt" if final_status is RunStatus.CANCELLED else "SubprocessExit"),
        error_message=persisted_error or (None if success else f"Operation exited with code {completed.returncode}."),
        metadata={"module_target": spec.module_target},
    )
    return OperationResult(
        spec.name,
        success,
        int(completed.returncode),
        f"Operation {spec.name} {'completed' if success else 'failed'} with exit code {completed.returncode}.",
        started,
        finished,
        MappingProxyType({"module_target": spec.module_target}),
        sanitize_captured_output(completed.stdout),
        sanitize_captured_output(completed.stderr),
        run_id,
        duration_ms,
        final_status.value,
    )
