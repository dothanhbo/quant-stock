from datetime import datetime, timedelta

from vnstock.api.quote import Quote

from core.database import (
    get_latest_price_date,
    save_price_data
)


def update_vnindex():
    symbol = "VNINDEX"

    latest_date = get_latest_price_date(symbol)

    if latest_date is None:
        start_date = (
            datetime.now()
            - timedelta(days=500)
        )
    else:
        start_date = (
            latest_date
            - timedelta(days=7)
        )

    end_date = datetime.now()

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
        print("⚠️ Không có dữ liệu VNINDEX")
        return False

    df["symbol"] = symbol

    saved_rows = save_price_data(df)

    print(
        f"✅ VNINDEX: upsert {saved_rows} dòng"
    )

    return True


if __name__ == "__main__":
    update_vnindex()