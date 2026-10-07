"""Audit and optionally quarantine structurally invalid OHLC rows."""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> bool:
        return False


INVALID = """
    open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL OR volume IS NULL
    OR open <= 0 OR high <= 0 OR low <= 0 OR close <= 0 OR volume < 0
    OR high < low OR open > high OR open < low OR close > high OR close < low
"""


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.getenv("MARKET_DATABASE_PATH", "data/market.db"))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="DISABLED in V1: deleting history would not advance the dataset version.",
    )
    args = parser.parse_args()
    if args.apply:
        print(
            "--apply is disabled in V1: quarantining/deleting rows would change "
            "market.db without advancing the versioned provenance lineage. "
            "Nothing was modified."
        )
        return 2
    uri = Path(args.db).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"SELECT * FROM prices WHERE {INVALID} ORDER BY symbol, time"
        ).fetchall()
    finally:
        connection.close()
    print(f"Invalid OHLC rows: {len(rows)}")
    for row in rows[:30]:
        print(
            f"{row['symbol']} {row['time']} O={row['open']} H={row['high']} "
            f"L={row['low']} C={row['close']} V={row['volume']}"
        )
    print("Dry-run only (the database was opened read-only; apply is disabled in V1).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
