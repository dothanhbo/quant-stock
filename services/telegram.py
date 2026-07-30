import os
import requests
from dotenv import load_dotenv


load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")


def format_price(price):
    """
    Dữ liệu VCI thường ở đơn vị nghìn đồng.
    Ví dụ 20.9 -> 20,900 VNĐ.
    """
    price_vnd = price * 1000

    return f"{price_vnd:,.0f} VNĐ"


def build_scan_message(signals, top_n=10):
    if not signals:
        return (
            "📊 *KẾT QUẢ QUÉT VN100*\n\n"
            "Không có mã nào đạt điều kiện hôm nay."
        )

    selected = signals[:top_n]

    lines = [
        "🚀 *TOP TÍN HIỆU NGẮN HẠN VN100*",
        ""
    ]

    for index, signal in enumerate(selected, start=1):
        breakout = (
            "Có"
            if signal["breakout_20d"]
            else "Chưa"
        )

        lines.extend([
            f"*{index}. {signal['symbol']}* "
            f"— {signal['score']}/100",
            f"📅 Ngày: `{signal['date']}`",
            f"💰 Entry: `{format_price(signal['entry'])}`",
            f"📈 RSI: `{signal['rsi']:.2f}` "
            f"| ADX: `{signal['adx']:.2f}`",
            f"📊 Volume: "
            f"`{signal['volume_ratio']:.2f}x MA20`",
            f"⚡ Breakout 20 phiên: `{breakout}`",
            f"📍 Cách EMA20: "
            f"`{signal['distance_ema20']:.2f}%`",
            f"🛑 Stop Loss: "
            f"`{format_price(signal['stop_loss'])}` "
            f"({signal['stop_loss_pct']:.2f}%)",
            f"🎯 Take Profit: "
            f"`{format_price(signal['take_profit'])}` "
            f"(+{signal['take_profit_pct']:.2f}%)",
            ""
        ])

    lines.append(
        f"✅ Tổng tín hiệu đạt điều kiện: "
        f"*{len(signals)}*"
    )

    return "\n".join(lines)


def send_telegram(message):
    if not TELEGRAM_TOKEN:
        raise ValueError(
            "Thiếu TELEGRAM_TOKEN trong file .env"
        )

    if not CHAT_ID:
        raise ValueError(
            "Thiếu CHAT_ID trong file .env"
        )

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }

    response = requests.post(
        url,
        json=payload,
        timeout=20
    )

    if not response.ok:
        raise RuntimeError(
            f"Telegram lỗi {response.status_code}: "
            f"{response.text}"
        )

    return True