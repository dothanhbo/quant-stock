from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from core.database import (
    engine,
    get_reference_market_date,
    load_price_data,
)
from strategy.cache import (
    clear_indicator_cache,
    get_indicators_cached,
)


def main() -> None:
    symbol = "HPG"

    with engine.connect() as connection:
        total_rows = connection.execute(
            text(
                '''
                SELECT COUNT(*)
                FROM prices
                WHERE symbol = :symbol
                '''
            ),
            {"symbol": symbol},
        ).scalar_one()

        duplicate_days = connection.execute(
            text(
                '''
                SELECT date(time) AS trading_date, COUNT(*) AS row_count
                FROM prices
                WHERE symbol = :symbol
                GROUP BY date(time)
                HAVING COUNT(*) > 1
                ORDER BY trading_date DESC
                LIMIT 20
                '''
            ),
            {"symbol": symbol},
        ).fetchall()

    df = load_price_data(symbol)
    clear_indicator_cache()
    indicators = get_indicators_cached(symbol, df)

    print("=" * 68)
    print("MARKET DATA INTEGRITY CHECK")
    print("=" * 68)
    print(f"Reference date : {get_reference_market_date()}")
    print(f"{symbol} raw rows : {total_rows}")
    print(f"Duplicate days : {len(duplicate_days)}")

    if duplicate_days:
        for trading_date, count in duplicate_days:
            print(f"- {trading_date}: {count} rows")

    if df.empty:
        print(f"{symbol} raw latest : N/A")
    else:
        raw_latest = pd.to_datetime(df["time"], errors="coerce").max()
        print(f"{symbol} raw latest : {raw_latest:%Y-%m-%d}")

    if indicators.empty:
        print(f"{symbol} indicator latest : N/A")
    else:
        indicator_latest = pd.to_datetime(
            indicators["time"],
            errors="coerce",
        ).max()
        print(
            f"{symbol} indicator latest : "
            f"{indicator_latest:%Y-%m-%d}"
        )

    print("=" * 68)


if __name__ == "__main__":
    main()
