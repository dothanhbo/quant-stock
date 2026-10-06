"""Preview/apply policy metadata to legacy open paper positions.

Stops and targets are deliberately preserved. Only missing maximum holding
days is populated, so existing risk decisions are never silently loosened.

Target database (unchanged default): ``--database`` if given, otherwise
``PAPER_DATABASE_PATH``, otherwise the legacy generic ``data/paper_trading.db``.
That default is NOT the canonical active store selected by
``config.paper_store`` (Q70 → ``paper_trading_v2.db``, V3 →
``paper_trading_v3.db``). Since 2026-10-06 the script prints the resolved
target and refuses ``--apply`` against a database that is not the active
store unless that database was named explicitly with ``--database``.
Dry-run output is unchanged apart from the target banner.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import date
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> bool:
        return False

from config.paper_store import resolve_active_paper_store
from config.trading_policy import TradingPolicy


def _target_database(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    return Path(os.getenv("PAPER_DATABASE_PATH", "data/paper_trading.db"))


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--database",
        help="paper database to inspect/migrate; required for --apply on a non-active store",
    )
    args = parser.parse_args(argv)
    policy = TradingPolicy.from_env()
    path = _target_database(args.database)
    active = resolve_active_paper_store()
    is_active_store = path.resolve() == active.database_path.resolve()
    print(
        f"Target paper database: {path.resolve()} "
        f"({'ACTIVE ' + active.strategy_identity + ' store' if is_active_store else 'NOT the active store; active is ' + str(active.database_path)})"
    )
    if args.apply and not is_active_store and not args.database:
        print(
            "Refusing --apply: the implicit target is not the active paper store. "
            "Re-run with --database <path> to name the store you intend to modify."
        )
        return 2

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT symbol, entry_date, maximum_holding_days
            FROM paper_position_lifecycle ORDER BY symbol
            """
        ).fetchall()
        for row in rows:
            current = row["maximum_holding_days"]
            age = (date.today() - date.fromisoformat(row["entry_date"])).days
            action = "KEEP" if current is not None else "SET"
            print(
                f"{row['symbol']}: age={age}d, max_hold={current}, "
                f"action={action} {policy.maximum_holding_days if current is None else ''}"
            )
        if args.apply:
            connection.execute(
                """
                UPDATE paper_position_lifecycle
                SET maximum_holding_days = ?
                WHERE maximum_holding_days IS NULL
                """,
                (policy.maximum_holding_days,),
            )
            connection.commit()
            print("Applied. Existing stop/target values were preserved.")
        else:
            print("Dry-run only. Re-run with --apply after reviewing the list.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
