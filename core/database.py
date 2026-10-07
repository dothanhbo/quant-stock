from collections import Counter
from sqlalchemy import create_engine, text
import pandas as pd
from datetime import datetime
from core.paths import resolve_market_database_path
# ==========================
# DATABASE CONFIG
# ==========================

DATABASE_PATH = resolve_market_database_path()

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


class UnguardedHistoryRewriteError(RuntimeError):
    """Duplicate groups hold conflicting values (a revision decision, not cleanup)."""


class MaintenanceDisabledError(RuntimeError):
    """A history-mutating maintenance path was invoked while V1 has it disabled."""


MAINTENANCE_DISABLED_MESSAGE = (
    "history-mutating maintenance is disabled in V1: a deletion in market.db "
    "would change the dataset without advancing the versioned provenance "
    "lineage. Analysis (dry-run) remains available; rebuild/repair is deferred "
    "(R4)."
)


def analyze_price_duplicates() -> dict[str, int]:
    """Read-only duplicate analysis; never modifies ``market.db``.

    Counts exactly redundant duplicate rows (identical canonical OHLCV) and
    groups whose duplicates conflict. Maintenance that would delete rows is
    disabled in V1 (``MaintenanceDisabledError``) because it would change the
    dataset without advancing the dataset version.
    """
    from core.market_observation_log import canonical_row

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                '''
                SELECT id, symbol, time, open, high, low, close, volume
                FROM prices
                ORDER BY symbol ASC, id ASC
                '''
            )
        ).mappings().all()

    groups: dict[tuple[str, str], list[dict]] = {}
    unnormalized = 0
    for row in rows:
        trading_date = _normalize_trading_date(row["time"])
        if trading_date is None:
            continue
        if str(row["time"]) != trading_date:
            unnormalized += 1
        groups.setdefault((str(row["symbol"]), trading_date), []).append(dict(row))

    redundant = 0
    conflicts: list[str] = []
    for (symbol, trading_date), members in groups.items():
        if len(members) < 2:
            continue
        tuples = set()
        for member in members:
            try:
                tuples.add(
                    canonical_row(
                        member["open"],
                        member["high"],
                        member["low"],
                        member["close"],
                        member["volume"],
                    )
                )
            except (TypeError, ValueError):
                tuples.add(("INVALID", str(member["id"])))
        if len(tuples) > 1:
            conflicts.append(f"{symbol}@{trading_date}")
            continue
        redundant += len(members) - 1

    return {
        "rows": len(rows),
        "redundant_identical_rows": redundant,
        "conflicting_groups": len(conflicts),
        "conflict_examples": conflicts[:5],
        "unnormalized_time_rows": unnormalized,
    }


def cleanup_price_duplicates(*, apply: bool = False) -> dict[str, int]:
    """Analyze duplicates (dry-run). ``apply=True`` is disabled in V1.

    The previous implementation deleted and re-inserted the whole table;
    the first R1/R2 revision deleted redundant rows. Either mutates stored
    history without advancing the dataset version, so applying is disabled
    (fail closed) and nothing is ever written here.
    """
    if apply:
        raise MaintenanceDisabledError(MAINTENANCE_DISABLED_MESSAGE)
    return analyze_price_duplicates()



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

    DATABASE_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

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

def save_price_data(df, *, context, symbol=None):
    """Hand one symbol's provider batch to the revision admission guard.

    This is the only supported way to write provider prices. It no longer does
    ``INSERT OR REPLACE``: historical rows are never overwritten. See
    :mod:`core.market_admission`. ``context`` (an ``AdmissionContext``) is
    mandatory so that every write carries its request window and provenance;
    a call without it fails instead of silently writing unguarded.

    Returns an ``AdmissionOutcome``; callers must treat ``outcome.blocked`` as
    "do not consume this symbol for this run".
    """
    from core.market_admission import admit_price_batch

    if context is None:
        raise TypeError("save_price_data requires an AdmissionContext")

    if symbol is None:
        if df is None or len(df) == 0:
            raise ValueError("symbol is required when the batch is empty")
        columns = {str(c).lower(): c for c in df.columns}
        if "symbol" not in columns:
            raise ValueError("batch has no symbol column and no symbol was given")
        symbols = {str(v).strip().upper() for v in df[columns["symbol"]]}
        if len(symbols) != 1:
            raise ValueError(
                "save_price_data accepts exactly one symbol per batch; got "
                + ", ".join(sorted(symbols)[:5])
            )
        symbol = next(iter(symbols))

    return admit_price_batch(
        df,
        symbol=symbol,
        context=context,
        market_db_path=DATABASE_PATH,
    )

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


def initialize_market_database() -> None:
    """Explicitly create the canonical market schema at a runtime boundary."""
    init_database()
    create_signal_table()

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
