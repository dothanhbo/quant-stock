from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from quantctl.commands.doctor import Check, collect_checks, overall_status
from quantctl.operations import OperationSpec, list_operations, operation_available
from quantctl.registry import (
    PROJECT_ROOT,
    QUANTCTL_VERSION,
    RunnerInfo,
    SystemSnapshot,
    discover_active_runners,
    inspect_system,
)
from quantctl.state import (
    ForwardSystemSnapshot,
    PaperSystemSnapshot,
    inspect_forward_system,
    inspect_paper_system,
)


@dataclass(frozen=True, slots=True)
class DashboardViewModel:
    quantctl_version: str
    doctor_status: str
    snapshot: SystemSnapshot
    paper: PaperSystemSnapshot
    forward: ForwardSystemSnapshot


@dataclass(frozen=True, slots=True)
class ResearchViewModel:
    runners: tuple[RunnerInfo, ...]


@dataclass(frozen=True, slots=True)
class SystemViewModel:
    checks: tuple[Check, ...]
    overall_status: str


@dataclass(frozen=True, slots=True)
class OperationViewModel:
    spec: OperationSpec
    available: bool


@dataclass(frozen=True, slots=True)
class OperationsViewModel:
    operations: tuple[OperationViewModel, ...]


@dataclass(frozen=True, slots=True)
class StateViewModel:
    paper: PaperSystemSnapshot
    forward: ForwardSystemSnapshot


def build_dashboard_model(
    *,
    root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
) -> DashboardViewModel:
    snapshot = inspect_system(root=root)
    checks = collect_checks(root=root, environ=environ, snapshot=snapshot)
    return DashboardViewModel(
        QUANTCTL_VERSION,
        overall_status(checks),
        snapshot,
        inspect_paper_system(
            root=root,
            environ=None if environ is None else dict(environ),
        ),
        inspect_forward_system(root=root),
    )


def build_research_model(*, root: Path = PROJECT_ROOT) -> ResearchViewModel:
    return ResearchViewModel(discover_active_runners(root=root))


def build_system_model(
    *,
    root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
) -> SystemViewModel:
    checks = collect_checks(root=root, environ=environ)
    return SystemViewModel(checks, overall_status(checks))


def build_operations_model(*, root: Path = PROJECT_ROOT) -> OperationsViewModel:
    return OperationsViewModel(
        tuple(
            OperationViewModel(spec, operation_available(spec, root=root))
            for spec in list_operations()
        )
    )


def build_state_model(
    *,
    root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
) -> StateViewModel:
    return StateViewModel(
        inspect_paper_system(
            root=root,
            environ=None if environ is None else dict(environ),
        ),
        inspect_forward_system(root=root),
    )
