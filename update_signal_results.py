from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import text

from database import engine, load_price_data


# ==========================================
# CẤU HÌNH
# ==========================================

# Số phiên giao dịch tối đa để theo dõi một tín hiệu.
MAX_HOLDING_DAYS = 15

# Khi trong cùng một phiên:
# Low <= Stop Loss và High >= Take Profit
#
# Dữ liệu ngày không biết mức nào xảy ra trước.
# Chọn LOSS để đánh giá bảo thủ.
SAME_DAY_POLICY = "LOSS"

VALID_SAME_DAY_POLICIES = {
    "LOSS",
    "WIN",
    "AMBIGUOUS"
}


# ==========================================
# BỔ SUNG CỘT NẾU DATABASE CŨ CHƯA CÓ
# ==========================================

def ensure_signal_result_columns() -> None:
    """
    Tự bổ sung các cột cần thiết vào bảng signals.

    Hàm này an toàn khi chạy nhiều lần:
    cột nào đã tồn tại sẽ không được tạo lại.
    """

    required_columns = {
        "exit_price": "REAL",
        "exit_date": "TEXT",
        "result": "REAL",
        "holding_days": "INTEGER"
    }

    with engine.begin() as connection:
        existing_rows = connection.execute(
            text("PRAGMA table_info(signals)")
        ).fetchall()

        existing_columns = {
            row[1]
            for row in existing_rows
        }

        for column_name, column_type in required_columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(
                        f"""
                        ALTER TABLE signals
                        ADD COLUMN {column_name} {column_type}
                        """
                    )
                )

                print(
                    f"✅ Đã thêm cột signals.{column_name}"
                )


# ==========================================
# ĐỌC TÍN HIỆU OPEN
# ==========================================

def load_open_signals() -> pd.DataFrame:
    query = text("""
        SELECT
            id,
            signal_date,
            symbol,
            entry,
            stop_loss,
            take_profit,
            status
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY signal_date ASC, symbol ASC
    """)

    return pd.read_sql(
        query,
        engine
    )


# ==========================================
# CẬP NHẬT MỘT TÍN HIỆU
# ==========================================

def update_signal_record(
    signal_id: int,
    status: str,
    exit_price: float | None,
    exit_date: str | None,
    result_pct: float | None,
    holding_days: int
) -> None:
    query = text("""
        UPDATE signals
        SET
            status = :status,
            exit_price = :exit_price,
            exit_date = :exit_date,
            result = :result,
            holding_days = :holding_days
        WHERE id = :signal_id
    """)

    payload = {
        "signal_id": signal_id,
        "status": status,
        "exit_price": exit_price,
        "exit_date": exit_date,
        "result": result_pct,
        "holding_days": holding_days
    }

    with engine.begin() as connection:
        connection.execute(
            query,
            payload
        )


def calculate_result_pct(
    entry: float,
    exit_price: float
) -> float:
    if entry <= 0:
        return 0.0

    return round(
        (exit_price / entry - 1) * 100,
        2
    )


