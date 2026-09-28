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
from quantctl.research_status import (
    ProductionPolicySnapshot,
    ResearchFrontierSnapshot,
    inspect_production_policy,
    inspect_research_frontier,
)
from quantctl.run_history import OperationHistoryStore, RunDetail, history_path


@dataclass(frozen=True, slots=True)
class DashboardViewModel:
    quantctl_version: str
    doctor_status: str
    snapshot: SystemSnapshot
    paper: PaperSystemSnapshot
    forward: ForwardSystemSnapshot
    production: ProductionPolicySnapshot
    research: ResearchFrontierSnapshot
    latest_run: RunDetail | None


@dataclass(frozen=True, slots=True)
class ResearchViewModel:
    runners: tuple[RunnerInfo, ...]
    frontier: ResearchFrontierSnapshot


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


@dataclass(frozen=True, slots=True)
class RunHistoryViewModel:
    latest: RunDetail | None
    recent: tuple[RunDetail, ...]


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
        inspect_production_policy(root=root, environ=environ),
        inspect_research_frontier(root=root),
        OperationHistoryStore(history_path(root=root)).latest_run(),
    )


def build_research_model(*, root: Path = PROJECT_ROOT) -> ResearchViewModel:
    return ResearchViewModel(discover_active_runners(root=root), inspect_research_frontier(root=root))


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


def build_run_history_model(*, root: Path = PROJECT_ROOT, limit: int = 20) -> RunHistoryViewModel:
    store = OperationHistoryStore(history_path(root=root))
    summaries = store.list_runs(limit)
    details = tuple(item for summary in summaries if (item := store.get_run(summary.run_id)) is not None)
    return RunHistoryViewModel(store.latest_run(), details)
