from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

from quantctl.commands.doctor import Check, collect_checks, overall_status
from quantctl.operations import OperationCapability, OperationSpec, list_operations, operation_available
from quantctl.registry import (
    PROJECT_ROOT,
    QUANTCTL_VERSION,
    SystemSnapshot,
    inspect_system,
)
from quantctl.evidence_compare import EvidenceComparisonCatalog, inspect_evidence_comparison_catalog
from quantctl.factor_explore import FactorExploreCatalog, inspect_factor_explore_catalog
from quantctl.portfolio_risk import PortfolioRiskCatalog, inspect_portfolio_risk_catalog
from quantctl.research_home import ResearchHomeCatalog, inspect_research_home_catalog
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
from quantctl.run_history import HistoryReadError, OperationHistoryStore, RunDetail, history_path


if TYPE_CHECKING:
    from quantctl.decision_gate import DecisionGateCatalog
    from quantctl.forward_evidence import ForwardEvidenceCatalog


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
    history_warning: str | None


@dataclass(frozen=True, slots=True)
class ResearchViewModel:
    frontier: ResearchFrontierSnapshot
    production: ProductionPolicySnapshot
    forward: ForwardSystemSnapshot
    catalog: ResearchHomeCatalog


@dataclass(frozen=True, slots=True)
class ExploreViewModel:
    catalog: FactorExploreCatalog


@dataclass(frozen=True, slots=True)
class CompareViewModel:
    catalog: EvidenceComparisonCatalog


@dataclass(frozen=True, slots=True)
class PortfolioRiskViewModel:
    catalog: PortfolioRiskCatalog


@dataclass(frozen=True, slots=True)
class ForwardEvidenceViewModel:
    catalog: ForwardEvidenceCatalog


@dataclass(frozen=True, slots=True)
class DecisionGateViewModel:
    catalog: DecisionGateCatalog


@dataclass(frozen=True, slots=True)
class SystemViewModel:
    checks: tuple[Check, ...]
    overall_status: str


@dataclass(frozen=True, slots=True)
class OperationViewModel:
    spec: OperationSpec
    available: bool
    unavailable_reason: str | None = None


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
    warning: str | None = None


def _latest_run(root: Path) -> tuple[RunDetail | None, str | None]:
    try:
        return OperationHistoryStore(history_path(root=root)).latest_run(), None
    except HistoryReadError as exc:
        return None, str(exc)


def build_dashboard_model(
    *,
    root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
) -> DashboardViewModel:
    snapshot = inspect_system(root=root)
    checks = collect_checks(root=root, environ=environ, snapshot=snapshot)
    latest_run, history_warning = _latest_run(root)
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
        latest_run,
        history_warning,
    )


def build_research_model(
    *,
    root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
) -> ResearchViewModel:
    return ResearchViewModel(
        inspect_research_frontier(root=root),
        inspect_production_policy(root=root, environ=environ),
        inspect_forward_system(root=root),
        inspect_research_home_catalog(root=root),
    )


def build_explore_model(*, root: Path = PROJECT_ROOT) -> ExploreViewModel:
    return ExploreViewModel(inspect_factor_explore_catalog(root=root))


def build_compare_model(*, root: Path = PROJECT_ROOT) -> CompareViewModel:
    return CompareViewModel(inspect_evidence_comparison_catalog(root=root))


def build_portfolio_risk_model(*, root: Path = PROJECT_ROOT) -> PortfolioRiskViewModel:
    return PortfolioRiskViewModel(inspect_portfolio_risk_catalog(root=root))


def build_forward_evidence_model(
    *,
    root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
) -> ForwardEvidenceViewModel:
    from quantctl.forward_evidence import inspect_forward_evidence_catalog

    return ForwardEvidenceViewModel(
        inspect_forward_evidence_catalog(root=root, environ=environ)
    )


def build_decision_gate_model(
    *,
    root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
) -> DecisionGateViewModel:
    from quantctl.decision_gate import inspect_decision_gate_catalog

    return DecisionGateViewModel(
        inspect_decision_gate_catalog(root=root, environ=environ)
    )


def build_system_model(
    *,
    root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
) -> SystemViewModel:
    checks = collect_checks(root=root, environ=environ)
    return SystemViewModel(checks, overall_status(checks))


def build_operations_model(
    *, root: Path = PROJECT_ROOT, environ: Mapping[str, str] | None = None,
) -> OperationsViewModel:
    active = inspect_paper_system(root=root, environ=None if environ is None else dict(environ)).active_store
    paper_issue = (
        "Active Paper store is unresolved." if active is None else
        "Active Paper store is unavailable or unreadable." if not active.readable else
        "Active Paper store has an incompatible schema." if active.schema_status != "OK" else None
    )
    return OperationsViewModel(
        tuple(
            OperationViewModel(
                spec,
                operation_available(spec, root=root) and not (
                    paper_issue and OperationCapability.PAPER_WRITE in spec.capabilities),
                paper_issue if OperationCapability.PAPER_WRITE in spec.capabilities else None,
            )
            for spec in list_operations()
        ),
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
    try:
        summaries = store.list_runs(limit)
        details = tuple(item for summary in summaries if (item := store.get_run(summary.run_id)) is not None)
        return RunHistoryViewModel(store.latest_run(), details)
    except HistoryReadError as exc:
        return RunHistoryViewModel(None, (), str(exc))
