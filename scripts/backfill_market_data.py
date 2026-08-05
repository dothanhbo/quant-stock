from __future__ import annotations

import argparse
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


# Bản Community của vnstock giới hạn OHLCV 1D
# tối đa khoảng 8 năm.
BACKFILL_YEARS = 8

# KBS đang phản hồi nhanh hơn VCI trong quá trình test.
DATA_SOURCE = "KBS"

# Khoảng nghỉ giữa các request.
REQUEST_DELAY_SECONDS = 1.2


def get_backfill_start_date() -> datetime:
    return (
        datetime.now()
        - timedelta(
            days=365 * BACKFILL_YEARS
        )
    )


def normalize_symbols(
    symbols: list[str],
) -> list[str]:
    return list(
        dict.fromkeys(
            symbol.strip().upper()
            for symbol in symbols
            if symbol.strip()
        )
    )


def backfill_symbol(
    symbol: str,
    *,
    start_date: datetime,
    end_date: datetime,
) -> bool:
    symbol = (
        symbol
        .strip()
        .upper()
    )

    print(
        f"📥 {symbol}: "
        f"{start_date:%Y-%m-%d} "
        f"→ {end_date:%Y-%m-%d}"
    )

    try:
        quote = Quote(
            symbol=symbol,
            source=DATA_SOURCE,
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
        error_name = type(
            error
        ).__name__

        print(
            f"❌ {symbol}: "
            f"{error_name}: {error}"
        )

        return False


def backfill_all_symbols(
    symbols: list[str],
) -> tuple[int, list[str]]:
    normalized_symbols = normalize_symbols(
        symbols
    )

    if not normalized_symbols:
        raise ValueError(
            "Không có mã hợp lệ để backfill."
        )

    start_date = (
        get_backfill_start_date()
    )
    end_date = datetime.now()

    success_count = 0
    failed_symbols: list[str] = []

    print(
        f"\n🚀 Bắt đầu backfill "
        f"{len(normalized_symbols)} mã..."
    )
    print(
        f"Nguồn dữ liệu: {DATA_SOURCE}"
    )
    print(
        f"Khoảng dữ liệu: "
        f"{start_date:%Y-%m-%d} "
        f"→ {end_date:%Y-%m-%d}"
    )

    for index, symbol in enumerate(
        normalized_symbols,
        start=1,
    ):
        print(
            f"\n[{index}/{len(normalized_symbols)}]"
        )

        success = backfill_symbol(
            symbol,
            start_date=start_date,
            end_date=end_date,
        )

        if success:
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
        "📊 KẾT QUẢ BACKFILL"
    )
    print(
        "=" * 60
    )
    print(
        f"✅ Thành công: "
        f"{success_count}/"
        f"{len(normalized_symbols)}"
    )

    if failed_symbols:
        print(
            "❌ Mã lỗi: "
            + ", ".join(
                failed_symbols
            )
        )
    else:
        print(
            "✅ Tất cả mã đã backfill thành công."
        )

    return (
        success_count,
        failed_symbols,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill dữ liệu lịch sử cho "
            "toàn bộ universe hoặc một số mã."
        )
    )

    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help=(
            "Chỉ backfill các mã được chỉ định. "
            "Ví dụ: --symbols SJS VIB"
        ),
    )

    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.symbols:
        symbols = normalize_symbols(
            args.symbols
        )
    else:
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