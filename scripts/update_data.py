from datetime import datetime, timedelta
import time

import pandas as pd

from vnstock.api.quote import Quote

from core.database import (
    get_latest_price_date,
    save_price_data
)
from vnstock import Vnstock

stock_api = Vnstock()


# ==========================================
# CẤU HÌNH
# ==========================================

INITIAL_HISTORY_DAYS = 500

# Lấy lùi lại vài ngày để cập nhật lại phiên gần nhất,
# phòng trường hợp dữ liệu cuối ngày bị điều chỉnh.
REFRESH_OVERLAP_DAYS = 7

REQUEST_DELAY_SECONDS = 1.1

def get_vn100_symbols():

    try:

        result = (
            stock_api
            .stock(
                symbol="ACB",
                source="VCI"
            )
            .listing
            .symbols_by_group("VN100")
        )


        print("\nDEBUG VN100 TYPE:")
        print(type(result))


        # Trường hợp trả về DataFrame
        if hasattr(result, "columns"):

            if "ticker" in result.columns:
                return result["ticker"].tolist()


            if "symbol" in result.columns:
                return result["symbol"].tolist()



        # Trường hợp trả về Series/list
        if hasattr(result, "tolist"):

            return result.tolist()



        return list(result)



    except Exception as e:

        print(
            "❌ Không lấy được VN100:",
            e
        )

        return []


def calculate_start_date(symbol):
    """
    Nếu chưa có dữ liệu:
        tải khoảng 500 ngày.

    Nếu đã có dữ liệu:
        tải lại từ 7 ngày trước ngày mới nhất.
    """

    latest_date = get_latest_price_date(symbol)

    if latest_date is None or pd.isna(latest_date):
        return (
            datetime.now()
            - timedelta(days=INITIAL_HISTORY_DAYS)
        )

    return (
        latest_date
        - timedelta(days=REFRESH_OVERLAP_DAYS)
    )


def update_symbol(symbol):
    start_date = calculate_start_date(symbol)
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
    symbols = get_vn100_symbols()

    if len(symbols) < 90:
        raise RuntimeError(
            f"Danh sách VN100 không hợp lệ: chỉ có {len(symbols)} mã"
        )

    update_all_symbols(symbols)
