import sqlite3

from scripts.audit_market_price_parity import load_latest_prices


def test_price_audit_loads_latest_non_index_prices(tmp_path):
    db = tmp_path / "market.db"
    with sqlite3.connect(db) as connection:
        connection.execute(
            """
            CREATE TABLE prices (
                symbol TEXT, time TEXT, open REAL, high REAL,
                low REAL, close REAL, volume REAL
            )
            """
        )
        connection.executemany(
            "INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("AAA", "2026-08-25", 10, 11, 9, 10.5, 1_000),
                ("VNINDEX", "2026-08-25", 1, 1, 1, 1, 1_000),
                ("AAA", "2026-08-24", 9, 10, 8, 9.5, 1_000),
            ],
        )
    result = load_latest_prices(db)
    assert result["symbol"].tolist() == ["AAA"]
    assert result["close"].tolist() == [10.5]
