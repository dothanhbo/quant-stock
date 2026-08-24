"""Preview/apply policy metadata to legacy open paper positions.

Stops and targets are deliberately preserved. Only missing maximum holding
days is populated, so existing risk decisions are never silently loosened.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import date

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> bool:
        return False

from config.trading_policy import TradingPolicy


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    policy = TradingPolicy.from_env()
    path = os.getenv("PAPER_DATABASE_PATH", "data/paper_trading.db")

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


if __name__ == "__main__":
    main()
