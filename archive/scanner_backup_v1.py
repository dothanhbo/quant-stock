from sqlalchemy import text
from core.signal_database import save_signal
from core.database import (
    engine,
    load_price_data,
    get_symbol_latest_dates,
    get_reference_market_date
)
from strategy.indicators import add_indicators
from services.telegram import build_scan_message, send_telegram
import pandas as pd

# ==========================================
# CẤU HÌNH CHIẾN LƯỢC
# ==========================================

MIN_DATA_ROWS = 80
TOP_RESULTS = 10

RSI_MIN = 45
RSI_MAX = 72

MAX_DISTANCE_EMA20 = 10.0

MIN_ADX = 20
MIN_VOL_RATIO = 1.2

MAX_RETURN_3D = 15.0

RR_RATIO = 2.0
ATR_STOP_MULTIPLIER = 1.5

DEBUG_REJECTED = False


# ==========================================
# LẤY DANH SÁCH MÃ TRONG DATABASE
# ==========================================

def get_all_symbols():
    query = text("""
        SELECT DISTINCT symbol
        FROM prices
        ORDER BY symbol ASC
    """)

    with engine.connect() as connection:
        rows = connection.execute(query).fetchall()

    return [row[0] for row in rows]


# ==========================================
# CHẤM ĐIỂM
# ==========================================

def calculate_score(latest):
    score = 0

    # Trend: tối đa 30 điểm
    if latest["close"] > latest["EMA10"]:
        score += 10

    if latest["EMA10"] > latest["EMA20"]:
        score += 10

    if latest["EMA20"] > latest["EMA50"]:
        score += 5

    if bool(latest["EMA20_Rising"]):
        score += 5

    # Volume: tối đa 20 điểm
    volume_ratio = float(latest["Vol_Ratio"])

    if volume_ratio >= 2.0:
        score += 20
    elif volume_ratio >= 1.5:
        score += 17
    elif volume_ratio >= 1.2:
        score += 13

    # RSI: tối đa 15 điểm
    rsi = float(latest["RSI"])

    if 52 <= rsi <= 65:
    	score += 15
    elif 48 <= rsi <= 72:
    	score += 10
    elif 45 <= rsi < 48:
    	score += 5

    # ADX: tối đa 10 điểm
    adx = float(latest["ADX14"])

    if adx >= 30:
        score += 10
    elif adx >= 25:
        score += 8
    elif adx >= 20:
        score += 6

    # Breakout: tối đa 15 điểm
    if bool(latest["Breakout_20D"]):
        score += 10

    if bool(latest["Volume_Breakout_5D"]):
        score += 5

    # Price action: tối đa 10 điểm
    if bool(latest["Green_Candle"]):
        score += 4

    if bool(latest["Close_Upper_Half"]):
        score += 3

    if float(latest["Body_Ratio"]) >= 0.35:
        score += 3

    return min(score, 100)


# ==========================================
# TÍNH STOP LOSS VÀ TAKE PROFIT
# ==========================================

def calculate_risk_levels(latest):
    entry = float(latest["close"])
    atr = float(latest["ATR14"])
    ema20 = float(latest["EMA20"])

    atr_stop = entry - (ATR_STOP_MULTIPLIER * atr)
    ema_stop = ema20 * 0.99

    # Chọn mức stop gần giá hơn nhưng vẫn nằm dưới entry
    valid_stops = [
        stop
        for stop in [atr_stop, ema_stop]
        if 0 < stop < entry
    ]

    if valid_stops:
        stop_loss = max(valid_stops)
    else:
        stop_loss = entry * 0.95

    risk = entry - stop_loss

    if risk <= 0:
        stop_loss = entry * 0.95
        risk = entry - stop_loss

    take_profit = entry + (risk * RR_RATIO)

    stop_loss_pct = (
        (stop_loss - entry)
        / entry
        * 100
    )

    take_profit_pct = (
        (take_profit - entry)
        / entry
        * 100
    )

    return {
        "entry": round(entry, 2),
        "stop_loss": round(stop_loss, 2),
        "take_profit": round(take_profit, 2),
        "stop_loss_pct": round(stop_loss_pct, 2),
        "take_profit_pct": round(take_profit_pct, 2)
    }


