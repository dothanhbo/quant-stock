from __future__ import annotations

"""Build a descriptive catalog/comparison from persisted Phase 12A snapshots."""

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quantlab.monitoring_history import evaluate_monitoring_history, write_monitoring_history_artifacts


DEFAULT_ROOT = PROJECT_ROOT / "research_results"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output-directory", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    root = arguments.snapshot_root.resolve(strict=True)
    if root != DEFAULT_ROOT.resolve():
        raise SystemExit("--snapshot-root must be the canonical research_results directory")
    result = evaluate_monitoring_history(root)
    valid = [item for item in result.catalog.snapshots if item.classification in {"VALID", "CONFLICTING_SESSION"}]
    latest = max((item.observed_market_session for item in valid), default="no-snapshot")
    output = arguments.output_directory or root / f"quantlab_monitoring_history_{latest}_{result.identity[:12]}"
    hashes = write_monitoring_history_artifacts(result, output)
    print(f"History state: {result.history_state}")
    print(f"Discovered: {result.catalog.discovered_count}; valid: {result.catalog.valid_count}; invalid: {result.catalog.invalid_count}; duplicates: {result.catalog.exact_duplicate_count}; conflicting-session snapshots: {result.catalog.conflicting_session_count}")
    print(f"Transitions: {len(result.transitions)}")
    print(f"Result identity: {result.identity}")
    print(f"Output: {Path(output).resolve()}")
    for name, digest in hashes.items():
        print(f"{name}: sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
