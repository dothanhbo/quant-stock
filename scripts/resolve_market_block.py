"""List and explicitly resolve unresolved market admission blocks.

A block stays active (and gates Daily, scanner, lifecycle and forward) until a
later accepted observation supersedes it or a human records a reviewed
resolution here.

``resolve`` has two actions:

* ``--action review`` (the default, unchanged behaviour): only appends a
  resolution row to the observation log; it never modifies ``market.db`` and
  never rewrites history. The revised history stays unapplied, so a provider
  that keeps proposing it (a back-adjusted corporate action) blocks again.
* ``--action rebase`` (V1 P1-OPS-1, never the default): a reviewed per-symbol
  rebase of ONE unresolved ``BLOCKED_REVISION_CHANGED`` block. It replaces only
  that symbol's changed sessions with the exact rows of the blocked observation
  (already persisted in the log; nothing is fetched), advances the dataset
  version once and resolves the block only after the market change committed.
  It requires the block id, its observation id, ``--confirm-symbol`` and
  ``--category CORPORATE_ACTION_REBASE``; ``--dry-run`` prints the exact plan
  without changing anything. The observation must cover the symbol's whole
  stored history (no partial basis seam in V1). Every canonical paper store is
  always checked for open exposure; ``--paper-db`` only ADDS stores. The operator asserts legitimacy; the system does
  not prove it. See docs/audit/2026-10-07-v1-operational-closure-audit.md
  ("CORPORATE ACTION / REBASE RUNBOOK").

``recover-rebase --symbol SYM`` settles an interrupted rebase without starting a
new one (finalize if its rows are in the market, abandon if the old basis is
intact). While a rebase intent is open, ``resolve --action review`` of its block
is refused (``BLOCK_OWNED_BY_PENDING_REBASE``).

Exit codes: 0 done (or dry run eligible), 2 refused (nothing changed).
"""

from __future__ import annotations

import argparse
import json
import sys

from core.market_observation_log import ObservationLogError, open_observation_log
from core.paths import resolve_market_database_path

ACTION_REVIEW = "review"
ACTION_REBASE = "rebase"
_REBASE_ONLY = ("observation_id", "confirm_symbol", "category", "note", "paper_db", "dry_run")


def _print(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _refused(code: str, message: str, detail: object = None) -> int:
    _print({"status": "REFUSED", "code": code, "message": message, "detail": detail or {}})
    return 2


def _rebase(args: argparse.Namespace, market) -> int:
    from core.market_rebase import RebaseRefused, preview_rebase, rebase_symbol

    missing = [
        flag
        for flag, value in (
            ("--observation-id", args.observation_id),
            ("--confirm-symbol", args.confirm_symbol),
            ("--category", args.category),
        )
        if not value
    ]
    if missing:
        return _refused("REBASE_ARGUMENTS_REQUIRED", f"--action rebase requires {', '.join(missing)}")
    paper_paths = tuple(args.paper_db or ())  # supplemental: canonical stores are always checked
    try:
        if args.dry_run:
            plan = preview_rebase(
                market,
                symbol=args.confirm_symbol,
                block_id=args.block_id,
                observation_id=args.observation_id,
                paper_paths=paper_paths,
                log_path=args.log,
            )
            _print({"status": "ELIGIBLE_DRY_RUN", "market_db_modified": False, "plan": plan.summary()})
            return 0
        outcome = rebase_symbol(
            market,
            symbol=args.confirm_symbol,
            block_id=args.block_id,
            observation_id=args.observation_id,
            category=args.category,
            reviewer=args.reviewer,
            reason=args.reason,
            note=args.note,
            paper_paths=paper_paths,
            log_path=args.log,
        )
    except RebaseRefused as error:
        return _refused(error.code, str(error), error.detail)
    if outcome.recovered:
        status = "RECOVERED"
    elif outcome.already_applied:
        status = "ALREADY_REBASED"
    else:
        status = "REBASED"
    _print({"status": status, "symbol": outcome.symbol, "block_id": outcome.block_id, "rebase": dict(outcome.rebase)})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=None, help="market database (default: resolved MARKET_DATABASE_PATH)")
    parser.add_argument("--log", default=None, help="observation log path")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show unresolved blocks")
    rebases = sub.add_parser("rebases", help="show recorded reviewed rebases")
    rebases.add_argument("--symbol", default=None)
    recover = sub.add_parser("recover-rebase", help="settle an interrupted reviewed rebase (no new rebase)")
    recover.add_argument("--symbol", required=True)
    resolve = sub.add_parser("resolve", help="record a reviewed resolution (or, explicitly, a reviewed rebase)")
    resolve.add_argument("--block-id", type=int, required=True)
    resolve.add_argument("--reviewer", required=True)
    resolve.add_argument("--reason", required=True)
    resolve.add_argument(
        "--action",
        choices=(ACTION_REVIEW, ACTION_REBASE),
        default=ACTION_REVIEW,
        help="review (default): record the review only; rebase: reviewed per-symbol rebase",
    )
    resolve.add_argument("--observation-id", default=None, help="rebase: the blocked observation id (from `list`)")
    resolve.add_argument("--confirm-symbol", default=None, help="rebase: the block's symbol, typed by the operator")
    resolve.add_argument("--category", default=None, help="rebase: CORPORATE_ACTION_REBASE")
    resolve.add_argument("--note", default=None, help="rebase: optional note (e.g. the external evidence reviewed)")
    resolve.add_argument(
        "--paper-db",
        action="append",
        default=None,
        help="rebase: ADDITIONAL paper store(s) to check; the canonical stores are always checked",
    )
    resolve.add_argument("--dry-run", action="store_true", help="rebase: print the exact plan, change nothing")
    args = parser.parse_args(argv)

    market = resolve_market_database_path(args.db)
    if args.command == "resolve" and args.action == ACTION_REBASE:
        return _rebase(args, market)
    if args.command == "resolve" and any(getattr(args, name) for name in _REBASE_ONLY):
        return _refused("REBASE_OPTIONS_WITHOUT_REBASE_ACTION", "rebase-only options need --action rebase; nothing done")
    if args.command == "recover-rebase":
        from core.market_rebase import RebaseRefused, recover_rebase

        try:
            _print({"status": "SETTLED", "intents": recover_rebase(market, symbol=args.symbol, log_path=args.log)})
        except RebaseRefused as error:
            return _refused(error.code, str(error), error.detail)
        return 0
    log = open_observation_log(market, args.log, require_baseline=True)
    if args.command == "list":
        _print(log.unresolved_blocks())
        return 0
    if args.command == "rebases":
        _print(log.symbol_rebases(None if args.symbol is None else str(args.symbol).strip().upper()))
        return 0
    try:
        result = log.resolve_block(args.block_id, reviewer=args.reviewer, reason=args.reason)
    except ObservationLogError as error:
        return _refused(getattr(error, "code", "RESOLUTION_REFUSED"), str(error))
    _print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