def evaluate_signal(
    signal: dict[str, Any]
) -> dict[str, Any]:
    """
    Kiểm tra một tín hiệu OPEN dựa trên dữ liệu OHLCV.

    Chỉ kiểm tra các phiên SAU signal_date vì entry được xác định
    theo giá đóng cửa của phiên tạo tín hiệu.
    """

    signal_id = int(signal["id"])
    symbol = str(signal["symbol"])

    entry = float(signal["entry"])
    stop_loss = float(signal["stop_loss"])
    take_profit = float(signal["take_profit"])

    signal_date = pd.to_datetime(
        signal["signal_date"],
        errors="coerce"
    )

    if pd.isna(signal_date):
        return {
            "id": signal_id,
            "symbol": symbol,
            "updated": False,
            "status": "OPEN",
            "message": "Ngày tín hiệu không hợp lệ"
        }

    prices = load_price_data(symbol)

    if prices.empty:
        return {
            "id": signal_id,
            "symbol": symbol,
            "updated": False,
            "status": "OPEN",
            "message": "Không có dữ liệu giá"
        }

    prices = prices.copy()

    prices["time"] = pd.to_datetime(
        prices["time"],
        errors="coerce"
    )

    numeric_columns = [
        "open",
        "high",
        "low",
        "close"
    ]

    for column in numeric_columns:
        prices[column] = pd.to_numeric(
            prices[column],
            errors="coerce"
        )

    prices = (
        prices
        .dropna(
            subset=[
                "time",
                "high",
                "low",
                "close"
            ]
        )
        .sort_values("time")
        .drop_duplicates(
            subset=["time"],
            keep="last"
        )
    )

    # Không dùng phiên tạo tín hiệu.
    future_prices = prices[
        prices["time"].dt.normalize()
        > signal_date.normalize()
    ].copy()

    if future_prices.empty:
        return {
            "id": signal_id,
            "symbol": symbol,
            "updated": False,
            "status": "OPEN",
            "message": "Chưa có phiên giao dịch mới"
        }

    # Chỉ theo dõi tối đa MAX_HOLDING_DAYS phiên.
    tracked_prices = future_prices.head(
        MAX_HOLDING_DAYS
    )

    for holding_day, (_, candle) in enumerate(
        tracked_prices.iterrows(),
        start=1
    ):
        candle_date = candle["time"].strftime(
            "%Y-%m-%d"
        )

        candle_low = float(candle["low"])
        candle_high = float(candle["high"])

        hit_stop_loss = candle_low <= stop_loss
        hit_take_profit = candle_high >= take_profit

        # Trường hợp cùng phiên chạm cả hai mức.
        if hit_stop_loss and hit_take_profit:
            if SAME_DAY_POLICY == "LOSS":
                exit_price = stop_loss
                status = "LOSS"

            elif SAME_DAY_POLICY == "WIN":
                exit_price = take_profit
                status = "WIN"

            else:
                exit_price = None
                status = "AMBIGUOUS"

            result_pct = (
                calculate_result_pct(
                    entry,
                    exit_price
                )
                if exit_price is not None
                else None
            )

            update_signal_record(
                signal_id=signal_id,
                status=status,
                exit_price=exit_price,
                exit_date=candle_date,
                result_pct=result_pct,
                holding_days=holding_day
            )

            return {
                "id": signal_id,
                "symbol": symbol,
                "updated": True,
                "status": status,
                "exit_date": candle_date,
                "exit_price": exit_price,
                "result_pct": result_pct,
                "holding_days": holding_day,
                "message": (
                    "Cùng phiên chạm SL và TP"
                )
            }

        # Kiểm tra Stop Loss trước theo nguyên tắc bảo thủ.
        if hit_stop_loss:
            exit_price = stop_loss

            result_pct = calculate_result_pct(
                entry,
                exit_price
            )

            update_signal_record(
                signal_id=signal_id,
                status="LOSS",
                exit_price=exit_price,
                exit_date=candle_date,
                result_pct=result_pct,
                holding_days=holding_day
            )

            return {
                "id": signal_id,
                "symbol": symbol,
                "updated": True,
                "status": "LOSS",
                "exit_date": candle_date,
                "exit_price": exit_price,
                "result_pct": result_pct,
                "holding_days": holding_day,
                "message": "Chạm Stop Loss"
            }

        if hit_take_profit:
            exit_price = take_profit

            result_pct = calculate_result_pct(
                entry,
                exit_price
            )

            update_signal_record(
                signal_id=signal_id,
                status="WIN",
                exit_price=exit_price,
                exit_date=candle_date,
                result_pct=result_pct,
                holding_days=holding_day
            )

            return {
                "id": signal_id,
                "symbol": symbol,
                "updated": True,
                "status": "WIN",
                "exit_date": candle_date,
                "exit_price": exit_price,
                "result_pct": result_pct,
                "holding_days": holding_day,
                "message": "Chạm Take Profit"
            }

    # Đã đủ số phiên theo dõi nhưng chưa chạm TP hoặc SL.
    if len(future_prices) >= MAX_HOLDING_DAYS:
        final_candle = tracked_prices.iloc[-1]

        exit_price = float(
            final_candle["close"]
        )

        exit_date = final_candle[
            "time"
        ].strftime("%Y-%m-%d")

        result_pct = calculate_result_pct(
            entry,
            exit_price
        )

        update_signal_record(
            signal_id=signal_id,
            status="EXPIRED",
            exit_price=exit_price,
            exit_date=exit_date,
            result_pct=result_pct,
            holding_days=MAX_HOLDING_DAYS
        )

        return {
            "id": signal_id,
            "symbol": symbol,
            "updated": True,
            "status": "EXPIRED",
            "exit_date": exit_date,
            "exit_price": exit_price,
            "result_pct": result_pct,
            "holding_days": MAX_HOLDING_DAYS,
            "message": (
                f"Hết {MAX_HOLDING_DAYS} phiên"
            )
        }

    return {
        "id": signal_id,
        "symbol": symbol,
        "updated": False,
        "status": "OPEN",
        "holding_days": len(future_prices),
        "message": "Tiếp tục theo dõi"
    }


# ==========================================
# CẬP NHẬT TẤT CẢ TÍN HIỆU
# ==========================================

def update_all_open_signals() -> list[dict[str, Any]]:
    if SAME_DAY_POLICY not in VALID_SAME_DAY_POLICIES:
        raise ValueError(
            "SAME_DAY_POLICY phải là LOSS, WIN "
            "hoặc AMBIGUOUS"
        )

    ensure_signal_result_columns()

    open_signals = load_open_signals()

    print("\n" + "=" * 65)
    print("📊 CẬP NHẬT KẾT QUẢ TÍN HIỆU")
    print("=" * 65)

    if open_signals.empty:
        print("Không có tín hiệu OPEN.")
        return []

    print(
        f"Tổng tín hiệu OPEN: {len(open_signals)}"
    )

    results = []

    for _, signal_row in open_signals.iterrows():
        signal = signal_row.to_dict()

        try:
            result = evaluate_signal(signal)
            results.append(result)

            symbol = result["symbol"]
            status = result["status"]
            message = result["message"]

            if result["updated"]:
                result_text = result.get(
                    "result_pct"
                )

                if result_text is None:
                    pct_text = ""
                else:
                    pct_text = (
                        f" | {result_text:+.2f}%"
                    )

                print(
                    f"✅ {symbol}: {status}"
                    f"{pct_text} | {message}"
                )

            else:
                holding_days = result.get(
                    "holding_days",
                    0
                )

                print(
                    f"⏳ {symbol}: OPEN | "
                    f"{holding_days} phiên | "
                    f"{message}"
                )

        except Exception as error:
            print(
                f"❌ {signal['symbol']}: {error}"
            )

    closed_count = sum(
        1
        for item in results
        if item["updated"]
    )

    open_count = sum(
        1
        for item in results
        if not item["updated"]
    )

    print("\n" + "-" * 65)
    print(f"Đã đóng/cập nhật: {closed_count}")
    print(f"Tiếp tục OPEN: {open_count}")

    return results


if __name__ == "__main__":
    update_all_open_signals()