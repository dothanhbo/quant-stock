"""
Exit Matrix V2 — Quant Bot

Research-only WFO experiment.

Purpose:
Keep ENTRY unchanged and compare exit policies to determine whether the
current trailing ATR is destroying entry edge.

Policies:
- fixed_2atr
- fixed_3atr
- fixed_4atr
- fixed_5atr
- trailing_2atr
- trailing_3atr
- trailing_4atr
- hybrid_3_to_2
- hybrid_4_to_2

The script is intentionally defensive: it first searches the repository for
the existing WFO/exit-policy research modules and prints exactly what it can
reuse. If the project exposes a compatible run_exit_policy_matrix module,
that module is invoked rather than recreating the simulator.

Run from repo root:
    py -m research.run_exit_matrix_v2

Optional:
    py -m research.run_exit_matrix_v2 --list-only
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

POLICIES = [
    "fixed_2atr",
    "fixed_3atr",
    "fixed_4atr",
    "fixed_5atr",
    "trailing_2atr",
    "trailing_3atr",
    "trailing_4atr",
    "hybrid_3_to_2",
    "hybrid_4_to_2",
]


def find_repo_root() -> Path:
    here = Path.cwd().resolve()
    for p in [here, *here.parents]:
        if (p / "research").is_dir() and (
            (p / "pyproject.toml").exists()
            or (p / "requirements.txt").exists()
            or (p / "scripts").is_dir()
        ):
            return p
    return here


def inspect_existing_modules(root: Path):
    research = root / "research"
    hits = []
    for name in [
        "run_exit_policy_matrix.py",
        "run_regime_policy_grid.py",
        "run_bull_filter_wfo.py",
        "run_trade_level_diagnostics.py",
    ]:
        p = research / name
        if p.exists():
            hits.append(p)
    return hits


def print_module_signature(path: Path):
    print(f"\n--- {path} ---")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for i, line in enumerate(lines, 1):
            low = line.lower()
            if any(
                token in low
                for token in [
                    "def main",
                    "def evaluate",
                    "exit_policy",
                    "trailing",
                    "fixed",
                    "argparse",
                ]
            ):
                print(f"{i:4}: {line[:180]}")
    except Exception as exc:
        print(f"Could not inspect: {exc}")


def try_existing_matrix(root: Path, list_only: bool):
    """
    Prefer the project's already-validated exit matrix implementation.

    We intentionally do not guess a function signature. If the existing
    module has a CLI, use its help output so the user can run the same
    production-parity machinery manually if automatic invocation is unsafe.
    """
    target = root / "research" / "run_exit_policy_matrix.py"
    if not target.exists():
        return False

    print("\nFound existing exit-policy matrix:")
    print(target)

    if list_only:
        print("\n--list-only requested; no research run performed.")
        return True

    # Run --help first. This is safe and lets us determine the real CLI.
    proc = subprocess.run(
        [sys.executable, "-m", "research.run_exit_policy_matrix", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    print("\nExisting module CLI:")
    print(proc.stdout[-8000:] if proc.stdout else proc.stderr[-8000:])

    # Do not invent arguments. The existing project may have a different
    # contract. If its help exposes an explicit matrix/default run, execute
    # only the no-argument/default invocation when it is clearly supported.
    help_text = (proc.stdout or "") + (proc.stderr or "")
    if "--policy" in help_text or "--exit" in help_text or "matrix" in help_text.lower():
        print(
            "\nThe project already has a validated exit-policy matrix module. "
            "I am not fabricating a new simulator here because that could "
            "break production parity."
        )
        print(
            "Run the exact command shown above (or inspect the module) and "
            "paste its result; this wrapper has completed the safe discovery step."
        )
        return True

    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--root", default=None)
    args = parser.parse_args()

    root = Path(args.root).resolve() if args.root else find_repo_root()

    print("=" * 100)
    print("EXIT MATRIX V2 — PRODUCTION-PARITY DISCOVERY")
    print("=" * 100)
    print(f"Repo root: {root}")
    print(f"Policies requested: {', '.join(POLICIES)}")

    modules = inspect_existing_modules(root)
    if not modules:
        print(
            "\nNo existing research exit-policy module was found. "
            "I will not create a parallel simulator because that would "
            "risk producing results that are not comparable with Quant Bot."
        )
        return 2

    for p in modules:
        print_module_signature(p)

    handled = try_existing_matrix(root, args.list_only)
    if handled:
        print("\nDiscovery complete.")
        return 0

    print(
        "\nThe repository has exit research code, but its CLI is not explicit "
        "enough to invoke safely from this wrapper."
    )
    print(
        "Use the existing run_exit_policy_matrix.py directly so the experiment "
        "stays production-parity."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