# ==========================================
# KIỂM TRA MỘT MÃ
# ==========================================
def check_signal(
    symbol,
    reference_date=None
):
    df = load_price_data(symbol)

    if df.empty or len(df) < MIN_DATA_ROWS:
        if DEBUG_REJECTED:
            print(f"⚠️ {symbol}: Không đủ dữ liệu")

        return None

    data = add_indicators(df)

    if data.empty:
        return None

    latest = data.iloc[-1]
    latest_date = pd.to_datetime(
        latest["time"],
        errors="coerce"
    )

    if pd.isna(latest_date):
        if DEBUG_REJECTED:
            print(
                f"⚠️ {symbol}: Ngày dữ liệu không hợp lệ"
            )

        return None

    latest_date_text = latest_date.strftime(
        "%Y-%m-%d"
    )

    # Chỉ quét mã có dữ liệu cùng ngày chuẩn thị trường
    if (
        reference_date is not None
        and latest_date_text != reference_date
    ):
        if DEBUG_REJECTED:
            print(
                f"⚠️ {symbol}: Dữ liệu cũ "
                f"{latest_date_text}, "
                f"ngày chuẩn {reference_date}"
            )

        return None

    required_columns = [
        "close",
        "EMA10",
        "EMA20",
        "EMA50",
        "EMA20_Rising",
        "RSI",
        "Vol_Ratio",
        "ATR14",
        "ADX14",
        "Distance_EMA20_Pct",
        "Return_3D_Pct",
        "Green_Candle",
        "Close_Upper_Half",
        "Body_Ratio"
    ]

    if latest[required_columns].isna().any():
        if DEBUG_REJECTED:
            print(f"⚠️ {symbol}: Chỉ báo chưa đầy đủ")

        return None

    # ======================================
    # ĐIỀU KIỆN CỨNG
    # ======================================

    cond_trend = (
        latest["close"] > latest["EMA10"]
        and latest["EMA10"] > latest["EMA20"]
        and latest["EMA20"] > latest["EMA50"]
        and bool(latest["EMA20_Rising"])
    )

    cond_volume = (
        latest["Vol_Ratio"] >= MIN_VOL_RATIO
        or bool(latest["Volume_Breakout_5D"])
    )

    cond_rsi = (
        RSI_MIN <= latest["RSI"] <= RSI_MAX
    )

    cond_adx = (
        latest["ADX14"] >= MIN_ADX
    )

    cond_not_extended = (
        0 <= latest["Distance_EMA20_Pct"]
        <= MAX_DISTANCE_EMA20
    )

    cond_not_overheated = (
        latest["Return_3D_Pct"]
        <= MAX_RETURN_3D
    )

    cond_price_action = (
        bool(latest["Green_Candle"])
        and bool(latest["Close_Upper_Half"])
        and latest["Body_Ratio"] >= 0.35
    )

    mandatory_passed = all([
        cond_trend,
        cond_not_extended,
        cond_not_overheated
    ])

    if not mandatory_passed:
        if DEBUG_REJECTED:
            print(
                f"❌ {symbol} | "
                f"Trend={cond_trend} | "
                f"Distance={cond_not_extended} "
                f"({latest['Distance_EMA20_Pct']:.2f}%) | "
                f"3D={cond_not_overheated} "
                f"({latest['Return_3D_Pct']:.2f}%)"
            )

        return None

    # Các điều kiện còn lại được dùng để chấm điểm,
    # không bắt buộc phải đồng thời đạt.
    score = calculate_score(latest)

    # Chỉ lấy mã đạt từ 55 điểm trở lên
    if score < 55:
        if DEBUG_REJECTED:
            print(
                f"❌ {symbol} | Score={score} | "
                f"Vol={latest['Vol_Ratio']:.2f}x | "
                f"RSI={latest['RSI']:.2f} | "
                f"ADX={latest['ADX14']:.2f} | "
                f"Candle={cond_price_action}"
            )

        return None

    risk = calculate_risk_levels(latest)

    return {
        "symbol": symbol,
        "date": latest["time"].strftime("%Y-%m-%d"),
        "score": score,
        "entry": risk["entry"],
        "stop_loss": risk["stop_loss"],
        "take_profit": risk["take_profit"],
        "stop_loss_pct": risk["stop_loss_pct"],
        "take_profit_pct": risk["take_profit_pct"],
        "ema10": round(float(latest["EMA10"]), 2),
        "ema20": round(float(latest["EMA20"]), 2),
        "ema50": round(float(latest["EMA50"]), 2),
        "rsi": round(float(latest["RSI"]), 2),
        "adx": round(float(latest["ADX14"]), 2),
        "atr": round(float(latest["ATR14"]), 2),
        "atr_percent": round(
            float(latest["ATR_Percent"]),
            2
        ),
        "volume_ratio": round(
            float(latest["Vol_Ratio"]),
            2
        ),
        "distance_ema20": round(
            float(latest["Distance_EMA20_Pct"]),
            2
        ),
        "return_3d": round(
            float(latest["Return_3D_Pct"]),
            2
        ),
        "breakout_20d": bool(
            latest["Breakout_20D"]
        ),
        "volume_breakout_5d": bool(
            latest["Volume_Breakout_5D"]
        )
    }


