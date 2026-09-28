from __future__ import annotations

from pathlib import Path

from quantctl.registry import PROJECT_ROOT, QUANTCTL_VERSION, inspect_git


def render(*, root: Path = PROJECT_ROOT) -> str:
    git = inspect_git(root=root)
    return "\n".join(
        (
            "QUANT SYSTEM VERSION",
            "",
            "Manager:",
            f"  quantctl: {QUANTCTL_VERSION}",
            "",
            "Repository:",
            f"  HEAD: {git.head or 'UNKNOWN'}",
            f"  Tag: {git.tag or 'UNKNOWN'}",
            f"  Working tree: {git.working_tree}",
        )
    )


def run(*, root: Path = PROJECT_ROOT) -> int:
    print(render(root=root))
    return 0
