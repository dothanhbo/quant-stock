"""List and explicitly resolve unresolved market admission blocks.

A block stays active (and gates Daily, scanner, lifecycle and forward) until a
later accepted observation supersedes it or a human records a reviewed
resolution here. Resolution only appends a row to the observation log; it never
modifies ``market.db`` and never rewrites history. Resolving a block does not
accept the revised data: the revised history stays unapplied.
"""

from __future__ import annotations

import argparse
import json

from core.market_observation_log import open_observation_log
from core.paths import resolve_market_database_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=None, help="market database (default: resolved MARKET_DATABASE_PATH)")
    parser.add_argument("--log", default=None, help="observation log path")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show unresolved blocks")
    resolve = sub.add_parser("resolve", help="record a reviewed resolution")
    resolve.add_argument("--block-id", type=int, required=True)
    resolve.add_argument("--reviewer", required=True)
    resolve.add_argument("--reason", required=True)
    args = parser.parse_args()

    market = resolve_market_database_path(args.db)
    log = open_observation_log(market, args.log, require_baseline=True)
    if args.command == "list":
        print(json.dumps(log.unresolved_blocks(), indent=2, sort_keys=True, default=str))
        return 0
    result = log.resolve_block(args.block_id, reviewer=args.reviewer, reason=args.reason)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