# ==========================================
# QUÉT TẤT CẢ MÃ
# ==========================================

def scan_all_symbols():
    symbols = get_all_symbols()

    # Không quét VNINDEX như một cổ phiếu
    symbols = [
        symbol
        for symbol in symbols
        if symbol != "VNINDEX"
    ]

    reference_date = get_reference_market_date()

    latest_dates = get_symbol_latest_dates()

    fresh_symbols = []
    stale_symbols = []

    for symbol in symbols:
        symbol_date = latest_dates.get(symbol)

        if symbol_date == reference_date:
            fresh_symbols.append(symbol)
        else:
            stale_symbols.append({
                "symbol": symbol,
                "latest_date": symbol_date
            })

    print("\n" + "=" * 65)
    print("📅 KIỂM TRA NGÀY DỮ LIỆU")
    print("=" * 65)
    print(f"Ngày chuẩn thị trường: {reference_date}")
    print(
        f"Mã dữ liệu đúng ngày: "
        f"{len(fresh_symbols)}/{len(symbols)}"
    )
    print(
        f"Mã dữ liệu cũ/lỗi: "
        f"{len(stale_symbols)}"
    )

    if stale_symbols:
        stale_text = ", ".join(
            (
                f"{item['symbol']}"
                f"({item['latest_date'] or 'không có'})"
            )
            for item in stale_symbols
        )

        print(f"⚠️ Danh sách: {stale_text}")

    signals = []
    scan_errors = []

    print(
        f"\n🔍 Bắt đầu quét "
        f"{len(fresh_symbols)} mã hợp lệ..."
    )

    for index, symbol in enumerate(
        fresh_symbols,
        start=1
    ):
        print(
            f"\rĐang quét "
            f"{index}/{len(fresh_symbols)}: {symbol}",
            end="",
            flush=True
        )

        try:
            signal = check_signal(
                symbol,
                reference_date=reference_date
            )

            if signal:
                signals.append(signal)

        except Exception as error:
            scan_errors.append({
                "symbol": symbol,
                "error": str(error)
            })

            print(
                f"\n❌ Lỗi quét {symbol}: {error}"
            )

    signals.sort(
        key=lambda item: (
            item["score"],
            item["volume_ratio"],
            item["adx"]
        ),
        reverse=True
    )

    scan_stats = {
        "reference_date": reference_date,
        "total_symbols": len(symbols),
        "fresh_count": len(fresh_symbols),
        "stale_count": len(stale_symbols),
        "stale_symbols": stale_symbols,
        "error_count": len(scan_errors),
        "scan_errors": scan_errors
    }

    return signals, scan_stats


