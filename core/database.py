from collections import Counter
from sqlalchemy import create_engine, text
import pandas as pd
from datetime import datetime
from pathlib import Path
# ==========================
# DATABASE CONFIG
# ==========================

DATABASE_PATH = (
    Path(__file__)
    .resolve()
    .parent
    .parent
    / "data"
    / "market.db"
)

DATABASE_PATH.parent.mkdir(
    parents=True,
    exist_ok=True,
)

DATABASE_URL = (
    f"sqlite:///{DATABASE_PATH.as_posix()}"
)

engine = create_engine(
    DATABASE_URL,
    echo=False
)


def _normalize_trading_date(value) -> str | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.strftime("%Y-%m-%d")

def ensure_price_unique_index() -> bool:
    """
    Chỉ tạo UNIQUE index khi database đã sạch duplicate.
    """
    with engine.begin() as conn:
        duplicate_groups = conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT symbol, time
                    FROM prices
                    GROUP BY symbol, time
                    HAVING COUNT(*) > 1
                )
                """
            )
        ).scalar()

        if duplicate_groups:
            return False

        conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                ux_prices_symbol_time
                ON prices(symbol, time)
                """
            )
        )

    return True


def cleanup_price_duplicates() -> dict[str, int]:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                '''
                SELECT id, symbol, time, open, high, low, close, volume
                FROM prices
                ORDER BY symbol ASC, id ASC
                '''
            )
        ).mappings().all()

        latest_by_day = {}
        for row in rows:
            trading_date = _normalize_trading_date(row["time"])
            if trading_date is None:
                continue
            latest_by_day[(str(row["symbol"]), trading_date)] = {
                "symbol": str(row["symbol"]),
                "time": trading_date,
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
            }

        conn.execute(text("DELETE FROM prices"))

        if latest_by_day:
            conn.execute(
                text(
                    '''
                    INSERT INTO prices (
                        symbol, time, open, high, low, close, volume
                    )
                    VALUES (
                        :symbol, :time, :open, :high, :low, :close, :volume
                    )
                    '''
                ),
                list(latest_by_day.values()),
            )

    before = len(rows)
    after = len(latest_by_day)

    ensure_price_unique_index()

    return {
        "before": before,
        "after": after,
        "removed": before - after,
    }



def get_symbol_latest_dates():
    """
    Lấy ngày dữ liệu mới nhất của từng mã.

    Return:
        {
            "ACB": "2026-07-28",
            "FPT": "2026-07-28",
            "HPG": "2026-07-27"
        }
    """

    query = text("""
        SELECT
            symbol,
            MAX(date(time)) AS latest_date
        FROM prices
        GROUP BY symbol
        ORDER BY symbol
    """)

    with engine.connect() as connection:
        rows = connection.execute(query).fetchall()

    result = {}

    for symbol, latest_date in rows:
        parsed_date = pd.to_datetime(
            latest_date,
            errors="coerce"
        )

        if pd.isna(parsed_date):
            continue

        result[str(symbol)] = parsed_date.strftime(
            "%Y-%m-%d"
        )

    return result


def get_reference_market_date(
    exclude_symbols=None
):
    """
    Lấy ngày dữ liệu phổ biến nhất trong database.

    Không dùng MAX(time), vì một mã có dữ liệu bất thường
    có thể làm sai ngày chuẩn.
    """

    if exclude_symbols is None:
        exclude_symbols = {"VNINDEX"}

    latest_dates = get_symbol_latest_dates()

    valid_dates = [
        latest_date
        for symbol, latest_date in latest_dates.items()
        if symbol not in exclude_symbols
    ]

    if not valid_dates:
        return None

    date_counts = Counter(valid_dates)

    reference_date, _ = date_counts.most_common(1)[0]

    return reference_date

# ==========================
# CREATE TABLE
# ==========================

def init_database():

    query = """
    CREATE TABLE IF NOT EXISTS prices (

        id INTEGER PRIMARY KEY AUTOINCREMENT,

        symbol TEXT NOT NULL,

        time TEXT NOT NULL,

        open REAL,

        high REAL,

        low REAL,

        close REAL,

        volume INTEGER,

        UNIQUE(symbol, time)
    )
    """

    with engine.connect() as conn:

        conn.execute(
            text(query)
        )

        conn.commit()



