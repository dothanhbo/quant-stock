from datetime import datetime, timedelta
import time

import pandas as pd

from vnstock.api.quote import Quote

from core.database import (
    get_latest_price_date,
    save_price_data
)

from core.universe import (
    BENCHMARK_SYMBOLS,
    get_all_symbols,
)

# ==========================================
# CẤU HÌNH
# ==========================================

DEFAULT_HISTORY_DAYS = 500
BENCHMARK_HISTORY_DAYS = 5_000

# Lấy lùi lại vài ngày để cập nhật lại phiên gần nhất,
# phòng trường hợp dữ liệu cuối ngày bị điều chỉnh.
REFRESH_OVERLAP_DAYS = 7

REQUEST_DELAY_SECONDS = 1.1

def calculate_start_date(symbol):
    latest_date = get_latest_price_date(symbol)

    if latest_date is None or pd.isna(latest_date):
        history_days = (
            BENCHMARK_HISTORY_DAYS
            if symbol in BENCHMARK_SYMBOLS
            else DEFAULT_HISTORY_DAYS
        )

        return (
            datetime.now()
            - timedelta(days=history_days)
        )

    return (
        latest_date
        - timedelta(days=REFRESH_OVERLAP_DAYS)
    )


def update_symbol(symbol, force_start_date: datetime | None = None,):

    start_date = (
        force_start_date
        if force_start_date is not None
        else calculate_start_date(symbol)
    )
    end_date = datetime.now()

    print(
        f"📥 {symbol}: "
        f"{start_date:%Y-%m-%d} "
        f"→ {end_date:%Y-%m-%d}"
    )

    try:
        quote = Quote(
            symbol=symbol,
            source="VCI"
        )

        df = quote.history(
            start=start_date.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"),
            interval="1D"
        )

        if df is None or df.empty:
            print(f"⚠️ {symbol}: Không có dữ liệu")
            return False

        df = df.copy()
        df["symbol"] = symbol

        saved_rows = save_price_data(df)

        latest_api_date = pd.to_datetime(
            df["time"],
            errors="coerce"
        ).max()

        latest_text = (
            latest_api_date.strftime("%Y-%m-%d")
            if not pd.isna(latest_api_date)
            else "không rõ"
        )

        print(
            f"✅ {symbol}: "
            f"upsert {saved_rows} dòng, "
            f"mới nhất {latest_text}"
        )

        return True

    except KeyboardInterrupt:
        print("\n⛔ Người dùng dừng cập nhật.")
        raise

    except Exception as error:
        print(
            f"❌ {symbol}: {error}"
        )

        return False


def update_all_symbols(symbols):
    success_count = 0
    failed_symbols = []

    print(
        f"\n🚀 Bắt đầu cập nhật {len(symbols)} mã..."
    )

    for index, symbol in enumerate(
        symbols,
        start=1
    ):
        print(
            f"\n[{index}/{len(symbols)}]"
        )

        if update_symbol(symbol):
            success_count += 1
        else:
            failed_symbols.append(symbol)

        time.sleep(
            REQUEST_DELAY_SECONDS
        )

    print("\n" + "=" * 60)
    print(
        f"✅ Thành công: "
        f"{success_count}/{len(symbols)}"
    )

    if failed_symbols:
        print(
            "❌ Mã lỗi: "
            + ", ".join(failed_symbols)
        )


if __name__ == "__main__":
    symbols = get_all_symbols()

    if len(symbols) < 100:
        raise RuntimeError(
            f"Danh sách universe không hợp lệ: chỉ có {len(symbols)} mã"
        )

    update_all_symbols(symbols)
