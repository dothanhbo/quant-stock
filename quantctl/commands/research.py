from __future__ import annotations

from pathlib import Path

from quantctl.registry import PROJECT_ROOT, discover_active_runners
from quantctl.research_status import ResearchDecisionStatus, inspect_research_frontier


def render_list(*, root: Path = PROJECT_ROOT) -> str:
    runners = discover_active_runners(root=root)
    lines = ["ACTIVE QUANT LAB RUNNERS", ""]
    for runner in runners:
        lines.extend(
            (
                runner.short_name,
                f"  {runner.path.as_posix()}",
                f"  Availability: {'AVAILABLE' if runner.available else 'UNAVAILABLE'}",
                "",
            )
        )
    lines.append(f"Total: {len(runners)}")
    return "\n".join(lines)


def run_list(*, root: Path = PROJECT_ROOT) -> int:
    print(render_list(root=root))
    return 0


def _decision_lines(title: str, decisions: tuple[ResearchDecisionStatus, ...]) -> list[str]:
    lines = [title]
    if not decisions:
        lines.append("  UNAVAILABLE")
        return lines
    lines.extend(
        f"  {item.name:<32} {item.decision.value:<18} {item.as_of or 'UNKNOWN'}"
        for item in decisions
    )
    return lines


def render_status(*, root: Path = PROJECT_ROOT) -> str:
    snapshot = inspect_research_frontier(root=root)
    lines = [
        "RESEARCH STATUS",
        "",
        "Framework",
        f"  {snapshot.framework}",
        "",
        "Current Stage",
        f"  {snapshot.latest_stage}",
        "",
        *_decision_lines("Factor Decisions", snapshot.factor_decisions),
        "",
        *_decision_lines("Policy Decisions", snapshot.policy_decisions),
        "",
        *_decision_lines("Portfolio / Risk / Decision Gate", snapshot.portfolio_risk_decisions),
        "",
        "Production Replacement",
        f"  {snapshot.production_replacement.decision.value}",
        f"  {snapshot.production_replacement.note}",
        "",
        "Production Readiness",
        f"  State: {snapshot.readiness.readiness}",
        f"  Conclusion: {snapshot.readiness.conclusion}",
        "  Open gaps: "
        + (
            str(snapshot.readiness.open_gap_count)
            if snapshot.readiness.open_gap_count is not None
            else "UNKNOWN"
        ),
        f"  Evidence identity: {snapshot.readiness.evidence_identity}",
        f"  Source: {snapshot.readiness.source_reference}",
        "",
        "As of",
        f"  {snapshot.as_of or 'UNKNOWN'}",
        "",
        "Sources",
    ]
    lines.extend(f"  {path.as_posix()}" for path in snapshot.source_artifacts)
    if not snapshot.source_artifacts:
        lines.append("  UNAVAILABLE")
    if snapshot.warnings:
        lines.extend(("", "Warnings"))
        lines.extend(f"  {warning}" for warning in snapshot.warnings)
    if snapshot.readiness.open_gaps:
        lines.extend(("", "Open Readiness Gaps"))
        lines.extend(
            f"  {gap.identifier}: {gap.severity}/{gap.state} — {gap.decision} — {gap.reason}"
            for gap in snapshot.readiness.open_gaps
        )
    if snapshot.readiness.limitations:
        lines.extend(("", "Readiness Limitations"))
        lines.extend(f"  {item}" for item in snapshot.readiness.limitations)
    return "\n".join(lines)


def run_status(*, root: Path = PROJECT_ROOT) -> int:
    print(render_status(root=root))
    return 0
