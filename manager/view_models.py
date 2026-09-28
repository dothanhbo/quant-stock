from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from quantctl.commands.doctor import Check, collect_checks, overall_status
from quantctl.registry import (
    PROJECT_ROOT,
    QUANTCTL_VERSION,
    RunnerInfo,
    SystemSnapshot,
    discover_active_runners,
    inspect_system,
)


@dataclass(frozen=True, slots=True)
class DashboardViewModel:
    quantctl_version: str
    doctor_status: str
    snapshot: SystemSnapshot


@dataclass(frozen=True, slots=True)
class ResearchViewModel:
    runners: tuple[RunnerInfo, ...]


@dataclass(frozen=True, slots=True)
class SystemViewModel:
    checks: tuple[Check, ...]
    overall_status: str


def build_dashboard_model(
    *,
    root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
) -> DashboardViewModel:
    snapshot = inspect_system(root=root)
    checks = collect_checks(root=root, environ=environ, snapshot=snapshot)
    return DashboardViewModel(QUANTCTL_VERSION, overall_status(checks), snapshot)


def build_research_model(*, root: Path = PROJECT_ROOT) -> ResearchViewModel:
    return ResearchViewModel(discover_active_runners(root=root))


def build_system_model(
    *,
    root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
) -> SystemViewModel:
    checks = collect_checks(root=root, environ=environ)
    return SystemViewModel(checks, overall_status(checks))
