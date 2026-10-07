"""Register the current market.db as the initial LEGACY baseline (read-only).

The baseline is also registered automatically the first time the revision
admission guard runs; this command exists to do it explicitly and to print the
record. ``market.db`` is opened read-only: it is neither copied nor altered.
The only file written is the separate ``market_observations.db`` log.
"""

from __future__ import annotations

import argparse
import json

from core.market_observation_log import open_observation_log
from core.paths import resolve_market_database_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None, help="market database (default: resolved MARKET_DATABASE_PATH)")
    parser.add_argument("--log", default=None, help="observation log path (default: beside the market database)")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="read-only: verify the registered binding and lineage; register nothing",
    )
    args = parser.parse_args()

    market = resolve_market_database_path(args.db)
    if args.verify:
        log = open_observation_log(market, args.log, readonly=True, require_baseline=True)
        problems = log.verify_dataset(market)
        print(json.dumps({"verified": not problems, "problems": problems}, indent=2, sort_keys=True))
        return 0 if not problems else 1
    log = open_observation_log(market, args.log)
    baseline = log.ensure_initial_baseline(market)
    print(json.dumps(baseline, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
