import sqlite3

import pandas as pd

from backtesting.engine import BacktestConfig, generate_candidate_trades
from backtesting.prepared_data import load_backtest_price_data


def create_db(path, close_value: float) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE prices (
                symbol TEXT,
                time TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL
            )
            """
        )
        connection.execute(
            "INSERT INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("AAA", "2026-08-21", 10.0, 12.0, 9.0, close_value, 1000),
        )


def test_explicit_db_path_cannot_silently_read_default_db(tmp_path) -> None:
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"
    create_db(first, 11.0)
    create_db(second, 99.0)

    first_data = load_backtest_price_data("AAA", db_path=str(first))
    second_data = load_backtest_price_data("AAA", db_path=str(second))

    assert float(first_data.iloc[0]["close"]) == 11.0
    assert float(second_data.iloc[0]["close"]) == 99.0
    assert pd.api.types.is_datetime64_any_dtype(first_data["time"])


def test_engine_forwards_db_path_to_prepared_dataset(monkeypatch) -> None:
    captured = {}

    def fake_prepare(symbol, **kwargs):
        captured["symbol"] = symbol
        captured.update(kwargs)
        return pd.DataFrame()

    monkeypatch.setattr(
        "backtesting.engine.prepare_backtest_dataset",
        fake_prepare,
    )

    result = generate_candidate_trades(
        "AAA",
        BacktestConfig(),
        db_path="explicit-research.db",
    )

    assert result == []
    assert captured["symbol"] == "AAA"
    assert captured["db_path"] == "explicit-research.db"
