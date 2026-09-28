from __future__ import annotations

"""Create one read-only Quant Lab operational monitoring snapshot."""

import argparse
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from quantlab.monitoring import (
    DEFAULT_LEDGER_PATH,
    DEFAULT_REFERENCE_ROOT,
    collect_monitoring_snapshot,
    load_frozen_monitoring_references,
    write_monitoring_artifacts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-path", type=Path, default=None)
    parser.add_argument("--ledger-path", type=Path, default=DEFAULT_LEDGER_PATH)
    parser.add_argument("--output-directory", type=Path, default=None)
    parser.add_argument("--observed-at-utc", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    # Validate and load the frozen reference chain before any output directory exists.
    references = load_frozen_monitoring_references(DEFAULT_REFERENCE_ROOT)
    result = collect_monitoring_snapshot(
        database_path=arguments.database_path,
        ledger_path=arguments.ledger_path,
        observed_at_utc=arguments.observed_at_utc,
        references=references,
    )
    session = result.snapshot.observed_market_session or "unknown"
    timestamp = result.snapshot.observed_at_utc.replace(":", "").replace("-", "")
    if timestamp.endswith("Z"):
        timestamp = timestamp[:-1]
    output = arguments.output_directory or (
        DEFAULT_REFERENCE_ROOT / f"quantlab_monitoring_snapshot_{session}_{timestamp}"
    )
    hashes = write_monitoring_artifacts(result, output)
    print(f"Monitoring status: {result.snapshot.overall_status.value}")
    print(f"Market session: {session}")
    print(f"Snapshot identity: {result.snapshot.identity}")
    print(f"Output: {Path(output).resolve()}")
    for name, digest in hashes.items():
        print(f"{name}: sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
