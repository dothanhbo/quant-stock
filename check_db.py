import sqlite3

connection = sqlite3.connect(
    "data/market.db"
)

latest_date = connection.execute(
    """
    SELECT MAX(substr(time, 1, 10))
    FROM prices
    """
).fetchone()

print(
    "Latest date:",
    latest_date,
)

connection.close()