# ==========================
# SAVE DATA
# ==========================

def save_price_data(df):

    if df.empty:
        return 0

    data = df.copy()

    # Chuẩn hóa tên cột
    data.columns = [
        c.lower()
        for c in data.columns
    ]

    parsed_time = pd.to_datetime(
        data["time"],
        errors="coerce",
    )

    if parsed_time.isna().any():
        invalid_count = int(parsed_time.isna().sum())
        raise ValueError(
            f"Có {invalid_count} dòng time không hợp lệ."
        )

    data["time"] = parsed_time.dt.strftime(
        "%Y-%m-%d"
    )

    data = (
        data
        .drop_duplicates(
            subset=["symbol", "time"],
            keep="last",
        )
        .reset_index(drop=True)
    )

    required = [
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "symbol"
    ]

    for col in required:
        if col not in data.columns:
            raise Exception(
                f"Missing column: {col}"
            )

    numeric_columns = ["open", "high", "low", "close", "volume"]
    for column in numeric_columns:
        data[column] = pd.to_numeric(data[column], errors="coerce")

    invalid_ohlc = (
        data[numeric_columns].isna().any(axis=1)
        | (data[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | (data["volume"] < 0)
        | (data["high"] < data["low"])
        | (data["open"] > data["high"])
        | (data["open"] < data["low"])
        | (data["close"] > data["high"])
        | (data["close"] < data["low"])
    )
    if invalid_ohlc.any():
        bad = data.loc[invalid_ohlc, ["symbol", "time"]]
        sample = ", ".join(
            f"{row.symbol}@{row.time}" for row in bad.head(5).itertuples()
        )
        raise ValueError(
            f"Có {int(invalid_ohlc.sum())} dòng OHLC không hợp lệ: {sample}"
        )

    records = data[
        required
    ].to_dict(
        orient="records"
    )

    insert_sql = """
        INSERT OR REPLACE INTO prices
        (
            symbol,
            time,
            open,
            high,
            low,
            close,
            volume
        )
        VALUES
        (
            :symbol,
            :time,
            :open,
            :high,
            :low,
            :close,
            :volume
        )
    """

    with engine.connect() as conn:
        conn.execute(
            text(insert_sql),
            records
        )
        conn.commit()

    return len(records)

# ==========================
# LOAD DATA
# ==========================

def load_price_data(symbol):


    query = """

    SELECT *

    FROM prices

    WHERE symbol = :symbol

    ORDER BY time ASC

    """


    return pd.read_sql(

        text(query),

        engine,

        params={
            "symbol": symbol
        }

    )

# ==========================
# CREATE SIGNAL TABLE
# ==========================

def create_signal_table():

    with engine.begin() as conn:

        conn.execute(text("""
        CREATE TABLE IF NOT EXISTS signals(

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            signal_date TEXT,

            symbol TEXT,

            score REAL,

            entry REAL,

            stop_loss REAL,

            take_profit REAL,

            rsi REAL,

            adx REAL,

            volume_ratio REAL,

            relative_strength REAL,

            status TEXT,

            result REAL,

            holding_days INTEGER

        )
        """))

def get_latest_price_date(symbol):
    """
    Trả về ngày dữ liệu mới nhất của một mã trong SQLite.

    Kết quả:
    - datetime nếu đã có dữ liệu
    - None nếu mã chưa có dữ liệu
    """

    query = text("""
        SELECT MAX(date(time))
        FROM prices
        WHERE symbol = :symbol
    """)

    with engine.connect() as connection:
        latest_time = connection.execute(
            query,
            {"symbol": symbol}
        ).scalar()

    if not latest_time:
        return None

    try:
        return datetime.fromisoformat(
            str(latest_time)
        )

    except ValueError:
        return pd.to_datetime(
            latest_time,
            errors="coerce"
        )

# ==========================
# INIT WHEN IMPORT
# ==========================

init_database()
create_signal_table()
