from collections import Counter
from sqlalchemy import create_engine, text
import pandas as pd
from sqlalchemy import text
from datetime import datetime

# ==========================
# DATABASE CONFIG
# ==========================

DATABASE_URL = "sqlite:///market.db"

engine = create_engine(
    DATABASE_URL,
    echo=False
)

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
            MAX(time) AS latest_date
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
        return


    data = df.copy()


    # Chuẩn hóa tên cột
    data.columns = [
        c.lower()
        for c in data.columns
    ]
    # Convert Timestamp sang string
    data["time"] = data["time"].astype(str)


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
        SELECT MAX(time)
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