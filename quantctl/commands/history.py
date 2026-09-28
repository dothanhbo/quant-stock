from __future__ import annotations

from pathlib import Path

from quantctl.registry import PROJECT_ROOT
from quantctl.run_history import OperationHistoryStore, RunDetail, RunSummary, history_path


def _duration(value: int | None) -> str:
    if value is None:
        return "UNKNOWN"
    seconds = value / 1000
    return f"{seconds:.1f}s" if seconds < 60 else f"{int(seconds // 60)}m {seconds % 60:.1f}s"


def _summary_lines(run: RunSummary) -> list[str]:
    lines = [
        run.started_at_utc,
        run.operation.upper(),
        run.status.value,
        f"Duration: {_duration(run.duration_ms)}",
    ]
    if run.failed_step:
        lines.append(f"Failed step: {run.failed_step}")
    lines.append(f"Run: {run.run_id}")
    return lines


def render_list(*, root: Path = PROJECT_ROOT, limit: int = 20) -> str:
    runs = OperationHistoryStore(history_path(root=root)).list_runs(limit)
    lines = ["RUN HISTORY", ""]
    if not runs:
        lines.append("No operational runs recorded.")
        return "\n".join(lines)
    for index, run in enumerate(runs):
        if index:
            lines.append("")
        lines.extend(_summary_lines(run))
    return "\n".join(lines)


def _detail_lines(detail: RunDetail) -> list[str]:
    run = detail.summary
    lines = [
        "RUN DETAIL",
        "",
        f"Run: {run.run_id}",
        f"Operation: {run.operation.upper()}",
        f"Status: {run.status.value}",
        f"Started: {run.started_at_utc}",
        f"Finished: {run.finished_at_utc or 'UNKNOWN'}",
        f"Duration: {_duration(run.duration_ms)}",
        f"Exit code: {run.exit_code if run.exit_code is not None else 'UNKNOWN'}",
        "Capabilities: " + (", ".join(run.capabilities) or "NONE"),
        "",
        "Steps",
    ]
    if detail.steps:
        lines.extend(
            f"  {item.sequence}. {item.step_name}: {item.status.value} ({_duration(item.duration_ms)})"
            for item in detail.steps
        )
    else:
        lines.append("  UNAVAILABLE")
    if run.error_message or run.failed_step:
        lines.extend(("", "Failure"))
        if run.failed_step:
            lines.append(f"  Step: {run.failed_step}")
        if run.error_type:
            lines.append(f"  Type: {run.error_type}")
        if run.error_message:
            lines.append(f"  {run.error_message}")
    return lines


def render_show(run_id: str, *, root: Path = PROJECT_ROOT) -> str:
    detail = OperationHistoryStore(history_path(root=root)).get_run(run_id)
    if detail is None:
        return f"RUN DETAIL\n\nRun not found: {run_id}"
    return "\n".join(_detail_lines(detail))


def run_list(*, root: Path = PROJECT_ROOT, limit: int = 20) -> int:
    print(render_list(root=root, limit=limit))
    return 0


def run_show(run_id: str, *, root: Path = PROJECT_ROOT) -> int:
    detail = OperationHistoryStore(history_path(root=root)).get_run(run_id)
    print(render_show(run_id, root=root))
    return 0 if detail is not None else 1
