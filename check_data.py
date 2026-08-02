from sqlalchemy import text
import pandas as pd

from core.database import engine


with engine.connect() as conn:

    symbols = pd.read_sql(
        text(
            """
            SELECT DISTINCT symbol
            FROM prices
            ORDER BY symbol
            """
        ),
        conn,
    )["symbol"].tolist()

    rows = []

    for symbol in symbols:

        df = pd.read_sql(
            text(
                """
                SELECT time
                FROM prices
                WHERE symbol = :symbol
                ORDER BY time
                """
            ),
            conn,
            params={
                "symbol": symbol,
            },
        )

        if df.empty:
            print(f"{symbol:5} | NO DATA")
            continue

        df["time"] = pd.to_datetime(df["time"])

        row_count = len(df)

        rows.append(row_count)

        print(
            f"{symbol:5} | "
            f"{row_count:4} rows | "
            f"{df.iloc[0]['time'].date()} -> "
            f"{df.iloc[-1]['time'].date()}"
        )

print()

print("=" * 60)

print(f"Symbols : {len(rows)}")

print(f"Min rows : {min(rows)}")

print(f"Max rows : {max(rows)}")

print(f"Average : {sum(rows)/len(rows):.0f}")

print("=" * 60)