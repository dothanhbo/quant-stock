from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from importlib.util import find_spec
from pathlib import Path
import subprocess
import sys
from types import MappingProxyType
from typing import Any, Callable, Mapping

from quantctl.registry import PROJECT_ROOT, inspect_system


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
) -> OperationResult:
    spec = get_operation(name)
    started = _timestamp()
    if spec.name == "data-status":
        return _data_status(root=root, started=started)
    if not operation_available(spec, root=root):
        return OperationResult(
            spec.name,
            False,
            127,
            f"Canonical module is unavailable: {spec.module_target}",
            started,
            _timestamp(),
            MappingProxyType({"module_target": spec.module_target}),
        )

    command = (sys.executable, "-m", str(spec.module_target))
    try:
        completed = process_runner(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return OperationResult(
            spec.name,
            False,
            127,
            f"Failed to start canonical operation: {type(exc).__name__}: {exc}",
            started,
            _timestamp(),
            MappingProxyType({"module_target": spec.module_target}),
        )

    success = completed.returncode == 0
    return OperationResult(
        spec.name,
        success,
        int(completed.returncode),
        f"Operation {spec.name} {'completed' if success else 'failed'} with exit code {completed.returncode}.",
        started,
        _timestamp(),
        MappingProxyType({"module_target": spec.module_target}),
        completed.stdout or "",
        completed.stderr or "",
    )
