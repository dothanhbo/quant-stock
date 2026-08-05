from __future__ import annotations

from datetime import datetime, timedelta
import time

import pandas as pd
from vnstock.api.quote import Quote

from core.database import (
    save_price_data,
)
from core.universe import (
    get_all_symbols,
)


# Bản Community của vnstock giới hạn OHLCV 1D tối đa khoảng 8 năm.
BACKFILL_YEARS = 8
REQUEST_DELAY_SECONDS = 3.0


def get_backfill_start_date() -> datetime:
    return (
        datetime.now()
        - timedelta(
            days=365 * BACKFILL_YEARS
        )
    )


def backfill_symbol(
    symbol: str,
    *,
    start_date: datetime,
    end_date: datetime,
) -> bool:
    print(
        f"📥 {symbol}: "
        f"{start_date:%Y-%m-%d} "
        f"→ {end_date:%Y-%m-%d}"
    )

    try:
        quote = Quote(
            symbol=symbol,
            source="VCI",
        )

        df = quote.history(
            start=start_date.strftime(
                "%Y-%m-%d"
            ),
            end=end_date.strftime(
                "%Y-%m-%d"
            ),
            interval="1D",
        )

        if (
            df is None
            or df.empty
        ):
            print(
                f"⚠️ {symbol}: Không có dữ liệu."
            )
            return False

        df = df.copy()
        df["symbol"] = symbol

        saved_rows = save_price_data(
            df
        )

        latest_api_date = pd.to_datetime(
            df["time"],
            errors="coerce",
        ).max()

        latest_text = (
            latest_api_date.strftime(
                "%Y-%m-%d"
            )
            if not pd.isna(
                latest_api_date
            )
            else "không rõ"
        )

        print(
            f"✅ {symbol}: "
            f"upsert {saved_rows} dòng, "
            f"mới nhất {latest_text}"
        )

        return True

    except KeyboardInterrupt:
        print(
            "\n⛔ Người dùng dừng backfill."
        )
        raise

    except Exception as error:
        print(
            f"❌ {symbol}: {error}"
        )
        return False


def backfill_all_symbols(
    symbols: list[str],
) -> tuple[int, list[str]]:
    start_date = (
        get_backfill_start_date()
    )
    end_date = datetime.now()

    success_count = 0
    failed_symbols: list[str] = []

    print(
        f"\n🚀 Bắt đầu backfill "
        f"{len(symbols)} mã..."
    )
    print(
        f"Khoảng dữ liệu: "
        f"{start_date:%Y-%m-%d} "
        f"→ {end_date:%Y-%m-%d}"
    )

    for index, symbol in enumerate(
        symbols,
        start=1,
    ):
        print(
            f"\n[{index}/{len(symbols)}]"
        )

        if backfill_symbol(
            symbol,
            start_date=start_date,
            end_date=end_date,
        ):
            success_count += 1
        else:
            failed_symbols.append(
                symbol
            )

        time.sleep(
            REQUEST_DELAY_SECONDS
        )

    print(
        "\n"
        + "=" * 60
    )
    print(
        f"✅ Thành công: "
        f"{success_count}/{len(symbols)}"
    )

    if failed_symbols:
        print(
            "❌ Mã lỗi: "
            + ", ".join(
                failed_symbols
            )
        )

    return (
        success_count,
        failed_symbols,
    )


def main() -> None:
    symbols = list(
        get_all_symbols()
    )

    if len(symbols) < 100:
        raise RuntimeError(
            "Danh sách universe không hợp lệ: "
            f"chỉ có {len(symbols)} mã"
        )

    backfill_all_symbols(
        symbols
    )


if __name__ == "__main__":
    main()
