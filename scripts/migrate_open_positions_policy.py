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

Since the 2026-10-06 review (P1-3) a missing holding value is NEVER inferred
from today's ``TradingPolicy`` default: the canonical default changed from 30
to 20 sessions, and neither value is evidence of what an older position ran
with. A NULL ``maximum_holding_days`` is filled only from that position's own
persisted entry evidence (the frozen ``execution_context.lifecycle`` of its
entry order). Positions without such evidence are reported as
``NO_EVIDENCE`` and left NULL; there is no default-derived backfill.
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

import json

from config.paper_store import resolve_active_paper_store


def _target_database(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    return Path(os.getenv("PAPER_DATABASE_PATH", "data/paper_trading.db"))


def _recorded_entry_holding(
    connection: sqlite3.Connection,
    entry_order_id: object,
) -> int | None:
    """Holding frozen on this position's own entry order, if persisted."""
    if not entry_order_id:
        return None
    order_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(paper_orders)")
    }
    if not {"client_order_id", "execution_context"} <= order_columns:
        return None
    row = connection.execute(
        "SELECT execution_context FROM paper_orders WHERE client_order_id = ?",
        (str(entry_order_id),),
    ).fetchone()
    if row is None or not row[0]:
        return None
    try:
        value = json.loads(str(row[0])).get("lifecycle", {}).get("maximum_holding_days")
    except (AttributeError, ValueError):
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--database",
        help="paper database to inspect/migrate; required for --apply on a non-active store",
    )
    args = parser.parse_args(argv)
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
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(paper_position_lifecycle)")
        }
        has_entry_order = "entry_order_id" in columns
        rows = connection.execute(
            f"""
            SELECT symbol, entry_date, maximum_holding_days,
                   {'entry_order_id' if has_entry_order else 'NULL AS entry_order_id'}
            FROM paper_position_lifecycle ORDER BY symbol
            """
        ).fetchall()
        updates: list[tuple[int, str]] = []
        for row in rows:
            current = row["maximum_holding_days"]
            age = (date.today() - date.fromisoformat(row["entry_date"])).days
            if current is not None:
                print(f"{row['symbol']}: age={age}d, max_hold={current}, action=KEEP")
                continue
            evidence = _recorded_entry_holding(connection, row["entry_order_id"])
            if evidence is None:
                print(
                    f"{row['symbol']}: age={age}d, max_hold=None, action=LEAVE_NULL "
                    "(NO_EVIDENCE: no persisted entry-order holding; today's default "
                    "is not historical evidence)"
                )
                continue
            updates.append((evidence, str(row["symbol"])))
            print(
                f"{row['symbol']}: age={age}d, max_hold=None, action=SET {evidence} "
                f"(RECORDED_ENTRY_EVIDENCE: order {row['entry_order_id']})"
            )
        if args.apply:
            connection.executemany(
                """
                UPDATE paper_position_lifecycle
                SET maximum_holding_days = ?
                WHERE symbol = ? AND maximum_holding_days IS NULL
                """,
                updates,
            )
            connection.commit()
            print(
                f"Applied {len(updates)} evidence-backed value(s). Positions without "
                "evidence were left NULL. Existing stop/target values were preserved."
            )
        else:
            print("Dry-run only. Re-run with --apply after reviewing the list.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
