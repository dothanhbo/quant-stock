"""Audit and optionally quarantine structurally invalid OHLC rows."""

from __future__ import annotations

import argparse
import os
import sqlite3

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


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.getenv("MARKET_DATABASE_PATH", "data/market.db"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with sqlite3.connect(args.db) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"SELECT * FROM prices WHERE {INVALID} ORDER BY symbol, time"
        ).fetchall()
        print(f"Invalid OHLC rows: {len(rows)}")
        for row in rows[:30]:
            print(
                f"{row['symbol']} {row['time']} O={row['open']} H={row['high']} "
                f"L={row['low']} C={row['close']} V={row['volume']}"
            )
        if not args.apply:
            print("Dry-run only. Use --apply to quarantine and remove from prices.")
            return
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS prices_quarantine AS
            SELECT *, '' AS quarantine_reason, '' AS quarantined_at
            FROM prices WHERE 0
            """
        )
        connection.execute(
            f"""
            INSERT INTO prices_quarantine
            SELECT *, 'INVALID_OHLC', datetime('now') FROM prices WHERE {INVALID}
            """
        )
        connection.execute(f"DELETE FROM prices WHERE {INVALID}")
        connection.commit()
        print(f"Quarantined and removed {len(rows)} rows. Backup remains in prices_quarantine.")


if __name__ == "__main__":
    main()