# ==========================================
# IN KẾT QUẢ
# ==========================================

def print_scan_results(signals):
    print("\n\n" + "=" * 65)
    print("🚀 KẾT QUẢ QUÉT CỔ PHIẾU NGẮN HẠN")
    print("=" * 65)

    if not signals:
        print(
            "Không có mã nào thỏa toàn bộ "
            "điều kiện hôm nay."
        )
        return

    for index, signal in enumerate(
        signals[:TOP_RESULTS],
        start=1
    ):
        breakout_text = (
            "Có"
            if signal["breakout_20d"]
            else "Chưa"
        )

        volume_breakout_text = (
            "Có"
            if signal["volume_breakout_5d"]
            else "Không"
        )

        print(
            f"\n#{index} {signal['symbol']} "
            f"| Điểm: {signal['score']}/100"
        )

        print(
            f"Ngày: {signal['date']}"
        )

        print(
            f"Entry: {signal['entry']:.2f}"
        )

        print(
            f"EMA10 / EMA20 / EMA50: "
            f"{signal['ema10']:.2f} / "
            f"{signal['ema20']:.2f} / "
            f"{signal['ema50']:.2f}"
        )

        print(
            f"RSI: {signal['rsi']:.2f} | "
            f"ADX: {signal['adx']:.2f}"
        )

        print(
            f"Volume: "
            f"{signal['volume_ratio']:.2f}x MA20"
        )

        print(
            f"ATR: {signal['atr']:.2f} "
            f"({signal['atr_percent']:.2f}%)"
        )

        print(
            f"Breakout 20D: {breakout_text} | "
            f"Volume breakout 5D: "
            f"{volume_breakout_text}"
        )

        print(
            f"Cách EMA20: "
            f"{signal['distance_ema20']:.2f}% | "
            f"Tăng 3 phiên: "
            f"{signal['return_3d']:.2f}%"
        )

        print(
            f"Stop Loss: "
            f"{signal['stop_loss']:.2f} "
            f"({signal['stop_loss_pct']:.2f}%)"
        )

        print(
            f"Take Profit: "
            f"{signal['take_profit']:.2f} "
            f"(+{signal['take_profit_pct']:.2f}%)"
        )

        print("-" * 65)

    print(
        f"\nTổng tín hiệu đạt điều kiện: "
        f"{len(signals)}"
    )


if __name__ == "__main__":
    results, scan_stats = scan_all_symbols()

    print_scan_results(results)

    # ======================================
    # 1. LƯU TÍN HIỆU VÀO DATABASE
    # ======================================

    saved_count = 0
    duplicate_count = 0
    save_failed_count = 0

    for signal in results:
        try:
            inserted = save_signal(signal)

            if inserted:
                saved_count += 1
                print(
                    f"✅ Đã lưu tín hiệu "
                    f"{signal['symbol']}"
                )
            else:
                duplicate_count += 1
                print(
                    f"ℹ️ Tín hiệu {signal['symbol']} "
                    f"đã tồn tại trong ngày"
                )

        except Exception as error:
            save_failed_count += 1

            print(
                f"❌ Không lưu được "
                f"{signal['symbol']}: {error}"
            )

    print("\n" + "-" * 60)
    print(f"Tín hiệu mới: {saved_count}")
    print(f"Tín hiệu trùng: {duplicate_count}")
    print(f"Lỗi lưu: {save_failed_count}")

    # ======================================
    # 2. TẠO NỘI DUNG TELEGRAM
    # ======================================

    try:
        message = build_scan_message(
            results,
            top_n=TOP_RESULTS
        )

    except Exception as error:
        message = None

        print(
            f"❌ Không tạo được nội dung Telegram: "
            f"{error}"
        )

    # ======================================
    # 3. GỬI TELEGRAM RIÊNG
    # ======================================

    if message:
        try:
            send_telegram(message)

            print(
                "\n✅ Đã gửi kết quả quét "
                "lên Telegram."
            )

        except Exception as error:
            print(
                f"\n❌ Gửi Telegram thất bại: "
                f"{error}"
            )