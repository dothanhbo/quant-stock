from __future__ import annotations

from pathlib import Path

from quantctl.registry import PROJECT_ROOT, discover_active_runners


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
